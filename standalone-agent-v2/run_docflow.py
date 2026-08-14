"""Run DocFlow with the independent Computer Use V2 Agent Core."""

import os
import signal
import sys
import time
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = V2_ROOT.parent
LEGACY_AGENT_ROOT = PROJECT_ROOT / "standalone-agent"

from local_supervisor import (
    ManagedLocalService,
    available_port,
)


def require_exact_port(requested_port, label, *, excluded=None):
    """Refuse duplicate V2 stacks instead of silently shifting ports.

    A second launcher must never point another backend and Agent Core at the
    same durable job database.  Doing so can create competing recovery
    watchers and multiple dedicated browsers.  The requested V2 ports are a
    singleton contract, not suggestions.
    """
    resolved = available_port(
        int(requested_port),
        excluded=set(excluded or ()),
    )
    if resolved != int(requested_port):
        raise RuntimeError(
            f"{label}端口 {requested_port} 已被占用；V2 已拒绝启动"
            "第二套实例，不会自动改端口或重复打开浏览器。"
        )
    return resolved


def main():
    requested_frontend_port = (
        int(sys.argv[1]) if len(sys.argv) > 1 else 4175
    )
    frontend_port = require_exact_port(
        requested_frontend_port,
        "DocFlow V2 前端",
    )
    requested_backend_port = (
        int(sys.argv[2]) if len(sys.argv) > 2 else 4176
    )
    backend_port = require_exact_port(
        requested_backend_port,
        "DocFlow V2 后端",
        excluded={frontend_port},
    )
    requested_agent_port = (
        int(sys.argv[3]) if len(sys.argv) > 3 else 8766
    )
    agent_port = require_exact_port(
        requested_agent_port,
        "Agent Core V2",
        excluded={frontend_port, backend_port},
    )

    frontend_origin = f"http://127.0.0.1:{frontend_port}"
    api_base_url = f"http://127.0.0.1:{backend_port}/api"
    agent_url = f"http://127.0.0.1:{agent_port}"

    child_env = os.environ.copy()
    child_env["DOCFLOW_BACKEND_HOST"] = "127.0.0.1"
    configured_origins = {
        item.strip().rstrip("/")
        for item in child_env.get(
            "DOCFLOW_ALLOWED_ORIGINS",
            "",
        ).split(",")
        if item.strip()
    }
    configured_origins.add(frontend_origin)
    child_env["DOCFLOW_ALLOWED_ORIGINS"] = ",".join(
        sorted(configured_origins)
    )
    child_env["DOCFLOW_AGENT_URL"] = agent_url
    child_env["DS160_TRANSLATION_PROVIDER"] = "agent"
    child_env["DS160_TEXT_ANALYSIS_PROVIDER"] = "agent"

    agent_env = child_env.copy()
    existing_python_path = agent_env.get("PYTHONPATH", "")
    agent_env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(LEGACY_AGENT_ROOT),
        str(V2_ROOT),
        existing_python_path,
    )))
    agent_env.update({
        "AGENT_HOST": "127.0.0.1",
        "AGENT_PORT": str(agent_port),
        "AGENT_INTEGRATION_MODE": "docflow-local",
        "AGENT_COMPUTER_USE_EXECUTION": "visual",
        "AGENT_V2_DATA_DIR": str(
            PROJECT_ROOT / "data" / "standalone-agent-v2"
        ),
    })

    agent_python = LEGACY_AGENT_ROOT / ".venv" / "bin" / "python"
    if not agent_python.is_file():
        agent_python = PROJECT_ROOT / ".venv-docling" / "bin" / "python"
    if not agent_python.is_file():
        agent_python = Path(sys.executable)

    services = [
        ManagedLocalService(
            "Agent Core V2",
            [
                str(agent_python),
                "-m",
                "visa_agent_v2",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(agent_port),
            ],
            V2_ROOT,
            agent_env,
            f"{agent_url}/health",
        ),
        ManagedLocalService(
            "DocFlow 后端",
            [
                sys.executable,
                "-m",
                "backend.api_main",
                str(backend_port),
            ],
            PROJECT_ROOT,
            child_env,
            f"{api_base_url}/health",
        ),
        ManagedLocalService(
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
        ),
    ]

    def stop_children(_signum=None, _frame=None):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    try:
        for service in services:
            service.start()
        print(f"DocFlow V2 网页: {frontend_origin}", flush=True)
        print(f"DocFlow V2 后端: {api_base_url}", flush=True)
        print(
            f"Agent Core V2: {agent_url} "
            "(semantic-first planning, visible browser)",
            flush=True,
        )
        while True:
            for service in services:
                try:
                    service.ensure_healthy()
                except Exception as error:
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


if __name__ == "__main__":
    main()
