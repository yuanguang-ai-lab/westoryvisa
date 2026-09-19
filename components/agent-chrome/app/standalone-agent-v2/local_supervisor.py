"""Run the separated frontend and backend as one local desktop experience."""

import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_ROOT = PROJECT_ROOT / "standalone-agent"
SUPERVISOR_LOCK_PATH = PROJECT_ROOT / "data" / "docflow-supervisor.lock"


class SupervisorLock:
    """Hold one process-wide lock for a DocFlow project directory."""

    def __init__(self, path=SUPERVISOR_LOCK_PATH):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.seek(0)
            try:
                existing = json.load(self.handle)
            except (json.JSONDecodeError, OSError):
                existing = {}
            self.handle.close()
            self.handle = None
            return existing
        return None

    def publish(self, **metadata):
        if self.handle is None:
            raise RuntimeError("Supervisor lock is not held")
        self.handle.seek(0)
        self.handle.truncate()
        json.dump(
            {"pid": os.getpid(), **metadata},
            self.handle,
            ensure_ascii=False,
        )
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def release(self):
        if self.handle is None:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def available_port(start, excluded=()):
    excluded = set(excluded)
    for port in range(start, start + 100):
        if port in excluded:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.05)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No local port available from {start} to {start + 99}")


def terminate(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def local_health_status(url, timeout=0.6):
    """Probe a loopback service directly, bypassing macOS proxy settings.

    ``urllib.request.urlopen`` honors system/PAC proxies.  A loopback health
    URL can consequently be emitted in proxy-style absolute form and rejected
    by the tiny frontend server, making a healthy process look offline.  The
    supervisor owns only local services, so a direct HTTP connection is the
    correct and deterministic transport.
    """
    parsed = urlsplit(str(url or ""))
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port is None
    ):
        raise ValueError("Health URL must be an explicit loopback HTTP URL")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection = HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=float(timeout),
    )
    try:
        connection.request(
            "GET",
            path,
            headers={"Connection": "close"},
        )
        response = connection.getresponse()
        response.read(64 * 1024)
        return int(response.status)
    finally:
        connection.close()


def wait_for_backend(url, process, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Backend exited before becoming ready (exit {process.returncode})"
            )
        try:
            if local_health_status(url, timeout=0.5) == 200:
                return
        except (OSError, ValueError):
            time.sleep(0.1)
    raise RuntimeError(f"Backend did not become ready within {timeout} seconds")


class ManagedLocalService:
    """Supervise one local child so a partial crash cannot look silently offline."""

    def __init__(
        self,
        label,
        command,
        cwd,
        env,
        health_url,
        failure_threshold=3,
    ):
        self.label = str(label)
        self.command = list(command)
        self.cwd = Path(cwd)
        self.env = dict(env)
        self.health_url = str(health_url)
        self.failure_threshold = max(1, int(failure_threshold))
        self.process = None
        self.health_failures = 0
        self.restart_count = 0

    def start(self):
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=self.env,
            start_new_session=True,
        )
        try:
            wait_for_backend(self.health_url, self.process)
        except Exception:
            terminate(self.process)
            self.process = None
            raise
        self.health_failures = 0
        return self.process

    def stop(self):
        terminate(self.process)
        self.process = None

    def ensure_healthy(self):
        exited = (
            self.process is None
            or self.process.poll() is not None
        )
        if not exited:
            try:
                if local_health_status(
                    self.health_url,
                    timeout=0.6,
                ) == 200:
                    self.health_failures = 0
                    return False
            except (OSError, ValueError):
                pass
            self.health_failures += 1
            if self.health_failures < self.failure_threshold:
                return False

        reason = (
            "进程已退出"
            if exited
            else f"连续 {self.health_failures} 次健康检查失败"
        )
        print(
            f"{self.label} {reason}，正在自动恢复；无需重新点击运行。",
            flush=True,
        )
        self.stop()
        self.start()
        self.restart_count += 1
        print(
            f"{self.label} 已恢复（第 {self.restart_count} 次自动重启）。",
            flush=True,
        )
        return True


