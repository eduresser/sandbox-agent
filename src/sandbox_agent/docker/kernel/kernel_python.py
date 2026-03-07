"""
Persistent Python kernel.
Runs as PID 1 inside the container. Keeps an IPython shell alive
and accepts commands via UNIX domain socket.

No HTTP. No Flask. No extra dependencies beyond IPython.
"""

import asyncio
import base64
import inspect
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


RICH_MIME_PRIORITY = [
    "text/html",
    "image/svg+xml",
    "image/png",
    "image/jpeg",
    "audio/wav",
    "audio/mpeg",
    "audio/ogg",
    "video/mp4",
]


def capture_matplotlib_figures() -> list[dict]:
    """Capture open matplotlib figures as display_output dicts."""
    outputs = []
    try:
        import matplotlib.pyplot as plt

        for n in plt.get_fignums():
            buf = io.BytesIO()
            plt.figure(n).savefig(buf, format="png", bbox_inches="tight", dpi=100)
            buf.seek(0)
            outputs.append({
                "type": "image/png",
                "data": base64.b64encode(buf.read()).decode(),
            })
        plt.close("all")
    except Exception:
        pass
    return outputs


_REPR_METHODS = {
    "text/html": "_repr_html_",
    "image/svg+xml": "_repr_svg_",
    "image/png": "_repr_png_",
    "image/jpeg": "_repr_jpeg_",
}


def _extract_rich_repr(obj) -> dict | None:
    """Extract the best rich representation from an object's _repr_*_ methods."""
    if obj is None:
        return None
    for mime, method in _REPR_METHODS.items():
        fn = getattr(obj, method, None)
        if fn is None:
            continue
        try:
            data = fn()
        except Exception:
            continue
        if not data:
            continue
        if isinstance(data, bytes):
            data = base64.b64encode(data).decode()
        return {"type": mime, "data": data}
    return None


def _try_self_contained_html(obj) -> str | None:
    """Plotly/Bokeh figures: produce a full self-contained HTML document.

    Plotly's _ipython_display_() emits require.js fragments that only work
    inside Jupyter.  to_html(full_html=True) gives a standalone page that
    renders correctly in an iframe.
    """
    if obj is None:
        return None
    try:
        if hasattr(obj, "to_html") and hasattr(obj, "layout") and hasattr(obj, "data"):
            return obj.to_html(full_html=True, include_plotlyjs=True)
    except Exception:
        pass
    return None


def _is_plotly_fragment(data: str) -> bool:
    """Detect Plotly notebook-renderer fragments (not self-contained)."""
    prefix = data[:2000]
    return (
        "PlotlyConfig" in prefix
        or "Plotly.newPlot" in prefix
        or "require(['plotly']" in prefix
    )


def _find_plotly_figure_in_captured(captured_outputs):
    """If captured outputs came from a Plotly figure's _ipython_display_(),
    try to recover the figure object and produce self-contained HTML."""
    for rich_output in captured_outputs:
        obj = getattr(rich_output, "_obj", None) or getattr(rich_output, "data", None)
        if isinstance(obj, dict) and "application/vnd.plotly.v1+json" in obj:
            try:
                import plotly.graph_objects as go
                fig = go.Figure(obj["application/vnd.plotly.v1+json"])
                return fig.to_html(full_html=True, include_plotlyjs=True)
            except Exception:
                pass
    return None


def build_display_outputs(captured_outputs, matplotlib_outputs, result_value=None) -> list[dict]:
    """Merge matplotlib figures, IPython display() captures, and last-expression repr."""
    outputs = list(matplotlib_outputs)

    sc_html = _try_self_contained_html(result_value)
    if not sc_html:
        sc_html = _find_plotly_figure_in_captured(captured_outputs)

    for rich_output in captured_outputs:
        data_dict = getattr(rich_output, "data", None)
        if not isinstance(data_dict, dict):
            continue
        for mime in RICH_MIME_PRIORITY:
            if mime in data_dict:
                data = data_dict[mime]
                if isinstance(data, bytes):
                    data = base64.b64encode(data).decode()
                if not data:
                    break
                if sc_html and mime == "text/html" and _is_plotly_fragment(data):
                    break
                outputs.append({"type": mime, "data": data})
                break

    if sc_html:
        outputs.append({"type": "text/html", "data": sc_html})
    elif result_value is not None and not hasattr(result_value, "_ipython_display_"):
        rich = _extract_rich_repr(result_value)
        if rich:
            outputs.append(rich)

    return outputs


def get_text_result(obj) -> dict | None:
    """Return text/plain representation only (rich reprs go to display_outputs)."""
    if obj is None:
        return None
    return {"text/plain": repr(obj)}


# ── Execution ──────────────────────────────────────────


def execute(code: str, timeout: int = 30) -> dict:
    timeout = min(timeout, 300)

    from IPython.utils.capture import capture_output

    old_out, old_err = sys.stdout, sys.stderr
    cap_out, cap_err = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = cap_out, cap_err

    response = {
        "success": True,
        "stdout": "",
        "stderr": "",
        "result": None,
        "error": None,
        "display_outputs": [],
    }

    def alarm_handler(signum, frame):
        raise TimeoutError(f"Execution exceeded {timeout}s")

    prev = signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(timeout)

    try:
        with capture_output(stdout=False, stderr=False, display=True) as captured:
            r = shell.run_cell(code, store_history=True, silent=False)

        result_value = r.result

        if r.success and inspect.isawaitable(result_value):
            result_value = asyncio.run(
                asyncio.wait_for(result_value, timeout=timeout)
            )

        signal.alarm(0)

        response["stdout"] = truncate(cap_out.getvalue())
        response["stderr"] = truncate(cap_err.getvalue())

        if r.success:
            response["result"] = get_text_result(result_value)
        else:
            response["success"] = False
            err = r.error_in_exec or r.error_before_exec
            if err:
                response["error"] = {
                    "type": type(err).__name__,
                    "message": str(err),
                    "traceback": "".join(traceback.format_exception(err)),
                }

        response["display_outputs"] = build_display_outputs(
            captured.outputs,
            capture_matplotlib_figures(),
            result_value if r.success else None,
        )

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
