import { useState, useRef, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Pencil } from "lucide-react";
import type { Message } from "../types";
import { ToolCallBlock } from "./ToolCallBlock";
import { ThinkingBlock } from "./ThinkingBlock";
import { ClickableImage } from "./ImageLightbox";
import { extractTextContent, extractThinking, getToolOutputText } from "../lib/utils";

/** True if there is any non-Tool message after this one (next AI with tool_calls, final AI content, or Human). */
function hasNextMessageAfterTools(messages: Message[], fromIndex: number): boolean {
  for (let i = fromIndex + 1; i < messages.length; i++) {
    const m = messages[i];
    if (m.type === "tool" || m.type === "ToolMessage") continue;
    return true; /* AI or Human */
  }
  return false;
}

interface MessageBubbleProps {
  message: Message;
  messageIndex: number;
  allMessages: Message[];
  streaming: boolean;
  toolResults: Map<string, Message>;
  threadId: string | null;
  sessionRuntimes: Map<string, string>;
  onEditMessage?: (index: number, newContent: string) => void;
}

export function MessageBubble({
  message,
  messageIndex,
  allMessages,
  streaming,
  toolResults,
  threadId,
  sessionRuntimes,
  onEditMessage,
}: MessageBubbleProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
      const len = textareaRef.current.value.length;
      textareaRef.current.setSelectionRange(len, len);
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [isEditing]);

  const handleEditStart = () => {
    setEditContent(typeof message.content === "string" ? message.content : extractTextContent(message.content));
    setIsEditing(true);
  };

  const handleEditCancel = () => {
    setIsEditing(false);
    setEditContent("");
  };

  const handleEditSave = () => {
    if (!editContent.trim() || !onEditMessage) return;
    setIsEditing(false);
    onEditMessage(messageIndex, editContent.trim());
  };

  const handleEditKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleEditSave();
    } else if (e.key === "Escape") {
      handleEditCancel();
    }
  };

  const isHuman = message.type === "human" || message.type === "HumanMessage";
  const isAI =
    message.type === "ai" ||
    message.type === "AIMessage" ||
    message.type === "AIMessageChunk";
  const isTool = message.type === "tool" || message.type === "ToolMessage";

  if (isTool) return null;

  if (isAI && message.tool_calls && message.tool_calls.length > 0) {
    const thinking = extractThinking(message.content);
    const toolCalls = message.tool_calls;
    const hasNextMessage = hasNextMessageAfterTools(allMessages, messageIndex);

    return (
      <div className="flex justify-start">
        <div className="max-w-[85%]">
          {thinking && <ThinkingBlock text={thinking} />}
          {toolCalls.map((tc, tcIndex) => {
            const toolMsg = toolResults.get(tc.id);
            const resultText = toolMsg
              ? getToolOutputText(toolMsg.content)
              : undefined;
            const isLastToolCall = tcIndex === toolCalls.length - 1;
            const shouldStayOpen =
              isLastToolCall && !hasNextMessage && streaming;

            return (
              <ToolCallBlock
                key={tc.id}
                toolCall={tc}
                result={resultText}
                resultContent={toolMsg?.content}
                threadId={threadId}
                sessionRuntimes={sessionRuntimes}
                shouldStayOpen={shouldStayOpen}
              />
            );
          })}
        </div>
      </div>
    );
  }

  const content = extractTextContent(message.content);
  if (!content.trim()) return null;

  if (isHuman) {
    const canEdit = onEditMessage && !streaming && typeof message.content === "string" && !!message.id;

    if (isEditing) {
      return (
        <div className="flex justify-end">
          <div className="flex w-full max-w-[80%] flex-col gap-2">
            <textarea
              ref={textareaRef}
              value={editContent}
              onChange={(e) => {
                setEditContent(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${e.target.scrollHeight}px`;
              }}
              onKeyDown={handleEditKeyDown}
              rows={1}
              className="w-full resize-none overflow-hidden rounded-2xl rounded-br-md border border-indigo-400 bg-indigo-700 px-4 py-2.5 text-sm text-white placeholder-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={handleEditCancel}
                className="rounded-lg border border-zinc-600 bg-zinc-800 px-3 py-1 text-xs text-zinc-300 transition-colors hover:bg-zinc-700"
              >
                Cancelar
              </button>
              <button
                onClick={handleEditSave}
                disabled={!editContent.trim()}
                className="rounded-lg bg-indigo-600 px-3 py-1 text-xs text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Salvar
              </button>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="group flex justify-end">
        <div className="flex max-w-[80%] items-start gap-2">
          {canEdit && (
            <button
              onClick={handleEditStart}
              className="mt-1 flex-shrink-0 rounded-lg p-1 text-zinc-500 opacity-0 transition-all hover:bg-zinc-800 hover:text-zinc-300 group-hover:opacity-100"
              title="Editar mensagem"
              aria-label="Editar mensagem"
            >
              <Pencil size={14} />
            </button>
          )}
          <div className="rounded-2xl rounded-br-md bg-indigo-600 px-4 py-2.5 text-sm text-white">
            <div className="whitespace-pre-wrap">{content}</div>
          </div>
        </div>
      </div>
    );
  }

  if (isAI) {
    const thinking = extractThinking(message.content);
    return (
      <div className="flex justify-start">
        <div className="max-w-[85%]">
          {thinking && <ThinkingBlock text={thinking} />}
          <div className="rounded-2xl rounded-bl-md bg-zinc-800 px-4 py-2.5 text-sm text-zinc-100">
            <div className="chat-markdown prose prose-invert prose-sm max-w-none prose-headings:text-zinc-100 prose-p:text-zinc-200 prose-li:text-zinc-200 prose-blockquote:border-zinc-600 prose-pre:bg-zinc-900 prose-code:before:content-none prose-code:after:content-none">
              <Markdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p({ children }) {
                    return <p className="mb-3 last:mb-0">{children}</p>;
                  },
                  ul({ children }) {
                    return <ul className="my-2 list-disc pl-5 space-y-1">{children}</ul>;
                  },
                  ol({ children }) {
                    return <ol className="my-2 list-decimal pl-5 space-y-1">{children}</ol>;
                  },
                  blockquote({ children }) {
                    return (
                      <blockquote className="my-2 pl-4 border-l-4 border-zinc-600 bg-zinc-900/50 py-1 text-zinc-300">
                        {children}
                      </blockquote>
                    );
                  },
                  table({ children }) {
                    return (
                      <div className="my-3 overflow-x-auto rounded-lg border border-zinc-700">
                        <table className="min-w-full text-sm">{children}</table>
                      </div>
                    );
                  },
                  th({ children }) {
                    return (
                      <th className="border border-zinc-600 bg-zinc-800 px-3 py-2 text-left font-semibold text-zinc-200">
                        {children}
                      </th>
                    );
                  },
                  td({ children }) {
                    return (
                      <td className="border border-zinc-700 px-3 py-2 text-zinc-300">
                        {children}
                      </td>
                    );
                  },
                  a({ href, children }) {
                    return (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-400 underline underline-offset-2 hover:text-indigo-300"
                      >
                        {children}
                      </a>
                    );
                  },
                  code({ className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || "");
                    const codeStr = String(children).replace(/\n$/, "");
                    if (match) {
                      return (
                        <SyntaxHighlighter
                          style={oneDark}
                          language={match[1]}
                          customStyle={{
                            margin: 0,
                            borderRadius: "0.375rem",
                            fontSize: "0.8125rem",
                            padding: "0.75rem 1rem",
                          }}
                          wrapLongLines
                        >
                          {codeStr}
                        </SyntaxHighlighter>
                      );
                    }
                    return (
                      <code
                        className="rounded bg-zinc-700 px-1.5 py-0.5 text-[0.8125rem] font-mono"
                        {...props}
                      >
                        {children}
                      </code>
                    );
                  },
                  pre({ children }) {
                    return (
                      <pre className="my-2 overflow-x-auto rounded-lg bg-zinc-900 p-3 text-[0.8125rem]">
                        {children}
                      </pre>
                    );
                  },
                  img({ src, alt }) {
                    if (!src) return null;
                    return (
                      <ClickableImage
                        src={src}
                        alt={alt}
                        className="my-2 max-w-full rounded-lg border border-zinc-800"
                      />
                    );
                  },
                }}
              >
                {content}
              </Markdown>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return null;
}