def main():
    supervisor_lock = SupervisorLock()
    existing = supervisor_lock.acquire()
    if existing is not None:
        existing_url = str(existing.get("frontendUrl") or "")
        if existing_url:
            print(f"DocFlow 已在运行：{existing_url}", flush=True)
        else:
            print("DocFlow 已在运行，本次不再重复启动。", flush=True)
        return

    requested_frontend_port = int(sys.argv[1]) if len(sys.argv) > 1 else 4175
    frontend_port = available_port(requested_frontend_port)
    requested_backend_port = int(sys.argv[2]) if len(sys.argv) > 2 else 4176
    backend_port = available_port(
        requested_backend_port, excluded={frontend_port}
    )
    frontend_origin = f"http://127.0.0.1:{frontend_port}"
    api_base_url = f"http://127.0.0.1:{backend_port}/api"
    requested_agent_port = int(sys.argv[3]) if len(sys.argv) > 3 else 4267
    agent_port = available_port(
        requested_agent_port, excluded={frontend_port, backend_port}
    )
    agent_url = f"http://127.0.0.1:{agent_port}"
    supervisor_lock.publish(
        frontendUrl=frontend_origin,
        backendUrl=api_base_url,
        agentUrl=agent_url,
    )

    child_env = os.environ.copy()
    child_env["DOCFLOW_BACKEND_HOST"] = "127.0.0.1"
    configured_origins = {
        item.strip().rstrip("/")
        for item in child_env.get("DOCFLOW_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    configured_origins.add(frontend_origin)
    child_env["DOCFLOW_ALLOWED_ORIGINS"] = ",".join(sorted(configured_origins))
    child_env["DOCFLOW_AGENT_URL"] = agent_url
    child_env["DS160_TRANSLATION_PROVIDER"] = "agent"
    child_env["DS160_TEXT_ANALYSIS_PROVIDER"] = "agent"

    services = []

    def stop_children(_signum=None, _frame=None):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    try:
        agent_env = child_env.copy()
        existing_python_path = agent_env.get("PYTHONPATH", "")
        agent_env["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(AGENT_ROOT), existing_python_path,
        )))
        agent_env.update({
            "AGENT_HOST": "127.0.0.1",
            "AGENT_PORT": str(agent_port),
            "AGENT_DATA_DIR": str(PROJECT_ROOT / "data" / "standalone-agent"),
            "AGENT_INTEGRATION_MODE": "docflow-local",
            # Keep provider credentials in standalone-agent/.env. The complete
            # project must not silently clear Gemini and fall back to Codex.
            "AGENT_COMPUTER_USE_EXECUTION": (
                agent_env.get("AGENT_COMPUTER_USE_EXECUTION") or "visual"
            ),
        })
        agent_python = AGENT_ROOT / ".venv" / "bin" / "python"
        if not agent_python.is_file():
            agent_python = PROJECT_ROOT / ".venv-docling" / "bin" / "python"
        if not agent_python.is_file():
            agent_python = Path(sys.executable)
        agent_service = ManagedLocalService(
            "Agent Core",
            [
                str(agent_python), "-m", "visa_agent", "serve",
                "--host", "127.0.0.1", "--port", str(agent_port),
            ],
            AGENT_ROOT,
            agent_env,
            f"{agent_url}/health",
        )
        backend_service = ManagedLocalService(
            "DocFlow 后端",
            [sys.executable, "-m", "backend.api_main", str(backend_port)],
            PROJECT_ROOT,
            child_env,
            f"{api_base_url}/health",
        )
        frontend_service = ManagedLocalService(
            "DocFlow 前端",
            [
                sys.executable,
                "-m",
                "frontend.dev_server",
                str(frontend_port),
                api_base_url,
            ],
            PROJECT_ROOT,
            child_env,
            frontend_origin,
        )
        services = [agent_service, backend_service, frontend_service]
        for service in services:
            service.start()
        print(f"DocFlow webpage: {frontend_origin}", flush=True)
        print(f"DocFlow backend: {api_base_url}", flush=True)
        print(
            f"DocFlow Agent Core: {agent_url} "
            "(Computer Use: Gemini visual loop)",
            flush=True,
        )
        while True:
            for service in services:
                try:
                    service.ensure_healthy()
                except Exception as error:
                    # Keep the supervisor alive and visible. A later cycle
                    # retries the same fixed command and port.
                    print(
                        f"{service.label} 自动恢复暂未成功："
                        f"{type(error).__name__}；稍后继续重试。",
                        flush=True,
                    )
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        for service in reversed(services):
            service.stop()
        supervisor_lock.release()


if __name__ == "__main__":
    main()
