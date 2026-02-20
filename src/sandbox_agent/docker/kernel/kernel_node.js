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
const { inspect } = require("util");
const Module = require("module");

const SOCKET_PATH = "/tmp/kernel.sock";
const MAX_OUTPUT = 2 * 1024 * 1024;
const ASYNC_DRAIN_MS = 150;

process.chdir("/workspace");

if (fs.existsSync(SOCKET_PATH)) fs.unlinkSync(SOCKET_PATH);

// ── Async Error Safety Net ──────────────────────────────
// User code may schedule callbacks (fs.readdir, setTimeout, etc.) that throw
// after vm.runInContext returns.  Without these handlers the uncaught error
// kills the kernel (PID 1) and the whole container dies.

const asyncErrors = [];

process.on("uncaughtException", (err) => {
  asyncErrors.push({ type: err.constructor.name, message: err.message });
});

process.on("unhandledRejection", (reason) => {
  const msg = reason instanceof Error ? reason.message : String(reason);
  const type = reason instanceof Error ? reason.constructor.name : "UnhandledRejection";
  asyncErrors.push({ type, message: msg });
});

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

// ── const/let → var rewrite ─────────────────────────────
// In a persistent VM context, `const` and `let` declarations cannot be
// re-declared across cells (V8 treats it as a SyntaxError).  Rewriting
// them to `var` allows re-declaration while keeping variables in the
// sandbox object — the same strategy used by Node.js's own REPL.

function rewriteConstLet(code) {
  return code.replace(
    /^([ \t]*)(const|let)\s/gm,
    (_match, indent, _keyword) => `${indent}var `,
  );
}

// ── Execution ─────────────────────────────────────────

async function execute(code, timeout = 30) {
  const stdoutChunks = [];
  const stderrChunks = [];

  const fmt = (v) =>
    typeof v === "string" ? v : inspect(v, { depth: 4, colors: false });

  sandbox.console = {
    log: (...args) => stdoutChunks.push(args.map(fmt).join(" ")),
    error: (...args) => stderrChunks.push(args.map(fmt).join(" ")),
    warn: (...args) => stderrChunks.push(args.map(fmt).join(" ")),
    info: (...args) => stdoutChunks.push(args.map(fmt).join(" ")),
    dir: (obj) => stdoutChunks.push(inspect(obj, { depth: 4, colors: false })),
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

    // Rewrite const/let → var so re-declarations across cells don't fail.
    let source = rewriteConstLet(code);

    // Decide execution strategy by parsing (no execution) first.
    // vm.Script only parses — if it throws SyntaxError for top-level
    // `await`, we check whether wrapping in an async IIFE fixes it.
    try {
      new vm.Script(source);
    } catch (parseErr) {
      if (parseErr instanceof SyntaxError && source.includes("await")) {
        const wrapped = `(async () => {\n${source}\n})()`;
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
    } else {
      // Brief drain: let pending I/O callbacks (fs.readdir, etc.) fire
      // so their console output and potential errors are captured in this
      // response rather than silently lost or surfaced as async errors.
      await new Promise((r) => setTimeout(r, ASYNC_DRAIN_MS));
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

  // Surface any async errors (uncaughtException / unhandledRejection) that
  // fired during this execution so the caller gets visibility.
  if (asyncErrors.length > 0) {
    const msgs = asyncErrors
      .splice(0)
      .map((e) => `[async ${e.type}] ${e.message}`);
    const prefix = response.stderr ? response.stderr + "\n" : "";
    response.stderr = (prefix + msgs.join("\n")).slice(0, MAX_OUTPUT);
    if (response.success) {
      response.success = false;
      response.error = {
        type: "AsyncError",
        message:
          "Code spawned async callbacks that threw errors: " + msgs.join("; "),
      };
    }
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
