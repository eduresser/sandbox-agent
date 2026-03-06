import type { Thread, ThreadState, SSEEvent, UploadedFileMeta } from "../types";

const BASE_URL = "/api";
const ASSISTANT_ID = "sandbox-agent";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export async function checkHealth(): Promise<boolean> {
  try {
    await request("/health");
    return true;
  } catch {
    return false;
  }
}

export async function createThread(
  metadata?: Record<string, unknown>,
): Promise<Thread> {
  return request<Thread>("/threads", {
    method: "POST",
    body: JSON.stringify({ metadata: metadata ?? {} }),
  });
}

export async function listThreads(limit = 50): Promise<Thread[]> {
  return request<Thread[]>("/threads/search", {
    method: "POST",
    body: JSON.stringify({ limit }),
  });
}

export async function deleteThread(threadId: string): Promise<void> {
  await fetch(`${BASE_URL}/threads/${threadId}`, { method: "DELETE" });
}

export async function getThreadState(
  threadId: string,
): Promise<ThreadState> {
  return request<ThreadState>(`/threads/${threadId}/state`);
}

export async function uploadFiles(
  threadId: string,
  files: File[],
): Promise<UploadedFileMeta[]> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f);
  }
  const res = await fetch(`${BASE_URL}/threads/${threadId}/files/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Upload error ${res.status}: ${text}`);
  }
  return res.json();
}

export function getDownloadUrl(
  threadId: string,
  sessionId: string,
  path: string,
): string {
  return `${BASE_URL}/threads/${threadId}/files/download?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`;
}

export async function* streamRun(
  threadId: string,
  messages: { role: string; content: string }[],
  configurable?: Record<string, string>,
): AsyncGenerator<SSEEvent> {
  const payload: Record<string, unknown> = {
    assistant_id: ASSISTANT_ID,
    input: { messages },
    stream_mode: ["values"],
  };
  if (configurable) {
    payload.config = { configurable };
  }

  const res = await fetch(`${BASE_URL}/threads/${threadId}/runs/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Stream error ${res.status}: ${text}`);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let eventType = "";
  const dataLines: string[] = [];

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      } else if (line === "") {
        if (dataLines.length > 0) {
          const raw = dataLines.join("\n");
          let parsed: unknown;
          try {
            parsed = JSON.parse(raw);
          } catch {
            parsed = raw;
          }
          yield { event: eventType, data: parsed };
        }
        eventType = "";
        dataLines.length = 0;
      }
    }
  }

  if (dataLines.length > 0) {
    const raw = dataLines.join("\n");
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = raw;
    }
    yield { event: eventType, data: parsed };
  }
}
