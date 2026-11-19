// JobLogs.tsx — One job's log: stored history (GET /api/jobs/{id}/logs) then live lines, backend timestamps. Exports: default JobLogs.
"use client";

import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import type { LogEvent } from '@/lib/types';

// Whole class strings per level (no runtime-built Tailwind classes).
const LEVEL: Record<LogEvent['level'], string> = {
  DEBUG: 'text-gray-500',
  INFO: 'text-gray-300',
  WARNING: 'text-amber-300',
  ERROR: 'text-red-300',
};

const key = (l: LogEvent) => `${l.ts}|${l.level}|${l.message}`;

export default function JobLogs({ jobId, live }: { jobId: string; live: LogEvent[] }) {
  const [history, setHistory] = useState<LogEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    api.jobLogs(jobId)
      .then(l => { if (alive) setHistory(l); })
      .catch((e: ApiError) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [jobId]);

  // History and the live stream overlap for lines emitted while the history request ran: dedupe.
  const lines = useMemo(() => {
    const seen = new Set<string>();
    return [...(history ?? []), ...live].filter(l => (seen.has(key(l)) ? false : (seen.add(key(l)), true)));
  }, [history, live]);

  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [lines.length]);

  if (error) {
    return (
      <div role="alert" className="flex items-center gap-2 text-xs text-red-300 px-3 py-2">
        <AlertCircle size={13} aria-hidden /> Could not read this job's log: {error}
      </div>
    );
  }
  return (
    <div ref={box} className="max-h-64 overflow-y-auto bg-black/60 border border-white/5 rounded-md px-3 py-2 font-mono text-xs leading-relaxed">
      {history === null && (
        <div className="flex items-center gap-2 text-gray-500"><Loader2 size={12} className="animate-spin" aria-hidden /> Loading the log…</div>
      )}
      {history !== null && lines.length === 0 && <div className="text-gray-500">No log lines for this job.</div>}
      {lines.map(l => (
        <div key={key(l)} className="flex gap-3">
          <span className="text-gray-600 shrink-0 tabular-nums">
            {new Date(l.ts).toLocaleTimeString([], { hour12: false })}
          </span>
          <span className={`shrink-0 w-14 ${LEVEL[l.level]}`}>{l.level === 'INFO' ? '' : l.level}</span>
          <span className={`break-words min-w-0 ${LEVEL[l.level]}`}>{l.message}</span>
        </div>
      ))}
    </div>
  );
}
