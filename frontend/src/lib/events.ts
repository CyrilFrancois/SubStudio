// events.ts — One EventSource on GET /api/events for the whole app; jobs kept by id. Exports: useJobEvents.
"use client";

import { useEffect, useRef, useState } from 'react';
import { API_BASE_URL } from './api';
import type { EventFrame, Job } from './types';

type Connection = 'connecting' | 'open' | 'lost';

/**
 * Jobs are full snapshots, so a frame simply replaces the job with that id; a fresh tab gets one
 * frame per unfinished job on connect. `onLibraryChange` fires when a job finished or the backend
 * says coverage changed, so the caller refetches the library instead of guessing.
 */
export function useJobEvents(onLibraryChange: () => void) {
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [connection, setConnection] = useState<Connection>('connecting');
  const callback = useRef(onLibraryChange);
  callback.current = onLibraryChange;
  const states = useRef<Record<string, Job['state']>>({});

  useEffect(() => {
    const es = new EventSource(`${API_BASE_URL}/api/events`);
    es.onopen = () => setConnection('open');
    // EventSource retries by itself; say so instead of pretending the stream is live.
    es.onerror = () => setConnection('lost');
    es.onmessage = (e) => {
      let frame: EventFrame;
      try {
        frame = JSON.parse(e.data);
      } catch {
        console.error('Unreadable SSE frame', e.data);
        return;
      }
      if (frame.kind === 'job') {
        const job = frame.job;
        // The previous state lives in a ref, not in the setState updater: React may run an
        // updater twice, and a side effect there would refetch twice.
        const before = states.current[job.id];
        states.current[job.id] = job.state;
        setJobs(prev => ({ ...prev, [job.id]: job }));
        if (before && before !== job.state && ['done', 'failed', 'cancelled'].includes(job.state)) {
          callback.current();
        }
      } else if (frame.kind === 'library') {
        callback.current();
      }
    };
    return () => es.close();
  }, []);

  return { jobs, connection };
}
