// page.tsx — Studio dashboard root: owns StudioState, SSE subscriptions and batch dispatch. Exports: default DashboardPage.
"use client";

import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import Sidebar from '@/legacy/components/Sidebar';
import GlobalProgress from '@/legacy/components/dashboard/GlobalProgress';
import VideoList from '@/legacy/components/dashboard/VideoList';
import { api } from '@/legacy/lib/api';
import { VideoFile, GlobalSettings, StudioState, ProcessingStatus } from '@/legacy/lib/types';
import { StudioContext, useStudio } from '@/legacy/lib/studio-context';
import { createSSEConnection, SSEEvent } from '@/legacy/lib/sse';
import { Terminal, Bug, X } from 'lucide-react';

const TERMINAL_LOG_LIMIT = 400;

// --- DEBUGGER ---
function AppStatusDebugger() {
  const { state } = useStudio();
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === 'D') setIsVisible(v => !v);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  if (!isVisible) return (
    <button
      onClick={() => setIsVisible(true)}
      aria-label="Open debug panel"
      title="Open debug panel (Ctrl+Shift+D)"
      className="fixed bottom-2 right-2 w-6 h-6 opacity-20 hover:opacity-100 z-[9999] flex items-center justify-center text-white bg-white/10 rounded-full transition-all"
    >
      <Bug size={12} />
    </button>
  );

  return (
    <div className="fixed inset-y-0 right-0 w-full max-w-[500px] bg-[#050505] border-l border-white/10 z-[10000] shadow-2xl flex flex-col">
      <div className="p-4 border-b border-white/10 flex justify-between items-center bg-white/[0.02]">
        <div className="flex items-center gap-2 text-indigo-400 font-mono text-[10px] font-bold tracking-widest">
          <Terminal size={14} /> SYSTEM_DEBUG_STDOUT
        </div>
        <button onClick={() => setIsVisible(false)} aria-label="Close debug panel" title="Close" className="text-gray-500 hover:text-white p-1">
          <X size={18} />
        </button>
      </div>
      <div className="flex-1 overflow-auto p-6 custom-scrollbar">
        <pre className="text-[10px] font-mono text-emerald-500/90 whitespace-pre-wrap">
          {JSON.stringify(state, (k, v) => v instanceof Set ? Array.from(v) : v, 2)}
        </pre>
      </div>
    </div>
  );
}

// --- DASHBOARD LAYOUT ---
function DashboardContent() {
  const { state } = useStudio();
  return (
    <div className="flex h-full w-full bg-[#0a0a0a] text-slate-200 overflow-hidden">
      <Sidebar />
      <main className="flex-1 relative flex flex-col overflow-hidden">
        <GlobalProgress />
        <div className="flex-1 overflow-y-auto custom-scrollbar p-6">
          <VideoList videos={state.items} />
        </div>
      </main>
      <AppStatusDebugger />
    </div>
  );
}

