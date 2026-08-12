"""Run the separated frontend and backend as one local desktop experience."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def wait_for_backend(url, process, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Backend exited before becoming ready (exit {process.returncode})"
            )
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.1)
    raise RuntimeError(f"Backend did not become ready within {timeout} seconds")


def main():
    requested_frontend_port = int(sys.argv[1]) if len(sys.argv) > 1 else 4175
    frontend_port = available_port(requested_frontend_port)
    requested_backend_port = int(sys.argv[2]) if len(sys.argv) > 2 else 4176
    backend_port = available_port(
        requested_backend_port, excluded={frontend_port}
    )
    frontend_origin = f"http://127.0.0.1:{frontend_port}"
    api_base_url = f"http://127.0.0.1:{backend_port}/api"

    child_env = os.environ.copy()
    child_env["DOCFLOW_BACKEND_HOST"] = "127.0.0.1"
    configured_origins = {
        item.strip().rstrip("/")
        for item in child_env.get("DOCFLOW_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    configured_origins.add(frontend_origin)
    child_env["DOCFLOW_ALLOWED_ORIGINS"] = ",".join(sorted(configured_origins))

    backend = None
    frontend = None

    def stop_children(_signum=None, _frame=None):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    try:
        backend = subprocess.Popen(
            [sys.executable, "-m", "backend.api_main", str(backend_port)],
            cwd=PROJECT_ROOT,
            env=child_env,
            start_new_session=True,
        )
        wait_for_backend(f"{api_base_url}/health", backend)
        frontend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "frontend.dev_server",
                str(frontend_port),
                api_base_url,
            ],
            cwd=PROJECT_ROOT,
            env=child_env,
            start_new_session=True,
        )
        print(f"DocFlow webpage: {frontend_origin}", flush=True)
        print(f"DocFlow backend: {api_base_url}", flush=True)
        frontend.wait()
        if frontend.returncode:
            raise RuntimeError(
                f"Frontend exited unexpectedly (exit {frontend.returncode})"
            )
    except KeyboardInterrupt:
        pass
    finally:
        terminate(frontend)
        terminate(backend)


if __name__ == "__main__":
    main()
