import { useState, useEffect } from "react";
import {
  ChevronDown,
  ChevronRight,
  Play,
  Terminal,
  Box,
  Square,
  Upload,
  Download,
  Wrench,
  CheckCircle2,
  XCircle,
  Loader2,
} from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import type { ToolCall } from "../types";
import {
  parseToolOutput,
  extractFigures,
  formatFileSize,
} from "../lib/utils";
import { getDownloadUrl } from "../api/aegra";
import { ClickableImage } from "./ImageLightbox";

interface ToolCallBlockProps {
  toolCall: ToolCall;
  result?: string;
  resultContent?: string | { type: string; text?: string; image_url?: { url: string } }[];
  threadId: string | null;
  sessionRuntimes: Map<string, string>;
  /** Keep dropdown open until another tool call, final response, or flow end */
  shouldStayOpen: boolean;
}

const RUNTIME_LANG: Record<string, string> = {
  python: "python",
  node: "javascript",
  r: "r",
  julia: "julia",
};

const MAX_OUTPUT_LINES = 60;

function truncateOutput(text: string): string {
  const lines = text.split("\n");
  if (lines.length <= MAX_OUTPUT_LINES) return text;
  return lines.slice(0, MAX_OUTPUT_LINES).join("\n") + `\n\n... +${lines.length - MAX_OUTPUT_LINES} lines omitted ...`;
}

function getToolMeta(name: string) {
  switch (name) {
    case "execute_code":
      return { icon: <Play size={14} className="text-green-400" />, label: "execute_code" };
    case "execute_terminal":
      return { icon: <Terminal size={14} className="text-yellow-400" />, label: "execute_terminal" };
    case "create_session":
      return { icon: <Box size={14} className="text-blue-400" />, label: "create_session" };
    case "stop_session":
      return { icon: <Square size={14} className="text-red-400" />, label: "stop_session" };
    case "import_files":
      return { icon: <Upload size={14} className="text-cyan-400" />, label: "import_files" };
    case "export_files":
      return { icon: <Download size={14} className="text-emerald-400" />, label: "export_files" };
    default:
      return { icon: <Wrench size={14} className="text-zinc-400" />, label: name };
  }
}

function StatusBadge({ status }: { status: "running" | "ok" | "error" }) {
  if (status === "running")
    return (
      <span className="flex items-center gap-1 text-yellow-400">
        <Loader2 size={10} className="animate-spin" /> Running...
      </span>
    );
  if (status === "error")
    return (
      <span className="flex items-center gap-1 text-red-400">
        <XCircle size={10} /> ERROR
      </span>
    );
  return (
    <span className="flex items-center gap-1 text-green-400">
      <CheckCircle2 size={10} /> OK
    </span>
  );
}

