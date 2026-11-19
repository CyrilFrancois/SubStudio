// AppShell.tsx — Left navigation, stub-data banner and backend status around every screen. Exports: default AppShell.
"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { AlertTriangle, Library, ListOrdered, Settings, History, Server, ServerOff } from 'lucide-react';
import { api, ApiError } from '@/lib/api';
import type { SystemStatus } from '@/lib/types';

const NAV = [
  { href: '/', label: 'Library', icon: Library },
  { href: '/queue', label: 'Queue', icon: ListOrdered },
  { href: '/settings', label: 'Settings', icon: Settings },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [down, setDown] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () => api.system()
      .then(s => { if (alive) { setSystem(s); setDown(null); } })
      .catch((e: ApiError) => { if (alive) setDown(e.message); });
    poll();
    const t = setInterval(poll, 10_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <nav aria-label="Main" className="w-52 shrink-0 bg-[#0a0a0a] border-r border-white/10 flex flex-col">
        <div className="px-5 py-5 flex items-center gap-2.5">
          <img src="/logo-96.png" alt="" className="w-7 h-7 object-contain" />
          <span className="text-base font-bold tracking-tight text-white">SubStudio</span>
        </div>
        <ul className="px-3 space-y-1 flex-1">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === '/' ? path === '/' : path.startsWith(href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? 'page' : undefined}
                  className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                    active ? 'bg-indigo-600/15 text-indigo-300' : 'text-gray-400 hover:text-gray-200 hover:bg-white/5'}`}
                >
                  <Icon size={16} /> {label}
                </Link>
              </li>
            );
          })}
          <li className="pt-4 mt-4 border-t border-white/5">
            <Link
              href="/legacy"
              title="The previous dashboard, kept until the Review screen replaces its last uses"
              className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors ${
                path.startsWith('/legacy') ? 'bg-white/5 text-gray-200' : 'text-gray-500 hover:text-gray-300'}`}
            >
              <History size={14} /> Old dashboard
            </Link>
          </li>
        </ul>
        <div className="px-5 py-4 border-t border-white/5 text-xs space-y-1.5">
          {down ? (
            <div className="flex items-start gap-2 text-red-400" role="status">
              <ServerOff size={14} className="shrink-0 mt-0.5" /> <span>Backend offline</span>
            </div>
          ) : system ? (
            <div className="flex items-center gap-2 text-emerald-400" role="status">
              <Server size={14} /> <span>Backend online</span>
            </div>
          ) : (
            <div className="text-gray-500">Connecting…</div>
          )}
          {system && (
            <div className="text-gray-500 font-mono truncate" title={system.media_root}>{system.media_root}</div>
          )}
        </div>
      </nav>

      <div className="flex-1 min-w-0 flex flex-col">
        {system?.stub && (
          <div role="alert" className="flex items-center gap-2 px-6 py-2 bg-amber-500/10 border-b border-amber-500/30 text-amber-300 text-xs">
            <AlertTriangle size={14} />
            Stub mode (SUBSTUDIO_API_STUB=1): jobs, progress and cues are simulated. Nothing is written to your files.
          </div>
        )}
        {down && (
          <div role="alert" className="flex items-center gap-2 px-6 py-2 bg-red-500/10 border-b border-red-500/30 text-red-300 text-xs">
            <ServerOff size={14} /> {down}
          </div>
        )}
        <main className="flex-1 min-h-0 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
