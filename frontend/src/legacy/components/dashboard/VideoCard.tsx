// VideoCard.tsx — Dashboard: one video row with per-file workflow, languages and start action. Exports: default VideoCard.
"use client";

import React, { useState } from 'react';
import { useStudio } from '@/legacy/lib/studio-context';
import SubImportModal from './SubImportModal';
import { 
  Check, Loader2, FileText, ChevronDown, Layers, Play, XCircle
} from 'lucide-react';
import { ProcessingStatus, VideoFile } from '@/legacy/lib/types';

export default function VideoCard({ video }: { video: VideoFile }) {
  const { state, actions } = useStudio();
  const [isModalOpen, setIsModalOpen] = useState(false);

  // --- DATA EXTRACTION ---
  const targetLanguages = video.targetLanguages || ['fr'];
  const workflowMode = video.workflowMode || 'hybrid';
  const stripExistingSubs = video.stripExistingSubs ?? state.settings.stripExistingSubs;
  const syncOffset = video.syncOffset ?? 0;

  // --- UI STATE ---
  const isSelected = state.selectedIds.has(video.id);
  const isProcessing = !['idle', 'done', 'error', 'cancelled', 'folder'].includes(video.status);
  
  const subInfo = video.subtitleInfo || {
    hasSubtitles: false,
    subType: null,
    externalPath: null,
    language: 'auto',
    embeddedTracks: []
  };
  
  // Decide if we are currently using Speech Recognition or an SRT source
  const effectiveWorkflow = video.workflowMode || (subInfo.hasSubtitles ? 'srt' : 'whisper');
  const isWhisperActive = effectiveWorkflow === 'whisper';
  const isSourceActive = !isWhisperActive;

  const langList = ["en", "fr", "es", "de", "it", "ja", "ko", "zh"];

  // --- HANDLERS ---
  const handleToggleOutLang = (lang: string) => {
    const next = targetLanguages.includes(lang) 
      ? targetLanguages.filter((l: string) => l !== lang)
      : [...targetLanguages, lang];
    actions.updateVideoData(video.id, { targetLanguages: next });
  };

  const getStatusColor = (status: ProcessingStatus) => {
    switch (status) {
      case 'done': return 'text-emerald-500';
      case 'error': return 'text-red-500';
      case 'cancelled': return 'text-amber-500';
      case 'idle': return 'text-gray-600';
      default: return 'text-indigo-500';
    }
  };

  // Helper to get just the filename from a full path for the tooltip
  const getSrtDisplayPath = () => {
    if (!subInfo.externalPath) return 'Embedded Stream';
    return subInfo.externalPath.split(/[\/]/).pop();
  };

  return (
    <div className={`group relative mb-3 bg-[#0d0d0d] border rounded-xl transition-all duration-300 ${
      isSelected ? 'border-indigo-500/50 shadow-[0_0_20px_rgba(79,70,229,0.1)]' : 'border-white/5 hover:border-white/10'
    }`}>
      
      {/* HEADER SECTION */}
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3 min-w-0">
          <input 
            type="checkbox"
            checked={isSelected}
            onChange={() => actions.toggleSelection(video.id, false)}
            className="w-4 h-4 rounded border-white/10 bg-white/5 accent-indigo-600 cursor-pointer shrink-0"
          />
          <h3 className="text-sm font-medium text-slate-200 truncate">{video.fileName}</h3>
          
          {subInfo.hasSubtitles && (
            <div className="relative group/tooltip flex shrink-0">
              <span className={`flex items-center gap-1 text-[9px] font-bold px-2 py-0.5 rounded uppercase border cursor-help transition-colors ${
                subInfo.subType === 'manual' 
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
                : 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20'
              }`}>
                <Check size={10} /> {subInfo.subType === 'manual' ? 'Imported SRT' : subInfo.subType}
              </span>

              {/* TOOLTIP */}
              <div className="absolute left-0 bottom-full mb-2 hidden group-hover/tooltip:flex flex-col z-[150] pointer-events-none">
                <div className="bg-[#161616] border border-white/10 p-3 rounded-lg shadow-2xl w-64 backdrop-blur-xl">
                  <div className="flex items-center gap-2 mb-2 border-b border-white/5 pb-2">
                    <FileText size={12} className="text-indigo-400" />
                    <span className="text-[10px] font-bold text-white uppercase">Subtitle Source</span>
                  </div>
                  <div className="space-y-1.5 text-[10px]">
                    <div className="flex justify-between">
                      <span className="text-gray-500">Status:</span>
                      <span className="text-emerald-400 font-bold uppercase">Ready</span>
                    </div>
                    <div className="text-gray-500">Linked File:</div>
                    <div className="text-indigo-300 font-mono break-all leading-tight text-[9px] bg-white/5 p-1.5 rounded">
                      {getSrtDisplayPath()}
                    </div>
                  </div>
                  <div className="absolute top-full left-4 w-2 h-2 bg-[#161616] border-r border-b border-white/10 rotate-45 -translate-y-1" />
                </div>
              </div>
            </div>
          )}
        </div>
        
        <div className={`flex items-center gap-2 text-[10px] font-black uppercase tracking-widest ${getStatusColor(video.status)}`}>
          {isProcessing && <Loader2 size={12} className="animate-spin" />}
          {video.statusText || video.status}
        </div>
      </div>

      {/* CONTROLS SECTION */}
      <div className="px-4 pb-4 grid grid-cols-12 gap-4 items-end">
        
        <div className="col-span-2 space-y-1.5">
          <label htmlFor={`sync-${video.id}`} className="text-[8px] font-bold text-gray-600 uppercase tracking-tighter block">
            Sync (s)
          </label>
          <div className="relative">
            <input
              id={`sync-${video.id}`}
              type="number"
              step="0.1"
              value={syncOffset}
              title="Shift every subtitle. Positive = later, negative = earlier."
              onChange={(e) => {
                const parsed = parseFloat(e.target.value);
                actions.updateVideoData(video.id, { syncOffset: Number.isFinite(parsed) ? parsed : 0 });
              }}
              className={`w-full bg-black/40 border p-2 rounded text-[10px] font-mono h-8 outline-none transition-colors ${
                syncOffset !== 0
                  ? 'border-amber-500/40 text-amber-400'
                  : 'border-white/5 text-gray-400 focus:border-indigo-500'
              }`}
            />
            {syncOffset !== 0 && (
              <span className="absolute -top-4 right-0 text-[8px] font-bold text-amber-500">
                {syncOffset > 0 ? `+${syncOffset}s` : `${syncOffset}s`}
              </span>
            )}
          </div>
        </div>

        <div className="col-span-2 space-y-1.5">
          <span className="text-[8px] font-bold text-gray-600 uppercase tracking-tighter">Output</span>
          <div className="relative group/multiselect">
            <button className="w-full flex items-center justify-between bg-black/40 border border-white/5 p-2 rounded text-[10px] text-indigo-400 font-mono uppercase truncate h-8">
              {targetLanguages.join(',')}
              <ChevronDown size={10} />
            </button>
            <div className="absolute bottom-full left-0 mb-1 hidden group-hover/multiselect:grid grid-cols-3 gap-1 bg-[#161616] border border-white/10 p-2 rounded z-[110] shadow-2xl w-36">
              {langList.map(l => (
                <button 
                  key={l} 
                  onClick={() => handleToggleOutLang(l)} 
                  className={`text-[10px] p-1 rounded font-mono uppercase transition-colors ${targetLanguages.includes(l) ? 'bg-indigo-600 text-white' : 'hover:bg-white/5 text-gray-500'}`}
                >
                  {l}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="col-span-3 space-y-1.5">
          <span className="text-[8px] font-bold text-gray-600 uppercase tracking-tighter">Workflow Mode</span>
          <div className="flex gap-1 h-8">
            <button 
              onClick={() => setIsModalOpen(true)}
              className={`flex-1 rounded text-[9px] font-bold uppercase transition-all ${isSourceActive ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-500/20' : 'bg-white/5 text-gray-600 hover:text-gray-400'}`}
            >
              Use SRT
            </button>
            <button 
              onClick={() => actions.updateVideoData(video.id, { workflowMode: 'whisper' })}
              className={`flex-1 rounded text-[9px] font-bold uppercase transition-all ${isWhisperActive ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-500/20' : 'bg-white/5 text-gray-600 hover:text-gray-400'}`}
            >
              AI Whisper
            </button>
          </div>
        </div>

        <div className="col-span-2 space-y-1.5">
          <span className="text-[8px] font-bold text-gray-600 uppercase tracking-tighter">Internal Subs</span>
          <button 
            onClick={() => actions.updateVideoData(video.id, { stripExistingSubs: !stripExistingSubs })}
            className={`w-full flex items-center justify-center gap-2 rounded text-[9px] font-bold uppercase transition-all border h-8 ${
              stripExistingSubs 
              ? 'bg-red-500/10 border-red-500/30 text-red-400' 
              : 'bg-white/5 border-white/5 text-gray-500 hover:text-gray-400'
            }`}
          >
            <Layers size={10} />
            {stripExistingSubs ? 'Strip' : 'Keep'}
          </button>
        </div>

        <div className="col-span-3">
          {isProcessing ? (
            <button
              onClick={() => actions.cancel(video)}
              title="Stop this job"
              className="w-full h-8 rounded font-black text-[10px] uppercase transition-all flex items-center justify-center gap-1.5 bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 active:scale-[0.97]"
            >
              <XCircle size={12} />
              Cancel
            </button>
          ) : (
            <button
              onClick={() => actions.process([video])}
              title="Transcribe, translate and mux this file"
              className="w-full h-8 rounded font-black text-[10px] uppercase transition-all flex items-center justify-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/10 active:scale-[0.97]"
            >
              <Play size={11} fill="currentColor" />
              Start Process
            </button>
          )}
        </div>
      </div>

      {/* PROGRESS BAR */}
      {isProcessing && (
        <div className="absolute bottom-0 left-0 right-0 h-1 bg-white/5 rounded-b-xl overflow-hidden">
          <div className="h-full bg-indigo-500 transition-all duration-1000 ease-out" style={{ width: `${video.progress}%` }} />
        </div>
      )}

      <SubImportModal 
        isOpen={isModalOpen} 
        onClose={() => setIsModalOpen(false)} 
        videoName={video.fileName}
        // Extract the directory path correctly
        videoPath={video.filePath.includes('/') 
          ? video.filePath.substring(0, video.filePath.lastIndexOf('/'))
          : '.' 
        }
        onFileSelect={async (file, realFileName, destinationPath) => {
          const serverPath = `${destinationPath}/${realFileName}`;
          actions.updateVideoData(video.id, {
            workflowMode: 'srt',
            statusText: 'SRT Ready',
            // selectedSrtPath is the field POST /api/process reads; subtitleInfo is display only.
            selectedSrtPath: serverPath,
            subtitleInfo: {
              hasSubtitles: true,
              subType: 'manual',
              externalPath: serverPath,
              language: subInfo.language,
              embeddedTracks: subInfo.embeddedTracks
            }
          });
        }}
      />
    </div>
  );
}