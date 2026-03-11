import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import { Send, Loader2, Paperclip, Square, X, Upload } from "lucide-react";
import type { Message } from "../types";
import { MessageBubble } from "./MessageBubble";
import { extractSessions } from "../lib/utils";

interface ChatAreaProps {
  messages: Message[];
  streaming: boolean;
  onSendMessage: (content: string, files?: File[]) => void;
  onEditMessage: (index: number, newContent: string) => void;
  onStopStreaming: () => void;
  threadId: string | null;
}

export function ChatArea({
  messages,
  streaming,
  onSendMessage,
  onEditMessage,
  onStopStreaming,
  threadId,
}: ChatAreaProps) {
  const [input, setInput] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [threadId]);

  const toolResults = useMemo(() => {
    const map = new Map<string, Message>();
    for (const msg of messages) {
      if (
        (msg.type === "tool" || msg.type === "ToolMessage") &&
        msg.tool_call_id
      ) {
        map.set(msg.tool_call_id, msg);
      }
    }
    return map;
  }, [messages]);

  const sessionRuntimes = useMemo(() => {
    const sessions = extractSessions(messages);
    const map = new Map<string, string>();
    for (const [sid, info] of sessions) {
      if (info.runtime) map.set(sid, info.runtime);
    }
    return map;
  }, [messages]);

  const handleSubmit = () => {
    const text = input.trim();
    if ((!text && pendingFiles.length === 0) || streaming) return;
    setInput("");
    const files = pendingFiles.length > 0 ? [...pendingFiles] : undefined;
    setPendingFiles([]);
    onSendMessage(text, files);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setPendingFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
    }
    e.target.value = "";
  };

  const removeFile = (index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  };

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (e.dataTransfer.types.includes("Files")) {
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current === 0) {
      setIsDragging(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      setPendingFiles((prev) => [...prev, ...Array.from(e.dataTransfer.files)]);
    }
  }, []);

  return (
    <div
      className="relative flex min-h-0 min-w-0 flex-1 flex-col w-full"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {isDragging && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-zinc-900/80 backdrop-blur-sm">
          <div className="flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-indigo-500 bg-zinc-800/90 px-12 py-10">
            <Upload size={40} className="text-indigo-400" />
            <p className="text-lg font-medium text-zinc-200">Solte os arquivos aqui</p>
            <p className="text-sm text-zinc-400">Os arquivos serão anexados à mensagem</p>
          </div>
        </div>
      )}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col w-full overflow-y-auto px-4 py-6">
        <div className="flex w-full min-w-0 flex-1 flex-col space-y-3">
          {messages.length === 0 && (
            <div className="flex h-full items-center justify-center pt-20 text-zinc-500">
              <p>Send a message to start the conversation.</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <MessageBubble
              key={i}
              message={msg}
              messageIndex={i}
              allMessages={messages}
              streaming={streaming}
              toolResults={toolResults}
              threadId={threadId}
              sessionRuntimes={sessionRuntimes}
              onEditMessage={onEditMessage}
            />
          ))}
          {streaming && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-2xl rounded-bl-md bg-zinc-800 px-4 py-2.5 text-sm text-zinc-400">
                <Loader2 size={14} className="animate-spin" />
                Thinking...
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="flex w-full min-w-0 shrink-0 flex-wrap border-t border-zinc-800 bg-zinc-900 p-4">
        <div className="flex w-full min-w-0 flex-col">
          {pendingFiles.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {pendingFiles.map((f, i) => (
                <div
                  key={i}
                  className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-300"
                >
                  <Paperclip size={12} className="text-zinc-500" />
                  <span className="max-w-[150px] truncate">{f.name}</span>
                  <button
                    onClick={() => removeFile(i)}
                    className="text-zinc-500 hover:text-zinc-300"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="flex w-full min-w-0 gap-3">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleFileSelect}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center justify-center rounded-xl border border-zinc-700 bg-zinc-800 px-3 text-zinc-400 transition-colors hover:bg-zinc-700 hover:text-zinc-200"
              title="Upload files"
            >
              <Paperclip size={18} />
            </button>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a message..."
              rows={1}
              className="min-w-0 flex-1 resize-none overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-800 px-4 py-2.5 text-sm text-zinc-100 placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
              style={{ minHeight: "2.5rem" }}
            />
            <button
              onClick={streaming ? onStopStreaming : handleSubmit}
              disabled={!streaming && !input.trim() && pendingFiles.length === 0}
              className="flex items-center justify-center rounded-xl bg-indigo-600 px-4 text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
              title={streaming ? "Stop response" : "Send message"}
              aria-label={streaming ? "Stop response" : "Send message"}
            >
              {streaming ? <Square size={18} fill="currentColor" /> : <Send size={18} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
