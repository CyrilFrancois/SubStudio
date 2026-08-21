// api.ts — Backend REST client. Exports: api, scanFolder, startJob.

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const api = {
  /** GET /api/scan — recursive directory walk with subtitle detection. */
  async scanFolder(path: string = "/data") {
    const params = new URLSearchParams({ target_path: path });

    const response = await fetch(`${API_BASE_URL}/api/scan?${params}`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    });

    return handleResponse(response, 'Failed to scan media library');
  },

  /** POST /api/process — queues a batch and returns immediately. */
  async startJob(payload: unknown) {
    const response = await fetch(`${API_BASE_URL}/api/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    return handleResponse(response, 'Failed to start the processing job');
  }
};

async function handleResponse(response: Response, defaultError: string) {
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || defaultError);
  }
  return response.json();
}

export const { scanFolder, startJob } = api;
