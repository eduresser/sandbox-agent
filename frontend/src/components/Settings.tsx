import { useState, useEffect } from "react";
import { X, CheckCircle, XCircle } from "lucide-react";
import type { Settings as SettingsType, Message } from "../types";
import { extractSessions, type SessionStatus } from "../lib/utils";
import { checkHealth } from "../api/aegra";

interface SettingsProps {
  settings: SettingsType;
  onSave: (s: SettingsType) => void;
  onClose: () => void;
  messages: Message[];
}

const STATUS_ICONS: Record<string, string> = {
  running: "🟢",
  starting: "🟡",
  stopped: "🔴",
  dead: "⚫",
};

export function Settings({ settings, onSave, onClose, messages }: SettingsProps) {
  const [form, setForm] = useState<SettingsType>({ ...settings });
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    checkHealth().then(setHealthy);
  }, []);

  const handleSave = () => {
    onSave(form);
    onClose();
  };

  const sessions: Map<string, SessionStatus> = extractSessions(messages);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-zinc-800 bg-zinc-900 p-6 shadow-2xl">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-zinc-100">Settings</h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
          >
            <X size={18} />
          </button>
        </div>

        {/* Health indicator */}
        {healthy !== null && (
          <div
            className={`mb-4 flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
              healthy
                ? "border border-green-800/50 bg-green-950/30 text-green-400"
                : "border border-red-800/50 bg-red-950/30 text-red-400"
            }`}
          >
            {healthy ? <CheckCircle size={16} /> : <XCircle size={16} />}
            {healthy ? "API Connected" : "API Unavailable"}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm text-zinc-400">Model</label>
            <input
              type="text"
              value={form.chatModel}
              onChange={(e) => setForm({ ...form, chatModel: e.target.value })}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
              placeholder="gpt-4o"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm text-zinc-400">Provider</label>
            <select
              value={form.chatModelProvider}
              onChange={(e) =>
                setForm({ ...form, chatModelProvider: e.target.value })
              }
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-indigo-500 focus:outline-none"
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="google_genai">Google GenAI</option>
              <option value="ollama">Ollama</option>
            </select>
          </div>

          <div>
            <label className="mb-1 block text-sm text-zinc-400">API Key</label>
            <input
              type="password"
              value={form.chatModelApiKey}
              onChange={(e) =>
                setForm({ ...form, chatModelApiKey: e.target.value })
              }
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
              placeholder="sk-..."
            />
          </div>

          <div>
            <label className="mb-1 block text-sm text-zinc-400">
              Base URL
            </label>
            <input
              type="text"
              value={form.chatModelBaseUrl}
              onChange={(e) =>
                setForm({ ...form, chatModelBaseUrl: e.target.value })
              }
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-indigo-500 focus:outline-none"
              placeholder="https://api.openai.com/v1"
            />
          </div>

          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="vision"
              checked={form.supportsVision}
              onChange={(e) =>
                setForm({ ...form, supportsVision: e.target.checked })
              }
              className="h-4 w-4 rounded border-zinc-600 bg-zinc-800 accent-indigo-600"
            />
            <label htmlFor="vision" className="text-sm text-zinc-400">
              Supports Vision
            </label>
          </div>
        </div>

        {/* Sandbox Sessions */}
        {sessions.size > 0 && (
          <div className="mt-5">
            <div className="mb-2 border-t border-zinc-800 pt-4">
              <h3 className="text-sm font-semibold text-zinc-300">
                Sandbox Sessions
              </h3>
            </div>
            <div className="space-y-1.5">
              {[...sessions.entries()].map(([sid, info]) => (
                <div
                  key={sid}
                  className="flex items-center gap-2 text-xs text-zinc-400"
                >
                  <span>{STATUS_ICONS[info.status] ?? "⚪"}</span>
                  <code className="rounded bg-zinc-800 px-1 py-0.5">
                    {sid}
                  </code>
                  {info.runtime && <span>({info.runtime})</span>}
                  <span className="text-zinc-500">— {info.status}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
