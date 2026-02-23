"""Custom FastAPI app for Aegra — intercepts thread deletion to cleanup sessions and storage."""

from __future__ import annotations

import logging
import re

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# DELETE /threads/{thread_id} — thread_id is UUID-like
_THREAD_DELETE_PATTERN = re.compile(r"^/threads/([a-fA-F0-9\-]+)$")


def _get_manager():
    """Lazy import to avoid circular deps and ensure graph is loaded first."""
    from sandbox_agent.agent.graph import _get_manager as _gm

    return _gm()


class ThreadDeleteCleanupMiddleware(BaseHTTPMiddleware):
    """When a thread is deleted via DELETE /threads/{id}, immediately cleanup sessions and storage."""

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
            logger.info("Thread delete cleanup: removed %d sessions and storage for %s", count, thread_id[:12])
        except Exception:
            logger.warning("Thread delete cleanup failed for %s", thread_id[:12], exc_info=True)

        return response


app = FastAPI()
app.add_middleware(ThreadDeleteCleanupMiddleware)
