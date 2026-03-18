"""Custom FastAPI app for Aegra — file downloads, uploads, and thread cleanup."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Query, UploadFile
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from sandbox_agent.crypto import (
    load_encrypted_settings,
    mask_api_key,
    save_encrypted_settings,
)

logger = logging.getLogger(__name__)

_THREAD_DELETE_PATTERN = re.compile(r"^/threads/([a-fA-F0-9\-]+)$")


def _get_manager():
    """Lazy import to avoid circular deps and ensure graph is loaded first."""
    from sandbox_agent.sandbox import get_manager

    return get_manager()


def _cleanup_thread_background(thread_id: str) -> None:
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


class ThreadDeleteCleanupMiddleware(BaseHTTPMiddleware):
    """On DELETE /threads/{id}, clean up Docker sessions and storage in background."""

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
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _cleanup_thread_background, thread_id)

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

    return get_settings().STORAGE_DIR


# ── Frontend settings persistence ──────────────────────────────────────────


class FrontendSettings(BaseModel):
    chatModel: str | None = None
    chatModelProvider: str | None = None
    chatModelApiKey: str | None = None
    chatModelBaseUrl: str | None = None
    supportsVision: bool | None = None


def _load_frontend_settings() -> dict:
    return load_encrypted_settings()


def _backend_defaults() -> dict:
    from sandbox_agent.settings import get_settings

    s = get_settings()
    return {
        "chatModel": s.CHAT_MODEL,
        "chatModelProvider": s.CHAT_MODEL_PROVIDER,
        "chatModelApiKey": "",
        "chatModelApiKeyHint": mask_api_key(s.CHAT_MODEL_API_KEY),
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

    stored_key = saved.get("chatModelApiKey", "")
    hint = mask_api_key(stored_key) if stored_key else defaults.get("chatModelApiKeyHint", "")

    merged = {**defaults, **saved}
    merged["chatModelApiKey"] = ""
    merged["chatModelApiKeyHint"] = hint
    return JSONResponse(merged)


@app.put("/settings")
async def save_frontend_settings(body: FrontendSettings):
    """Persist frontend settings to database (encrypted)."""
    data = body.model_dump(exclude_none=True)

    new_key = data.get("chatModelApiKey", "")
    if not new_key:
        existing = _load_frontend_settings()
        stored = existing.get("chatModelApiKey", "")
        if stored:
            data["chatModelApiKey"] = stored
        else:
            data.pop("chatModelApiKey", None)

    save_encrypted_settings(data)

    plain_key = data.get("chatModelApiKey", "")
    defaults = _backend_defaults()
    result = {**defaults, **data}
    result["chatModelApiKey"] = ""
    result["chatModelApiKeyHint"] = (
        mask_api_key(plain_key) if plain_key else defaults.get("chatModelApiKeyHint", "")
    )
    return JSONResponse(result)


# ── Active sandbox sessions management ─────────────────────────────────────


def _runtime_from_image(image_tag: str) -> str:
    """Extract runtime name from Docker image tag like 'sandbox-python:latest'."""
    name = image_tag.split(":")[0]
    return name.removeprefix("sandbox-") if name.startswith("sandbox-") else name


@app.get("/sessions")
async def list_sessions():
    """Return all active sandbox containers (from Docker, enriched with in-memory info)."""
    import docker

    manager = _get_manager()
    try:
        containers = manager.client.containers.list(
            filters={"label": "sandbox-agent=true"},
        )
    except docker.errors.DockerException:
        logger.warning("Failed to list Docker containers", exc_info=True)
        return JSONResponse([])

    result = []
    for container in containers:
        sid = container.labels.get("session-id", container.short_id)
        image_tag = ",".join(container.image.tags) if container.image.tags else ""
        mem_info = manager.sessions.get(sid)
        result.append({
            "session_id": sid,
            "container_id": container.short_id,
            "container_name": container.name,
            "runtime": mem_info.runtime if mem_info else _runtime_from_image(image_tag),
            "status": container.status,
            "thread_id": mem_info.thread_id if mem_info else None,
            "created_at": (
                mem_info.created_at.isoformat()
                if mem_info
                else container.attrs.get("Created", "")
            ),
            "last_activity": (
                mem_info.last_activity.isoformat() if mem_info else None
            ),
        })
    return JSONResponse(result)


@app.delete("/sessions/{session_id}")
async def kill_session(session_id: str):
    """Stop and remove a sandbox session (tracked or orphaned)."""
    manager = _get_manager()

    if manager.stop_session(session_id):
        return JSONResponse({"ok": True, "session_id": session_id})

    try:
        containers = manager.client.containers.list(
            all=True,
            filters={"label": f"session-id={session_id}"},
        )
        if not containers:
            return JSONResponse(
                {"error": "Session not found"}, status_code=404,
            )
        for c in containers:
            c.stop(timeout=3)
            c.remove(force=True)
        return JSONResponse({"ok": True, "session_id": session_id})
    except Exception:
        logger.warning("Failed to kill session %s", session_id, exc_info=True)
        return JSONResponse(
            {"error": "Failed to stop session"}, status_code=500,
        )


@app.post("/threads/{thread_id}/files/upload")
async def upload_thread_files(thread_id: str, files: list[UploadFile]):
    """Upload files to be available for import into sandbox sessions."""
    dest_dir = _get_storage_dir() / thread_id / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for f in files:
        if not f.filename:
            continue
        relative = Path(f.filename)
        safe_parts = [p for p in relative.parts if p not in (".", "..", "/")]
        if not safe_parts:
            continue
        safe_relative = Path(*safe_parts)
        file_path = dest_dir / safe_relative
        if not file_path.resolve().is_relative_to(dest_dir.resolve()):
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        content = await f.read()
        file_path.write_bytes(content)
        results.append({
            "name": str(safe_relative),
            "path": str(file_path.resolve()),
            "size": len(content),
        })

    return JSONResponse(results)
