import { useState, useEffect, useCallback } from "react";
import type { Thread } from "../types";
import * as api from "../api/aegra";

export function useThreads() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listThreads(50);
      setThreads(list);
    } catch (err) {
      console.error("Failed to load threads:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const createThread = useCallback(async () => {
    const thread = await api.createThread({ source: "frontend" });
    setThreads((prev) => [thread, ...prev]);
    setActiveThreadId(thread.thread_id);
    return thread;
  }, []);

  const deleteThread = useCallback(
    async (threadId: string) => {
      await api.deleteThread(threadId);
      setThreads((prev) => prev.filter((t) => t.thread_id !== threadId));
      if (activeThreadId === threadId) {
        setActiveThreadId(null);
      }
    },
    [activeThreadId],
  );

  return {
    threads,
    activeThreadId,
    setActiveThreadId,
    createThread,
    deleteThread,
    loading,
    refresh,
  };
}
