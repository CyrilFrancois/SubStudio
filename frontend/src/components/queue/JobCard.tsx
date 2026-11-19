// JobCard.tsx — One job in the Queue: name, languages, state in words, stepper when running, cancel, log. Exports: default JobCard, JOB_STATE.
"use client";

import { useState } from 'react';
import Link from 'next/link';
import { AlertCircle, Ban, CheckCircle2, ChevronDown, ChevronRight, Clock, Loader2, XCircle } from 'lucide-react';
import type { Job, LogEvent } from '@/lib/types';
import { languageName } from '@/lib/languages';
import StageStepper from './StageStepper';
import JobLogs from './JobLogs';

export const JOB_STATE: Record<Job['state'], { label: string; tone: string }> = {
  queued: { label: 'queued', tone: 'text-indigo-300' },
  running: { label: 'running', tone: 'text-indigo-200' },
  done: { label: 'done', tone: 'text-emerald-300' },
  failed: { label: 'failed', tone: 'text-red-300' },
  cancelled: { label: 'cancelled', tone: 'text-amber-300' },
};

function StateIcon({ state }: { state: Job['state'] }) {
  const p = { size: 15, 'aria-hidden': true } as const;
  switch (state) {
    case 'queued': return <Clock {...p} />;
    case 'running': return <Loader2 {...p} className="animate-spin" />;
    case 'done': return <CheckCircle2 {...p} />;
    case 'failed': return <AlertCircle {...p} />;
    case 'cancelled': return <Ban {...p} />;
  }
}

function took(job: Job): string | null {
  if (!job.started_at) return null;
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  const s = Math.max(0, Math.round((end - Date.parse(job.started_at)) / 1000));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

interface Props {
  job: Job;
  live: LogEvent[];
  onCancel?: (job: Job) => void;
  cancelling?: boolean;
}

export default function JobCard({ job, live, onCancel, cancelling }: Props) {
  const [showLog, setShowLog] = useState(false);
  const s = JOB_STATE[job.state];
  const duration = took(job);
  const active = job.state === 'queued' || job.state === 'running';

  return (
    <article className={`border rounded-lg ${job.state === 'running' ? 'border-indigo-500/30 bg-indigo-500/[0.04]' : 'border-white/10 bg-white/[0.02]'}`}>
      <header className="flex items-center gap-3 px-4 py-3">
        <span className={`flex items-center gap-1.5 text-xs shrink-0 w-24 ${s.tone}`}>
          <StateIcon state={job.state} /> {s.label}
          {job.state === 'queued' && job.position != null && <span className="text-gray-500">#{job.position + 1}</span>}
        </span>
        <h3 className="text-sm text-gray-200 truncate min-w-0 flex-1" title={job.video_name}>{job.video_name}</h3>
        <span className="text-xs text-gray-500 shrink-0 flex gap-2" title={job.languages.map(languageName).join(', ')}>
          {job.state === 'done' && <span className="text-gray-400">Review:</span>}
          {job.state === 'done'
            // A finished job opens each language it made in Review.
            ? job.languages.map(l => (
                <Link key={l} href={`/review?video=${encodeURIComponent(job.video_id)}&lang=${encodeURIComponent(l)}`}
                      className="underline underline-offset-2 hover:text-gray-200" aria-label={`Review ${languageName(l)}`}>
                  {l.toUpperCase()}
                </Link>))
            : job.languages.map(l => l.toUpperCase()).join(' · ')}
        </span>
        {duration && <span className="text-xs text-gray-500 tabular-nums shrink-0 w-24 text-right">{duration}</span>}
        {active && onCancel && (
          <button
            type="button"
            disabled={cancelling}
            onClick={() => onCancel(job)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border border-red-500/30 text-red-300 hover:bg-red-500/10 disabled:opacity-50"
          >
            <XCircle size={13} aria-hidden /> {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
        <button
          type="button"
          onClick={() => setShowLog(v => !v)}
          aria-expanded={showLog}
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200"
        >
          {showLog ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />} Log
        </button>
      </header>

      {job.state === 'failed' && job.error && (
        <p role="status" className="px-4 pb-3 -mt-1 text-sm text-red-300 flex items-start gap-2">
          <AlertCircle size={15} className="shrink-0 mt-0.5" aria-hidden /> {job.error}
        </p>
      )}
      {job.state === 'running' && (
        <div className="px-4 pb-4"><StageStepper stages={job.stages} /></div>
      )}
      {showLog && <div className="px-4 pb-4"><JobLogs jobId={job.id} live={live} /></div>}
    </article>
  );
}
