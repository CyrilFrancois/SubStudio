// StageStepper.tsx — One job's stages as the backend reports them (check → download → transcribe → translate → write; « correct » only if a job ever reports it, D-0005), each with its own bar. Exports: default StageStepper, eta.
"use client";

import { AlertCircle, Check, Circle, Loader2, MinusCircle } from 'lucide-react';
import type { StageProgress } from '@/lib/types';

const LABEL: Record<StageProgress['stage'], string> = {
  check: 'Check',
  download: 'Download',
  transcribe: 'Transcribe',
  correct: 'Correct',
  translate: 'Translate',
  write: 'Write',
};

// What each stage does, in words (the stage's hover hint; the backend's own detail line follows it).
export const STAGE_WHY: Record<StageProgress['stage'], string> = {
  check: 'Check: the quality check judges the subtitles already in or next to the video; good ones are kept',
  download: 'Download: look for a human-made subtitle online and re-time it to the audio',
  transcribe: 'Transcribe: write down what is said in the audio (when nothing good exists in that language)',
  correct: 'Correct: tidy the transcription',
  translate: 'Translate: translate a good subtitle into the missing languages',
  write: 'Write: put the new subtitles into the video file, check the copy, then replace the original',
};

// Whole class strings: a Tailwind class assembled at runtime ships no CSS.
const TONE: Record<StageProgress['state'], string> = {
  pending: 'text-gray-500',
  running: 'text-indigo-300',
  done: 'text-emerald-300',
  skipped: 'text-gray-600',
  failed: 'text-red-300',
};

const BAR: Record<StageProgress['state'], string> = {
  pending: 'bg-white/10',
  running: 'bg-indigo-500',
  done: 'bg-emerald-500/70',
  skipped: 'bg-white/5',
  failed: 'bg-red-500/70',
};

export function eta(seconds?: number | null): string | null {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  if (seconds < 60) return 'under a minute left';
  const m = Math.round(seconds / 60);
  return m < 90 ? `about ${m} min left` : `about ${Math.round(m / 6) / 10} h left`;
}

function Icon({ state }: { state: StageProgress['state'] }) {
  const p = { size: 13, 'aria-hidden': true } as const;
  switch (state) {
    case 'pending': return <Circle {...p} />;
    case 'running': return <Loader2 {...p} className="animate-spin" />;
    case 'done': return <Check {...p} />;
    case 'skipped': return <MinusCircle {...p} />;
    case 'failed': return <AlertCircle {...p} />;
  }
}

export default function StageStepper({ stages }: { stages: StageProgress[] }) {
  return (
    <ol className="grid gap-2" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }} aria-label="Stages">
      {stages.map(s => {
        const pct = s.state === 'done' ? 100 : s.state === 'running' ? Math.round((s.progress ?? 0) * 100) : 0;
        const left = s.state === 'running' ? eta(s.eta_s) : null;
        return (
          <li key={s.stage} className="min-w-0" title={`${STAGE_WHY[s.stage]}.${s.detail ? `\n${s.detail}` : ''}`}>
            <div className={`flex items-center gap-1.5 text-xs ${TONE[s.state]}`}>
              <Icon state={s.state} />
              <span className="truncate">{LABEL[s.stage]}</span>
              <span className="ml-auto tabular-nums text-[11px]">
                {s.state === 'running' ? `${pct}%` : s.state === 'skipped' ? 'skipped' : s.state === 'failed' ? 'failed' : ''}
              </span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-white/[0.04] overflow-hidden"
                 role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${LABEL[s.stage]} ${s.state}`}>
              <div className={`h-full transition-[width] duration-700 ${BAR[s.state]}`} style={{ width: `${s.state === 'skipped' ? 100 : pct}%` }} />
            </div>
            {(left || s.detail) && (
              <div className="mt-1 text-[11px] text-gray-500 truncate">{left ?? s.detail}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