export function ToolCallBlock({
  toolCall,
  result,
  resultContent,
  threadId,
  sessionRuntimes,
  shouldStayOpen,
}: ToolCallBlockProps) {
  const isRunning = result === undefined;
  const [expanded, setExpanded] = useState(isRunning || shouldStayOpen);

  useEffect(() => {
    if (!shouldStayOpen) setExpanded(false);
  }, [shouldStayOpen]);

  const { icon, label } = getToolMeta(toolCall.name);

  let status: "running" | "ok" | "error" = "running";
  let parsedOutput: Record<string, unknown> | null = null;
  let outputIsError = false;

  if (result !== undefined) {
    const parsed = parseToolOutput(result);
    parsedOutput = parsed.data;
    outputIsError = parsed.isError;
    status = outputIsError ? "error" : "ok";
  }

  const inputDisplay = getInputDisplay(toolCall, sessionRuntimes);
  const figures = resultContent ? extractFigures(resultContent) : [];
  if (parsedOutput?.figures && Array.isArray(parsedOutput.figures)) {
    for (const fig of parsedOutput.figures) {
      if (typeof fig === "string" && fig && !figures.includes(fig)) {
        figures.push(fig);
      }
    }
  }

  const htmlContent = extractHtmlFromResult(parsedOutput);

  return (
    <div className="my-1.5">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 text-xs">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex w-full items-center gap-2 px-3 py-2 text-zinc-400 transition-colors hover:text-zinc-200"
        >
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {icon}
          <span className="flex-1 text-left">{label}</span>
          <StatusBadge status={status} />
        </button>

        {expanded && (
          <div className="border-t border-zinc-800">
            <div className="px-3 pt-2 pb-1">
              <span className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">
                Input
              </span>
            </div>
            <div className="tool-code-block px-1 pb-2">
              <SyntaxHighlighter
                language={inputDisplay.lang}
                style={oneDark}
                customStyle={{
                  margin: 0,
                  padding: "0.5rem 0.75rem",
                  fontSize: "0.75rem",
                  background: "transparent",
                  border: "none",
                }}
                wrapLongLines
              >
                {inputDisplay.code}
              </SyntaxHighlighter>
            </div>

            <div className="px-3 pt-1 pb-1">
              <span className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">
                Output
              </span>
            </div>
            <div className="tool-code-block px-1 pb-2">
              {isRunning ? (
                <div className="flex items-center gap-2 px-3 py-2 text-zinc-500">
                  <Loader2 size={12} className="animate-spin" />
                  Running...
                </div>
              ) : (
                <OutputDisplay
                  parsedOutput={parsedOutput}
                  rawResult={result!}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* Session info cards */}
      {toolCall.name === "create_session" && parsedOutput != null && (
        <SessionCard
          sessionId={String(parsedOutput.session_id ?? "")}
          runtime={String(parsedOutput.runtime ?? "")}
          status={String(parsedOutput.status ?? "")}
          type="create"
        />
      )}
      {toolCall.name === "stop_session" && parsedOutput != null && (
        <SessionCard
          sessionId={String(parsedOutput.session_id ?? "")}
          runtime=""
          status="stopped"
          type="stop"
        />
      )}

      {/* File results for import/export */}
      {(toolCall.name === "import_files" || toolCall.name === "export_files") &&
        parsedOutput != null &&
        Array.isArray(parsedOutput.files) && (
          <FileResults
            files={parsedOutput.files as Record<string, unknown>[]}
            isExport={toolCall.name === "export_files"}
            threadId={threadId}
          />
        )}

      {/* Rich HTML output (DataFrames, Plotly, etc.) */}
      {htmlContent && (
        <div
          className="mt-2 overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-900 p-3 text-sm tool-html-output"
          dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
      )}

      {/* Inline figures */}
      {figures.length > 0 && (
        <div className="mt-2 space-y-2">
          {figures.map((b64, i) => (
            <ClickableImage
              key={i}
              src={b64.startsWith("data:") ? b64 : `data:image/png;base64,${b64}`}
              alt="Figure"
              className="max-w-full rounded-lg border border-zinc-800"
            />
          ))}
        </div>
      )}
    </div>
  );
}

function extractHtmlFromResult(
  parsedOutput: Record<string, unknown> | null,
): string | null {
  if (!parsedOutput) return null;

  const result = parsedOutput.result;
  if (result && typeof result === "object" && !Array.isArray(result)) {
    const r = result as Record<string, unknown>;
    if (typeof r["text/html"] === "string" && r["text/html"].trim()) {
      return r["text/html"];
    }
  }

  return null;
}

function getInputDisplay(
  toolCall: ToolCall,
  sessionRuntimes: Map<string, string>,
): { code: string; lang: string } {
  const args = toolCall.args;

  if (toolCall.name === "execute_code" && typeof args.code === "string") {
    const sessionId = String(args.session_id ?? "");
    const runtime = sessionRuntimes.get(sessionId) ?? "";
    const lang = RUNTIME_LANG[runtime] ?? "python";
    return { code: args.code, lang };
  }

  if (toolCall.name === "execute_terminal" && typeof args.command === "string") {
    return { code: args.command, lang: "bash" };
  }

  return {
    code: JSON.stringify(args, null, 2),
    lang: "json",
  };
}

function OutputDisplay({
  parsedOutput,
  rawResult,
}: {
  parsedOutput: Record<string, unknown> | null;
  rawResult: string;
}) {
  let displayText: string;

  if (parsedOutput) {
    const clean = { ...parsedOutput };
    delete clean.figures;
    displayText = JSON.stringify(clean, null, 2);
  } else {
    displayText = rawResult;
  }

  displayText = truncateOutput(displayText);

  return (
    <SyntaxHighlighter
      language="json"
      style={oneDark}
      customStyle={{
        margin: 0,
        padding: "0.5rem 0.75rem",
        fontSize: "0.75rem",
        background: "transparent",
        border: "none",
      }}
      wrapLongLines
    >
      {displayText}
    </SyntaxHighlighter>
  );
}

function SessionCard({
  sessionId,
  runtime,
  status,
  type,
}: {
  sessionId: string;
  runtime: string;
  status: string;
  type: "create" | "stop";
}) {
  const statusIcon =
    type === "stop"
      ? "🔴"
      : status === "running"
        ? "🟢"
        : status === "starting"
          ? "🟡"
          : "⚪";

  const bg = type === "stop" ? "bg-red-950/30 border-red-900/50" : "bg-blue-950/30 border-blue-900/50";

  return (
    <div className={`mt-1.5 rounded-lg border px-3 py-2 text-xs ${bg}`}>
      <span>
        {statusIcon} Session{" "}
        <code className="rounded bg-zinc-800 px-1 py-0.5">{sessionId}</code>
        {runtime && <span className="text-zinc-400"> ({runtime})</span>}
        {" — "}
        <span className="text-zinc-300">{type === "stop" ? "stopped" : status}</span>
      </span>
    </div>
  );
}

function FileResults({
  files,
  isExport,
  threadId,
}: {
  files: Record<string, unknown>[];
  isExport: boolean;
  threadId: string | null;
}) {
  return (
    <div className="mt-1.5 space-y-1">
      {files.map((fr, i) => {
        const success = fr.success as boolean;
        const filename =
          String(fr.path ?? fr.source ?? fr.destination ?? "file");
        const displayName = filename.includes("/")
          ? filename.split("/").pop()!
          : filename;
        const size = fr.size as number | undefined;
        const sessionId = fr.session_id as string | undefined;
        const path = fr.path as string | undefined;
        const error = fr.error as string | undefined;

        if (!success) {
          return (
            <div
              key={i}
              className="rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-1.5 text-xs text-red-300"
            >
              ❌ {displayName} — {error ?? "Failed"}
            </div>
          );
        }

        return (
          <div
            key={i}
            className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-1.5 text-xs"
          >
            <span className="text-zinc-300">
              ✅ {displayName}
              {size ? (
                <span className="ml-1 text-zinc-500">
                  ({formatFileSize(size)})
                </span>
              ) : null}
            </span>
            {isExport && sessionId && path && threadId && (
              <a
                href={getDownloadUrl(threadId, sessionId, path)}
                className="ml-auto rounded bg-green-700 px-2 py-0.5 text-[10px] font-medium text-white transition-colors hover:bg-green-600"
                download
              >
                Download
              </a>
            )}
          </div>
        );
      })}
    </div>
  );
}
