// types.ts — Shared data shapes for scan/process/SSE. Exports: ProcessingStatus, SubtitleInfo, VideoFile, GlobalSettings, StudioState, ScanResponse, ProcessRequest.

/** Values the backend actually puts in the SSE `status` field, plus local-only UI states. */
export type ProcessingStatus =
  | 'idle'
  | 'queued'
  | 'processing'
  | 'transcribing'
  | 'translating'
  | 'muxing'
  | 'done'
  | 'error'
  | 'cancelled'
  | 'folder';

export type WorkflowMode = 'hybrid' | 'whisper' | 'srt';

/** Mirrors VideoScanner._get_subtitle_meta() in backend/core/scanner.py. */
export interface SubtitleInfo {
  hasSubtitles: boolean;
  subType: 'embedded' | 'external' | 'manual' | null;
  /** Single code ("eng", "fr") or "auto" when undetermined. */
  language: string;
  /** Absolute server-side path to the sidecar file, when subType is external/manual. */
  externalPath: string | null;
  /** Language tags of embedded streams found by ffprobe. */
  embeddedTracks: string[];
}

export interface VideoFile {
  id: string;
  fileName: string;
  filePath: string;
  extension?: string;
  is_directory: boolean;

  subtitleInfo?: SubtitleInfo;

  // UI State
  status: ProcessingStatus;
  progress: number;
  statusText?: string;

  // Tree Structure
  children?: VideoFile[] | null;

  // Per-file settings (override the global settings)
  sourceLang?: string[];
  targetLanguages?: string[];
  workflowMode?: WorkflowMode;
  syncOffset?: number;
  /** Server-side path of an explicitly chosen SRT; sent to the backend as-is. */
  selectedSrtPath?: string | null;
  /** If true, the muxing engine drops all existing internal subtitle tracks. */
  stripExistingSubs?: boolean;
}

export interface GlobalSettings {
  sourceLang: string[];
  targetLanguages: string[];
  workflowMode: WorkflowMode;
  modelSize: 'tiny' | 'base' | 'small' | 'medium' | 'large';
  autoGenerate: boolean;
  shouldMux: boolean;
  shouldRemoveOriginal: boolean;
  stripExistingSubs: boolean;
}

export interface StudioState {
  items: VideoFile[];
  selectedIds: Set<string>;
  /** Keyed by filePath; appended from SSE events of type "log". */
  logs: Record<string, string[]>;
  isScanning: boolean;
  currentPath: string;
  settings: GlobalSettings;
}

export interface ScanResponse {
  files: VideoFile[];
}

/** Payload for POST /api/process — matches VideoJob/GlobalOptions in backend/main.py. */
export interface ProcessRequest {
  videos: Array<{
    name: string;
    path: string;
    selectedSrtPath: string | null;
    src: string;
    out: string[];
    workflowMode: string;
    syncOffset: number;
    stripExistingSubs: boolean;
  }>;
  globalOptions: {
    transcriptionEngine: string;
    generateSRT: boolean;
    muxIntoMkv: boolean;
    cleanUp: boolean;
  };
}
