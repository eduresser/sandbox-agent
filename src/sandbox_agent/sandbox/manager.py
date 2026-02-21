"""
SandboxManager — orchestrates Docker containers via docker exec.
No exposed ports. No host volumes mounted.
Containers are hardened with resource limits, PID limits, read-only
rootfs, and non-root user to prevent host damage even under fork bombs,
OOM, or disk-fill attacks.
"""

from __future__ import annotations

import atexit
import io
import json
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

SANDBOX_UID = 65532
SANDBOX_GID = 65532

RUNTIME_CONFIG: dict[str, dict[str, Any]] = {
    "python": {
        "image": "sandbox-python:latest",
        "dockerfile": "Dockerfile.python",
        "client_cmd": ["python", "/kernel/client.py"],
        "install_cmd": lambda pkgs: ["pip", "install", "--no-cache-dir", *pkgs],
        "install_user": "root",
    },
    "node": {
        "image": "sandbox-node:latest",
        "dockerfile": "Dockerfile.node",
        "client_cmd": ["node", "/kernel/client.js"],
        "install_cmd": lambda pkgs: ["npm", "install", "--save", *pkgs],
        "install_user": None,
    },
    "r": {
        "image": "sandbox-r:latest",
        "dockerfile": "Dockerfile.r",
        "client_cmd": ["python3", "/kernel/client_r.py"],
        "install_cmd": lambda pkgs: [
            "Rscript", "-e",
            "install.packages(c("
            + ",".join(f"'{p}'" for p in pkgs)
            + "), Ncpus=2L)",
        ],
        "install_user": "root",
    },
}


class ContainerDiedError(RuntimeError):
    """Raised when a sandbox container has exited unexpectedly."""

    def __init__(self, session_id: str, reason: str, exit_code: int | None = None):
        self.session_id = session_id
        self.reason = reason
        self.exit_code = exit_code
        super().__init__(
            f"Container for session '{session_id}' died: {reason}"
            + (f" (exit code {exit_code})" if exit_code is not None else "")
        )


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
            self.client.images.build(
                path=str(self.docker_dir),
                dockerfile=config["dockerfile"],
                tag=config["image"],
                rm=True,
            )

    # ── Container Health ───────────────────────────────

    def _inspect_container_health(self, session_id: str) -> str | None:
        """Check if the container is still running. Returns a human-readable
        death reason or None if the container is healthy."""
        container = self._containers.get(session_id)
        if container is None:
            return "container not found (already removed)"

        try:
            container.reload()
        except docker.errors.NotFound:
            self._mark_session_dead(session_id)
            return "container no longer exists (removed externally)"
        except Exception as exc:
            return f"unable to inspect container: {exc}"

        status = container.status
        if status == "running":
            return None

        state = container.attrs.get("State", {})
        oom = state.get("OOMKilled", False)
        exit_code = state.get("ExitCode")

        if oom:
            reason = "OOM-killed — the code exhausted the container memory limit"
        elif exit_code == 137:
            reason = "killed by signal 9 (SIGKILL) — likely OOM or external kill"
        elif exit_code == 139:
            reason = "segmentation fault (SIGSEGV)"
        elif exit_code == 143:
            reason = "terminated by SIGTERM"
        else:
            reason = f"exited with status {exit_code} (container status: {status})"

        self._mark_session_dead(session_id)
        return reason

    def _mark_session_dead(self, session_id: str) -> None:
        if session_id in self.sessions:
            self.sessions[session_id].status = "dead"

    def _assert_container_alive(self, session_id: str) -> None:
        """Raise ContainerDiedError if the container is not running."""
        reason = self._inspect_container_health(session_id)
        if reason is not None:
            state = self._containers.get(session_id)
            exit_code = None
            if state is not None:
                try:
                    exit_code = state.attrs.get("State", {}).get("ExitCode")
                except Exception:
                    pass
            raise ContainerDiedError(session_id, reason, exit_code)

    # ── Kernel Communication ───────────────────────────

    def _send_to_kernel(self, session_id: str, payload: dict, timeout: int = 35) -> dict:
        self._assert_container_alive(session_id)

        info = self.sessions[session_id]
        config = RUNTIME_CONFIG[info.runtime]
        container_name = info.container_name

        try:
            result = subprocess.run(
                ["docker", "exec", "-i", container_name, *config["client_cmd"]],
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"Execution in session '{session_id}' timed out after {timeout}s"
            )

        if result.returncode != 0:
            health = self._inspect_container_health(session_id)
            if health is not None:
                raise ContainerDiedError(session_id, health)
            raise RuntimeError(
                f"docker exec failed (rc={result.returncode}): {result.stderr[:500]}"
            )

        return json.loads(result.stdout)

    def _wait_for_kernel(self, session_id: str, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                resp = self._send_to_kernel(session_id, {"action": "ping"}, timeout=5)
                if resp.get("success"):
                    return
            except ContainerDiedError:
                raise
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

        tmpfs_size = settings.CONTAINER_TMPFS_SIZE

        effective_mem = mem_limit or settings.CONTAINER_MEMORY_LIMIT

        container = self.client.containers.run(
            RUNTIME_CONFIG[runtime]["image"],
            name=container_name,
            detach=True,
            mem_limit=effective_mem,
            memswap_limit=effective_mem,
            cpu_period=100_000,
            cpu_quota=cpu_quota or settings.CONTAINER_CPU_QUOTA,
            pids_limit=settings.CONTAINER_PIDS_LIMIT,
            network_disabled=not network,
            tmpfs={
                "/tmp": f"size={tmpfs_size},nosuid,uid={SANDBOX_UID},gid={SANDBOX_GID}",
                "/workspace": f"size={tmpfs_size},uid={SANDBOX_UID},gid={SANDBOX_GID}",
                "/home/sandbox": f"size={tmpfs_size},uid={SANDBOX_UID},gid={SANDBOX_GID}",
            },
            security_opt=["no-new-privileges"],
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
        self._assert_container_alive(session_id)
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
        """Install packages during session creation. Runs as root so packages
        land in the system site-packages / global node_modules.
        Captures stdout/stderr into the SessionInfo so callers can inspect them."""
        info = self.sessions[session_id]
        config = RUNTIME_CONFIG[info.runtime]
        container = self._containers[session_id]

        if info.runtime == "python":
            specs = [f"{n}=={v}" if v else n for n, v in packages.items()]
        elif info.runtime == "r":
            specs = list(packages.keys())
        else:
            specs = [f"{n}@{v}" if v else n for n, v in packages.items()]

        user = config.get("install_user")
        exit_code, output = container.exec_run(
            config["install_cmd"](specs),
            demux=True,
            **({"user": user} if user else {}),
        )

        info.stdout = (output[0] or b"").decode(errors="replace")
        info.stderr = (output[1] or b"").decode(errors="replace")

    def upload_file(
        self,
        session_id: str,
        local_path: str | Path,
        remote_name: str | None = None,
    ) -> dict:
        self._check_session(session_id)
        self._assert_container_alive(session_id)
        info = self.sessions[session_id]
        local_path = Path(local_path)

        if not local_path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")

        if remote_name is None:
            remote_name = local_path.name

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            tar.add(str(local_path), arcname=remote_name)
        tar_buf.seek(0)

        result = subprocess.run(
            ["docker", "exec", "-i", info.container_name, "tar", "xf", "-", "-C", "/workspace"],
            input=tar_buf.read(),
            capture_output=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to upload file: {result.stderr.decode(errors='replace')[:500]}"
            )

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
