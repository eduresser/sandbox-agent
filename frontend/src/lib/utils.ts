import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { ContentBlock, Message } from "../types";

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

export function extractFigures(
  content: string | ContentBlock[],
): string[] {
  const figures: string[] = [];
  const seen = new Set<string>();

  if (Array.isArray(content)) {
    for (const block of content) {
      if (block.type === "image_url" && block.image_url?.url) {
        const url = block.image_url.url;
        const b64 = url.includes(",") ? url.split(",")[1] : url;
        if (b64 && !seen.has(b64)) {
          seen.add(b64);
          figures.push(b64);
        }
      }
    }
  }

  if (typeof content === "string") {
    try {
      const data = JSON.parse(content);
      if (data?.figures && Array.isArray(data.figures)) {
        for (const fig of data.figures) {
          if (typeof fig === "string" && fig && !seen.has(fig)) {
            seen.add(fig);
            figures.push(fig);
          }
        }
      }
    } catch {
      // not JSON
    }
  }

  return figures;
}
