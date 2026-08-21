// studio-context.tsx — Shared StudioContext + useStudio hook. Exports: StudioContext, useStudio, StudioValue.
"use client";

import { createContext, useContext } from 'react';
import { StudioState, VideoFile, GlobalSettings } from './types';

export interface StudioActions {
  navigate: (path: string) => void;
  setSettings: (updates: Partial<GlobalSettings>) => void;
  updateVideoData: (id: string, updates: Partial<VideoFile>) => void;
  toggleSelection: (id: string, isDir: boolean, children?: VideoFile[]) => void;
  scan: () => void;
  process: (targetVideos: VideoFile[]) => Promise<void>;
}

export interface StudioValue {
  state: StudioState;
  actions: StudioActions;
}

// Lives outside app/page.tsx: the App Router rejects any non-standard export from a page module
// ("StudioContext is not a valid Page export field"), which makes `next build` fail.
export const StudioContext = createContext<StudioValue | null>(null);

export const useStudio = (): StudioValue => {
  const context = useContext(StudioContext);
  if (!context) throw new Error("useStudio must be used within StudioContext");
  return context;
};
