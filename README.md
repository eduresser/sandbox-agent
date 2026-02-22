# Sandbox Agent

LangGraph agent with Docker-based sandboxed code execution. Each session runs in an isolated Docker container with a persistent kernel — IPython for Python, `vm.createContext` for Node.js, a dedicated R environment, and a Julia REPL. Supports 4 runtimes, provider-agnostic LLM configuration, and vision (auto-detection of multimodal models). Available as an interactive CLI or as an MCP server for integration with Cursor, Claude Desktop, and other MCP-compatible clients.

## Features

- **Docker isolation** — each session runs in its own container, no ports exposed, no host volumes
- **Hardened containers** — non-root user (UID 65532), PID limits, memory+swap limits, tmpfs-only writable dirs, `no-new-privileges`
- **Crash detection** — OOM-kill, fork bombs, segfaults are detected and reported clearly to the agent
- **Persistent state** — variables survive between code executions (like Jupyter cells)
- **Checkpointer PostgreSQL** — conversation history persists across CLI restarts (shared with Aegra)
- **Async support** — Promises (Node.js) and coroutines (Python) are automatically awaited
- **Multi-runtime** — Python, Node.js, R, and Julia
- **Vision support** — auto-detects multimodal LLMs and sends matplotlib/ggplot figures as base64 PNG images
- **Provider-agnostic** — works with OpenAI, Anthropic, or any compatible provider via `langchain-openai` init
- **Runtime package install** — `pip install` / `npm install` / `install.packages()` / `Pkg.add()` at session creation or via terminal
- **6 tools** — create_session, execute_code, execute_terminal, import_files, export_files, stop_session
- **MCP server** — expose the same tools via Model Context Protocol (stdio transport)
- **File export** — export files and directories from sandboxes to the host, organized by session (`OUTPUT_DIR/<session_id>/`)
- **File import** — import files and directories from the host into sandboxes, including entire folder trees
- **Cross-session transfer** — export from one session and import into another using the returned host paths
- **Auto-cleanup** — all containers are stopped and removed when the agent exits

## Prerequisites

- Python 3.11+
- Docker Engine
- API key for your LLM provider (`CHAT_MODEL_API_KEY`)

## Setup

```bash
# Docker — installs (if needed), configures permissions, and builds all 4 images
sudo ./setup-docker.sh

# Install dependencies (open a new terminal so the docker group is active)
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your CHAT_MODEL_API_KEY and LLM settings

# Docker images are also built automatically on first use if not already present
```

## Usage

### CLI

```bash
uv run sandbox-agent
```

