"""Custom FastAPI app for Aegra — file downloads, uploads, and thread cleanup."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Query, UploadFile
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

_THREAD_DELETE_PATTERN = re.compile(r"^/threads/([a-fA-F0-9\-]+)$")


def _get_manager():
    """Lazy import to avoid circular deps and ensure graph is loaded first."""
    from sandbox_agent.sandbox import get_manager

    return get_manager()


class ThreadDeleteCleanupMiddleware(BaseHTTPMiddleware):
    """On DELETE /threads/{id}, clean up Docker sessions and storage."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        if request.method != "DELETE":
            return response

        match = _THREAD_DELETE_PATTERN.match(request.url.path)
        if not match:
            return response

        if response.status_code not in (200, 204):
            return response

        thread_id = match.group(1)
        try:
            manager = _get_manager()
            count = manager.cleanup_thread_sessions(thread_id)
            logger.info(
                "Thread delete cleanup: removed %d sessions for %s",
                count,
                thread_id[:12],
            )
        except Exception:
            logger.warning(
                "Thread delete cleanup failed for %s",
                thread_id[:12],
                exc_info=True,
            )

        return response


app = FastAPI()
app.add_middleware(ThreadDeleteCleanupMiddleware)


@app.get("/threads/{thread_id}/files/download")
async def download_thread_file(
    thread_id: str,
    session_id: str = Query(..., description="Session that exported the file"),
    path: str = Query(..., description="Container path (e.g. /workspace/report.pdf)"),
):
    """Download a file exported from a sandbox session."""
    manager = _get_manager()
    path_decoded = unquote(path)

    try:
        if not manager.is_file_exported(thread_id, session_id, path_decoded):
            return Response(
                status_code=403,
                content="File not exported or access denied",
            )
    except ValueError:
        return Response(status_code=400, content="Invalid path")

    try:

        def stream():
            yield from manager.stream_exported_file(
                thread_id, session_id, path_decoded
            )

        basename = (
            path_decoded.rsplit("/", 1)[-1]
            if "/" in path_decoded
            else path_decoded
        )
        is_file = manager.is_exported_path_file(
            thread_id, session_id, path_decoded
        )
        filename = basename if is_file else f"{basename}.tar"
        media_type = (
            "application/octet-stream" if is_file else "application/x-tar"
        )
        return StreamingResponse(
            stream(),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    except (ValueError, RuntimeError) as exc:
        return Response(status_code=403, content=str(exc))


def _get_storage_dir() -> Path:
    from sandbox_agent.settings import get_settings

    settings = get_settings()
    sd = Path(settings.STORAGE_DIR)
    if not sd.is_absolute():
        sd = Path(__file__).resolve().parent.parent.parent / sd
    return sd


# ── Frontend settings persistence ──────────────────────────────────────────


class FrontendSettings(BaseModel):
    chatModel: str | None = None
    chatModelProvider: str | None = None
    chatModelApiKey: str | None = None
    chatModelBaseUrl: str | None = None
    supportsVision: bool | None = None


def _settings_file() -> Path:
    return _get_storage_dir() / "frontend_settings.json"


def _load_frontend_settings() -> dict:
    path = _settings_file()
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read frontend settings file", exc_info=True)
    return {}


def _backend_defaults() -> dict:
    from sandbox_agent.settings import get_settings

    s = get_settings()
    return {
        "chatModel": s.CHAT_MODEL,
        "chatModelProvider": s.CHAT_MODEL_PROVIDER,
        "chatModelApiKey": s.CHAT_MODEL_API_KEY,
        "chatModelBaseUrl": s.CHAT_MODEL_BASE_URL or "",
        "supportsVision": s.CHAT_MODEL_SUPPORTS_VISION
        if s.CHAT_MODEL_SUPPORTS_VISION is not None
        else True,
    }


@app.get("/settings")
async def get_frontend_settings():
    """Return persisted frontend settings merged over backend defaults."""
    defaults = _backend_defaults()
    saved = _load_frontend_settings()
    return JSONResponse({**defaults, **saved})


@app.put("/settings")
async def save_frontend_settings(body: FrontendSettings):
    """Persist frontend settings to disk."""
    data = body.model_dump(exclude_none=True)
    path = _settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), "utf-8")
    defaults = _backend_defaults()
    return JSONResponse({**defaults, **data})


@app.post("/threads/{thread_id}/files/upload")
async def upload_thread_files(thread_id: str, files: list[UploadFile]):
    """Upload files to be available for import into sandbox sessions."""
    dest_dir = _get_storage_dir() / thread_id / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for f in files:
        if not f.filename:
            continue
        file_path = dest_dir / f.filename
        content = await f.read()
        file_path.write_bytes(content)
        results.append({
            "name": f.filename,
            "path": str(file_path.resolve()),
            "size": len(content),
        })

    return JSONResponse(results)