// --- ROOT PAGE ---
export default function DashboardPage() {
  const [mounted, setMounted] = useState(false);
  const activeListeners = useRef<Record<string, EventSource>>({});
  const hasInitialScanned = useRef(false);
  const scanInFlight = useRef(false);

  const [state, setState] = useState<StudioState>({
    items: [],
    selectedIds: new Set(),
    logs: {},
    isScanning: false,
    currentPath: "/data",
    settings: {
      sourceLang: ['auto'],
      targetLanguages: ['fr'],
      workflowMode: 'whisper',
      autoGenerate: true,
      shouldMux: true,
      shouldRemoveOriginal: false,
      stripExistingSubs: false
    }
  });

  // 1. RECURSIVE UPDATE HELPER
  const updateVideoInList = useCallback((id: string, updates: Partial<VideoFile>) => {
    setState(prev => {
      const updateRecursive = (list: VideoFile[]): VideoFile[] => {
        return list.map(item => {
          if (item.id === id || item.filePath === id) return { ...item, ...updates };
          if (item.children) return { ...item, children: updateRecursive(item.children) };
          return item;
        });
      };
      return { ...prev, items: updateRecursive(prev.items) };
    });
  }, []);

  const appendLog = useCallback((fileId: string, line: string) => {
    setState(prev => {
      // Stamped on arrival, once: rendering must not re-stamp it with the current time.
      const next = [...(prev.logs[fileId] || []), { at: Date.now(), text: line }];
      return {
        ...prev,
        logs: {
          ...prev.logs,
          [fileId]: next.length > TERMINAL_LOG_LIMIT ? next.slice(-TERMINAL_LOG_LIMIT) : next
        }
      };
    });
  }, []);

  // 2. STABLE SCAN ACTION
  const performScan = useCallback(async (path: string) => {
    // Guard lives in a ref, not in state: a setState updater runs when React processes the
    // update, not at the call site, so reading the flag back from it races. Keeping it out of
    // state also keeps this callback's identity stable, so the mount effect fires once.
    if (scanInFlight.current) return;
    scanInFlight.current = true;
    setState(prev => ({ ...prev, isScanning: true }));

    try {
      const data = await api.scanFolder(path);
      setState(prev => {
        const hydrate = (files: any[]): VideoFile[] => files.map(f => ({
          ...f,
          id: f.filePath,
          status: (f.is_directory ? 'folder' : 'idle') as ProcessingStatus,
          progress: 0,
          sourceLang: prev.settings.sourceLang,
          targetLanguages: prev.settings.targetLanguages,
          workflowMode: prev.settings.workflowMode,
          stripExistingSubs: prev.settings.stripExistingSubs,
          selectedSrtPath: f.subtitleInfo?.externalPath ?? null,
          children: f.is_directory ? hydrate(f.children || []) : null
        }));
        return { ...prev, items: hydrate(data.files || []), isScanning: false };
      });
    } catch (e) {
      console.error("Scan Failed:", e);
      setState(prev => ({ ...prev, isScanning: false }));
    } finally {
      scanInFlight.current = false;
    }
  }, []);

  // 3. SSE MANAGEMENT
  const subscribeToUpdates = useCallback((fileId: string) => {
    if (activeListeners.current[fileId]) activeListeners.current[fileId].close();

    // Pass the RAW path. createSSEConnection does the single encodeURIComponent; encoding here
    // as well produced %252F..., which the backend decodes to a key no emitter ever writes to,
    // so the stream stayed silent and every card froze at 5%.
    const es = createSSEConnection(fileId, (data: SSEEvent) => {
      if (data.type === 'log') {
        appendLog(fileId, data.message);
        return;
      }
      if (data.type === 'status') {
        updateVideoInList(fileId, {
          status: (data.status || 'processing') as ProcessingStatus,
          progress: data.progress ?? 0,
          statusText: data.message
        });
        if (['done', 'error', 'cancelled'].includes(data.status || '')) {
          es.close();
          delete activeListeners.current[fileId];
        }
      }
    }, () => {
      delete activeListeners.current[fileId];
    });

    activeListeners.current[fileId] = es;
  }, [updateVideoInList, appendLog]);

  // 4. COMBINED ACTIONS
  const actions = useMemo(() => ({
    navigate: (path: string) => {
      setState(prev => ({ ...prev, currentPath: path }));
      performScan(path);
    },

    setSettings: (updates: Partial<GlobalSettings>) => {
      setState(prev => {
        const nextSettings = { ...prev.settings, ...updates };
        const videoUpdates: Partial<VideoFile> = {};
        if (updates.sourceLang) videoUpdates.sourceLang = updates.sourceLang;
        if (updates.targetLanguages) videoUpdates.targetLanguages = updates.targetLanguages;
        if (updates.workflowMode) videoUpdates.workflowMode = updates.workflowMode;
        if (updates.stripExistingSubs !== undefined) videoUpdates.stripExistingSubs = updates.stripExistingSubs;

        const applyToAll = (list: VideoFile[]): VideoFile[] => list.map(item => ({
          ...item, ...videoUpdates,
          children: item.children ? applyToAll(item.children) : null
        }));

        return { ...prev, settings: nextSettings, items: applyToAll(prev.items) };
      });
    },

    updateVideoData: (id: string, updates: Partial<VideoFile>) => updateVideoInList(id, updates),

    toggleSelection: (id: string, isDir: boolean, children?: VideoFile[]) => {
      setState(prev => {
        const next = new Set(prev.selectedIds);
        const isAdding = !next.has(id);
        const apply = (targetId: string, kids?: VideoFile[] | null) => {
          if (isAdding) next.add(targetId); else next.delete(targetId);
          if (kids) kids.forEach(child => apply(child.filePath, child.children));
        };
        apply(id, children);
        return { ...prev, selectedIds: next };
      });
    },

    scan: () => performScan(state.currentPath),

    process: async (targetVideos: VideoFile[]) => {
      const videosToStart = targetVideos.filter(v => v.status !== 'processing');
      if (videosToStart.length === 0) return;

      const payload = {
        videos: videosToStart.map(v => ({
          name: v.fileName,
          path: v.filePath,
          selectedSrtPath: v.selectedSrtPath || null,
          src: v.sourceLang?.[0] || state.settings.sourceLang[0],
          out: v.targetLanguages || state.settings.targetLanguages,
          workflowMode: v.workflowMode || state.settings.workflowMode,
          syncOffset: v.syncOffset || 0,
          stripExistingSubs: v.stripExistingSubs ?? state.settings.stripExistingSubs,
        })),
        globalOptions: {
          generateSRT: state.settings.autoGenerate,
          muxIntoMkv: state.settings.shouldMux,
          cleanUp: state.settings.shouldRemoveOriginal
        }
      };

      videosToStart.forEach(v => {
        updateVideoInList(v.id, { status: 'processing', progress: 5, statusText: 'Initializing...' });
        subscribeToUpdates(v.filePath);
      });

      try {
        await api.startJob(payload);
      } catch (err) {
        console.error("Job start failed:", err);
        videosToStart.forEach(v => {
          updateVideoInList(v.id, { status: 'error', statusText: 'Failed to connect to server' });
        });
      }
    },

    cancel: async (video: VideoFile) => {
      updateVideoInList(video.id, { statusText: 'Cancelling...' });
      try {
        await api.cancelJob(video.filePath);
      } catch (err) {
        console.error("Cancel failed:", err);
        updateVideoInList(video.id, { statusText: 'Cancel failed' });
      }
      // The backend confirms with a "cancelled" SSE frame; the card settles when it arrives.
    },

    abortAll: async () => {
      try {
        await api.abortAll();
      } catch (err) {
        console.error("Abort failed:", err);
      }
    }
  }), [performScan, subscribeToUpdates, updateVideoInList, state.currentPath, state.settings]);

  // 5. LIFECYCLE
  useEffect(() => {
    setMounted(true);
    if (!hasInitialScanned.current) {
      hasInitialScanned.current = true;
      performScan("/data");
    }

    const listeners = activeListeners.current;
    return () => {
      Object.values(listeners).forEach(es => es.close());
    };
  }, [performScan]);

  if (!mounted) return <div className="h-full w-full bg-[#0a0a0a]" />;

  return (
    <StudioContext.Provider value={{ state, actions }}>
      <DashboardContent />
    </StudioContext.Provider>
  );
}
