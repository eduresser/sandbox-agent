"""Shared fixtures for sandbox-agent tests.

Tests require Docker to be running on the host.
"""

from __future__ import annotations

import pytest

from sandbox_agent.sandbox.manager import SandboxManager


@pytest.fixture(scope="session")
def manager():
    """Shared SandboxManager that cleans up all containers after the test session."""
    mgr = SandboxManager()
    yield mgr
    mgr.cleanup_all()


@pytest.fixture()
def python_session(manager: SandboxManager):
    """Creates a Python sandbox session and cleans it up after the test."""
    info = manager.create_session(runtime="python")
    yield info.session_id
    manager.stop_session(info.session_id)


@pytest.fixture()
def r_session(manager: SandboxManager):
    """Creates an R sandbox session and cleans it up after the test."""
    info = manager.create_session(runtime="r")
    yield info.session_id
    manager.stop_session(info.session_id)


@pytest.fixture()
def node_session(manager: SandboxManager):
    """Creates a Node.js sandbox session and cleans it up after the test."""
    info = manager.create_session(runtime="node")
    yield info.session_id
    manager.stop_session(info.session_id)


@pytest.fixture()
def julia_session(manager: SandboxManager):
    """Creates a Julia sandbox session and cleans it up after the test."""
    info = manager.create_session(runtime="julia")
    yield info.session_id
    manager.stop_session(info.session_id)
