// CuePreview.tsx — The selected line as a viewer would see it on a dark frame, with its timing, reading speed and line lengths against the gate's limits. Exports: default CuePreview.
"use client";

import { AlertTriangle } from 'lucide-react';
import type { Cue, CueList } from '@/lib/types';
import { cps, cueFlagLabel, flagWhy, time } from './flags';

interface Props {
  cue: Cue | null;
  text: string;          // the edited text when edited, else cue.text
  limits: CueList['limits'];
  sourceLabel: string | null;
}

export default function CuePreview({ cue, text, limits, sourceLabel }: Props) {
  if (!cue) {
    return <p className="text-sm text-gray-500">Pick a line to see it here.</p>;
  }
  const speed = cps(text, cue.start_ms, cue.end_ms);
  const longest = Math.max(0, ...text.split('\n').map(l => l.length));
  const fast = limits && speed != null && speed > limits.max_cps;
  const long = limits && longest > limits.max_line_chars;
  const edited = text !== cue.text;

  return (
    <div className="space-y-3">
      {/* No video frame: the API serves no picture yet. A dark 16:9 frame shows line breaks and length as a player would. */}
      <div className="relative aspect-video rounded-md border border-white/10 bg-gradient-to-b from-[#1a1d24] to-[#07080a] overflow-hidden">
        <p className="absolute inset-x-6 bottom-[8%] text-center text-white text-[17px] leading-snug whitespace-pre-line [text-shadow:0_1px_3px_#000,0_0_2px_#000]">
          {text || <span className="text-gray-500 italic [text-shadow:none]">(empty line)</span>}
        </p>
        <span className="absolute left-2 top-2 text-[10px] text-gray-500">preview, no picture</span>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-gray-500">Line</dt>
        <dd className="text-gray-300 tabular-nums">#{cue.id} · {time(cue.start_ms)} → {time(cue.end_ms)} ({((cue.end_ms - cue.start_ms) / 1000).toFixed(1)} s)</dd>
        <dt className="text-gray-500">Reading speed</dt>
        <dd className={`tabular-nums ${fast ? 'text-amber-300' : 'text-gray-300'}`}>
          {speed == null ? '—' : `${speed.toFixed(0)} characters a second`}{limits ? ` (limit ${limits.max_cps})` : ''}
        </dd>
        <dt className="text-gray-500">Longest line</dt>
        <dd className={`tabular-nums ${long ? 'text-amber-300' : 'text-gray-300'}`}>
          {longest} characters{limits ? ` (limit ${limits.max_line_chars})` : ''}
        </dd>
        {cue.source_text != null && (
          <>
            <dt className="text-gray-500">{sourceLabel ?? 'Source'}</dt>
            <dd className="text-gray-400 whitespace-pre-line">{cue.source_text}</dd>
          </>
        )}
      </dl>

      {(cue.flags ?? []).length > 0 && (
        <ul className="space-y-1 text-xs">
          {(cue.flags ?? []).map(f => (
            <li key={f} className="flex items-start gap-1.5 text-amber-300">
              <AlertTriangle size={13} className="shrink-0 mt-0.5" aria-hidden />
              <span><span className="font-medium">{cueFlagLabel(f, cue)}:</span> {flagWhy(f, limits)}</span>
            </li>
          ))}
          {edited && <li className="text-gray-500 pl-5">Flags are from the saved text; they are measured again when you save.</li>}
        </ul>
      )}
    </div>
  );
}
