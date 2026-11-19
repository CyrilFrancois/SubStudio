// api.ts — REST client for the UI contract (backend/api). Every failure throws ApiError with the backend's sentence. Exports: API_BASE_URL, ApiError, api.
import type {
  Job, JobCreate, JobCreateResponse, JobList, LibraryResponse, LogEvent, Settings, SettingsResponse, SystemStatus,
} from './types';

// `||` and not `??`: a defined-but-empty NEXT_PUBLIC_API_URL must fall back too.
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // fetch only rejects when no HTTP answer came back at all.
    throw new ApiError(0, `The backend does not answer at ${API_BASE_URL}. Is it running?`);
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    const detail = data?.detail;
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ') : '';
    throw new ApiError(res.status, message || `${method} ${path} failed with HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  library: () => request<LibraryResponse>('GET', '/api/library'),
  createJobs: (req: JobCreate) => request<JobCreateResponse>('POST', '/api/jobs', req),
  jobs: () => request<JobList>('GET', '/api/jobs'),
  cancelJob: (id: string) => request<Job>('DELETE', `/api/jobs/${encodeURIComponent(id)}`),
  jobLogs: (id: string, limit = 500) => request<LogEvent[]>('GET', `/api/jobs/${encodeURIComponent(id)}/logs?limit=${limit}`),
  settings: () => request<SettingsResponse>('GET', '/api/settings'),
  saveSettings: (s: Settings) => request<SettingsResponse>('PUT', '/api/settings', s),
  system: () => request<SystemStatus>('GET', '/api/system'),
};
