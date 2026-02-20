/**
 * Persistent Node.js kernel.
 * Same pattern as the Python kernel: UNIX socket + persistent context.
 *
 * vm.createContext() is the equivalent of the IPython shell:
 * variables defined in one execution persist in the next.
 */

const vm = require("vm");
const net = require("net");
const fs = require("fs");
const { execSync } = require("child_process");

const SOCKET_PATH = "/tmp/kernel.sock";
const MAX_OUTPUT = 2 * 1024 * 1024;

fs.mkdirSync("/workspace", { recursive: true });
process.chdir("/workspace");

if (fs.existsSync(SOCKET_PATH)) fs.unlinkSync(SOCKET_PATH);

// ── Persistent Context (equivalent to IPython shell) ──

const sandbox = {
  require,
  console: undefined,
  process: {
    env: { ...process.env },
    cwd: () => process.cwd(),
    chdir: (d) => process.chdir(d),
  },
  Buffer,
  setTimeout,
  setInterval,
  clearTimeout,
  clearInterval,
  Promise,
  __filename: "<sandbox>",
  __dirname: "/workspace",
  module: { exports: {} },
  exports: {},
};

const context = vm.createContext(sandbox);

// ── Execution ─────────────────────────────────────────

async function execute(code, timeout = 30) {
  const stdoutChunks = [];
  const stderrChunks = [];

  sandbox.console = {
    log: (...args) => stdoutChunks.push(args.map(String).join(" ")),
    error: (...args) => stderrChunks.push(args.map(String).join(" ")),
    warn: (...args) => stderrChunks.push(args.map(String).join(" ")),
    info: (...args) => stdoutChunks.push(args.map(String).join(" ")),
    dir: (obj) => stdoutChunks.push(JSON.stringify(obj, null, 2)),
    table: (data) => stdoutChunks.push(JSON.stringify(data, null, 2)),
  };

  const response = {
    success: true,
    stdout: "",
    stderr: "",
    result: null,
    error: null,
    figures: [],
  };

  try {
    let result;

    if (code.includes("await ")) {
      const wrapped = `(async () => { ${code} })()`;
      result = await vm.runInContext(wrapped, context, {
        filename: "<cell>",
        timeout: timeout * 1000,
        breakOnSigint: true,
      });
    } else {
      result = vm.runInContext(code, context, {
        filename: "<cell>",
        timeout: timeout * 1000,
        breakOnSigint: true,
      });
    }

    response.stdout = stdoutChunks.join("\n").slice(0, MAX_OUTPUT);
    response.stderr = stderrChunks.join("\n").slice(0, MAX_OUTPUT);

    if (result !== undefined) {
      response.result = {
        "text/plain":
          typeof result === "string" ? result : JSON.stringify(result, null, 2),
      };
    }
  } catch (err) {
    response.success = false;
    response.stdout = stdoutChunks.join("\n").slice(0, MAX_OUTPUT);
    response.stderr = stderrChunks.join("\n").slice(0, MAX_OUTPUT);
    response.error = {
      type: err.constructor.name,
      message: err.message,
      traceback: err.stack,
    };
  }

  return response;
}

// ── Restart ───────────────────────────────────────────

function restart() {
  const keep = new Set([
    "require",
    "console",
    "process",
    "Buffer",
    "setTimeout",
    "setInterval",
    "clearTimeout",
    "clearInterval",
    "Promise",
    "__filename",
    "__dirname",
    "module",
    "exports",
  ]);

  for (const key of Object.keys(sandbox)) {
    if (!keep.has(key)) {
      delete sandbox[key];
    }
  }

  return { success: true, message: "Kernel restarted" };
}

// ── Request Handler ───────────────────────────────────

async function handleRequest(data) {
  let req;
  try {
    req = JSON.parse(data);
  } catch (e) {
    return {
      success: false,
      error: { type: "JSONError", message: e.message },
    };
  }

  const action = req.action || "execute";

  switch (action) {
    case "execute":
      return await execute(req.code || "", req.timeout || 30);
    case "restart":
      return restart();
    case "ping":
      return { success: true };
    default:
      return {
        success: false,
        error: { type: "ValueError", message: `Unknown: ${action}` },
      };
  }
}

// ── UNIX Socket Server ───────────────────────────────

const server = net.createServer({ allowHalfOpen: true }, (conn) => {
  const chunks = [];

  conn.on("data", (chunk) => chunks.push(chunk));

  conn.on("end", async () => {
    const data = Buffer.concat(chunks).toString("utf-8");
    const result = await handleRequest(data);
    conn.end(JSON.stringify(result));
  });

  conn.on("error", () => {});
});

server.listen(SOCKET_PATH, () => {
  fs.chmodSync(SOCKET_PATH, 0o777);
  console.log("KERNEL_READY");
});
