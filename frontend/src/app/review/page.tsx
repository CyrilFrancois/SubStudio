// page.tsx — Review: one video × one language as lines, the ones the quality gate would object to flagged, edit and save. Exports: default ReviewPage.
"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { AlertCircle, ArrowLeft, CheckCircle2, Filter, Loader2, Save, Undo2 } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import { languageName } from '@/lib/languages';
import type { CueFlag, CueList, LibraryVideo } from '@/lib/types';
import CueTable from '@/components/review/CueTable';
import CuePreview from '@/components/review/CuePreview';
import { FLAG_ORDER, flagCount } from '@/components/review/flags';

type SaveState = { state: 'idle' | 'saving' | 'saved' } | { state: 'error'; message: string };

function Review() {
  const params = useSearchParams();
  const router = useRouter();
  const videoId = params.get('video') ?? '';
  const lang = params.get('lang') ?? '';

  const [video, setVideo] = useState<LibraryVideo | null>(null);
  const [list, setList] = useState<CueList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [save, setSave] = useState<SaveState>({ state: 'idle' });

  const load = useCallback(async () => {
    if (!videoId || !lang) return;
    setError(null);
    setList(null);
    try {
      const [lib, cues] = await Promise.all([api.library(), api.cues(videoId, lang)]);
      setVideo(lib.videos.find(v => v.id === videoId) ?? null);
      setList(cues);
      setEdits({});
      setSelected(cues.cues.find(c => (c.flags ?? []).length)?.id ?? cues.cues[0]?.id ?? null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, [videoId, lang]);
  useEffect(() => { load(); }, [load]);

  const changed = useMemo(
    () => Object.entries(edits).filter(([id, t]) => list?.cues.find(c => c.id === Number(id))?.text !== t).length,
    [edits, list]);

  // Leaving with unsaved edits loses them: the browser asks first.
  useEffect(() => {
    if (!changed) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [changed]);

  // Stable, so CueTable's memoised rows skip rendering on every other line's keystroke.
  const onEdit = useCallback((id: number, t: string) => {
    setEdits(p => ({ ...p, [id]: t }));
    setSave({ state: 'idle' });
  }, []);

  const counts = useMemo(() => {
    const m = new Map<CueFlag, number>();
    list?.cues.forEach(c => (c.flags ?? []).forEach(f => m.set(f, (m.get(f) ?? 0) + 1)));
    return m;
  }, [list]);
  const flaggedLines = list?.cues.filter(c => (c.flags ?? []).length).length ?? 0;
  const shown = list ? (onlyFlagged ? list.cues.filter(c => (c.flags ?? []).length || c.id in edits) : list.cues) : [];
  const current = list?.cues.find(c => c.id === selected) ?? null;
  const sourceLabel = list?.from_language ? languageName(list.from_language) : null;

  const doSave = async () => {
    if (!list) return;
    setSave({ state: 'saving' });
    try {
      const saved = await api.saveCues({ ...list, cues: list.cues.map(c => ({ ...c, text: edits[c.id] ?? c.text })) });
      setList(saved);
      setEdits({});
      setSave({ state: 'saved' });
    } catch (e) {
      // Edits stay on screen: a failed save must never look like a saved one, nor lose the work.
      setSave({ state: 'error', message: (e as ApiError).message });
    }
  };

  const languages = video?.coverage ?? [];
  const title = video?.name ?? videoId;

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 pt-5 pb-3 border-b border-white/10 space-y-2">
        <Link href="/" className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200">
          <ArrowLeft size={13} aria-hidden /> Library
        </Link>
        <div className="flex items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-white truncate" title={title}>{title || 'Review'}</h1>
            {list && (
              <p className="text-xs text-gray-500 mt-0.5">
                {list.cues.length} lines · {list.origin}{sourceLabel ? ` from ${sourceLabel}` : ''}
                {' · '}{flaggedLines ? `${flaggedLines} need a look` : 'nothing flagged'}
                {FLAG_ORDER.filter(f => counts.get(f)).map(f => <span key={f}> · {flagCount(f, counts.get(f) ?? 0)}</span>)}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {languages.length > 0 && (
              <label className="flex items-center gap-2 text-xs text-gray-400">
                Language
                <select
                  value={lang}
                  onChange={e => {
                    if (changed && !window.confirm(`${changed} edited line${changed === 1 ? '' : 's'} will be lost. Switch language?`)) return;
                    router.push(`/review?video=${encodeURIComponent(videoId)}&lang=${encodeURIComponent(e.target.value)}`);
                  }}
                  className="bg-black/40 border border-white/10 rounded-md px-2 py-1.5 text-sm text-gray-200 outline-none focus:border-indigo-500"
                >
                  {languages.map(c => (
                    <option key={c.language} value={c.language}>
                      {c.language === 'ov' ? `Original version${c.resolved ? ` (${languageName(c.resolved)})` : ''}` : languageName(c.language)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {list && <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
              <input type="checkbox" checked={onlyFlagged} onChange={e => setOnlyFlagged(e.target.checked)} className="w-4 h-4 accent-indigo-600" />
              <Filter size={13} aria-hidden /> Only lines that need a look
            </label>}
          </div>
        </div>
      </header>

      {!videoId || !lang ? (
        <p className="px-6 py-10 text-sm text-gray-400">
          Open a subtitle from the <Link href="/" className="underline underline-offset-2">Library</Link> (a validated cell) or from a finished job in the <Link href="/queue" className="underline underline-offset-2">Queue</Link>.
        </p>
      ) : error ? (
        <div role="alert" className="mx-6 mt-4 flex items-start gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
          <AlertCircle size={16} className="shrink-0 mt-0.5" aria-hidden />
          <span>Could not read these subtitles: {error}</span>
        </div>
      ) : !list ? (
        <div className="flex items-center gap-2 px-6 py-10 text-sm text-gray-400"><Loader2 size={16} className="animate-spin" aria-hidden /> Reading the subtitles…</div>
      ) : (
        <div className="flex-1 min-h-0 grid grid-cols-[1fr_22rem]">
          <div className="min-h-0 overflow-y-auto border-r border-white/10">
            {shown.length === 0
              ? <p className="px-6 py-10 text-sm text-gray-400 flex items-center gap-2"><CheckCircle2 size={15} className="text-emerald-400" aria-hidden /> No line needs a look.</p>
              : <CueTable cues={shown} edits={edits} selected={selected} onSelect={setSelected}
                          onEdit={onEdit}
                          sourceLabel={sourceLabel} />}
          </div>
          <aside className="min-h-0 overflow-y-auto p-4">
            <CuePreview cue={current} text={current ? (edits[current.id] ?? current.text) : ''} limits={list.limits} sourceLabel={sourceLabel} />
          </aside>
        </div>
      )}

      {list && (
        <footer className="px-6 py-3 border-t border-white/10 flex items-center gap-4 bg-[#0a0a0a]">
          {save.state === 'error' ? (
            <span role="alert" className="flex items-center gap-2 text-sm text-red-300 min-w-0">
              <AlertCircle size={16} className="shrink-0" aria-hidden /> <span className="truncate" title={save.message}>Not saved: {save.message}</span>
            </span>
          ) : save.state === 'saved' ? (
            <span role="status" className="flex items-center gap-2 text-sm text-emerald-300"><CheckCircle2 size={16} aria-hidden /> Saved and checked again.</span>
          ) : (
            <span className="text-xs text-gray-500">
              {changed ? `${changed} line${changed === 1 ? '' : 's'} edited, not saved.` : 'Click a line to edit it.'}
            </span>
          )}
          <button type="button" disabled={!changed || save.state === 'saving'} onClick={() => { setEdits({}); setSave({ state: 'idle' }); }}
                  className="ml-auto flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm border border-white/10 text-gray-300 hover:bg-white/5 disabled:opacity-40">
            <Undo2 size={14} aria-hidden /> Discard
          </button>
          <button type="button" disabled={!changed || save.state === 'saving'} onClick={doSave}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white disabled:bg-white/5 disabled:text-gray-600 disabled:cursor-not-allowed">
            {save.state === 'saving' ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Save size={15} aria-hidden />} Save
          </button>
        </footer>
      )}
    </div>
  );
}

// useSearchParams needs a Suspense boundary for `next build` to prerender the page shell.
export default function ReviewPage() {
  return <Suspense fallback={null}><Review /></Suspense>;
}
