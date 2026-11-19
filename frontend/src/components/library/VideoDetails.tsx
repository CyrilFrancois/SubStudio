// VideoDetails.tsx — Library « Advanced » drawer: every subtitle found (counted or why not) and per-video overrides. Exports: default VideoDetails, hasOverrides.
"use client";

import { Check, X } from 'lucide-react';
import type { JobOptions, LibraryVideo } from '@/lib/types';
import { languageName } from '@/lib/languages';

export const NO_OVERRIDES: JobOptions = { force_asr: false, source_track: null, sync_offset_ms: 0 };

export function hasOverrides(o?: JobOptions): boolean {
  return !!o && (!!o.force_asr || (o.sync_offset_ms ?? 0) !== 0 || !!o.source_track);
}

interface Props {
  video: LibraryVideo;
  options: JobOptions;
  onChange: (o: JobOptions) => void;
}

export default function VideoDetails({ video, options, onChange }: Props) {
  const set = (patch: Partial<JobOptions>) => onChange({ ...options, ...patch });

  return (
    <div className="grid grid-cols-[1fr_18rem] gap-6 px-4 py-4 bg-black/30 border-t border-white/5 text-xs">
      <section aria-label="Subtitles found">
        <h4 className="text-gray-400 font-semibold mb-2">Subtitles found ({video.tracks.length})</h4>
        {video.probe_error && (
          <p className="text-amber-300 mb-2">Could not read the tracks inside this video: {video.probe_error}</p>
        )}
        {video.tracks.length === 0 ? (
          <p className="text-gray-500">None inside the video and no subtitle file next to it.</p>
        ) : (
          <table className="w-full">
            <thead className="text-gray-500 text-left">
              <tr>
                <th className="font-normal py-1 pr-3">Counted</th>
                <th className="font-normal py-1 pr-3">Language</th>
                <th className="font-normal py-1 pr-3">Where</th>
                <th className="font-normal py-1">Why not counted</th>
              </tr>
            </thead>
            <tbody>
              {video.tracks.map((t, i) => (
                <tr key={i} className="border-t border-white/5 align-top">
                  <td className="py-1.5 pr-3">
                    {t.counted
                      ? <span className="inline-flex items-center gap-1 text-emerald-300"><Check size={12} aria-hidden /> yes</span>
                      : <span className="inline-flex items-center gap-1 text-gray-400"><X size={12} aria-hidden /> no</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-gray-300">
                    {t.language ? languageName(t.language) : <span className="text-gray-500">no tag</span>}
                    {t.language_tag && t.language_tag !== t.language && <span className="text-gray-600"> ({t.language_tag})</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-gray-400 break-all">
                    {t.kind === 'embedded'
                      ? `inside the video, track ${t.stream_index}${t.title ? ` « ${t.title} »` : ''} (${t.codec})`
                      : t.path}
                  </td>
                  <td className="py-1.5 text-gray-500">{t.reason ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section aria-label="Overrides for this video" className="space-y-3">
        <h4 className="text-gray-400 font-semibold">
          Overrides {hasOverrides(options) && <span className="text-amber-300 font-normal">(set)</span>}
        </h4>
        <label className="flex items-center gap-2 text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={!!options.force_asr}
            onChange={e => set({ force_asr: e.target.checked })}
            className="accent-indigo-600"
          />
          Always transcribe the audio (ignore every subtitle found)
        </label>
        <label className="block text-gray-300">
          <span className="block mb-1">Timing shift (milliseconds, + = later)</span>
          <input
            type="number"
            step={100}
            value={options.sync_offset_ms ?? 0}
            onChange={e => {
              const v = parseInt(e.target.value, 10);
              set({ sync_offset_ms: Number.isFinite(v) ? v : 0 });
            }}
            className={`w-32 bg-black/40 border rounded px-2 py-1 font-mono outline-none focus:border-indigo-500 ${
              (options.sync_offset_ms ?? 0) !== 0 ? 'border-amber-500/50 text-amber-300' : 'border-white/10'}`}
          />
        </label>
        {hasOverrides(options) && (
          <button
            type="button"
            onClick={() => onChange(NO_OVERRIDES)}
            className="text-gray-400 hover:text-gray-200 underline underline-offset-2"
          >
            Clear overrides
          </button>
        )}
      </section>
    </div>
  );
}
