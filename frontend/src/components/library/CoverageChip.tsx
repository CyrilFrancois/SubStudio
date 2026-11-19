// CoverageChip.tsx — One cell of the coverage matrix: state as icon + text, never colour alone. Exports: default CoverageChip, coverageLabel.
"use client";

import { AlertCircle, Check, CircleDashed, Clock, HelpCircle, Loader2, Minus } from 'lucide-react';
import type { CoverageOrigin, LanguageCoverage, SubtitleTrack } from '@/lib/types';
import { languageName } from '@/lib/languages';

// Whole class strings: a Tailwind class assembled at runtime ships no CSS.
const STYLE: Record<LanguageCoverage['state'], string> = {
  passed: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/25',
  missing: 'bg-white/[0.03] text-gray-400 border-white/10',
  unchecked: 'bg-white/[0.03] text-gray-300 border-dashed border-white/20',
  pending: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/25',
  running: 'bg-indigo-500/15 text-indigo-200 border-indigo-400/40',
  failed: 'bg-red-500/10 text-red-300 border-red-500/30',
  unknown: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
};

// D-0002 §3 verdict origins; 'existing' = counted only because Settings counts existing subtitles.
const ORIGIN: Record<CoverageOrigin, string> = {
  kept: 'kept',
  downloaded: 'downloaded',
  translated: 'translated',
  transcribed: 'transcribed',
  existing: 'existing',
};

export function coverageLabel(c: LanguageCoverage): string {
  switch (c.state) {
    case 'passed': return c.origin ? ORIGIN[c.origin] : 'SubStudio';
    // OV of a video whose audio is untagged: the job detects it, so nothing can be said missing yet.
    case 'missing': return c.language === 'ov' && !c.resolved ? 'language not detected yet' : 'missing';
    case 'unchecked': return 'not checked yet';
    case 'pending': return 'queued';
    case 'running': return 'running';
    case 'failed': return 'failed';
    case 'unknown': return 'unknown';
  }
}

/** « 3 lines left in English »: a translation that let some lines through untranslated (≤ 10 %, workspace-6e). */
export function leftNote(c: LanguageCoverage): string | null {
  const n = c.untranslated ?? 0;
  if (c.state !== 'passed' || n <= 0) return null;
  return `${n} line${n === 1 ? '' : 's'} left in ${c.from_language ? languageName(c.from_language) : 'the source language'}`;
}

/** The why, shown under the cell without a click: one short line each, none when the label says it all. `tracks` are
 *  the video's subtitles as listed by the Library, to say when an old one stays in the file unused. */
export function coverageNotes(c: LanguageCoverage, tracks: SubtitleTrack[] = []): string[] {
  switch (c.state) {
    case 'passed': {
      if (c.origin === 'existing') return ['counted without a check'];
      const lang = c.language === 'ov' ? c.resolved : c.language;
      // Ours is used and first in its language (workspace-6e's track order); an old one in the same language that
      // the Library did not count stays in the file, never deleted, never the default.
      const old = tracks.filter(t => lang && t.language === lang && !t.counted && !t.made_by_substudio);
      const name = lang ? languageName(lang) : '';
      return [
        leftNote(c) ?? (c.from_language ? `from ${languageName(c.from_language)}` : null),
        old.some(t => t.kind === 'embedded') ? `old ${name} track kept in the file, not used` : null,
        old.some(t => t.kind === 'sidecar') ? `old ${name} subtitle file next to it, not used` : null,
      ].filter((s): s is string => !!s);
    }
    case 'missing':
      if (c.language === 'ov' && !c.resolved) return ['detected from the audio at Start'];
      return c.reason ? [c.reason] : [];
    case 'unchecked': return ['checked when the video is started'];
    case 'pending': return [];
    case 'running': return [];
    case 'failed': return [c.reason ?? 'no reason recorded: open the job log in the Queue'];
    case 'unknown': return c.reason ? [c.reason] : [];
  }
}

function Icon({ state }: { state: LanguageCoverage['state'] }) {
  const p = { size: 13, 'aria-hidden': true } as const;
  switch (state) {
    case 'passed': return <Check {...p} />;
    case 'missing': return <Minus {...p} />;
    case 'unchecked': return <CircleDashed {...p} />;
    case 'pending': return <Clock {...p} />;
    case 'running': return <Loader2 {...p} className="animate-spin" />;
    case 'failed': return <AlertCircle {...p} />;
    case 'unknown': return <HelpCircle {...p} />;
  }
}

function tooltip(c: LanguageCoverage): string {
  const lang = c.language === 'ov'
    ? `Original version${c.resolved ? ` (${languageName(c.resolved)})` : ''}`
    : languageName(c.language);
  const base = {
    passed: `${lang} subtitles ${c.origin === 'existing' ? 'already there, counted but not checked' : `validated (${c.origin ? ORIGIN[c.origin] : 'made by SubStudio'}${c.from_language ? ` from ${languageName(c.from_language)}` : ''})`}${c.path ? `: ${c.path}` : ''}`,
    missing: `No usable ${lang} subtitles`,
    unchecked: `${lang} subtitles are there but the quality check has not judged them yet, so they do not count`,
    pending: `${lang} is queued`,
    running: `${lang} is being made now`,
    failed: `${lang} failed`,
    unknown: `Cannot tell whether ${lang} subtitles exist`,
  }[c.state];
  const reviewed = c.state === 'passed' && c.reviewed ? '. Checked by a person in Review' : '';
  const left = leftNote(c);
  const leftWhy = left ? `. ${left}: the translator kept them as they were; Review shows them` : '';
  return c.reason ? `${base}${reviewed}${leftWhy}. ${c.reason}` : `${base}${reviewed}${leftWhy}`;
}

export default function CoverageChip({ coverage }: { coverage: LanguageCoverage }) {
  const ignored = coverage.state === 'missing' && coverage.reason?.startsWith('ignored');
  const rejected = coverage.state === 'missing' && coverage.reason?.startsWith('rejected');
  // Not a clean ✓: a translation with lines left in the source language says so on the chip itself.
  const left = coverage.state === 'passed' ? coverage.untranslated ?? 0 : 0;
  return (
    <span
      title={tooltip(coverage)}
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-xs whitespace-nowrap ${
        left ? 'bg-emerald-500/10 text-emerald-300 border-amber-500/50' : STYLE[coverage.state]}`}
    >
      <Icon state={coverage.state} />
      {coverageLabel(coverage)}
      {coverage.language === 'ov' && coverage.resolved && (
        <span className="font-mono text-[11px] opacity-70">{coverage.resolved.toUpperCase()}</span>
      )}
      {ignored && <span className="text-gray-500">· old ignored</span>}
      {rejected && <span className="text-red-300">· old rejected</span>}
      {left > 0 && <span className="text-amber-300">· {left} left</span>}
      {coverage.state === 'passed' && coverage.reviewed && <span className="text-emerald-200/80">· reviewed</span>}
    </span>
  );
}
