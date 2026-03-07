import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { ContentBlock, DisplayOutput, Message } from "../types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function extractTextContent(
  content: string | ContentBlock[],
): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      if (block.type === "text" && block.text) parts.push(block.text);
    }
    if (parts.length > 0) return parts.join("\n");
  }
  return String(content);
}

export function extractThinking(content: string | ContentBlock[]): string {
  if (!Array.isArray(content)) return "";
  const parts: string[] = [];
  for (const b of content) {
    if (b.type === "thinking" || b.type === "reasoning") {
      const text = b.thinking || b.reasoning || b.text || "";
      if (text.trim()) parts.push(text.trim());
    }
  }
  return parts.join("\n\n");
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function timeAgo(isoStr: string): string {
  try {
    const dt = new Date(isoStr);
    const seconds = Math.floor((Date.now() - dt.getTime()) / 1000);
    if (seconds < 60) return "now";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d`;
    const months = Math.floor(days / 30);
    return `${months} month${months > 1 ? "s" : ""}`;
  } catch {
    return "";
  }
}

export interface SessionStatus {
  sessionId: string;
  runtime: string;
  status: string;
}

export function extractSessions(
  messages: Message[],
): Map<string, SessionStatus> {
  const sessions = new Map<string, SessionStatus>();
  for (const msg of messages) {
    if (msg.type !== "tool" && msg.type !== "ToolMessage") continue;
    const name = msg.name ?? "";
    const raw =
      typeof msg.content === "string" ? msg.content : undefined;
    if (!raw) continue;

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(raw);
    } catch {
      continue;
    }
    if (typeof data !== "object" || data === null) continue;

    if (name === "create_session" && data.session_id) {
      const sid = String(data.session_id);
      sessions.set(sid, {
        sessionId: sid,
        runtime: String(data.runtime ?? ""),
        status: String(data.status ?? "unknown"),
      });
    } else if (name === "stop_session" && data.session_id) {
      const sid = String(data.session_id);
      const existing = sessions.get(sid);
      if (existing) {
        existing.status = "stopped";
      } else {
        sessions.set(sid, {
          sessionId: sid,
          runtime: "",
          status: "stopped",
        });
      }
    }
  }
  return sessions;
}

export function parseToolOutput(raw: string): {
  data: Record<string, unknown> | null;
  isError: boolean;
} {
  try {
    const data = JSON.parse(raw);
    if (typeof data === "object" && data !== null) {
      const isError =
        data.success === false ||
        (typeof raw === "string" && raw.includes("Error invoking tool"));
      return { data, isError };
    }
    return { data: null, isError: false };
  } catch {
    const isError = raw.includes("Error invoking tool");
    return { data: null, isError };
  }
}

export function getToolOutputText(
  content: string | ContentBlock[],
): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    for (const block of content) {
      if (block.type === "text" && block.text) return block.text;
    }
  }
  return String(content);
}

export function extractDisplayOutputs(
  content: string | ContentBlock[],
): DisplayOutput[] {
  const outputs: DisplayOutput[] = [];

  // From multimodal content blocks (vision mode — image_url blocks)
  if (Array.isArray(content)) {
    for (const block of content) {
      if (block.type === "image_url" && block.image_url?.url) {
        const url = block.image_url.url;
        const match = url.match(/^data:([^;]+);base64,(.+)$/);
        if (match) {
          outputs.push({ type: match[1], data: match[2] });
        } else {
          outputs.push({ type: "image/png", data: url });
        }
      }
    }
  }

  // From JSON string (non-vision mode — display_outputs in payload)
  if (typeof content === "string") {
    try {
      const data = JSON.parse(content);
      if (data?.display_outputs && Array.isArray(data.display_outputs)) {
        for (const out of data.display_outputs) {
          if (out && typeof out.type === "string" && typeof out.data === "string") {
            outputs.push({ type: out.type, data: out.data });
          }
        }
      }
    } catch {
      // not JSON
    }
  }

  return outputs;
}
