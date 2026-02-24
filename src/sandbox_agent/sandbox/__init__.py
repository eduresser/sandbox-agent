"""Docker-based sandbox with persistent kernels."""

from __future__ import annotations

import threading

from sandbox_agent.sandbox.manager import SandboxManager, current_thread_id

__all__ = ["SandboxManager", "current_thread_id", "get_manager"]

_manager: SandboxManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> SandboxManager:
    """Return the process-wide ``SandboxManager`` singleton (thread-safe)."""
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is None:
            _manager = SandboxManager()
    return _manager
