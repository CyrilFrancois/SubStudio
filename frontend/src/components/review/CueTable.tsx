// CueTable.tsx — The subtitle as a table of lines: number, time, source, text (editable on the selected line), flags in words. Exports: default CueTable.
"use client";

import { memo, useEffect, useRef } from 'react';
import { AlertTriangle, Pencil } from 'lucide-react';
import type { Cue } from '@/lib/types';
import { cueFlagLabel, time } from './flags';

interface RowProps {
  cue: Cue;
  text: string;
  edited: boolean;
  selected: boolean;
  cols: string;
  showSource: boolean;
  onSelect: (id: number) => void;
  onEdit: (id: number, text: string) => void;
}

// Memoised: a keystroke changes one row's props, so only that row renders again. Measured 2026-11-19 on
// 450 lines: without it every keystroke re-rendered the whole table (~60 ms each).
const CueRow = memo(function CueRow({ cue: c, text, edited, selected, cols, showSource, onSelect, onEdit }: RowProps) {
  const editor = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (selected) editor.current?.focus(); }, [selected]);
  const flags = c.flags ?? [];
  // A line the translator left in the source language stands out in the list, not only in its badge.
  const left = flags.includes('untranslated');
  return (
    <div
      role="row"
      aria-selected={selected}
      onClick={() => onSelect(c.id)}
      className={`grid ${cols} gap-3 px-3 py-2 border-b border-white/5 border-l-2 cursor-pointer ${
        left ? 'border-l-amber-400/70' : 'border-l-transparent'} ${
        selected ? 'bg-indigo-500/[0.08]' : left ? 'bg-amber-500/[0.04] hover:bg-amber-500/[0.07]' : 'hover:bg-white/[0.02]'}`}
    >
      <span role="cell" className="text-xs text-gray-500 tabular-nums pt-0.5">{c.id}</span>
      <span role="cell" className="text-xs text-gray-500 tabular-nums pt-0.5">{time(c.start_ms)}</span>
      {showSource && <span role="cell" className="text-gray-400 whitespace-pre-line">{c.source_text ?? ''}</span>}
      <span role="cell" className="min-w-0">
        {selected ? (
          <textarea
            ref={editor}
            aria-label={`Subtitle line ${c.id}`}
            value={text}
            rows={Math.max(2, text.split('\n').length)}
            onChange={e => onEdit(c.id, e.target.value)}
            className="w-full resize-none bg-black/50 border border-indigo-500/50 rounded px-2 py-1 text-gray-100 outline-none"
          />
        ) : (
          <span className="text-gray-200 whitespace-pre-line">{text}</span>
        )}
        {edited && (
          <span className="flex items-center gap-1 text-[11px] text-indigo-300 mt-0.5"><Pencil size={11} aria-hidden /> edited, not saved</span>
        )}
      </span>
      <span role="cell" className="flex flex-wrap gap-1 content-start">
        {flags.map(f => (
          <span key={f} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-[11px] text-amber-300 whitespace-nowrap">
            <AlertTriangle size={11} aria-hidden /> {cueFlagLabel(f, c)}
          </span>
        ))}
      </span>
    </div>
  );
});

interface Props {
  cues: Cue[];
  edits: Record<number, string>;
  selected: number | null;
  onSelect: (id: number) => void;          // must be stable (useCallback / a state setter) for the rows to skip renders
  onEdit: (id: number, text: string) => void;
  sourceLabel: string | null;  // « English » when the lines were translated; null hides the column
}

export default function CueTable({ cues, edits, selected, onSelect, onEdit, sourceLabel }: Props) {
  const cols = sourceLabel ? 'grid-cols-[3rem_6.5rem_1fr_1fr_9rem]' : 'grid-cols-[3rem_6.5rem_1fr_9rem]';
  return (
    <div role="table" aria-label="Subtitle lines" className="text-sm">
      <div role="row" className={`grid ${cols} gap-3 px-3 py-2 text-xs text-gray-500 border-b border-white/10 sticky top-0 bg-[#0a0a0a] z-10`}>
        <span role="columnheader">#</span>
        <span role="columnheader">Time</span>
        {sourceLabel && <span role="columnheader">{sourceLabel}</span>}
        <span role="columnheader">Subtitle</span>
        <span role="columnheader">Needs a look</span>
      </div>
      <div role="rowgroup">
        {cues.map(c => {
          const text = edits[c.id] ?? c.text;
          return (
            <CueRow key={c.id} cue={c} text={text} edited={text !== c.text} selected={c.id === selected}
                    cols={cols} showSource={!!sourceLabel} onSelect={onSelect} onEdit={onEdit} />
          );
        })}
      </div>
    </div>
  );
}
