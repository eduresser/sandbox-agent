import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "./components/Sidebar";
import { ChatArea } from "./components/ChatArea";
import { Settings } from "./components/Settings";
import { useThreads } from "./hooks/useThreads";
import { useChat } from "./hooks/useChat";
import { getSettings, saveSettings as apiSaveSettings } from "./api/aegra";
import type { Settings as SettingsType } from "./types";
import { DEFAULT_SETTINGS } from "./types";

export default function App() {
  const [settings, setSettings] = useState<SettingsType>(DEFAULT_SETTINGS);
  const [showSettings, setShowSettings] = useState(false);

  useEffect(() => {
    getSettings()
      .then((remote) => {
        setSettings((prev) => ({ ...prev, ...remote } as SettingsType));
      })
      .catch(() => {});
  }, []);

  const {
    threads,
    activeThreadId,
    setActiveThreadId,
    createThread,
    deleteThread,
    deletingThreadId,
  } = useThreads();

  const { messages, streaming, sendMessage, editMessage, stopStreaming, loadMessages } = useChat(
    activeThreadId,
    settings,
    createThread,
  );

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  const handleSaveSettings = useCallback((s: SettingsType) => {
    setSettings(s);
    apiSaveSettings(s).catch(() => {});
  }, []);

  const handleNewThread = useCallback(async () => {
    await createThread();
  }, [createThread]);

  return (
    <div className="flex h-screen w-full min-w-0 overflow-hidden bg-zinc-950 text-zinc-100">
      <Sidebar
        threads={threads}
        activeThreadId={activeThreadId}
        deletingThreadId={deletingThreadId}
        streaming={streaming}
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
          onEditMessage={editMessage}
          onStopStreaming={stopStreaming}
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
