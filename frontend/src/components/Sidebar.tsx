import { useState, useEffect, useCallback, useRef } from "react";
import { Plus, Trash2, MessageSquare, Settings } from "lucide-react";
import type { Thread, Message } from "../types";
import { cn, timeAgo, extractTextContent } from "../lib/utils";
import * as api from "../api/aegra";

interface SidebarProps {
  threads: Thread[];
  activeThreadId: string | null;
  streaming: boolean;
  onSelectThread: (id: string) => void;
  onNewThread: () => void;
  onDeleteThread: (id: string) => void;
  onOpenSettings: () => void;
}

export function Sidebar({
  threads,
  activeThreadId,
  streaming,
  onSelectThread,
  onNewThread,
  onDeleteThread,
  onOpenSettings,
}: SidebarProps) {
  const [previews, setPreviews] = useState<Map<string, string | null>>(new Map());
  const prevStreamingRef = useRef(false);

  const loadPreview = useCallback(async (threadId: string) => {
    try {
      const state = await api.getThreadState(threadId);
      const msgs: Message[] = state.values?.messages ?? [];

      const hasResponse = msgs.some(
        (m) =>
          m.type === "ai" ||
          m.type === "AIMessage" ||
          m.type === "AIMessageChunk" ||
          m.type === "tool" ||
          m.type === "ToolMessage",
      );
      if (!hasResponse) {
        setPreviews((prev) => new Map(prev).set(threadId, null));
        return;
      }

      for (const m of msgs) {
        if (m.type === "human" || m.type === "HumanMessage") {
          const text = extractTextContent(m.content).trim().split("\n")[0];
          const preview = text.length > 40 ? text.slice(0, 40) + "..." : text;
          setPreviews((prev) => new Map(prev).set(threadId, preview));
          return;
        }
      }
      setPreviews((prev) => new Map(prev).set(threadId, null));
    } catch {
      setPreviews((prev) => new Map(prev).set(threadId, null));
    }
  }, []);

  useEffect(() => {
    for (const t of threads) {
      if (!previews.has(t.thread_id)) {
        loadPreview(t.thread_id);
      }
    }
  }, [threads, previews, loadPreview]);

  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = streaming;

    if (wasStreaming && !streaming && activeThreadId) {
      loadPreview(activeThreadId);
    }
  }, [streaming, activeThreadId, loadPreview]);

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900">
      <div className="flex items-center justify-between p-4">
        <h1 className="text-sm font-semibold text-zinc-300">Sandbox Agent</h1>
        <button
          onClick={onNewThread}
          className="rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
          title="New chat"
        >
          <Plus size={18} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 flex flex-col gap-0.5">
        {threads.length === 0 && (
          <p className="px-3 py-4 text-xs text-zinc-500">No conversations yet.</p>
        )}
        {threads.map((thread) => {
          const elapsed = thread.updated_at ? timeAgo(thread.updated_at) : "";
          const preview = previews.get(thread.thread_id) ?? "";

          return (
            <div
              key={thread.thread_id}
              className={cn(
                "group flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm cursor-pointer transition-colors",
                thread.thread_id === activeThreadId
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200",
              )}
              onClick={() => onSelectThread(thread.thread_id)}
            >
              <MessageSquare size={14} className="shrink-0" />
              <div className="flex-1 min-w-0">
                <span className="block truncate leading-tight">
                  {preview || "New conversation"}
                </span>
                {elapsed && (
                  <span className="block text-[10px] text-zinc-500 leading-tight">{elapsed}</span>
                )}
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteThread(thread.thread_id);
                }}
                className="hidden shrink-0 rounded p-0.5 text-zinc-500 hover:text-red-400 group-hover:block"
                title="Delete"
              >
                <Trash2 size={14} />
              </button>
            </div>
          );
        })}
      </div>

      <div className="border-t border-zinc-800 p-3">
        <button
          onClick={onOpenSettings}
          className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
        >
          <Settings size={14} />
          Settings
        </button>
      </div>
    </aside>
  );
}
