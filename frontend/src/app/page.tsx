// page.tsx — Library (home): which of my videos lack which language; select, then Start (D-0002). Exports: default LibraryPage.
"use client";

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { AlertCircle, CheckCircle2, Loader2, Play, RefreshCw, Search, WifiOff, X } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import { useJobEvents } from '@/lib/events';
import { languageName } from '@/lib/languages';
import type { Job, JobOptions, LanguageCoverage, LibraryResponse } from '@/lib/types';
import LibraryTable, { Row } from '@/components/library/LibraryTable';
import { hasOverrides } from '@/components/library/VideoDetails';

type Notice = { tone: 'ok' | 'error'; text: string } | null;

/** Live job states win over the last scan for cells that have not passed. */
function overlay(cov: LanguageCoverage[], videoId: string, jobs: Job[]): LanguageCoverage[] {
  return cov.map(c => {
    if (c.state === 'passed') return c;
    const job = jobs
      .filter(j => j.video_id === videoId && j.languages.includes(c.language))
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    if (!job || !['queued', 'running', 'failed'].includes(job.state)) return c;
    const state: LanguageCoverage['state'] = job.state === 'queued' ? 'pending' : job.state === 'running' ? 'running' : 'failed';
    return { ...c, state, job_id: job.id, reason: job.error ?? c.reason };
  });
}

// OV of an untagged video is « not detected yet » (the job detects it), not missing: the chip says so too.
function isMissing(c: LanguageCoverage): boolean {
  return c.state === 'missing' && !(c.language === 'ov' && !c.resolved);
}

