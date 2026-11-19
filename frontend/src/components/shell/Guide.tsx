// Guide.tsx — The first-run guide: how SubStudio works in four steps and what each cell means. Opens by itself once, then from « How it works » in the menu. Exports: default Guide, useGuide.
"use client";

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, Check, CircleDashed, Clock, HelpCircle, Loader2, Minus, X } from 'lucide-react';

const SEEN_KEY = 'substudio.guide.seen';

/** Open on the first visit of this browser; a storage error (private window) just shows it again next time. */
export function useGuide() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    try {
      if (!localStorage.getItem(SEEN_KEY)) setOpen(true);
    } catch { /* storage blocked: the « How it works » button still opens it */ }
  }, []);
  const close = useCallback(() => {
    setOpen(false);
    try { localStorage.setItem(SEEN_KEY, '1'); } catch { /* see above */ }
  }, []);
  return { open, show: () => setOpen(true), close };
}

const STEPS: [string, string][] = [
  ['Pick videos, or none', 'In the Library, tick videos or a whole folder. With nothing ticked, Start takes every video still to do.'],
  ['Press Start', 'Each video gets the languages set in Settings (remembered). Per language SubStudio keeps a good subtitle already there, else downloads one, else translates, else transcribes the audio. Every result passes a quality check.'],
  ['Watch the Queue', 'One bar for the whole batch, and the stages of the video being worked on. A failure says why in one sentence.'],
  ['Read and fix', 'Click a ✓ cell in the Library (or a language of a finished job) to read the lines. Lines that need a look are flagged; edit one and Save writes it back into the video.'],
];

const LEGEND: { icon: React.ReactNode; label: string; why: string }[] = [
  { icon: <Check size={13} />, label: 'kept · downloaded · translated · transcribed', why: 'passed the quality check, and how it was made. Click it to read the lines.' },
  { icon: <CircleDashed size={13} />, label: 'not checked yet', why: 'a subtitle is there, but it only counts once the check passes it (when the video is started).' },
  { icon: <Minus size={13} />, label: 'missing', why: 'nothing usable yet. Start makes it.' },
  { icon: <Clock size={13} />, label: 'queued', why: 'waiting its turn in the Queue.' },
  { icon: <Loader2 size={13} />, label: 'running', why: 'being made now.' },
  { icon: <AlertCircle size={13} />, label: 'failed', why: 'the reason is written under the cell. Start tries again.' },
  { icon: <HelpCircle size={13} />, label: 'unknown', why: 'the video could not be read.' },
];

export default function Guide({ onClose }: { onClose: () => void }) {
  const closeBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeBtn.current?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="guide-title"
        onClick={e => e.stopPropagation()}
        className="w-full max-w-2xl max-h-full overflow-y-auto rounded-xl border border-white/10 bg-[#111] p-6 text-sm text-gray-300 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="guide-title" className="text-base font-semibold text-white">How SubStudio works</h2>
            <p className="text-xs text-gray-500 mt-1">Subtitles in your languages for every video, written into the video file itself.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close the guide" className="p-1 rounded text-gray-400 hover:bg-white/10">
            <X size={16} />
          </button>
        </div>

        <ol className="mt-5 space-y-3">
          {STEPS.map(([title, text], i) => (
            <li key={title} className="flex gap-3">
              <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-600/20 text-indigo-300 text-xs font-semibold flex items-center justify-center">{i + 1}</span>
              <div><div className="text-gray-100 font-medium">{title}</div><p className="text-gray-400 mt-0.5">{text}</p></div>
            </li>
          ))}
        </ol>

        <h3 className="mt-6 text-xs font-semibold text-gray-400">What a Library cell says</h3>
        <ul className="mt-2 space-y-1.5 text-xs">
          {LEGEND.map(l => (
            <li key={l.label} className="flex gap-2">
              <span className="shrink-0 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border border-white/15 text-gray-200" aria-hidden>{l.icon}</span>
              <span><span className="text-gray-200">{l.label}</span>: {l.why}</span>
            </li>
          ))}
        </ul>
        <p className="mt-4 text-xs text-gray-500">
          « Original version » (OV) is the language spoken in the video; when the file does not say, SubStudio detects it from the audio.
          A video whose subtitles were already good is left exactly as it is.
        </p>

        <div className="mt-6 flex justify-end">
          <button ref={closeBtn} type="button" onClick={onClose}
                  className="px-4 py-2 rounded-lg text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white">
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
