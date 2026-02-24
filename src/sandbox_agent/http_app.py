"""Custom FastAPI app for Aegra — file downloads and thread cleanup."""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote

from fastapi import FastAPI, Query
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

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
