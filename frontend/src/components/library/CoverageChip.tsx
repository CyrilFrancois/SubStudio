// CoverageChip.tsx — One cell of the coverage matrix: state as icon + text, never colour alone. Exports: default CoverageChip, coverageLabel.
"use client";

import { AlertCircle, Check, CircleDashed, Clock, HelpCircle, Loader2, Minus } from 'lucide-react';
import type { CoverageOrigin, LanguageCoverage } from '@/lib/types';
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
  return c.reason ? `${base}${reviewed}. ${c.reason}` : `${base}${reviewed}`;
}

export default function CoverageChip({ coverage }: { coverage: LanguageCoverage }) {
  const ignored = coverage.state === 'missing' && coverage.reason?.startsWith('ignored');
  const rejected = coverage.state === 'missing' && coverage.reason?.startsWith('rejected');
  return (
    <span
      title={tooltip(coverage)}
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-xs whitespace-nowrap ${STYLE[coverage.state]}`}
    >
      <Icon state={coverage.state} />
      {coverageLabel(coverage)}
      {coverage.language === 'ov' && coverage.resolved && (
        <span className="font-mono text-[11px] opacity-70">{coverage.resolved.toUpperCase()}</span>
      )}
      {ignored && <span className="text-gray-500">· old ignored</span>}
      {rejected && <span className="text-red-300">· old rejected</span>}
      {coverage.state === 'passed' && coverage.reviewed && <span className="text-emerald-200/80">· reviewed</span>}
    </span>
  );
}
