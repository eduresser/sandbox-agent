import { useState } from "react";
import { ChevronDown, ChevronRight, Brain } from "lucide-react";

interface ThinkingBlockProps {
  text: string;
}

export function ThinkingBlock({ text }: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(false);

  if (!text.trim()) return null;

  return (
    <div className="my-1.5 rounded-lg border border-zinc-800 bg-zinc-900/50 text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-zinc-400 transition-colors hover:text-zinc-200"
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Brain size={14} className="text-purple-400" />
        <span>Agent thinking</span>
      </button>

      {expanded && (
        <div className="border-t border-zinc-800 px-3 py-2">
          <pre className="whitespace-pre-wrap text-zinc-300">{text}</pre>
        </div>
      )}
    </div>
  );
}
