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
const Module = require("module");

const SOCKET_PATH = "/tmp/kernel.sock";
const MAX_OUTPUT = 2 * 1024 * 1024;

process.chdir("/workspace");

if (fs.existsSync(SOCKET_PATH)) fs.unlinkSync(SOCKET_PATH);

// require() that resolves from /workspace so `npm install`-ed packages are found.
const workspaceRequire = Module.createRequire("/workspace/");

// ── Persistent Context (equivalent to IPython shell) ──

const sandbox = {
  require: workspaceRequire,
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

  const timeoutMs = timeout * 1000;

  try {
    let result;

    // Decide execution strategy by parsing (no execution) first.
    // vm.Script only parses — if it throws SyntaxError for top-level
    // `await`, we check whether wrapping in an async IIFE fixes it.
    let source = code;
    try {
      new vm.Script(code);
    } catch (parseErr) {
      if (parseErr instanceof SyntaxError && code.includes("await")) {
        const wrapped = `(async () => {\n${code}\n})()`;
        try {
          new vm.Script(wrapped);
          source = wrapped;
        } catch {
          // Wrapper doesn't fix the SyntaxError — let execution
          // report the original error to the user.
        }
      }
    }

    result = vm.runInContext(source, context, {
      filename: "<cell>",
      timeout: timeoutMs,
      breakOnSigint: true,
    });

    // If the completion value is a thenable (Promise), await it so that
    // async output (console.log inside .then / async functions) is captured.
    if (result != null && typeof result.then === "function") {
      let timer;
      const timeoutPromise = new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(`Async execution exceeded ${timeout}s`)),
          timeoutMs,
        );
      });
      try {
        result = await Promise.race([result, timeoutPromise]);
      } finally {
        clearTimeout(timer);
      }
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
