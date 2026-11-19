// page.tsx — Settings: the one wanted-language list, output, whether existing subtitles count; saved on change. Exports: default SettingsPage.
"use client";

import { useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowDown, ArrowUp, Check, CheckCircle2, Loader2, Plus, X, XCircle } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import { ISO_639_1, languageName } from '@/lib/languages';
import type { ProviderStatus, Settings } from '@/lib/types';

type Save = { state: 'idle' | 'saving' | 'saved' } | { state: 'error'; message: string };

// OV first: it is a choice of its own (the audio's language), not a language among the others.
const SORTED = ['ov', ...[...ISO_639_1].sort((a, b) => languageName(a).localeCompare(languageName(b)))];

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="py-5 border-b border-white/5">
      <h2 className="text-sm font-semibold text-gray-200">{title}</h2>
      {hint && <p className="text-xs text-gray-500 mt-1 max-w-xl">{hint}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [save, setSave] = useState<Save>({ state: 'idle' });
  const [adding, setAdding] = useState('');

  useEffect(() => {
    api.settings()
      .then(r => { setSettings(r.settings); setProviders(r.providers); })
      .catch((e: ApiError) => setLoadError(e.message));
  }, []);

  // One save at a time: two PUTs in flight could land in either order, leaving the file and the
  // screen different. The ref closes the window between a click and React disabling the fieldset.
  const saving = useRef(false);

  const apply = async (next: Settings) => {
    if (saving.current) return;
    saving.current = true;
    const previous = settings;
    setSettings(next);
    setSave({ state: 'saving' });
    try {
      const r = await api.saveSettings(next);
      setSettings(r.settings); // the backend normalises codes; show what it stored
      setProviders(r.providers);
      setSave({ state: 'saved' });
    } catch (e) {
      // Roll back: the screen must never show a value the backend did not keep.
      setSettings(previous);
      setSave({ state: 'error', message: (e as ApiError).message });
    } finally {
      saving.current = false;
    }
  };

  if (loadError) {
    return (
      <div className="p-6">
        <div role="alert" className="flex items-start gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
          <AlertCircle size={16} className="shrink-0 mt-0.5" aria-hidden /> Could not read the settings: {loadError}
        </div>
      </div>
    );
  }
  if (!settings) {
    return <div className="p-6 flex items-center gap-2 text-sm text-gray-400"><Loader2 size={16} className="animate-spin" aria-hidden /> Loading settings…</div>;
  }

  const langs = settings.languages;
  const move = (i: number, d: -1 | 1) => {
    const next = [...langs];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    apply({ ...settings, languages: next });
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl px-6 py-5">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold text-white">Settings</h1>
          <span role="status" className="text-xs flex items-center gap-1.5">
            {save.state === 'saving' && <><Loader2 size={13} className="animate-spin text-gray-400" aria-hidden /><span className="text-gray-400">Saving…</span></>}
            {save.state === 'saved' && <><CheckCircle2 size={13} className="text-emerald-400" aria-hidden /><span className="text-emerald-300">Saved</span></>}
            {save.state === 'error' && <><AlertCircle size={13} className="text-red-400" aria-hidden /><span className="text-red-300">Not saved: {save.message}</span></>}
          </span>
        </div>

        <fieldset disabled={save.state === 'saving'} className="disabled:opacity-70">
        <Section
          title="Languages I want"
          hint="Every video should end up with subtitles in these languages, remembered for next time. This is the only language list: the Library columns and « Start » use it, in this order. When the original version is already English or French, it is one track, made once."
        >
          <ol className="space-y-1.5 max-w-md">
            {langs.map((l, i) => (
              <li key={l} className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-white/[0.03] border border-white/10 text-sm">
                <span className="text-gray-200">{languageName(l)}</span>
                {l === 'ov'
                  ? <span className="text-xs text-gray-500">OV, the language of the audio</span>
                  : <span className="text-xs text-gray-500 font-mono">{l}</span>}
                <span className="ml-auto flex items-center gap-1">
                  <button type="button" disabled={i === 0} onClick={() => move(i, -1)}
                          aria-label={`Move ${languageName(l)} up`} title="Move up"
                          className="p-1 rounded text-gray-400 hover:bg-white/10 disabled:opacity-30"><ArrowUp size={14} /></button>
                  <button type="button" disabled={i === langs.length - 1} onClick={() => move(i, 1)}
                          aria-label={`Move ${languageName(l)} down`} title="Move down"
                          className="p-1 rounded text-gray-400 hover:bg-white/10 disabled:opacity-30"><ArrowDown size={14} /></button>
                  <button type="button" disabled={langs.length === 1}
                          onClick={() => apply({ ...settings, languages: langs.filter(x => x !== l) })}
                          aria-label={`Remove ${languageName(l)}`}
                          title={langs.length === 1 ? 'Keep at least one language' : 'Remove'}
                          className="p-1 rounded text-gray-400 hover:bg-red-500/20 hover:text-red-300 disabled:opacity-30"><X size={14} /></button>
                </span>
              </li>
            ))}
          </ol>
          <form
            className="flex items-center gap-2 mt-3"
            onSubmit={e => {
              e.preventDefault();
              if (adding && !langs.includes(adding)) apply({ ...settings, languages: [...langs, adding] });
              setAdding('');
            }}
          >
            <label className="sr-only" htmlFor="add-language">Add a language</label>
            <select
              id="add-language"
              value={adding}
              onChange={e => setAdding(e.target.value)}
              className="bg-black/40 border border-white/10 rounded-md px-2 py-1.5 text-sm outline-none focus:border-indigo-500"
            >
              <option value="">Add a language…</option>
              {SORTED.filter(c => !langs.includes(c)).map(c => (
                <option key={c} value={c}>{c === 'ov' ? 'Original version (language of the audio)' : `${languageName(c)} (${c})`}</option>
              ))}
            </select>
            <button type="submit" disabled={!adding}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm bg-indigo-600 hover:bg-indigo-500 text-white disabled:bg-white/5 disabled:text-gray-600">
              <Plus size={14} aria-hidden /> Add
            </button>
          </form>
        </Section>

        <Section
          title="Subtitles that already exist"
          hint="On: subtitles already in or next to a video are checked for timing, language and readability when the video is started; good ones are kept and the file stays as it is. Until then the Library shows them as « not checked yet ». Off: they are ignored and every language is made again."
        >
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
            <input type="checkbox" checked={settings.count_existing}
                   onChange={e => apply({ ...settings, count_existing: e.target.checked })}
                   className="w-4 h-4 accent-indigo-600" />
            Keep existing subtitles when the quality check passes them
          </label>
        </Section>

        <Section
          title="Where subtitles go"
          hint="Into the video file itself. SubStudio writes a new copy next to it, checks it (same picture and sound, same length, every new subtitle readable), and only then replaces the original. The name gets the languages added, for example « Ghosts S05E01 (Sub-EN,FR).mkv ». A video whose subtitles were already good is not touched at all."
        >
          <span className="sr-only">No setting: this is how SubStudio always works.</span>
        </Section>

        <Section title="Services" hint="Only whether each service is ready is shown, never a key. Keys live in the backend's .env file.">
          <ul className="space-y-1.5 text-sm">
            {providers.map(p => (
              <li key={p.name} className="flex items-center gap-2">
                {p.configured
                  ? <><Check size={14} className="text-emerald-400" aria-hidden /><span className="text-gray-300">{p.name}</span><span className="text-xs text-emerald-300">ready</span></>
                  : <><XCircle size={14} className="text-gray-500" aria-hidden /><span className="text-gray-300">{p.name}</span><span className="text-xs text-gray-500">not set up</span></>}
              </li>
            ))}
          </ul>
        </Section>
        </fieldset>
      </div>
    </div>
  );
}
