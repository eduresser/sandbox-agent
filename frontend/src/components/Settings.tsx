import { useState, useEffect, useCallback } from "react";
import { X, CheckCircle, XCircle, Trash2, RefreshCw, Loader2 } from "lucide-react";
import { cn } from "../lib/utils";
import type { Settings as SettingsType } from "../types";
import {
  checkHealth,
  listSessions,
  killSession,
  type ActiveSession,
} from "../api/aegra";

interface SettingsProps {
  settings: SettingsType;
  onSave: (s: SettingsType) => void;
  onClose: () => void;
  messages?: unknown[];
}

const STATUS_ICONS: Record<string, string> = {
  running: "🟢",
  starting: "🟡",
  stopped: "🔴",
  dead: "⚫",
};

export function Settings({ settings, onSave, onClose }: SettingsProps) {
  const [form, setForm] = useState<SettingsType>({ ...settings });
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [sessions, setSessions] = useState<ActiveSession[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [killingSessions, setKillingSessions] = useState<Set<string>>(new Set());

  const fetchSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      const data = await listSessions();
      setSessions(data);
    } catch {
      setSessions([]);
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  useEffect(() => {
    checkHealth().then(setHealthy);
    fetchSessions();
  }, [fetchSessions]);

  const handleKill = async (sessionId: string) => {
    setKillingSessions((prev) => new Set(prev).add(sessionId));
    try {
      await killSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    } catch {
      // refresh list to get actual state
      await fetchSessions();
    } finally {
      setKillingSessions((prev) => {
        const next = new Set(prev);
        next.delete(sessionId);
        return next;
      });
    }
  };

  const handleSave = () => {
    onSave(form);
    onClose();
  };

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
              placeholder={form.chatModelApiKeyHint || "sk-..."}
            />
            {form.chatModelApiKeyHint && !form.chatModelApiKey && (
              <p className="mt-1 text-xs text-zinc-500">
                Key stored securely. Leave blank to keep current.
              </p>
            )}
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

        {/* Active Sandbox Sessions */}
        <div className="mt-5">
          <div className="mb-2 border-t border-zinc-800 pt-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-zinc-300">
                Active Containers
              </h3>
              <button
                onClick={fetchSessions}
                disabled={loadingSessions}
                className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300 disabled:opacity-50"
                title="Refresh"
              >
                <RefreshCw
                  size={14}
                  className={loadingSessions ? "animate-spin" : ""}
                />
              </button>
            </div>
          </div>
          {sessions.length === 0 ? (
            <p className="text-xs text-zinc-500">
              {loadingSessions ? "Loading…" : "No active containers."}
            </p>
          ) : (
            <div className="space-y-2">
              {sessions.map((s) => {
                const isKilling = killingSessions.has(s.session_id);
                return (
                  <div
                    key={s.session_id}
                    className={cn(
                      "relative flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-800/40 px-3 py-2 text-xs text-zinc-400",
                      isKilling && "pointer-events-none",
                    )}
                  >
                    {isKilling && (
                      <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-zinc-900/80">
                        <Loader2 size={16} className="animate-spin text-zinc-400" />
                      </div>
                    )}
                    <span className={cn(isKilling && "opacity-30")}>{STATUS_ICONS[s.status] ?? "⚪"}</span>
                    <div className={cn("min-w-0 flex-1", isKilling && "opacity-30")}>
                      <code className="block truncate text-zinc-300">
                        {s.session_id}
                      </code>
                      <span className="text-zinc-500">
                        {s.runtime} · {s.container_id}
                      </span>
                    </div>
                    <button
                      onClick={() => handleKill(s.session_id)}
                      disabled={isKilling}
                      className="ml-auto shrink-0 rounded p-1.5 text-zinc-500 transition-colors hover:bg-red-950/50 hover:text-red-400 disabled:opacity-50"
                      title="Stop container"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>

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
