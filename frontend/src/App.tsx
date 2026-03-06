import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "./components/Sidebar";
import { ChatArea } from "./components/ChatArea";
import { Settings } from "./components/Settings";
import { useThreads } from "./hooks/useThreads";
import { useChat } from "./hooks/useChat";
import type { Settings as SettingsType } from "./types";
import { DEFAULT_SETTINGS } from "./types";

const STORAGE_KEY = "sandbox-agent-settings";

function loadSettings(): SettingsType {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    // ignore
  }
  return { ...DEFAULT_SETTINGS };
}

export default function App() {
  const [settings, setSettings] = useState<SettingsType>(loadSettings);
  const [showSettings, setShowSettings] = useState(false);

  const {
    threads,
    activeThreadId,
    setActiveThreadId,
    createThread,
    deleteThread,
  } = useThreads();

  const { messages, streaming, sendMessage, loadMessages } = useChat(
    activeThreadId,
    settings,
    createThread,
  );

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  const handleSaveSettings = useCallback((s: SettingsType) => {
    setSettings(s);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  }, []);

  const handleNewThread = useCallback(async () => {
    await createThread();
  }, [createThread]);

  return (
    <div className="flex h-screen w-full min-w-0 overflow-hidden bg-zinc-950 text-zinc-100">
      <Sidebar
        threads={threads}
        activeThreadId={activeThreadId}
        onSelectThread={setActiveThreadId}
        onNewThread={handleNewThread}
        onDeleteThread={deleteThread}
        onOpenSettings={() => setShowSettings(true)}
      />

      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden basis-0 w-full">
        <ChatArea
          messages={messages}
          streaming={streaming}
          onSendMessage={sendMessage}
          threadId={activeThreadId}
        />
      </main>

      {showSettings && (
        <Settings
          settings={settings}
          onSave={handleSaveSettings}
          onClose={() => setShowSettings(false)}
          messages={messages}
        />
      )}
    </div>
  );
}
