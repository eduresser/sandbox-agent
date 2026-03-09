import { useState, useCallback, useRef } from "react";
import type { Message, Settings, UploadedFileMeta } from "../types";
import * as api from "../api/aegra";
import { formatFileSize } from "../lib/utils";

function buildUserContent(text: string, fileMetas: UploadedFileMeta[]): string {
  if (fileMetas.length === 0) return text;

  const fileBlock = fileMetas
    .map((f) => `- \`${f.name}\` (${formatFileSize(f.size)}) saved at \`${f.path}\``)
    .join("\n");

  const parts: string[] = [];
  if (text) parts.push(text);
  parts.push(`\n\n**Uploaded files:**\n${fileBlock}`);
  return parts.join("\n");
}

export function useChat(
  threadId: string | null,
  settings: Settings,
  createThread: () => Promise<{ thread_id: string }>,
) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const loadMessages = useCallback(async () => {
    if (!threadId) {
      setMessages([]);
      return;
    }
    try {
      const state = await api.getThreadState(threadId);
      const msgs = state.values?.messages ?? [];
      setMessages(msgs);
    } catch {
      setMessages([]);
    }
  }, [threadId]);

  const sendMessage = useCallback(
    async (content: string, files?: File[]) => {
      if ((!content.trim() && (!files || files.length === 0)) || streaming) return;

      let threadIdToUse = threadId;
      if (!threadIdToUse) {
        try {
          const thread = await createThread();
          threadIdToUse = thread.thread_id;
        } catch (err) {
          console.error("Failed to create thread:", err);
          return;
        }
      }

      setStreaming(true);

      let fileMetas: UploadedFileMeta[] = [];
      if (files && files.length > 0) {
        try {
          fileMetas = await api.uploadFiles(threadIdToUse, files);
        } catch (err) {
          console.error("File upload error:", err);
        }
      }

      const fullContent = buildUserContent(content, fileMetas);
      if (!fullContent.trim()) {
        setStreaming(false);
        return;
      }

      const userMsg: Message = { type: "human", content: fullContent };
      setMessages((prev) => [...prev, userMsg]);

      try {
        const abortController = new AbortController();
        abortControllerRef.current = abortController;

        const configurable: Record<string, string> = {
          chat_model: settings.chatModel,
          chat_model_provider: settings.chatModelProvider,
          chat_model_api_key: settings.chatModelApiKey,
          chat_model_base_url: settings.chatModelBaseUrl ?? "",
          chat_model_supports_vision: String(settings.supportsVision),
        };

        for await (const event of api.streamRun(
          threadIdToUse,
          [{ role: "human", content: fullContent }],
          configurable,
          abortController.signal,
        )) {
          if (event.event === "error") {
            const errData = event.data;
            let errMsg = "An unexpected error occurred.";
            if (typeof errData === "string") {
              errMsg = errData;
            } else if (typeof errData === "object" && errData !== null) {
              const obj = errData as Record<string, unknown>;
              errMsg = (obj.message ?? obj.error ?? JSON.stringify(obj)) as string;
            }
            throw new Error(errMsg);
          }
          if (
            event.event === "values" &&
            typeof event.data === "object" &&
            event.data !== null
          ) {
            const data = event.data as Record<string, unknown>;
            const allMsgs = (data.messages ?? []) as Message[];
            setMessages(allMsgs);
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        console.error("Stream error:", err);
        const errorMsg: Message = {
          type: "ai",
          content: `Error: ${err instanceof Error ? err.message : String(err)}`,
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        abortControllerRef.current = null;
        setStreaming(false);
      }
    },
    [threadId, streaming, settings, createThread],
  );

  const editMessage = useCallback(
    async (messageIndex: number, newContent: string) => {
      if (!threadId || streaming || !newContent.trim()) return;

      const msgsToRemove = messages.slice(messageIndex);
      const missingId = msgsToRemove.some((m) => !m.id);
      if (missingId) {
        console.error("Cannot edit: one or more messages lack an id.");
        return;
      }

      const editedMsg: Message = {
        ...messages[messageIndex],
        content: newContent,
      };

      setMessages([...messages.slice(0, messageIndex), editedMsg]);
      setStreaming(true);

      try {
        const removals = msgsToRemove.map((m) => ({
          type: "remove",
          id: m.id as string,
          content: "",
        }));
        await api.updateThreadState(threadId, removals);

        const abortController = new AbortController();
        abortControllerRef.current = abortController;

        const configurable: Record<string, string> = {
          chat_model: settings.chatModel,
          chat_model_provider: settings.chatModelProvider,
          chat_model_api_key: settings.chatModelApiKey,
          chat_model_base_url: settings.chatModelBaseUrl ?? "",
          chat_model_supports_vision: String(settings.supportsVision),
        };

        for await (const event of api.streamRun(
          threadId,
          [{ role: "human", content: newContent }],
          configurable,
          abortController.signal,
        )) {
          if (event.event === "error") {
            const errData = event.data;
            let errMsg = "An unexpected error occurred.";
            if (typeof errData === "string") {
              errMsg = errData;
            } else if (typeof errData === "object" && errData !== null) {
              const obj = errData as Record<string, unknown>;
              errMsg = (obj.message ?? obj.error ?? JSON.stringify(obj)) as string;
            }
            throw new Error(errMsg);
          }
          if (
            event.event === "values" &&
            typeof event.data === "object" &&
            event.data !== null
          ) {
            const data = event.data as Record<string, unknown>;
            const allMsgs = (data.messages ?? []) as Message[];
            setMessages(allMsgs);
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        console.error("Edit stream error:", err);
        const errorMsg: Message = {
          type: "ai",
          content: `Error: ${err instanceof Error ? err.message : String(err)}`,
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        abortControllerRef.current = null;
        setStreaming(false);
      }
    },
    [threadId, streaming, messages, settings],
  );

  const stopStreaming = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  return {
    messages,
    streaming,
    sendMessage,
    editMessage,
    stopStreaming,
    loadMessages,
    setMessages,
  };
}
