"""
SandboxManager — orchestrates Docker containers via docker exec.
No exposed ports. No host volumes mounted.
Containers are hardened with resource limits, PID limits, read-only
rootfs, and non-root user to prevent host damage even under fork bombs,
OOM, or disk-fill attacks.
"""

from __future__ import annotations

import atexit
import contextvars
import io
import json
import logging
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import docker
import docker.errors

from sandbox_agent.sandbox.models import (
    ExecutionResult,
    ExportFileResult,
    ExportResult,
    ImportFileResult,
    ImportResult,
    SessionInfo,
    TerminalResult,
    truncate_field,
)
from sandbox_agent.settings import get_settings

logger = logging.getLogger(__name__)

current_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_thread_id", default=None,
)

SANDBOX_UID = 65532
SANDBOX_GID = 65532

RUNTIME_CONFIG: dict[str, dict[str, Any]] = {
    "python": {
        "image": "sandbox-python:latest",
        "dockerfile": "Dockerfile.python",
        "client_cmd": ["python", "/client/client_python.py"],
        "install_cmd": lambda pkgs: ["pip", "install", "--no-cache-dir", *pkgs],
        "install_user": "root",
    },
    "node": {
        "image": "sandbox-node:latest",
        "dockerfile": "Dockerfile.node",
        "client_cmd": ["node", "/client/client_node.js"],
        "install_cmd": lambda pkgs: ["npm", "install", "--save", *pkgs],
        "install_user": None,
    },
    "r": {
        "image": "sandbox-r:latest",
        "dockerfile": "Dockerfile.r",
        "client_cmd": ["/client/client_c"],
        "install_cmd": lambda pkgs: [
            "Rscript", "-e",
            "install.packages(c("
            + ",".join(f"'{p}'" for p in pkgs)
            + "), Ncpus=2L)",
        ],
        "install_user": "root",
    },
    "julia": {
        "image": "sandbox-julia:latest",
        "dockerfile": "Dockerfile.julia",
        "client_cmd": ["/client/client_c"],
        "install_cmd": lambda pkgs: [
            "sh", "-c",
            "JULIA_DEPOT_PATH=/opt/julia-depot "
            "julia -e 'using Pkg; Pkg.add(["
            + ", ".join(f'"{p}"' for p in pkgs)
            + "]); Pkg.precompile()'"
            " && chown -R 65532:65532 /opt/julia-depot",
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
        self.thread_sessions: dict[str, set[str]] = {}
        self._exported_files: dict[str, dict[str, set[str]]] = {}  # thread_key -> session_id -> set(paths)
        self._lock = threading.Lock()
        self.docker_dir = (
            Path(docker_dir) if docker_dir else Path(__file__).parent.parent / "docker"
        )
        self._cleanup_registered = False
        self._register_cleanup()
        self._cleanup_orphaned_containers()
        self._start_gc()
        self._ensure_exported_files_table()

    def _ensure_exported_files_table(self) -> None:
        """Create exported_files table in PostgreSQL for cross-process sharing."""
        try:
            from sandbox_agent.clients import get_db_conninfo
            import psycopg
        except Exception:
            return
        try:
            with psycopg.connect(get_db_conninfo(), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS exported_files (
                            thread_key TEXT NOT NULL,
                            session_id TEXT NOT NULL,
                            path TEXT NOT NULL,
                            PRIMARY KEY (thread_key, session_id, path)
                        )
                    """)
        except Exception:
            logger.debug("Could not create exported_files table", exc_info=True)

    def _db_add_exported(self, thread_key: str, session_id: str, path: str) -> None:
        try:
            from sandbox_agent.clients import get_db_conninfo
            import psycopg
            with psycopg.connect(get_db_conninfo(), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO exported_files (thread_key, session_id, path) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (thread_key, session_id, path),
                    )
        except Exception:
            pass

    def _db_remove_exported_session(self, session_id: str) -> None:
        try:
            from sandbox_agent.clients import get_db_conninfo
            import psycopg
            with psycopg.connect(get_db_conninfo(), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM exported_files WHERE session_id = %s",
                        (session_id,),
                    )
        except Exception:
            pass

    def _db_remove_exported_thread(self, thread_key: str) -> None:
        try:
            from sandbox_agent.clients import get_db_conninfo
            import psycopg
            with psycopg.connect(get_db_conninfo(), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM exported_files WHERE thread_key = %s",
                        (thread_key,),
                    )
        except Exception:
            pass

    def _db_is_file_exported(self, thread_key: str, session_id: str, path: str) -> bool:
        try:
            from sandbox_agent.clients import get_db_conninfo
            import psycopg
            norm = str(Path(path).resolve())
            with psycopg.connect(get_db_conninfo(), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT path FROM exported_files WHERE thread_key = %s AND session_id = %s",
                        (thread_key, session_id),
                    )
                    rows = cur.fetchall()
            for (p,) in rows:
                if norm == p or norm.startswith(p.rstrip("/") + "/"):
                    return True
            return False
        except Exception:
            return False

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

    # ── Orphan Cleanup & GC ──────────────────────────────

    def _cleanup_orphaned_containers(self) -> None:
        """Remove sandbox containers left over from previous runs."""
        try:
            containers = self.client.containers.list(
                all=True,
                filters={"label": "sandbox-agent=true"},
            )
        except Exception:
            logger.warning("Failed to list orphaned containers", exc_info=True)
            return

        for container in containers:
            cid = container.short_id
            try:
                container.stop(timeout=3)
                container.remove(force=True)
                logger.info("Removed orphaned container %s", cid)
            except Exception:
                logger.warning("Failed to remove orphaned container %s", cid, exc_info=True)

    def _start_gc(self) -> None:
        """Launch a daemon thread that periodically reaps idle/expired sessions."""
        t = threading.Thread(target=self._gc_loop, daemon=True, name="sandbox-gc")
        t.start()

    def _gc_loop(self) -> None:
        settings = get_settings()
        while True:
            time.sleep(settings.GC_INTERVAL_SECONDS)
            try:
                self._gc_threads()
            except Exception:
                logger.exception("GC thread-based loop error")
            try:
                self._gc_session_hard_cap()
            except Exception:
                logger.exception("GC session hard-cap error")

    # ── Thread-based GC (primary) ─────────────────────

    def _gc_threads(self) -> None:
        """Evict threads by TTL and capacity, deleting from DB + killing containers."""
        try:
            from sandbox_agent.clients import get_db_conninfo
            import psycopg
        except Exception:
            logger.debug("DB not available for thread GC, skipping")
            return

        settings = get_settings()
        now = datetime.now(timezone.utc)

        try:
            with psycopg.connect(get_db_conninfo(), autocommit=False) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT thread_id, updated_at "
                        "FROM thread "
                        "WHERE status != 'busy' "
                        "ORDER BY updated_at DESC"
                    )
                    rows = cur.fetchall()
        except Exception:
            logger.debug("Failed to query threads for GC", exc_info=True)
            return

        threads_to_keep: list[str] = []
        threads_to_evict: list[tuple[str, str]] = []

        for thread_id, updated_at in rows:
            idle = (now - updated_at).total_seconds()

            if idle > settings.SESSION_IDLE_TTL_SECONDS:
                threads_to_evict.append((thread_id, f"idle for {idle:.0f}s"))
            elif len(threads_to_keep) < settings.MAX_ACTIVE_THREADS:
                threads_to_keep.append(thread_id)
            else:
                threads_to_evict.append(
                    (thread_id, f"exceeded max active threads ({settings.MAX_ACTIVE_THREADS})")
                )

        for thread_id, reason in threads_to_evict:
            logger.info("GC: evicting thread %s (%s)", thread_id[:12], reason)
            self.cleanup_thread_sessions(thread_id)
            self._delete_thread_from_db(thread_id)

    def _delete_thread_from_db(self, thread_id: str) -> None:
        """Delete a thread and all its checkpoint data from PostgreSQL."""
        try:
            from sandbox_agent.clients import get_db_conninfo
            import psycopg
        except Exception:
            return

        try:
            with psycopg.connect(get_db_conninfo(), autocommit=False) as conn:
                with conn.cursor() as cur:
                    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                        cur.execute(
                            f"DELETE FROM {table} WHERE thread_id = %s",  # noqa: S608
                            (thread_id,),
                        )
                    # CASCADE deletes runs automatically
                    cur.execute("DELETE FROM thread WHERE thread_id = %s", (thread_id,))
                conn.commit()
            logger.info("GC: deleted thread %s from DB", thread_id[:12])
        except Exception:
            logger.warning("GC: failed to delete thread %s from DB", thread_id[:12], exc_info=True)

    # ── Session hard-cap GC (safety net) ──────────────

    def _gc_session_hard_cap(self) -> None:
        """Kill any container that exceeds SESSION_MAX_LIFETIME_SECONDS."""
        settings = get_settings()
        now = datetime.now(timezone.utc)

        with self._lock:
            snapshot = list(self.sessions.items())

        for sid, info in snapshot:
            lifetime = (now - info.created_at).total_seconds()
            if lifetime > settings.SESSION_MAX_LIFETIME_SECONDS:
                logger.info("GC: hard-cap stop session %s (lifetime %.0fs)", sid, lifetime)
                self.stop_session(sid)

    def _touch_session(self, session_id: str) -> None:
        """Update the last_activity timestamp for a session."""
        info = self.sessions.get(session_id)
        if info:
            info.last_activity = datetime.now(timezone.utc)

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
        thread_id = current_thread_id.get(None)

        if runtime not in RUNTIME_CONFIG:
            raise ValueError(f"Runtime '{runtime}' not supported. Use: {list(RUNTIME_CONFIG)}")

        with self._lock:
            if len(self.sessions) >= settings.MAX_SESSIONS:
                raise RuntimeError(
                    f"Maximum number of sessions ({settings.MAX_SESSIONS}) reached. "
                    "Stop an existing session first."
                )

            if thread_id is not None:
                thread_count = len(self.thread_sessions.get(thread_id, set()))
                if thread_count >= settings.MAX_SESSIONS_PER_THREAD:
                    raise RuntimeError(
                        f"Maximum sessions per conversation ({settings.MAX_SESSIONS_PER_THREAD}) reached. "
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
            thread_id=thread_id,
            dependencies=dict(dependencies or {}),
        )

        with self._lock:
            self._containers[session_id] = container
            self.sessions[session_id] = info
            if thread_id is not None:
                self.thread_sessions.setdefault(thread_id, set()).add(session_id)

        self._wait_for_kernel(session_id)

        if dependencies:
            self._install_initial_packages(session_id, dependencies)

        info.status = "running"
        return info

    def execute_code(
        self, session_id: str, code: str, timeout: int | None = None
    ) -> ExecutionResult:
        self._check_session(session_id)
        self._touch_session(session_id)
        settings = get_settings()
        timeout = timeout or settings.EXECUTION_TIMEOUT_SECONDS

        try:
            resp = self._send_to_kernel(
                session_id,
                {"action": "execute", "code": code, "timeout": timeout},
                timeout=timeout + 10,
            )
        except TimeoutError:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                result=None,
                error={
                    "type": "TimeoutError",
                    "message": f"Execution timed out after {timeout}s",
                },
                figures=[],
            )

        if "stdout" in resp:
            resp["stdout"] = truncate_field(resp["stdout"], settings.MAX_STDOUT_CHARS)
        if "stderr" in resp:
            resp["stderr"] = truncate_field(resp["stderr"], settings.MAX_STDERR_CHARS)
        if "result" in resp and isinstance(resp["result"], dict):
            for key in list(resp["result"]):
                val = resp["result"][key]
                if isinstance(val, str):
                    resp["result"][key] = truncate_field(val, settings.MAX_RESULT_CHARS)

        return ExecutionResult(**resp)

    def execute_terminal(self, session_id: str, command: str) -> TerminalResult:
        self._check_session(session_id)
        self._touch_session(session_id)
        self._assert_container_alive(session_id)
        container = self._containers[session_id]
        settings = get_settings()

        exit_code, output = container.exec_run(
            ["sh", "-c", command],
            demux=True,
            workdir="/workspace",
            user="root" if settings.TERMINAL_ROOT else None,
        )

        stdout = (output[0] or b"").decode(errors="replace")
        stderr = (output[1] or b"").decode(errors="replace")

        return TerminalResult(
            exit_code=exit_code,
            stdout=truncate_field(stdout, settings.MAX_STDOUT_CHARS),
            stderr=truncate_field(stderr, settings.MAX_STDERR_CHARS),
        )

    def _install_initial_packages(self, session_id: str, packages: dict[str, str]) -> None:
        """Install packages during session creation. Runs as root so packages
        land in the system site-packages / global node_modules.
        Captures stdout/stderr into the SessionInfo so callers can inspect them."""
        info = self.sessions[session_id]
        config = RUNTIME_CONFIG[info.runtime]
        container = self._containers[session_id]

        if info.runtime == "python":
            specs = [f"{n}=={v}" if v else n for n, v in packages.items()]
        elif info.runtime in ("r", "julia"):
            specs = list(packages.keys())
        else:
            specs = [f"{n}@{v}" if v else n for n, v in packages.items()]

        user = config.get("install_user")
        exit_code, output = container.exec_run(
            config["install_cmd"](specs),
            demux=True,
            **({"user": user} if user else {}),
        )

        raw_stderr = (output[1] or b"").decode(errors="replace")
        info.stderr = truncate_field(raw_stderr, get_settings().MAX_STDERR_CHARS)

    def import_files(
        self,
        session_id: str,
        files: list[dict[str, str]],
    ) -> ImportResult:
        """Copy files or directories into the sandbox.

        Each entry can be:
        - Host file: ``{"source": "<host path>", "destination": "..."}``
        - Cross-session: ``{"session_id": "<src_session>", "path": "<container path>", "destination": "..."}``
          (file must have been exported from src_session in the same thread)

        Args:
            session_id: Active session ID (destination).
            files: List of file entries (see above).

        Returns:
            ImportResult with per-file status.
        """
        self._check_session(session_id)
        self._touch_session(session_id)
        self._assert_container_alive(session_id)
        info = self.sessions[session_id]
        thread_key = current_thread_id.get(None) or f"__mcp__{session_id}"

        results: list[ImportFileResult] = []
        all_ok = True

        for entry in files:
            src_session_id = entry.get("session_id")
            src_path = entry.get("path", "")
            src = entry.get("source", "")
            dst = entry.get("destination", "")

            if src_session_id:
                # Cross-session: copy from container src to container dst
                try:
                    src_path_norm = self._normalize_container_path(src_path)
                except ValueError as exc:
                    results.append(ImportFileResult(
                        source=f"{src_session_id}:{src_path}",
                        destination=dst or Path(src_path).name,
                        success=False,
                        error=str(exc),
                    ))
                    all_ok = False
                    continue

                if not dst:
                    dst = Path(src_path_norm).name
                remote_path = dst if dst.startswith("/") else f"/workspace/{dst}"

                # Validate src_session belongs to same thread
                src_info = self.sessions.get(src_session_id)
                if not src_info:
                    results.append(ImportFileResult(
                        source=f"{src_session_id}:{src_path_norm}",
                        destination=remote_path,
                        success=False,
                        error=f"Source session '{src_session_id}' not found",
                    ))
                    all_ok = False
                    continue

                src_thread = src_info.thread_id or f"__mcp__{src_session_id}"
                if src_thread != thread_key:
                    results.append(ImportFileResult(
                        source=f"{src_session_id}:{src_path_norm}",
                        destination=remote_path,
                        success=False,
                        error="Source session belongs to another conversation",
                    ))
                    all_ok = False
                    continue

                if not self.is_file_exported(thread_key, src_session_id, src_path_norm):
                    results.append(ImportFileResult(
                        source=f"{src_session_id}:{src_path_norm}",
                        destination=remote_path,
                        success=False,
                        error="File not exported from source session (use export_files first)",
                    ))
                    all_ok = False
                    continue

                try:
                    src_container = src_info.container_name
                    proc_tar = subprocess.Popen(
                        [
                            "docker", "exec", "-i", src_container,
                            "tar", "cf", "-", "-C",
                            str(Path(src_path_norm).parent),
                            Path(src_path_norm).name,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    proc_extract = subprocess.run(
                        [
                            "docker", "exec", "-i", info.container_name,
                            "tar", "xf", "-", "-C", "/workspace",
                        ],
                        stdin=proc_tar.stdout,
                        capture_output=True,
                        timeout=120,
                    )
                    proc_tar.wait()
                    if proc_tar.returncode != 0:
                        err = (proc_tar.stderr.read() or b"").decode(errors="replace")[:500]
                        results.append(ImportFileResult(
                            source=f"{src_session_id}:{src_path_norm}",
                            destination=remote_path,
                            success=False,
                            error=f"tar failed: {err}",
                        ))
                        all_ok = False
                        continue
                    if proc_extract.returncode != 0:
                        err = proc_extract.stderr.decode(errors="replace")[:500]
                        results.append(ImportFileResult(
                            source=f"{src_session_id}:{src_path_norm}",
                            destination=remote_path,
                            success=False,
                            error=f"tar extract failed: {err}",
                        ))
                        all_ok = False
                        continue

                    # Move to destination if it differs from extracted path
                    extracted_name = Path(src_path_norm).name
                    if remote_path != f"/workspace/{extracted_name}":
                        dest_parent = str(Path(remote_path).parent)
                        subprocess.run(
                            ["docker", "exec", "-i", info.container_name, "mkdir", "-p", dest_parent],
                            capture_output=True,
                            timeout=10,
                        )
                        subprocess.run(
                            [
                                "docker", "exec", "-i", info.container_name,
                                "mv", f"/workspace/{extracted_name}", remote_path,
                            ],
                            capture_output=True,
                            timeout=10,
                        )

                    size = 0
                    try:
                        proc_du = subprocess.run(
                            ["docker", "exec", "-i", info.container_name, "du", "-sb", remote_path],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if proc_du.returncode == 0:
                            parts = proc_du.stdout.strip().split()
                            if parts and parts[0].isdigit():
                                size = int(parts[0])
                    except Exception:
                        pass

                    results.append(ImportFileResult(
                        source=f"{src_session_id}:{src_path_norm}",
                        destination=remote_path,
                        success=True,
                        size=size,
                    ))
                except Exception as exc:
                    results.append(ImportFileResult(
                        source=f"{src_session_id}:{src_path_norm}",
                        destination=remote_path,
                        success=False,
                        error=f"{type(exc).__name__}: {exc}",
                    ))
                    all_ok = False
            else:
                # Host: copy from host path
                if not src:
                    results.append(ImportFileResult(
                        source=src, destination=dst, success=False,
                        error="source or (session_id, path) is required",
                    ))
                    all_ok = False
                    continue

                local_path = Path(src)
                if not local_path.exists():
                    results.append(ImportFileResult(
                        source=src, destination=dst, success=False,
                        error=f"File or directory not found: {local_path}",
                    ))
                    all_ok = False
                    continue

                if not dst:
                    dst = local_path.name

                remote_path = dst if dst.startswith("/") else f"/workspace/{dst}"

                try:
                    if local_path.is_file():
                        size = local_path.stat().st_size
                    else:
                        size = sum(f.stat().st_size for f in local_path.rglob("*") if f.is_file())

                    tar_buf = io.BytesIO()
                    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
                        tar.add(str(local_path), arcname=Path(remote_path).name)
                    tar_buf.seek(0)

                    dest_dir = str(Path(remote_path).parent)
                    proc = subprocess.run(
                        [
                            "docker", "exec", "-i", info.container_name,
                            "tar", "xf", "-", "-C", dest_dir,
                        ],
                        input=tar_buf.read(),
                        capture_output=True,
                        timeout=120,
                    )

                    if proc.returncode != 0:
                        err = proc.stderr.decode(errors="replace")[:500]
                        results.append(ImportFileResult(
                            source=src, destination=remote_path, success=False,
                            error=f"tar extract failed: {err}",
                        ))
                        all_ok = False
                        continue

                    results.append(ImportFileResult(
                        source=src, destination=remote_path,
                        success=True, size=size,
                    ))

                except Exception as exc:
                    results.append(ImportFileResult(
                        source=src, destination=remote_path, success=False,
                        error=f"{type(exc).__name__}: {exc}",
                    ))
                    all_ok = False

        return ImportResult(success=all_ok, files=results)

    def export_files(
        self,
        session_id: str,
        files: list[dict[str, str]],
    ) -> ExportResult:
        """Register files as "released" for download and cross-session import.

        Does NOT copy to host. Files become available via HTTP download endpoint
        and import_files (cross-session) while thread_id and session_id exist.

        Args:
            session_id: Active session ID.
            files: List of ``{"source": "<container path>", "destination": "..."}``.
                *source* is relative to ``/workspace/`` when not absolute.
                *destination* is ignored (kept for API compatibility).

        Returns:
            ExportResult with ExportFileResult(session_id, path, success, size, error).
            path is always absolute (e.g. /workspace/file.png).
        """
        self._check_session(session_id)
        self._touch_session(session_id)
        self._assert_container_alive(session_id)
        info = self.sessions[session_id]

        thread_key = current_thread_id.get(None) or f"__mcp__{session_id}"

        results: list[ExportFileResult] = []
        all_ok = True

        for entry in files:
            src = entry.get("source", "")

            if not src:
                results.append(ExportFileResult(
                    session_id=session_id, path="", success=False,
                    error="source is required",
                ))
                all_ok = False
                continue

            try:
                container_path = self._normalize_container_path(src)
            except ValueError as exc:
                results.append(ExportFileResult(
                    session_id=session_id, path=src, success=False,
                    error=str(exc),
                ))
                all_ok = False
                continue

            try:
                proc = subprocess.run(
                    ["docker", "exec", "-i", info.container_name, "test", "-e", container_path],
                    capture_output=True,
                    timeout=10,
                )

                if proc.returncode != 0:
                    results.append(ExportFileResult(
                        session_id=session_id, path=container_path, success=False,
                        error="File or directory not found in container",
                    ))
                    all_ok = False
                    continue

                size = 0
                try:
                    proc_size = subprocess.run(
                        ["docker", "exec", "-i", info.container_name, "du", "-sb", container_path],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if proc_size.returncode == 0:
                        parts = proc_size.stdout.strip().split()
                        if parts and parts[0].isdigit():
                            size = int(parts[0])
                except Exception:
                    pass

                with self._lock:
                    self._exported_files.setdefault(thread_key, {}).setdefault(
                        session_id, set()
                    ).add(container_path)
                self._db_add_exported(thread_key, session_id, container_path)

                results.append(ExportFileResult(
                    session_id=session_id, path=container_path,
                    success=True, size=size,
                ))

            except Exception as exc:
                results.append(ExportFileResult(
                    session_id=session_id, path=container_path, success=False,
                    error=f"{type(exc).__name__}: {exc}",
                ))
                all_ok = False

        return ExportResult(success=all_ok, files=results)

    def is_file_exported(self, thread_key: str, session_id: str, path: str) -> bool:
        """Check if (session_id, path) is released for download (DB + in-memory)."""
        if self._db_is_file_exported(thread_key, session_id, path):
            return True
        paths = self._exported_files.get(thread_key, {}).get(session_id, set())
        norm = str(Path(path).resolve())
        return any(
            norm == p or norm.startswith(p.rstrip("/") + "/")
            for p in paths
        )

    def is_exported_path_file(
        self,
        thread_id: str | None,
        session_id: str,
        path: str,
    ) -> bool:
        """Return True if path is a regular file (not directory) in the container."""
        if not self.is_file_exported(
            thread_id if thread_id else f"__mcp__{session_id}",
            session_id,
            path,
        ):
            return False
        container_path = path if path.startswith("/") else f"/workspace/{path}"
        container_path = self._normalize_container_path(container_path)
        container_name = (
            self.sessions[session_id].container_name
            if session_id in self._containers
            else f"sandbox-{session_id}"
        )
        return (
            subprocess.run(
                ["docker", "exec", "-i", container_name, "test", "-f", container_path],
                capture_output=True,
                timeout=10,
            ).returncode
            == 0
        )

    def stream_exported_file(
        self,
        thread_id: str | None,
        session_id: str,
        path: str,
    ) -> Generator[bytes, None, None]:
        """Stream bytes from file in container. Raises ValueError if not released."""
        key = thread_id if thread_id else f"__mcp__{session_id}"
        if not self.is_file_exported(key, session_id, path):
            raise ValueError(f"File not exported: {session_id}:{path}")
        container_path = path if path.startswith("/") else f"/workspace/{path}"
        container_path = self._normalize_container_path(container_path)

        container_name = (
            self.sessions[session_id].container_name
            if session_id in self._containers
            else f"sandbox-{session_id}"
        )

        if session_id in self._containers:
            self._check_session(session_id)

        # Single file: stream raw bytes. Directory: stream tar.
        is_file = subprocess.run(
            ["docker", "exec", "-i", container_name, "test", "-f", container_path],
            capture_output=True,
            timeout=10,
        ).returncode == 0

        if is_file:
            proc = subprocess.Popen(
                ["docker", "exec", "-i", container_name, "cat", container_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            proc = subprocess.Popen(
                [
                    "docker", "exec", "-i", container_name,
                    "tar", "cf", "-", "-C", str(Path(container_path).parent),
                    Path(container_path).name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        try:
            chunk_size = 65536
            while True:
                chunk = proc.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.wait()
            if proc.returncode != 0:
                err = (proc.stderr.read() or b"").decode(errors="replace")
                raise RuntimeError(f"docker exec failed: {err}")

    def stop_session(self, session_id: str) -> bool:
        with self._lock:
            container = self._containers.pop(session_id, None)
            info = self.sessions.pop(session_id, None)
            if info and info.thread_id:
                ts = self.thread_sessions.get(info.thread_id)
                if ts:
                    ts.discard(session_id)
                    if not ts:
                        del self.thread_sessions[info.thread_id]

            # Remove exported files registry for this session
            if info:
                thread_key = info.thread_id or f"__mcp__{session_id}"
                if thread_key in self._exported_files:
                    self._exported_files[thread_key].pop(session_id, None)
                    if not self._exported_files[thread_key]:
                        del self._exported_files[thread_key]
                self._db_remove_exported_session(session_id)

        if container is None:
            return False

        try:
            container.stop(timeout=3)
            container.remove(force=True)
        except Exception:
            pass

        return True

    def cleanup_thread_sessions(self, thread_id: str) -> int:
        """Stop all sessions belonging to a specific thread. Returns count stopped."""
        with self._lock:
            sids = list(self.thread_sessions.get(thread_id, set()))

        count = 0
        for sid in sids:
            if self.stop_session(sid):
                count += 1

        with self._lock:
            self._exported_files.pop(thread_id, None)
        self._db_remove_exported_thread(thread_id)

        # Remove STORAGE_DIR/thread_id/ (uploads + any session dirs)
        thread_dir = Path(get_settings().STORAGE_DIR) / thread_id
        if thread_dir.exists():
            try:
                shutil.rmtree(thread_dir, ignore_errors=True)
            except Exception:
                pass

        return count

    def cleanup_all(self) -> None:
        with self._lock:
            sids = list(self.sessions)
        for sid in sids:
            self.stop_session(sid)

    # ── Internal ───────────────────────────────────────

    def _normalize_container_path(self, path: str) -> str:
        """Resolve path and ensure it is inside /workspace (prevents path traversal)."""
        p = Path(path)
        if not p.is_absolute():
            p = Path("/workspace") / p
        resolved = p.resolve()
        workspace = Path("/workspace").resolve()
        if not str(resolved).startswith(str(workspace)):
            raise ValueError(f"Path outside /workspace: {path}")
        return str(resolved)

    def _check_session(self, session_id: str) -> None:
        if session_id not in self._containers:
            raise ValueError(f"Session '{session_id}' not found")

        thread_id = current_thread_id.get(None)
        if thread_id is not None:
            info = self.sessions.get(session_id)
            if info and info.thread_id and info.thread_id != thread_id:
                raise PermissionError(
                    f"Session '{session_id}' belongs to another conversation. "
                    "Use a session from your own conversation or create a new one."
                )

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
