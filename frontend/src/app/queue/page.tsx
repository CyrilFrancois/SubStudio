// page.tsx — Queue: the batch bar, the running job's stages, what is next, and the history with its reasons. Exports: default QueuePage.
"use client";

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { AlertCircle, Inbox, Loader2, WifiOff } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import { useJobEvents } from '@/lib/events';
import type { Job } from '@/lib/types';
import JobCard from '@/components/queue/JobCard';

const FINISHED: Job['state'][] = ['done', 'failed', 'cancelled'];

/** 0..1 for one job: finished = 1, queued = 0, running = share of its non-skipped stages done. */
function jobShare(j: Job): number {
  if (FINISHED.includes(j.state)) return 1;
  if (j.state !== 'running') return 0;
  const stages = j.stages.filter(s => s.state !== 'skipped');
  if (!stages.length) return 0;
  return stages.reduce((n, s) => n + (s.state === 'done' ? 1 : s.state === 'running' ? (s.progress ?? 0) : 0), 0) / stages.length;
}

export default function QueuePage() {
  const [listed, setListed] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setListed((await api.jobs()).jobs);
      setError(null);
    } catch (e) {
      setError((e as ApiError).message);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  // A finished job: reload the list so history order and finished_at come from the store.
  const { jobs: live, logs, connection } = useJobEvents(load);

  // Live frames win over the listed snapshot (they are newer full copies of the same jobs).
  const all = useMemo(() => {
    const byId = new Map((listed ?? []).map(j => [j.id, j]));
    Object.values(live).forEach(j => byId.set(j.id, j));
    return Array.from(byId.values());
  }, [listed, live]);

  const running = all.filter(j => j.state === 'running');
  const queued = all.filter(j => j.state === 'queued').sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const history = all.filter(j => FINISHED.includes(j.state))
    .sort((a, b) => (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at));

  // The batch = every job of the Start(s) still open, by batch_id (one per POST /api/jobs), so a job
  // that finished before this page loaded still counts. A job without batch_id counts on its own.
  const batch = useMemo(() => {
    const open = all.filter(j => !FINISHED.includes(j.state));
    const ids = new Set(open.map(j => j.batch_id).filter(Boolean));
    return all.filter(j => (j.batch_id ? ids.has(j.batch_id) : open.includes(j)));
  }, [all]);
  const done = batch.filter(j => FINISHED.includes(j.state)).length;
  const failed = batch.filter(j => j.state === 'failed').length;
  const pct = batch.length ? Math.round((batch.reduce((n, j) => n + jobShare(j), 0) / batch.length) * 100) : 0;

  const cancel = async (job: Job) => {
    setCancelling(prev => new Set(prev).add(job.id));
    setNotice(null);
    try {
      await api.cancelJob(job.id);
      // The confirming frame arrives on the stream; the list reload covers a lost stream.
      await load();
    } catch (e) {
      setNotice(`Could not cancel « ${job.video_name} »: ${(e as ApiError).message}`);
    } finally {
      setCancelling(prev => { const n = new Set(prev); n.delete(job.id); return n; });
    }
  };

  const card = (j: Job) => (
    <JobCard key={j.id} job={j} live={logs[j.id] ?? []} onCancel={cancel} cancelling={cancelling.has(j.id)} />
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-5xl px-6 py-5 space-y-6">
        <header className="space-y-3">
          <div className="flex items-end justify-between gap-4">
            <h1 className="text-lg font-semibold text-white">Queue</h1>
            {connection === 'lost' && (
              <span role="status" className="flex items-center gap-1.5 text-xs text-amber-300">
                <WifiOff size={13} aria-hidden /> live updates lost, reconnecting
              </span>
            )}
          </div>
          {batch.length > 0 ? (
            <div>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-gray-200">
                  {done} of {batch.length} video{batch.length === 1 ? '' : 's'} finished
                  {failed > 0 && <span className="text-red-300"> · {failed} failed</span>}
                </span>
                <span className="text-xs text-gray-500 tabular-nums">{pct}%</span>
              </div>
              <div className="mt-2 h-2 rounded-full bg-white/[0.05] overflow-hidden"
                   role="progressbar" aria-label="Batch progress" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <div className="h-full bg-indigo-500 transition-[width] duration-700" style={{ width: `${pct}%` }} />
              </div>
            </div>
          ) : listed && (
            <p className="text-sm text-gray-400">Nothing is running. Pick videos in the <Link href="/" className="underline underline-offset-2 hover:text-gray-200">Library</Link> and press Start.</p>
          )}
        </header>

        {error && (
          <div role="alert" className="flex items-start gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
            <AlertCircle size={16} className="shrink-0 mt-0.5" aria-hidden />
            Could not read the queue: {error}{listed ? ' The list below may be out of date.' : ''}
          </div>
        )}
        {notice && (
          <div role="alert" className="flex items-start gap-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
            <AlertCircle size={16} className="shrink-0 mt-0.5" aria-hidden /> {notice}
          </div>
        )}
        {!listed && !error && (
          <div className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={16} className="animate-spin" aria-hidden /> Reading the queue…</div>
        )}

        {running.length > 0 && (
          <section aria-label="Running now" className="space-y-2">
            <h2 className="text-xs font-semibold text-gray-400">Running now</h2>
            {running.map(card)}
          </section>
        )}
        {queued.length > 0 && (
          <section aria-label="Up next" className="space-y-2">
            <h2 className="text-xs font-semibold text-gray-400">Up next ({queued.length})</h2>
            {queued.map(card)}
          </section>
        )}
        {listed && (
          <section aria-label="History" className="space-y-2">
            <h2 className="text-xs font-semibold text-gray-400">History ({history.length})</h2>
            {history.length === 0
              ? <p className="flex items-center gap-2 text-sm text-gray-500"><Inbox size={15} aria-hidden /> No finished job yet.</p>
              : history.map(card)}
          </section>
        )}
      </div>
    </div>
  );
}