export default function LibraryPage() {
  const [lib, setLib] = useState<LibraryResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [overrides, setOverrides] = useState<Record<string, JobOptions>>({});
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setLib(await api.library());
      setLoadError(null);
    } catch (e) {
      // Keep the last good table on screen, but say loudly that it is not fresh.
      setLoadError((e as ApiError).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const { jobs, connection } = useJobEvents(load);
  const jobList = useMemo(() => Object.values(jobs), [jobs]);

  const rows: Row[] = useMemo(() => (lib?.videos ?? []).map(v => ({
    video: v, coverage: overlay(v.coverage, v.id, jobList),
  })), [lib, jobList]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter(({ video, coverage }) => {
      if (q && !`${video.folder}/${video.name}`.toLowerCase().includes(q)) return false;
      if (filter === 'todo') return !video.validated;
      if (filter.startsWith('missing:')) return coverage.some(c => c.language === filter.slice(8) && isMissing(c));
      if (filter === 'unchecked') return coverage.some(c => c.state === 'unchecked');
      if (filter === 'failed') return coverage.some(c => c.state === 'failed' || c.state === 'unknown');
      if (filter === 'active') return coverage.some(c => c.state === 'pending' || c.state === 'running');
      return true;
    });
  }, [rows, query, filter]);

  const groups = useMemo(() => {
    const m = new Map<string, Row[]>();
    for (const r of visible) m.set(r.video.folder, [...(m.get(r.video.folder) ?? []), r]);
    return Array.from(m.entries());
  }, [visible]);

  const select = (ids: string[], on: boolean) => setSelected(prev => {
    const next = new Set(prev);
    ids.forEach(id => (on ? next.add(id) : next.delete(id)));
    return next;
  });

  const start = async (ids: string[]) => {
    if (ids.length === 0) return;
    setBusy(true);
    setNotice(null);
    // One request per distinct set of overrides: videos without overrides travel together.
    const plain = ids.filter(id => !hasOverrides(overrides[id]));
    const batches: [string[], JobOptions | undefined][] = [
      ...(plain.length ? [[plain, undefined] as [string[], undefined]] : []),
      ...ids.filter(id => hasOverrides(overrides[id])).map(id => [[id], overrides[id]] as [string[], JobOptions]),
    ];
    let created = 0, skipped = 0;
    try {
      for (const [videoIds, options] of batches) {
        const res = await api.createJobs({ video_ids: videoIds, languages: null, ...(options ? { options } : {}) });
        created += res.jobs.length;
        skipped += res.skipped.length;
      }
      setNotice({ tone: 'ok', text: `${created} video${created === 1 ? '' : 's'} queued${skipped ? `, ${skipped} skipped (already validated or already queued)` : ''}.` });
      setSelected(new Set());
    } catch (e) {
      const done = created ? ` (${created} queued before the error)` : '';
      setNotice({ tone: 'error', text: `${(e as ApiError).message}${done}` });
    } finally {
      setBusy(false);
    }
  };

  const languages = lib?.languages ?? [];
  const missingCount = (lang: string) => rows.filter(r => r.coverage.some(c => c.language === lang && isMissing(c))).length;
  // Existing subtitles count only once checked (D-0002): neither missing nor present, so said apart.
  const uncheckedCount = rows.filter(r => r.coverage.some(c => c.state === 'unchecked')).length;
  const selectedRows = rows.filter(r => selected.has(r.video.id));
  // Validated videos are skipped by Start (D-0002 §5): never sent, and said so.
  // Nothing ticked: Start takes every video still to do among those shown (the common path is one click:
  // « subtitle what is missing in my usual languages », user-process §7). Ticking narrows it.
  const startAll = selectedRows.length === 0;
  const toStart = (startAll ? visible : selectedRows).filter(r => !r.video.validated).map(r => r.video.id);
  const skippedValidated = startAll ? 0 : selectedRows.length - toStart.length;
  const filtered = query.trim() !== '' || filter !== 'all';

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 pt-5 pb-3 border-b border-white/10 space-y-3">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-white">Library</h1>
            <p className="text-xs text-gray-500 mt-0.5">
              {lib ? (
                <>
                  <span title={lib.root}>{lib.videos.length} video{lib.videos.length === 1 ? "" : "s"}</span>
                  {languages.map(l => <span key={l}> · {languageName(l)} missing on {missingCount(l)}</span>)}
                  {uncheckedCount > 0 && <span> · existing subtitles not checked yet on {uncheckedCount}</span>}
                  {' · '}scanned {new Date(lib.scanned_at).toLocaleTimeString()}
                  {connection === 'lost' && (
                    <span role="status" className="text-amber-300">
                      {' · '}<WifiOff size={12} className="inline -mt-0.5" aria-hidden /> live updates lost, reconnecting (cells may be stale)
                    </span>
                  )}
                </>
              ) : loading ? 'Reading the library…' : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs border border-white/10 text-gray-300 hover:bg-white/5 disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} aria-hidden />
            {loading ? 'Rescanning…' : 'Rescan'}
          </button>
        </div>

        <div className="flex items-center gap-3">
          <label className="relative flex-1 max-w-sm">
            <span className="sr-only">Search videos</span>
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" aria-hidden />
            <input
              type="search"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search by name or folder"
              className="w-full bg-black/40 border border-white/10 rounded-md pl-8 pr-2 py-1.5 text-sm outline-none focus:border-indigo-500"
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-gray-400">
            Show
            <select
              value={filter}
              onChange={e => setFilter(e.target.value)}
              className="bg-black/40 border border-white/10 rounded-md px-2 py-1.5 text-sm text-gray-200 outline-none focus:border-indigo-500"
            >
              <option value="all">All videos</option>
              <option value="todo">Not validated yet</option>
              {languages.map(l => <option key={l} value={`missing:${l}`}>Missing {languageName(l)}</option>)}
              <option value="unchecked">Existing subtitles not checked yet</option>
              <option value="failed">Failed or unreadable</option>
              <option value="active">In the queue</option>
            </select>
          </label>
          <span className="text-xs text-gray-500">{visible.length} shown</span>
          <Link href="/settings" title="The languages every video gets, remembered between runs: change them in Settings"
                className="ml-auto text-xs text-gray-400 hover:text-gray-200 underline underline-offset-2">
            Languages: {languages.map(l => l.toUpperCase()).join(', ') || '—'} (change)
          </Link>
        </div>
      </header>

      {loadError && (
        <div role="alert" className="mx-6 mt-3 flex items-start gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
          <AlertCircle size={16} className="shrink-0 mt-0.5" aria-hidden />
          <span>Could not read the library: {loadError}{lib ? ' The table below is from the previous scan.' : ''}</span>
        </div>
      )}
      {lib && lib.warnings.length > 0 && (
        <div role="status" className="mx-6 mt-3 px-3 py-2 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs space-y-0.5">
          {lib.warnings.map(w => <div key={w}>{w}</div>)}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto">
        {!lib && loading && (
          <div className="flex items-center justify-center gap-3 py-24 text-sm text-gray-400">
            <Loader2 size={18} className="animate-spin text-indigo-400" aria-hidden /> Reading the library (first scan probes every video)…
          </div>
        )}
        {lib && lib.videos.length === 0 && (
          <div className="py-24 text-center text-sm text-gray-400">
            No videos under <span className="font-mono">{lib.root}</span>. Put .mkv, .mp4 or .avi files there, then Rescan.
          </div>
        )}
        {lib && lib.videos.length > 0 && visible.length === 0 && (
          <div className="py-24 text-center text-sm text-gray-400">
            No video matches this search or filter.{' '}
            <button type="button" onClick={() => { setQuery(''); setFilter('all'); }} className="underline underline-offset-2 hover:text-gray-200">Show all videos</button>
          </div>
        )}
        {lib && visible.length > 0 && (
          <LibraryTable
            groups={groups}
            languages={languages}
            selected={selected}
            onSelect={select}
            overrides={overrides}
            onOverride={(id, o) => setOverrides(prev => ({ ...prev, [id]: o }))}
          />
        )}
      </div>

      <footer className="px-6 py-3 border-t border-white/10 flex items-center gap-4 bg-[#0a0a0a]">
        {notice ? (
          <div role="status" className={`flex items-center gap-2 text-sm min-w-0 ${notice.tone === 'ok' ? 'text-emerald-300' : 'text-red-300'}`}>
            {notice.tone === 'ok' ? <CheckCircle2 size={16} aria-hidden /> : <AlertCircle size={16} aria-hidden />}
            <span className="truncate" title={notice.text}>{notice.text}</span>
            {notice.tone === 'ok' && (
              <Link href="/queue" className="shrink-0 underline underline-offset-2 hover:text-emerald-200">Open the Queue</Link>
            )}
            <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" title="Dismiss" className="text-gray-500 hover:text-gray-300">
              <X size={14} />
            </button>
          </div>
        ) : (
          <span className="text-xs text-gray-500">
            {!startAll
              ? `${selectedRows.length} selected${skippedValidated ? `, ${skippedValidated} already validated (skipped)` : ''}.`
              : toStart.length
                ? `Start makes every missing subtitle for the ${toStart.length} video${toStart.length === 1 ? '' : 's'} still to do${filtered ? ' in this list' : ''}. Tick videos to start fewer.`
                : `Every video${filtered ? ' in this list' : ''} is validated: nothing to start.`}
          </span>
        )}
        <button
          type="button"
          disabled={busy || toStart.length === 0}
          onClick={() => start(toStart)}
          className="ml-auto flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white disabled:bg-white/5 disabled:text-gray-600 disabled:cursor-not-allowed"
        >
          {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Play size={15} aria-hidden />}
          {/* What one click does, on the button itself: how many videos, which languages (workspace-64). */}
          {startAll ? `Start all to do: ${toStart.length} video${toStart.length === 1 ? '' : 's'}` : `Start ${toStart.length} video${toStart.length === 1 ? '' : 's'}`}
          {toStart.length > 0 && languages.length > 0 && <span className="font-normal text-indigo-200"> · {languages.map(l => l.toUpperCase()).join(' ')}</span>}
        </button>
      </footer>
    </div>
  );
}
