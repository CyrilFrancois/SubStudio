// sse.ts — EventSource wrapper for GET /api/events/{file_id}. Exports: SSEEvent, createSSEConnection.

export type SSEEvent = {
  type: 'status' | 'log';
  fileId: string;
  /** Status description, or the log line for type 'log'. */
  message: string;
  status?: string;
  progress?: number;
  level?: string;
};

/**
 * Opens the progress stream for one file.
 *
 * `fileId` must be the RAW server-side path — this function applies the single
 * encodeURIComponent that the backend's {file_id:path} route expects. Encoding it before
 * calling produces a double-encoded key that matches no emitter, and the stream stays silent.
 */
export const createSSEConnection = (
  fileId: string,
  onMessage: (data: SSEEvent) => void,
  onError?: (error: Event) => void
) => {
  const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const url = `${API_BASE_URL}/api/events/${encodeURIComponent(fileId)}`;

  const eventSource = new EventSource(url);

  eventSource.onmessage = (event) => {
    try {
      const data: SSEEvent = JSON.parse(event.data);
      if (data && data.type) onMessage(data);
    } catch (err) {
      console.error("SSE parse error:", err, "Raw data:", event.data);
    }
  };

  eventSource.onerror = (error) => {
    console.error(`SSE connection error for ${fileId}:`, error);
    if (onError) onError(error);
  };

  return eventSource;
};
