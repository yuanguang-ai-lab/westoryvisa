"""Job-owned official Chrome process for the human-first V2 runtime.

This module deliberately does not import Playwright.  During the human entry
phase it only owns an ordinary headed Chrome process and performs localhost
process/readiness checks; page inspection starts later in the browser adapter.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ChromeProcessError(RuntimeError):
    """A redacted Chrome lifecycle failure safe to show to an operator."""


@dataclass(frozen=True)
class ChromeProcessInfo:
    job_id: str
    pid: int
    profile_dir: Path
    executable: Path
    cdp_port: int
    started_at: float


class ChromeProcessManager:
    """Start and stop exactly one job-owned official Chrome Stable process."""

    START_TIMEOUT_SECONDS = 20.0
    METADATA_NAME = "docflow-human-first-chrome.json"
    JOB_ID_PATTERN = re.compile(r"agent-job-[A-Za-z0-9-]+")

    def __init__(
        self,
        *,
        job_id,
        profile_dir,
        executable=None,
        port_min=9300,
        port_max=9399,
        environ=None,
        popen_factory=None,
        monotonic=None,
        sleep=None,
    ):
        candidate_job_id = str(job_id or "")
        if not self.JOB_ID_PATTERN.fullmatch(candidate_job_id):
            raise ValueError("Invalid job id for Chrome process")
        profile = Path(profile_dir).expanduser().resolve()
        if profile.is_symlink() or not profile.is_dir():
            raise ValueError("Chrome profile must be an existing directory")
        self.job_id = candidate_job_id
        self.profile_dir = profile
        self.environ = dict(os.environ if environ is None else environ)
        self.executable = self._resolve_executable(executable)
        self.port_min = max(1024, int(port_min))
        self.port_max = min(65535, int(port_max))
        if self.port_min > self.port_max:
            raise ValueError("Invalid Chrome CDP port range")
        self._popen_factory = popen_factory or subprocess.Popen
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep
        self._process = None
        self._info = None

    def _resolve_executable(self, configured):
        raw_candidates = [
            configured,
            self.environ.get("DOCFLOW_CHROME_EXECUTABLE"),
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
            (
                "/Applications/Google Chrome.app/Contents/MacOS/"
                "Google Chrome"
            ),
        ]
        for raw_path in raw_candidates:
            if not str(raw_path or "").strip():
                continue
            path = Path(str(raw_path)).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return path.resolve()
        raise ChromeProcessError(
            "Official Google Chrome Stable is not installed at the configured path"
        )

    @property
    def info(self):
        return self._info

    @property
    def cdp_url(self):
        if self._info is None:
            return ""
        return f"http://127.0.0.1:{self._info.cdp_port}"

    @property
    def is_alive(self):
        return bool(
            self._process is not None
            and self._process.poll() is None
        )

    def _select_port(self):
        for port in range(self.port_min, self.port_max + 1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            finally:
                sock.close()
            return port
        raise ChromeProcessError("No private localhost Chrome debugging port is free")

    def _metadata_path(self):
        return self.profile_dir / self.METADATA_NAME

    def _write_metadata(self):
        info = self._info
        if info is None:
            return
        payload = {
            "version": 1,
            "jobId": info.job_id,
            "pid": info.pid,
            "profileDir": str(info.profile_dir),
            "executable": str(info.executable),
            "cdpPort": info.cdp_port,
            "startedAtEpoch": info.started_at,
        }
        path = self._metadata_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def _remove_metadata(self):
        try:
            self._metadata_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _version_payload(self):
        if not self.cdp_url:
            return {}
        request = urllib.request.Request(
            f"{self.cdp_url}/json/version",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            OSError,
            TimeoutError,
            UnicodeError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ):
            return {}
        websocket_url = str(payload.get("webSocketDebuggerUrl") or "")
        try:
            parsed = urlsplit(websocket_url)
            valid_endpoint = bool(
                parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.port == self._info.cdp_port
            )
        except ValueError:
            valid_endpoint = False
        if not valid_endpoint:
            return {}
        return payload

    def start(self, start_url):
        if self.is_alive:
            return self._info
        if self._process is not None:
            raise ChromeProcessError("The previous job-owned Chrome already exited")
        port = self._select_port()
        command = [
            str(self.executable),
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session",
            str(start_url),
        ]
        process_environment = dict(self.environ)
        display = str(process_environment.get("DISPLAY") or "").strip()
        if os.name != "nt" and not display and str(self.executable).startswith("/usr/"):
            raise ChromeProcessError("DISPLAY is required for headed Chrome")
        try:
            self._process = self._popen_factory(
                command,
                env=process_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            self._process = None
            raise ChromeProcessError(
                f"Official Chrome failed to start: {type(error).__name__}"
            ) from error
        self._info = ChromeProcessInfo(
            job_id=self.job_id,
            pid=int(self._process.pid),
            profile_dir=self.profile_dir,
            executable=self.executable,
            cdp_port=port,
            started_at=time.time(),
        )
        deadline = self._monotonic() + self.START_TIMEOUT_SECONDS
        while self._monotonic() < deadline:
            if not self.is_alive:
                self._remove_metadata()
                raise ChromeProcessError("Official Chrome exited during startup")
            if self._version_payload():
                self._write_metadata()
                return self._info
            self._sleep(0.1)
        self.stop()
        raise ChromeProcessError("Official Chrome debugging endpoint did not become ready")

    def runtime_info(self):
        info = self._info
        return {
            "jobId": self.job_id,
            "pid": int(info.pid) if info else 0,
            "profileDir": str(self.profile_dir),
            "executable": str(self.executable),
            "cdpHost": "127.0.0.1",
            "chromeAlive": self.is_alive,
            "cdpReady": bool(self.is_alive and self._version_payload()),
        }

    def stop(self, timeout=5.0):
        process = self._process
        if process is None:
            self._remove_metadata()
            return True
        if process.poll() is None:
            try:
                os.kill(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = self._monotonic() + max(0.1, float(timeout))
            while process.poll() is None and self._monotonic() < deadline:
                self._sleep(0.05)
            if process.poll() is None:
                try:
                    os.kill(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self._remove_metadata()
        return process.poll() is not None
