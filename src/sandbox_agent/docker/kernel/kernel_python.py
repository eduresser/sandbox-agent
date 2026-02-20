"""
Persistent Python kernel.
Runs as PID 1 inside the container. Keeps an IPython shell alive
and accepts commands via UNIX domain socket.

No HTTP. No Flask. No extra dependencies beyond IPython.
"""

import base64
import io
import json
import os
import signal
import socket
import sys
import traceback

SOCKET_PATH = "/tmp/kernel.sock"
MAX_OUTPUT = 2 * 1024 * 1024

os.chdir("/workspace")

# Ensure user site-packages is on sys.path so that packages installed
# at runtime via `pip install` (which land in ~/.local/...) are importable.
import site  # noqa: E402

user_site = site.getusersitepackages()
os.makedirs(user_site, exist_ok=True)
if user_site not in sys.path:
    sys.path.insert(0, user_site)

if os.path.exists(SOCKET_PATH):
    os.unlink(SOCKET_PATH)

# ── IPython Shell ──────────────────────────────────────

from IPython.core.interactiveshell import InteractiveShell  # noqa: E402

shell = InteractiveShell.instance()
shell.colors = "NoColor"

shell.run_cell(
    "try:\n    import matplotlib; matplotlib.use('Agg')\nexcept ImportError:\n    pass",
    silent=True,
)


# ── Helpers ────────────────────────────────────────────


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) > limit:
        return text[: limit // 2] + "\n\n... [TRUNCATED] ...\n"
    return text


def capture_figures() -> list[str]:
    """Capture open matplotlib figures as base64 PNG."""
    figs = []
    try:
        import matplotlib.pyplot as plt

        for n in plt.get_fignums():
            buf = io.BytesIO()
            plt.figure(n).savefig(buf, format="png", bbox_inches="tight", dpi=100)
            buf.seek(0)
            figs.append(base64.b64encode(buf.read()).decode())
        plt.close("all")
    except Exception:
        pass
    return figs


def get_rich_result(obj) -> dict | None:
    """Extract rich representations (HTML for DataFrames, etc.)."""
    if obj is None:
        return None
    display = {}
    if hasattr(obj, "_repr_html_"):
        try:
            display["text/html"] = obj._repr_html_()
        except Exception:
            pass
    display["text/plain"] = repr(obj)
    return display


# ── Execution ──────────────────────────────────────────


def execute(code: str, timeout: int = 30) -> dict:
    timeout = min(timeout, 300)

    old_out, old_err = sys.stdout, sys.stderr
    cap_out, cap_err = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = cap_out, cap_err

    response = {
        "success": True,
        "stdout": "",
        "stderr": "",
        "result": None,
        "error": None,
        "figures": [],
    }

    def alarm_handler(signum, frame):
        raise TimeoutError(f"Execution exceeded {timeout}s")

    prev = signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(timeout)

    try:
        r = shell.run_cell(code, store_history=True, silent=False)
        signal.alarm(0)

        response["stdout"] = truncate(cap_out.getvalue())
        response["stderr"] = truncate(cap_err.getvalue())

        if r.success:
            response["result"] = get_rich_result(r.result)
        else:
            response["success"] = False
            err = r.error_in_exec or r.error_before_exec
            if err:
                response["error"] = {
                    "type": type(err).__name__,
                    "message": str(err),
                    "traceback": "".join(traceback.format_exception(err)),
                }

        response["figures"] = capture_figures()

    except TimeoutError as e:
        signal.alarm(0)
        response.update(
            {
                "success": False,
                "stdout": truncate(cap_out.getvalue()),
                "stderr": truncate(cap_err.getvalue()),
                "error": {"type": "TimeoutError", "message": str(e)},
            }
        )
    except Exception as e:
        signal.alarm(0)
        response.update(
            {
                "success": False,
                "stdout": truncate(cap_out.getvalue()),
                "stderr": truncate(cap_err.getvalue()),
                "error": {
                    "type": type(e).__name__,
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                },
            }
        )
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        signal.signal(signal.SIGALRM, prev)

    return response


# ── Request Handler ────────────────────────────────────


def handle_request(data: bytes) -> dict:
    try:
        req = json.loads(data)
    except json.JSONDecodeError as e:
        return {"success": False, "error": {"type": "JSONDecodeError", "message": str(e)}}

    action = req.get("action", "execute")

    if action == "execute":
        return execute(req.get("code", ""), req.get("timeout", 30))

    if action == "restart":
        shell.reset(new_session=True)
        shell.run_cell(
            "try:\n    import matplotlib; matplotlib.use('Agg')\nexcept ImportError:\n    pass",
            silent=True,
        )
        return {"success": True, "message": "Kernel restarted"}

    if action == "ping":
        return {"success": True}

    return {
        "success": False,
        "error": {"type": "ValueError", "message": f"Unknown action: {action}"},
    }


# ── UNIX Socket Server ────────────────────────────────

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET_PATH)
server.listen(5)
os.chmod(SOCKET_PATH, 0o777)

print("KERNEL_READY", flush=True)

while True:
    conn, _ = server.accept()
    try:
        data = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk

        result = handle_request(data)
        conn.sendall(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        try:
            err = json.dumps(
                {"success": False, "error": {"type": type(e).__name__, "message": str(e)}}
            )
            conn.sendall(err.encode())
        except Exception:
            pass
    finally:
        conn.close()
