# bench_translate.py — Phase A benchmark of local subtitle translators (speed, RAM, chrF, id integrity). Exports: main.
"""Run one candidate on the EN->FR bench set and append one JSON line of results.

  .venv/Scripts/python.exe -X utf8 bench/translate/bench_translate.py --model tg-q4 --mode batch --ngl 99
  .venv/Scripts/python.exe -X utf8 bench/translate/bench_translate.py --model nllb

Modes: `cue` = one request per cue (what a pure MT model is trained for); `batch` = N numbered cues per request
(what the Translator interface sends), scored also on whether the answer carries exactly the ids it was given.
Every llama.cpp run starts its own llama-server with --threads 4 (D-0001: at most half the machine) and stops it.
"""
import argparse, json, os, re, subprocess, sys, threading, time
from pathlib import Path

import httpx, psutil, sacrebleu

HERE = Path(__file__).resolve().parent
ROOT = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SubStudio"
MODELS = ROOT / "models"
PORT = 8091
THREADS = 4

# family decides the prompt; file is under %LOCALAPPDATA%/SubStudio/models
CANDIDATES = {
    "tg-q8": ("translategemma", "translategemma-4b-it-Q8_0.gguf"),
    "tg-q4": ("translategemma", "translategemma-4b-it-Q4_K_M.gguf"),
    "hymt-q8": ("hymt", "HY-MT1.5-1.8B-Q8_0.gguf"),
    "qwen3-q4": ("qwen", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
    "nllb": ("nllb", "nllb-200-distilled-1.3B-ct2-int8"),
}
LINE = re.compile(r"^\s*\[?(\d+)\]?\s*[|:.)\]]\s*(.*)$")


class MemSampler(threading.Thread):
    """Peak RSS of the model process, and the peak drop of system available memory (an iGPU's VRAM is system RAM)."""

    def __init__(self, pid):
        super().__init__(daemon=True)
        self.proc, self.stop = psutil.Process(pid), threading.Event()
        self.base_avail = psutil.virtual_memory().available
        self.peak_rss = self.peak_drop = 0
        self.cpu = []

    def run(self):
        self.proc.cpu_percent(None)
        while not self.stop.wait(0.5):
            try:
                self.peak_rss = max(self.peak_rss, self.proc.memory_info().rss)
                self.cpu.append(self.proc.cpu_percent(None) / 100)  # in logical CPUs busy
            except psutil.NoSuchProcess:
                return
            self.peak_drop = max(self.peak_drop, self.base_avail - psutil.virtual_memory().available)


def load_set(limit):
    rows = [json.loads(l) for l in (HERE / "opensubs_en_fr_150.jsonl").read_text(encoding="utf-8").splitlines()]
    return rows[:limit]


# ---------- llama.cpp ----------

def start_server(gguf, ngl, build):
    exe = ROOT / "llama.cpp" / f"b12532-{build}" / "llama-server.exe"
    args = [str(exe), "-m", str(MODELS / gguf), "--port", str(PORT), "--threads", str(THREADS),
            "--threads-batch", str(THREADS), "-c", "4096", "-np", "1", "-ngl", str(ngl), "--no-webui"]
    log = open(HERE / "out" / "server.log", "w", encoding="utf-8")
    t0 = time.time()
    p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    while True:
        if p.poll() is not None:
            raise RuntimeError(f"llama-server exited {p.returncode}; see bench/translate/out/server.log")
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200:
                return p, time.time() - t0
        except httpx.HTTPError:
            pass
        time.sleep(0.5)


def gemma_prompt(text, src, tgt):
    # Rendered by the GGUF's own chat template (read once), so the prompt is the one the model was tuned on.
    import jinja2, gguf
    if not hasattr(gemma_prompt, "tpl"):
        r = gguf.GGUFReader(str(MODELS / CANDIDATES["tg-q4"][1]))
        f = r.fields["tokenizer.chat_template"]
        env = jinja2.Environment()
        env.globals["raise_exception"] = lambda m: (_ for _ in ()).throw(ValueError(m))
        gemma_prompt.tpl = env.from_string(bytes(f.parts[f.data[0]]).decode())
    msgs = [{"role": "user", "content": [{"type": "text", "source_lang_code": src, "target_lang_code": tgt, "text": text}]}]
    return gemma_prompt.tpl.render(messages=msgs, add_generation_prompt=True, bos_token="<bos>")


def build_request(family, text, batch):
    """Return (endpoint, json body) for one request. `batch` tells the model the text is numbered lines."""
    limit = 64 + len(text)  # ~ chars: French runs ~1.2x English tokens; a char budget is a safe ceiling
    common = {"temperature": 0, "cache_prompt": True}
    if family == "translategemma":
        return "/completion", {**common, "prompt": gemma_prompt(text, "en", "fr"), "n_predict": limit}
    common["max_tokens"] = limit
    if family == "hymt":
        # Model card template for XX<=>XX (https://huggingface.co/tencent/HY-MT1.5-1.8B, read 2026-11-19).
        user = f"Translate the following segment into French, without additional explanation.\n\n{text}"
        if batch:
            user = ("Translate the following segment into French, without additional explanation. "
                    "Keep every line and its number prefix exactly.\n\n" + text)
        return "/v1/chat/completions", {**common, "messages": [{"role": "user", "content": user}]}
    sys_p = ("You translate film and TV subtitles from English to French. Natural spoken French, concise, keep the "
             "register (tu/vous) consistent. Output only the translation.")
    if batch:
        sys_p += (" The input is numbered lines `N|text`. Answer with exactly the same numbers, one line each, "
                  "`N|translation`, nothing else.")
    return "/v1/chat/completions", {**common, "messages": [{"role": "system", "content": sys_p},
                                                           {"role": "user", "content": text}]}


def ask(family, text, batch):
    path, body = build_request(family, text, batch)
    r = httpx.post(f"http://127.0.0.1:{PORT}{path}", json=body, timeout=600)
    r.raise_for_status()
    j = r.json()
    out = j["content"] if path == "/completion" else j["choices"][0]["message"]["content"]
    t = j.get("timings", {})
    return out.strip(), t.get("predicted_n", 0), t.get("predicted_ms", 0), t.get("prompt_n", 0), t.get("prompt_ms", 0)


def run_llama(family, rows, mode, bsize):
    hyp, bad_batches, tok = {}, 0, {"pn": 0, "pms": 0, "gn": 0, "gms": 0}
    chunks = [rows[i:i + bsize] for i in range(0, len(rows), bsize)] if mode == "batch" else [[r] for r in rows]
    for ch in chunks:
        if mode == "cue":
            text = ch[0]["en"]
        else:
            text = "\n".join(f"{r['id']}|{r['en'].replace(chr(10), ' ')}" for r in ch)
        out, gn, gms, pn, pms = ask(family, text, mode == "batch")
        tok["gn"] += gn; tok["gms"] += gms; tok["pn"] += pn; tok["pms"] += pms
        if mode == "cue":
            hyp[ch[0]["id"]] = out
            continue
        got = {}
        for line in out.splitlines():
            m = LINE.match(line)
            if m:
                got[int(m.group(1))] = m.group(2).strip()
        want = {r["id"] for r in ch}
        if set(got) != want or any(not v for v in got.values()):
            bad_batches += 1
        for i in want:
            hyp[i] = got.get(i, "")
    return hyp, bad_batches, len(chunks), tok


# ---------- NLLB on CTranslate2 ----------

def run_nllb(rows, bsize):
    import ctranslate2, tokenizers
    path = MODELS / CANDIDATES["nllb"][1]
    tk = tokenizers.Tokenizer.from_file(str(path / "tokenizer.json"))
    tr = ctranslate2.Translator(str(path), device="cpu", compute_type="int8", intra_threads=THREADS, inter_threads=1)
    hyp, gn = {}, 0
    for i in range(0, len(rows), bsize):
        ch = rows[i:i + bsize]
        src = [["eng_Latn"] + tk.encode(r["en"], add_special_tokens=False).tokens + ["</s>"] for r in ch]
        res = tr.translate_batch(src, target_prefix=[["fra_Latn"]] * len(ch), beam_size=4, max_decoding_length=256)
        for r, out in zip(ch, res):
            toks = [t for t in out.hypotheses[0] if t not in ("fra_Latn", "</s>")]
            gn += len(toks)
            hyp[r["id"]] = tk.decode([tk.token_to_id(t) for t in toks]).strip()
    return hyp, gn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=CANDIDATES, required=True)
    ap.add_argument("--mode", choices=["cue", "batch"], default="batch")
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--ngl", type=int, default=0, help="layers on the iGPU (Vulkan build only); 0 = CPU")
    ap.add_argument("--build", choices=["vulkan", "cpu"], default="vulkan")
    ap.add_argument("--limit", type=int, default=150)
    a = ap.parse_args()
    (HERE / "out").mkdir(exist_ok=True)
    rows = load_set(a.limit)
    family, _ = CANDIDATES[a.model]
    # Other threads share this PC: record how busy it was, so a slow run is read in context.
    host = {"host_cpu_pct_before": psutil.cpu_percent(interval=2),
            "host_free_gb_before": round(psutil.virtual_memory().available / 2**30, 1)}
    # Never swap the shared PC: a model must fit in what is free now, plus 1 GB for the KV cache and the runtime.
    need = sum(f.stat().st_size for f in (MODELS / CANDIDATES[a.model][1]).glob("**/*") if f.is_file()) \
        if (MODELS / CANDIDATES[a.model][1]).is_dir() else (MODELS / CANDIDATES[a.model][1]).stat().st_size
    free = psutil.virtual_memory().available
    if free < need + 2**30:
        res = {"model": a.model, "mode": a.mode, "skipped": f"needs {(need + 2**30) / 2**30:.1f} GB, "
               f"{free / 2**30:.1f} GB free", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **host}
        with open(HERE / "results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(res) + "\n")
        print(json.dumps(res))
        return 0
    t0 = time.time()
    if family == "nllb":
        sampler = MemSampler(os.getpid()); sampler.start()
        hyp, gn = run_nllb(rows, a.batch)
        load_s, bad, nb, tok = None, 0, None, {"gn": gn, "gms": 0, "pn": 0, "pms": 0}
        a.mode, a.ngl, a.build = "batch", 0, "ct2-cpu"
    else:
        server, load_s = start_server(CANDIDATES[a.model][1], a.ngl, a.build)
        sampler = MemSampler(server.pid); sampler.start()
        try:
            t0 = time.time()
            hyp, bad, nb, tok = run_llama(family, rows, a.mode, a.batch)
        finally:
            sampler.stop.set(); server.terminate(); server.wait(30)
    wall = time.time() - t0
    sampler.stop.set()
    refs = [r["fr"] for r in rows]
    hyps = [hyp.get(r["id"], "") for r in rows]
    res = {
        "model": a.model, "mode": a.mode, "batch": a.batch if a.mode == "batch" else 1, "ngl": a.ngl,
        "build": a.build, "cues": len(rows), "load_s": round(load_s, 1) if load_s else None,
        "wall_s": round(wall, 1), "s_per_cue": round(wall / len(rows), 2),
        "gen_tok_s": round(tok["gn"] / tok["gms"] * 1000, 1) if tok["gms"] else None,
        "prompt_tok_s": round(tok["pn"] / tok["pms"] * 1000, 1) if tok["pms"] else None,
        "peak_rss_gb": round(sampler.peak_rss / 2**30, 2), "peak_sys_drop_gb": round(sampler.peak_drop / 2**30, 2),
        "avg_cpus_busy": round(sum(sampler.cpu) / len(sampler.cpu), 2) if sampler.cpu else None,
        "chrf": round(sacrebleu.corpus_chrf(hyps, [refs]).score, 1),
        "empty": sum(1 for h in hyps if not h), "bad_batches": bad, "batches": nb,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **host,
    }
    with open(HERE / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(res) + "\n")
    tag = f"{a.model}-{a.mode}-ngl{a.ngl}"
    (HERE / "out" / f"{tag}.jsonl").write_text(
        "".join(json.dumps({"id": r["id"], "en": r["en"], "ref": r["fr"], "hyp": h}, ensure_ascii=False) + "\n"
                for r, h in zip(rows, hyps)), encoding="utf-8")
    print(json.dumps(res))


if __name__ == "__main__":
    sys.exit(main())
