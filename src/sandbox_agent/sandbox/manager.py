"""
SandboxManager — orchestrates Docker containers via docker exec.
No exposed ports. No host volumes mounted.
"""

from __future__ import annotations

import atexit
import io
import json
import logging
import signal
import subprocess
import tarfile
import time
import uuid
from pathlib import Path
from typing import Any

import docker
import docker.errors

from sandbox_agent.sandbox.models import ExecutionResult, SessionInfo, TerminalResult
from sandbox_agent.settings import get_settings

logger = logging.getLogger(__name__)

RUNTIME_CONFIG: dict[str, dict[str, Any]] = {
    "python": {
        "image": "sandbox-python:latest",
        "dockerfile": "Dockerfile.python",
        "client_cmd": ["python", "/kernel/client.py"],
        "install_cmd": lambda pkgs: ["pip", "install", "--no-cache-dir", *pkgs],
    },
    "node": {
        "image": "sandbox-node:latest",
        "dockerfile": "Dockerfile.node",
        "client_cmd": ["node", "/kernel/client.js"],
        "install_cmd": lambda pkgs: ["npm", "install", "--save", *pkgs],
    },
}


class SandboxManager:
    """Manages Docker container sandboxes with persistent kernels."""

    def __init__(self, docker_dir: str | Path | None = None) -> None:
        self.client = docker.from_env()
        self.sessions: dict[str, SessionInfo] = {}
        self._containers: dict[str, Any] = {}
        self.docker_dir = (
            Path(docker_dir) if docker_dir else Path(__file__).parent.parent / "docker"
        )
        self._cleanup_registered = False
        self._register_cleanup()

    # ── Image Build ────────────────────────────────────

    def _ensure_image(self, runtime: str) -> None:
        config = RUNTIME_CONFIG[runtime]
        try:
            self.client.images.get(config["image"])
        except docker.errors.ImageNotFound:
            logger.info("Building %s ...", config["image"])
            self.client.images.build(
                path=str(self.docker_dir),
                dockerfile=config["dockerfile"],
                tag=config["image"],
                rm=True,
            )
            logger.info("Image %s built.", config["image"])

    # ── Kernel Communication ───────────────────────────

    def _send_to_kernel(self, session_id: str, payload: dict, timeout: int = 35) -> dict:
        info = self.sessions[session_id]
        config = RUNTIME_CONFIG[info.runtime]
        container_name = info.container_name

        result = subprocess.run(
            ["docker", "exec", "-i", container_name, *config["client_cmd"]],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            raise RuntimeError(f"docker exec failed (rc={result.returncode}): {result.stderr}")

        return json.loads(result.stdout)

    def _wait_for_kernel(self, session_id: str, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                resp = self._send_to_kernel(session_id, {"action": "ping"}, timeout=5)
                if resp.get("success"):
                    return
            except Exception as e:
                last_err = e
            time.sleep(0.5)
        raise TimeoutError(f"Kernel did not start in {timeout}s: {last_err}")

    # ── Public API (Tools) ─────────────────────────────

    def create_session(
        self,
        runtime: str = "python",
        dependencies: dict[str, str] | None = None,
        mem_limit: str | None = None,
        cpu_quota: int | None = None,
        network: bool = True,
    ) -> SessionInfo:
        settings = get_settings()

        if runtime not in RUNTIME_CONFIG:
            raise ValueError(f"Runtime '{runtime}' not supported. Use: {list(RUNTIME_CONFIG)}")

        if len(self.sessions) >= settings.MAX_SESSIONS:
            raise RuntimeError(
                f"Maximum number of sessions ({settings.MAX_SESSIONS}) reached. "
                "Stop an existing session first."
            )

        self._ensure_image(runtime)

        session_id = uuid.uuid4().hex[:8]
        container_name = f"sandbox-{session_id}"

        container = self.client.containers.run(
            RUNTIME_CONFIG[runtime]["image"],
            name=container_name,
            detach=True,
            mem_limit=mem_limit or settings.CONTAINER_MEMORY_LIMIT,
            cpu_period=100_000,
            cpu_quota=cpu_quota or settings.CONTAINER_CPU_QUOTA,
            network_disabled=not network,
            labels={"sandbox-agent": "true", "session-id": session_id},
        )

        info = SessionInfo(
            session_id=session_id,
            container_id=container.id,
            container_name=container_name,
            runtime=runtime,
            status="starting",
            dependencies=dict(dependencies or {}),
        )

        self._containers[session_id] = container
        self.sessions[session_id] = info

        self._wait_for_kernel(session_id)

        if dependencies:
            self._install_initial_packages(session_id, dependencies)

        info.status = "running"
        return info

    def execute_code(
        self, session_id: str, code: str, timeout: int | None = None
    ) -> ExecutionResult:
        self._check_session(session_id)
        settings = get_settings()
        timeout = timeout or settings.EXECUTION_TIMEOUT_SECONDS

        resp = self._send_to_kernel(
            session_id,
            {"action": "execute", "code": code, "timeout": timeout},
            timeout=timeout + 10,
        )
        return ExecutionResult(**resp)

    def execute_terminal(self, session_id: str, command: str) -> TerminalResult:
        self._check_session(session_id)
        container = self._containers[session_id]

        exit_code, output = container.exec_run(
            ["sh", "-c", command],
            demux=True,
            workdir="/workspace",
        )

        stdout = (output[0] or b"").decode(errors="replace")
        stderr = (output[1] or b"").decode(errors="replace")

        return TerminalResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    def _install_initial_packages(self, session_id: str, packages: dict[str, str]) -> None:
        """Install packages during session creation. Internal only."""
        info = self.sessions[session_id]
        config = RUNTIME_CONFIG[info.runtime]
        container = self._containers[session_id]

        if info.runtime == "python":
            specs = [f"{n}=={v}" if v else n for n, v in packages.items()]
        else:
            specs = [f"{n}@{v}" if v else n for n, v in packages.items()]

        exit_code, output = container.exec_run(
            config["install_cmd"](specs),
            demux=True,
        )

        if exit_code != 0:
            stderr = (output[1] or b"").decode(errors="replace")
            logger.warning("Failed to install initial packages %s: %s", specs, stderr[-500:])

    def upload_file(
        self,
        session_id: str,
        local_path: str | Path,
        remote_name: str | None = None,
    ) -> dict:
        self._check_session(session_id)
        container = self._containers[session_id]
        local_path = Path(local_path)

        if not local_path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")

        if remote_name is None:
            remote_name = local_path.name

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            tar.add(str(local_path), arcname=remote_name)
        tar_buf.seek(0)

        container.put_archive("/workspace", tar_buf)

        return {
            "success": True,
            "remote_path": f"/workspace/{remote_name}",
            "size": local_path.stat().st_size,
        }

    def stop_session(self, session_id: str) -> bool:
        if session_id not in self._containers:
            return False

        container = self._containers[session_id]
        try:
            container.stop(timeout=3)
            container.remove(force=True)
        except Exception:
            pass

        self._containers.pop(session_id, None)
        self.sessions.pop(session_id, None)
        return True

    def cleanup_all(self) -> None:
        for sid in list(self.sessions):
            self.stop_session(sid)

    # ── Internal ───────────────────────────────────────

    def _check_session(self, session_id: str) -> None:
        if session_id not in self._containers:
            raise ValueError(f"Session '{session_id}' not found")

    def _register_cleanup(self) -> None:
        if self._cleanup_registered:
            return
        self._cleanup_registered = True

        atexit.register(self.cleanup_all)

        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _handle_signal(signum: int, frame: Any) -> None:
            self.cleanup_all()
            handler = original_sigterm if signum == signal.SIGTERM else original_sigint
            if callable(handler):
                handler(signum, frame)

        try:
            signal.signal(signal.SIGTERM, _handle_signal)
            signal.signal(signal.SIGINT, _handle_signal)
        except ValueError:
            pass
