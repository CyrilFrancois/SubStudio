// flags.ts — What each line flag means, in words, with the gate's own limits from the response. Exports: FLAG_ORDER, flagLabel, cueFlagLabel, flagCount, flagWhy, time, cps.
import type { Cue, CueFlag, CueList } from '@/lib/types';
import { languageName } from '@/lib/languages';

type Limits = NonNullable<CueList['limits']>;

export const FLAG_ORDER: CueFlag[] = ['too_fast', 'long_line', 'untranslated', 'broken_text', 'duplicate'];

const LABEL: Record<CueFlag, string> = {
  too_fast: 'too fast',
  long_line: 'line too long',
  untranslated: 'not translated',
  broken_text: 'broken accents',
  duplicate: 'repeats the line before',
};

const PLURAL: Record<CueFlag, string> = {
  too_fast: 'too fast',
  long_line: 'lines too long',
  untranslated: 'not translated',
  broken_text: 'with broken accents',
  duplicate: 'repeating the line before',
};

export const flagLabel = (f: CueFlag) => LABEL[f];
/** One line's badge: « left in English » when the file names the language the line stayed in, else the plain label. */
export function cueFlagLabel(f: CueFlag, cue: Pick<Cue, 'untranslated_from'>): string {
  const from = cue.untranslated_from;
  return f === 'untranslated' && from && from !== 'und' ? `left in ${languageName(from)}` : LABEL[f];
}
/** « 1 line too long », « 2 lines too long »: for the summary counts. */
export const flagCount = (f: CueFlag, n: number) => `${n} ${n === 1 ? LABEL[f] : PLURAL[f]}`;

export function flagWhy(f: CueFlag, limits: Limits | null | undefined): string {
  switch (f) {
    case 'too_fast': return `More than ${limits?.max_cps ?? '?'} characters a second: too fast to read.`;
    case 'long_line': return `A line is longer than ${limits?.max_line_chars ?? '?'} characters.`;
    case 'untranslated': return 'Still in the source language: the translator kept this line as it was (it may leave up to 1 line in 10). Translate it here and Save.';
    case 'broken_text': return 'Looks decoded with the wrong code page (« ? » or « Ã© » where an accent stood).';
    case 'duplicate': return 'Same text as the line just before it.';
  }
}

/** 75_300 → « 1:15.3 » (minutes:seconds.tenths). */
export function time(ms: number): string {
  const t = Math.max(0, Math.round(ms / 100));
  const m = Math.floor(t / 600), s = Math.floor((t % 600) / 10), d = t % 10;
  return `${m}:${String(s).padStart(2, '0')}.${d}`;
}

/** Reading speed shown beside the preview: characters (line breaks excluded) per second, as the gate counts it. */
export function cps(text: string, startMs: number, endMs: number): number | null {
  const s = (endMs - startMs) / 1000;
  return s > 0 ? text.replace(/\n/g, '').length / s : null;
}
