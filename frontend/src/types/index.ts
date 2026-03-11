export interface ContentBlock {
  type: string;
  text?: string;
  thinking?: string;
  reasoning?: string;
  image_url?: { url: string };
}

export interface DisplayOutput {
  type: string;  // MIME type (image/png, text/html, audio/wav, etc.)
  data: string;  // raw string (HTML/SVG) or base64 (binary types)
}

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface Message {
  id?: string;
  type: string;
  content: string | ContentBlock[];
  name?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface Thread {
  thread_id: string;
  metadata: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface ThreadState {
  values: {
    messages?: Message[];
  };
}

export interface SSEEvent {
  event: string;
  data: unknown;
}

export interface Settings {
  chatModel: string;
  chatModelProvider: string;
  chatModelApiKey: string;
  chatModelApiKeyHint: string;
  chatModelBaseUrl: string;
  supportsVision: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  chatModel: "gpt-4o",
  chatModelProvider: "openai",
  chatModelApiKey: "",
  chatModelApiKeyHint: "",
  chatModelBaseUrl: "",
  supportsVision: true,
};

export interface UploadedFileMeta {
  name: string;
  path: string;
  size: number;
}

export const RUNTIME_LANGUAGE: Record<string, string> = {
  python: "python",
  node: "javascript",
  r: "r",
};
