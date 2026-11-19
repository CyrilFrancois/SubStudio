// LibraryTable.tsx — Coverage matrix: one row per video grouped by folder, one column per wanted language. Exports: default LibraryTable.
"use client";

import { Fragment, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { BadgeCheck, ChevronDown, ChevronRight, Folder, SlidersHorizontal } from 'lucide-react';
import type { JobOptions, LanguageCoverage, LibraryVideo } from '@/lib/types';
import { languageName } from '@/lib/languages';
import CoverageChip, { coverageNote } from './CoverageChip';
import VideoDetails, { hasOverrides, NO_OVERRIDES } from './VideoDetails';

export interface Row {
  video: LibraryVideo;
  /** Coverage after applying live job states from the event stream. */
  coverage: LanguageCoverage[];
}

interface Props {
  groups: [string, Row[]][];
  languages: string[];
  selected: Set<string>;
  onSelect: (ids: string[], on: boolean) => void;
  overrides: Record<string, JobOptions>;
  onOverride: (id: string, o: JobOptions) => void;
}

function duration(s?: number | null) {
  if (s == null) return '—';
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s % 60)).padStart(2, '0')}`;
}


function FolderCheckbox({ rows, selected, onSelect }: { rows: Row[]; selected: Set<string>; onSelect: Props['onSelect'] }) {
  const ref = useRef<HTMLInputElement>(null);
  const n = rows.filter(r => selected.has(r.video.id)).length;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = n > 0 && n < rows.length;
  }, [n, rows.length]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label="Select every video in this folder"
      checked={n === rows.length && n > 0}
      onChange={e => onSelect(rows.map(r => r.video.id), e.target.checked)}
      className="w-4 h-4 accent-indigo-600 cursor-pointer"
    />
  );
}

export default function LibraryTable({ groups, languages, selected, onSelect, overrides, onOverride }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const cols = `2.5rem minmax(0,1fr) 4rem repeat(${languages.length}, 12.5rem) 2.5rem`;

  const toggleFolder = (f: string) => setCollapsed(prev => {
    const next = new Set(prev);
    if (next.has(f)) next.delete(f); else next.add(f);
    return next;
  });

  return (
    <div role="table" aria-label="Subtitle coverage" className="text-sm">
      <div role="row" className="grid items-center gap-3 px-4 py-2 sticky top-0 z-10 bg-[#0a0a0a] border-b border-white/10 text-xs text-gray-500"
           style={{ gridTemplateColumns: cols }}>
        {/* sr-only text INSIDE the cell: an sr-only cell is absolutely positioned and leaves the grid. */}
        <span role="columnheader"><span className="sr-only">Select</span></span>
        <span role="columnheader">Video</span>
        <span role="columnheader">Length</span>
        {languages.map(l => (
          <span role="columnheader" key={l}
                title={l === 'ov' ? 'The language spoken in the video (OV), detected from the audio when the file does not say' : languageName(l)}>
            {l === 'ov' ? 'Original version (spoken)' : languageName(l)}
          </span>
        ))}
        <span role="columnheader"><span className="sr-only">Details</span></span>
      </div>

      {groups.map(([folder, rows]) => {
        const isCollapsed = collapsed.has(folder);
        const done = rows.filter(r => r.video.validated).length;
        return (
          <div role="rowgroup" key={folder} className="border-b border-white/5">
            <div role="row" className="flex items-center gap-3 px-4 py-2 bg-white/[0.02]">
              <FolderCheckbox rows={rows} selected={selected} onSelect={onSelect} />
              <button
                type="button"
                onClick={() => toggleFolder(folder)}
                aria-expanded={!isCollapsed}
                className="flex items-center gap-2 min-w-0 flex-1 text-left text-gray-200 hover:text-white"
              >
                {isCollapsed ? <ChevronRight size={15} aria-hidden /> : <ChevronDown size={15} aria-hidden />}
                <Folder size={15} className="text-indigo-400 shrink-0" aria-hidden />
                <span className="font-medium truncate">{folder || 'Library root'}</span>
                <span className="text-xs text-gray-500 shrink-0">
                  {rows.length} video{rows.length === 1 ? '' : 's'} · {done} validated · {rows.length - done} to do
                </span>
              </button>
            </div>

            {!isCollapsed && rows.map(({ video, coverage }) => {
              const isOpen = open === video.id;
              const opts = overrides[video.id] ?? NO_OVERRIDES;
              return (
                <Fragment key={video.id}>
                  <div
                    role="row"
                    className={`grid items-center gap-3 px-4 py-2 border-t border-white/[0.04] hover:bg-white/[0.02] ${
                      selected.has(video.id) ? 'bg-indigo-500/[0.06]' : ''}`}
                    style={{ gridTemplateColumns: cols }}
                  >
                    <span role="cell">
                      <input
                        type="checkbox"
                        aria-label={`Select ${video.name}`}
                        checked={selected.has(video.id)}
                        onChange={e => onSelect([video.id], e.target.checked)}
                        className="w-4 h-4 accent-indigo-600 cursor-pointer"
                      />
                    </span>
                    <span role="cell" className="min-w-0 flex items-center gap-2">
                      <span className="truncate text-gray-200" title={video.path}>{video.name}</span>
                      {video.validated && (
                        <span title="Every language is validated: Start skips this video"
                              className="shrink-0 inline-flex items-center gap-1 text-[11px] px-1.5 rounded border border-emerald-500/40 text-emerald-300">
                          <BadgeCheck size={12} aria-hidden /> validated
                        </span>
                      )}
                      {hasOverrides(opts) && (
                        <span className="shrink-0 text-[11px] px-1.5 rounded border border-amber-500/40 text-amber-300">overrides</span>
                      )}
                    </span>
                    <span role="cell" className="text-xs text-gray-500 tabular-nums">{duration(video.duration_s)}</span>
                    {coverage.map(c => {
                      const note = coverageNote(c);
                      return (
                        <span role="cell" key={c.language} className="min-w-0">
                          {c.state === 'passed' ? (
                            // A validated cell opens its lines in Review.
                            <Link href={`/review?video=${encodeURIComponent(video.id)}&lang=${encodeURIComponent(c.language)}`}
                                  aria-label={`Read and fix the ${c.language === 'ov' ? 'original version' : languageName(c.language)} subtitles of ${video.name}`}
                                  onClick={e => e.stopPropagation()} className="inline-block rounded-md hover:brightness-125">
                              <CoverageChip coverage={c} />
                            </Link>
                          ) : <CoverageChip coverage={c} />}
                          {/* The verdict's reason in words, visible without hovering (D-0002 §5). */}
                          {note && <span className="block mt-0.5 text-[11px] leading-snug text-gray-500 line-clamp-2" title={note}>{note}</span>}
                        </span>
                      );
                    })}
                    <span role="cell">
                      <button
                        type="button"
                        onClick={() => setOpen(isOpen ? null : video.id)}
                        aria-expanded={isOpen}
                        aria-label={`Details and overrides for ${video.name}`}
                        title="Subtitles found and overrides"
                        className={`p-1 rounded hover:bg-white/10 ${isOpen ? 'text-indigo-300' : 'text-gray-500'}`}
                      >
                        <SlidersHorizontal size={15} />
                      </button>
                    </span>
                  </div>
                  {isOpen && (
                    <VideoDetails video={video} options={opts} onChange={o => onOverride(video.id, o)} />
                  )}
                </Fragment>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