The interactive CLI uses [Rich](https://github.com/Textualize/rich) for syntax-highlighted tool I/O panels, streaming agent output with Markdown rendering.

### MCP Server

Run the MCP server (stdio transport) for integration with Cursor, Claude Desktop, or any MCP-compatible client:

```bash
uv run sandbox-agent-mcp
```

#### Claude Desktop or Cursor

Add the following MCP config:

```json
{
  "mcpServers": {
    "sandbox-agent": {
      "command": "uv",
      "args": ["--directory", "/path/to/sandbox-agent", "run", "sandbox-agent-mcp"]
    }
  }
}
```

The MCP server exposes the same 6 tools as the CLI agent. The `import_files` tool accepts file content directly (as text or base64 via `file_content`/`encoding` keys) or host paths (via `source`/`destination` keys), since MCP clients don't always share a filesystem with the server. The `export_files` tool accepts an optional `output_dir` override.

### Aegra (REST API)

Run the agent as a REST API via [Aegra](https://aegra.dev/) (self-hosted LangGraph Platform alternative):

```bash
# Add to .env: DATABASE_URL=postgresql://sandbox_agent:sandbox_agent_secret@localhost:5432/sandbox_agent
uv run aegra dev
```

The server runs at `http://localhost:8000` with OpenAPI docs at `/docs`. Use the LangGraph SDK or curl to create assistants, threads, and stream runs. Compatible with Agent Chat UI, LangGraph Studio, and CopilotKit.

### Streamlit Frontend

A web UI for chatting with the agent via the Aegra API:

```bash
# Install frontend dependencies (streamlit, httpx)
uv sync --extra frontend

# Start the frontend (requires Aegra running: uv run aegra dev)
uv run sandbox-agent-frontend
```

The frontend runs at `http://localhost:8501`.

### Programmatic

```python
from sandbox_agent.sandbox import SandboxManager

manager = SandboxManager()

info = manager.create_session(
    runtime="python",
    dependencies={"pandas": "2.2.3", "matplotlib": ""},
)
sid = info.session_id

r1 = manager.execute_code(sid, """
import pandas as pd
df = pd.DataFrame({'x': [1, 2, 3], 'y': [4, 5, 6]})
print(df.describe())
""")
print(r1.stdout)

# Variables persist between calls
r2 = manager.execute_code(sid, "df.shape")
print(r2.result)

# Export files from the sandbox to the host
manager.execute_code(sid, "df.to_csv('/workspace/output.csv', index=False)")
export = manager.export_files(sid, [
    {"source": "output.csv", "destination": "output.csv"},
])
print(export.files[0].destination)  # ./outputs/<session_id>/output.csv

manager.stop_session(sid)
```

#### Exporting Files

`export_files` copies files and directories from the sandbox to the host. Files are organized under `OUTPUT_DIR/<session_id>/`:

```python
# Export a single file
result = manager.export_files(sid, [
    {"source": "report.pdf", "destination": "report.pdf"},
])

# Export an entire directory
result = manager.export_files(sid, [
    {"source": "results/", "destination": "results/"},
])

# Export multiple files at once
result = manager.export_files(sid, [
    {"source": "data.csv", "destination": "data.csv"},
    {"source": "chart.png", "destination": "charts/chart.png"},
    {"source": "/workspace/logs/", "destination": "logs/"},
])

# The result contains absolute host paths for each file
for f in result.files:
    print(f"{f.source} -> {f.destination} ({'OK' if f.success else f.error})")
```

#### Cross-Session File Transfer

Use `export_files` + `import_files` to move files between sessions:

```python
# Session A (Python): produce data
sid_a = manager.create_session(runtime="python", dependencies={"pandas": ""}).session_id
manager.execute_code(sid_a, """
import pandas as pd
df = pd.DataFrame({'x': [1,2,3], 'y': [4,5,6]})
df.to_csv('/workspace/data.csv', index=False)
""")
export = manager.export_files(sid_a, [{"source": "data.csv", "destination": "data.csv"}])
host_path = export.files[0].destination  # absolute path on host

# Session B (R): consume the same data
sid_b = manager.create_session(runtime="r", dependencies={"readr": ""}).session_id
manager.import_files(sid_b, [{"source": host_path, "destination": "data.csv"}])
manager.execute_code(sid_b, 'df <- readr::read_csv("/workspace/data.csv"); summary(df)')
```

#### Importing Files and Directories

`import_files` copies files and directories from the host into the sandbox:

```python
# Import a single file
result = manager.import_files(sid, [
    {"source": "/home/user/data.csv", "destination": "data.csv"},
])

# Import an entire directory (tree is preserved)
result = manager.import_files(sid, [
    {"source": "/home/user/project/", "destination": "project/"},
])

# Import multiple items at once
result = manager.import_files(sid, [
    {"source": "/home/user/config.json", "destination": "config.json"},
    {"source": "/home/user/assets/", "destination": "assets/"},
])
```

Other runtimes work the same way — pass `runtime="node"`, `runtime="r"`, or `runtime="julia"` to `create_session`.

### Async Code

**Node.js** — if the last expression returns a Promise, the kernel awaits it before collecting output. Top-level `await` is also supported (falls back to an async IIFE wrapper when needed).

```javascript
const axios = require('axios');
async function fetchData() {
    const resp = await axios.get('https://api.example.com/data');
    console.log(resp.data);
}
fetchData(); // Promise is awaited automatically
```

**Python** — IPython's `autoawait` handles top-level `await`. If a cell returns an unawaited coroutine, the kernel detects it and runs it with `asyncio.run()`.

```python
import aiohttp

async def fetch_data():
    async with aiohttp.ClientSession() as session:
        resp = await session.get('https://api.example.com/data')
        print(await resp.text())

fetch_data()  # coroutine is detected and executed automatically
```

## Container Security

Each container is created with the following protections:

| Protection | Setting | Effect |
|---|---|---|
| Memory limit | `2048m` (no swap) | OOM-kill on overflow, host unaffected |
| PID limit | `512` | Fork bombs are contained and killed |
| CPU quota | `2` cores | Prevents CPU starvation on host |
| Writable dirs | tmpfs only (`/workspace`, `/tmp`, `/home/sandbox`) | Cannot fill host disk |
| tmpfs size | `200m` per mount | Limits in-container disk usage |
| User | `sandbox` (UID 65532) | No root inside container |
| Privileges | `no-new-privileges` | Cannot escalate via setuid/setgid |
| Network | Configurable (enabled by default) | Can be disabled per session |

When a container crashes, the agent receives a clear `CONTAINER_DIED` error with the reason (OOM-killed, SIGKILL, segfault, etc.) and a hint to recreate the session.

## Configuration

All settings can be overridden via environment variables or `.env`:

```bash
# LLM (provider-agnostic)
CHAT_MODEL=gpt-4o                    # Model name
CHAT_MODEL_PROVIDER=openai           # Provider (openai, anthropic, etc.)
CHAT_MODEL_API_KEY=sk-...            # API key (required)
CHAT_MODEL_BASE_URL=                 # Custom API base URL (optional)
CHAT_MODEL_SUPPORTS_VISION=          # Override vision detection (true/false, empty = auto)

# Container limits
CONTAINER_MEMORY_LIMIT=2048m         # Docker memory limit (no swap)
CONTAINER_CPU_QUOTA=200000           # CPU quota (100000 = 1 core)
CONTAINER_PIDS_LIMIT=512             # Max PIDs per container
CONTAINER_TMPFS_SIZE=200m            # tmpfs size for writable dirs
EXECUTION_TIMEOUT_SECONDS=30         # Default code execution timeout
MAX_SESSIONS=5                       # Maximum concurrent sandbox sessions
TERMINAL_ROOT=False                  # Run terminal commands as root

# Export
OUTPUT_DIR=./outputs                 # Base directory for exported files (organized by session_id)

# Output truncation limits (characters)
MAX_STDOUT_CHARS=20000
MAX_STDERR_CHARS=10000
MAX_RESULT_CHARS=20000
MAX_TRACEBACK_CHARS=5000

# Agent
MAX_ITERATIONS=25                    # Max LangGraph iterations (recursion limit)

# Checkpointer uses POSTGRES_* (same as Aegra) — PostgreSQL must be running
```

## Runtimes

| Runtime | Base Image | Kernel | IPC | Pre-installed |
|---|---|---|---|---|
| Python | `python:3.12-slim` | IPython shell | UNIX socket | IPython + system libs |
| Node.js | `node:22-slim` | `vm.createContext` | UNIX socket | Bare runtime |
| R | `rocker/r-ver:4` | Dedicated R env | TCP `:8765` | tidyverse, data.table, ggplot2, jsonlite, and more |
| Julia | `julia:1.11-bookworm` | Julia REPL | TCP `:8765` | DataFrames, CSV, Statistics, HTTP, and more |

R and Julia containers use a compiled C client binary for IPC, while Python and Node.js use native clients.

## Architecture

```mermaid
flowchart TB
    CLI["CLI · Rich REPL"]
    MCP["MCP Server · FastMCP (stdio)"]

    CLI --> Agent["LangGraph ReAct Agent"]
    Agent --> Tools["LangChain Tools"]
    MCP --> MCPTools["MCP Tools"]

    Tools --> SM["SandboxManager · Docker SDK"]
    MCPTools --> SM

    SM -->|"docker exec -i + JSON pipe"| Docker

    subgraph Docker ["Docker Containers — isolated, hardened"]
        direction LR
        PY["Python\nIPython · UNIX socket"]
        JS["Node.js\nvm.createContext · UNIX socket"]
        R["R\nR env · TCP :8765"]
        JL["Julia\nJulia env · TCP :8765"]
    end
```

Inside each container, a persistent **kernel** (PID 1) holds execution state, and an ephemeral **client** connects to it via UNIX socket (Python/Node.js) or TCP (R/Julia) for each `docker exec` call:

```mermaid
flowchart TB
    SM["SandboxManager"] -->|"docker exec -i"| Client["Client (ephemeral)"]

    subgraph container ["Container"]
        Client -->|"UNIX socket / TCP"| Kernel["Kernel (PID 1, persistent)"]
        Kernel --- State["State\nvariables, imports, data"]
    end
```

## Testing

```bash
# Requires Docker running
uv run pytest tests/ -v
```

## License

[MIT](LICENSE) — Eduardo Ramon Resser
