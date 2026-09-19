import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from visa_agent.config import AgentConfig, ProviderConfig, load_config
from visa_agent.models import JobState
from visa_agent.page_plans import CEACPageClassification
from visa_agent.service import AgentService, ServiceError
from visa_agent_v2.browser import HumanFirstFastVisibleChromeBrowser
from visa_agent_v2.chrome_process import ChromeProcessError, ChromeProcessManager

ROOT = Path(__file__).resolve().parents[2]


class FakeProcess:
    def __init__(self, pid=43210):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


class FakeNativeInput:
    name = "linux-x11-xtest"

    def require_authorized(self):
        return None


class FakeChromeManager:
    def __init__(self):
        self.is_alive = True
        self.cdp_url = "http://127.0.0.1:9342"
        self.started = []
        self.stop_calls = 0

    def start(self, url):
        self.started.append(url)

    def stop(self, timeout=5.0):
        self.stop_calls += 1
        self.is_alive = False
        return True

    def runtime_info(self):
        return {
            "jobId": "agent-job-test",
            "pid": 43210,
            "profileDir": "/private/profile",
            "executable": "/usr/bin/google-chrome-stable",
            "cdpHost": "127.0.0.1",
            "chromeAlive": self.is_alive,
            "cdpReady": self.is_alive,
        }


class FakePage:
    url = (
        "https://ceac.state.gov/GenNIV/General/complete/"
        "complete_personal.aspx?node=Personal1"
    )

    def __init__(self):
        self.goto_calls = []

    @staticmethod
    def is_closed():
        return False

    @staticmethod
    def set_default_timeout(_timeout):
        return None

    @staticmethod
    def set_default_navigation_timeout(_timeout):
        return None

    @staticmethod
    def bring_to_front():
        return None

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)


class FakeContext:
    def __init__(self, pages):
        self.pages = list(pages)
        self.init_scripts = []
        self.new_page_calls = 0

    @staticmethod
    def set_default_timeout(_timeout):
        return None

    @staticmethod
    def set_default_navigation_timeout(_timeout):
        return None

    def add_init_script(self, *, script):
        self.init_scripts.append(script)

    def new_page(self):
        self.new_page_calls += 1
        raise AssertionError("human-first attach must not create a page")

    @staticmethod
    def new_cdp_session(_page):
        raise RuntimeError("window normalization is optional in this fake")


class FakeAttachedBrowser:
    def __init__(self, contexts):
        self.contexts = list(contexts)
        self.closed = False

    def close(self):
        self.closed = True


class ChromeProcessManagerTests(unittest.TestCase):
    def test_launch_is_localhost_job_profile_owned_and_has_no_evasion_flags(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "google-chrome-stable"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            captured = {}
            process = FakeProcess()

            def popen(command, **kwargs):
                captured["command"] = list(command)
                captured["kwargs"] = dict(kwargs)
                return process

            manager = ChromeProcessManager(
                job_id="agent-job-test",
                profile_dir=profile,
                executable=executable,
                port_min=9342,
                port_max=9342,
                environ={"DISPLAY": ":99"},
                popen_factory=popen,
            )
            with (
                patch.object(manager, "_select_port", return_value=9342),
                patch.object(
                    manager,
                    "_version_payload",
                    return_value={"Browser": "Chrome"},
                ),
            ):
                info = manager.start("https://ceac.state.gov/GenNIV/Default.aspx")

            command = captured["command"]
            self.assertEqual(info.pid, process.pid)
            self.assertIn("--remote-debugging-address=127.0.0.1", command)
            self.assertIn("--remote-debugging-port=9342", command)
            self.assertIn(f"--user-data-dir={profile.resolve()}", command)
            joined = " ".join(command).casefold()
            self.assertNotIn("user-agent", joined)
            self.assertNotIn("disable-blink-features", joined)
            self.assertNotIn("automationcontrolled", joined)

    def test_stop_signals_only_the_exact_owned_pid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "google-chrome-stable"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            process = FakeProcess(pid=45678)
            manager = ChromeProcessManager(
                job_id="agent-job-test",
                profile_dir=profile,
                executable=executable,
                environ={"DISPLAY": ":99"},
                popen_factory=lambda *_args, **_kwargs: process,
            )
            manager._process = process
            signals = []

            def kill(pid, selected_signal):
                signals.append((pid, selected_signal))
                process.returncode = 0

            with patch("visa_agent_v2.chrome_process.os.kill", side_effect=kill):
                self.assertTrue(manager.stop())
            self.assertEqual([item[0] for item in signals], [45678])


class HumanFirstBrowserTests(unittest.TestCase):
    def _browser(self):
        with patch(
            "visa_agent_v2.browser.create_native_input",
            return_value=FakeNativeInput(),
        ):
            return HumanFirstFastVisibleChromeBrowser(
                ProviderConfig(provider="playwright", model="chromium")
            )

    def test_start_does_not_create_playwright_before_human_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            browser = self._browser()
            browser.set_profile_dir(Path(temp_dir) / "agent-job-test")
            browser.set_job_id("agent-job-test")
            fake_manager = FakeChromeManager()
            with patch(
                "visa_agent_v2.browser.ChromeProcessManager",
                return_value=fake_manager,
            ):
                info = browser.start(
                    "https://ceac.state.gov/GenNIV/Default.aspx"
                )
            self.assertIsNone(browser._playwright)
            self.assertIsNone(browser._context)
            self.assertIsNone(browser._page)
            self.assertEqual(info["phase"], "WAITING_HUMAN_ENTRY")
            self.assertFalse(info["executorAttached"])
            self.assertEqual(len(fake_manager.started), 1)
            browser.close()

    def test_attach_reuses_one_existing_formal_page_without_navigation(self):
        browser = self._browser()
        manager = FakeChromeManager()
        page = FakePage()
        context = FakeContext([page])
        attached = FakeAttachedBrowser([context])
        browser._chrome_process = manager
        browser._browser = attached
        browser.set_execution_mode("hybrid")
        with patch.object(
            browser,
            "_classify_live_page",
            return_value=CEACPageClassification(kind="formal", stage_score=100),
        ):
            info = browser.attach_existing_chrome()
        self.assertIs(browser._page, page)
        self.assertIs(browser._context, context)
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(context.new_page_calls, 0)
        self.assertTrue(info["executorAttached"])
        self.assertEqual(info["phase"], "RUNNING_AUTOFILL")
        browser.close()

    def test_rejected_attach_keeps_the_official_chrome_session_alive(self):
        browser = self._browser()
        manager = FakeChromeManager()
        page = FakePage()
        context = FakeContext([page])
        attached = FakeAttachedBrowser([context])
        browser._chrome_process = manager
        browser._browser = attached
        browser.set_execution_mode("hybrid")
        with patch.object(
            browser,
            "_classify_live_page",
            return_value=CEACPageClassification(kind="captcha"),
        ):
            with self.assertRaises(ChromeProcessError):
                browser.attach_existing_chrome()
        self.assertTrue(manager.is_alive)
        self.assertEqual(manager.stop_calls, 0)
        self.assertFalse(attached.closed)
        self.assertEqual(page.goto_calls, [])
        self.assertEqual(
            browser.runtime_info()["phase"],
            "WAITING_HUMAN_CHECKPOINT",
        )
        browser.close()

    def test_config_requires_both_human_first_switches(self):
        config = load_config({
            "DOCFLOW_BROWSER_RUNTIME": "chrome_stable_human_first",
            "DOCFLOW_HUMAN_FIRST_BOOTSTRAP": "true",
        })
        self.assertEqual(config.browser_runtime, "chrome_stable_human_first")
        self.assertTrue(config.human_first_bootstrap)


class HumanFirstServiceTests(unittest.TestCase):
    class Browser:
        def __init__(self):
            self.attached = False
            self.attach_calls = 0

        def attach_existing_chrome(self):
            self.attach_calls += 1
            self.attached = True
            return self.runtime_info()

        def runtime_info(self):
            return {
                "runtime": "chrome_stable_human_first",
                "humanFirst": True,
                "phase": (
                    "RUNNING_AUTOFILL"
                    if self.attached
                    else "WAITING_HUMAN_ENTRY"
                ),
                "chromeAlive": True,
                "executorAttached": self.attached,
            }

        @staticmethod
        def close():
            return None

    class Runtime:
        def __init__(self):
            self.browser = HumanFirstServiceTests.Browser()
            self.checkpoint_store = None

        @staticmethod
        def run(job):
            job.state = JobState.WAITING_HUMAN
            job.wait_kind = "manual_page_change"
            job.human_checkpoint = "manual checkpoint"
            return job

    @staticmethod
    def reviewed_job(service):
        created = service.create_job({
            "startUrl": "https://ceac.state.gov/GenNIV/Default.aspx",
            "requiredFieldIds": ["personal.surname"],
            "fields": [{
                "id": "personal.surname",
                "value": "ZHANG",
                "confidence": 1.0,
            }],
        })
        return service.review_job(created["id"], {
            "actor": "consultant-test",
            "decisions": [{
                "fieldId": "personal.surname",
                "approved": True,
                "value": "ZHANG",
            }],
        })

    def test_confirm_is_required_and_idempotent_before_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtimes = []

            def factory(_job):
                runtime = self.Runtime()
                runtimes.append(runtime)
                return runtime

            service = AgentService(
                AgentConfig(
                    data_dir=Path(temp_dir),
                    browser_runtime="chrome_stable_human_first",
                    human_first_bootstrap=True,
                ),
                runtime_factory=factory,
            )
            reviewed = self.reviewed_job(service)
            opened = service.open_job(reviewed["id"])
            self.assertEqual(opened["wait_kind"], "human_form_entry")
            with self.assertRaises(ServiceError):
                service.start_job(reviewed["id"])

            confirmed = service.confirm_form_entry(reviewed["id"])
            confirmed_again = service.confirm_form_entry(reviewed["id"])
            self.assertEqual(confirmed["state"], "ready_for_form")
            self.assertEqual(confirmed_again["state"], "ready_for_form")
            self.assertEqual(runtimes[0].browser.attach_calls, 1)

            resumed = service.start_job(reviewed["id"])
            self.assertEqual(resumed["state"], "waiting_human")
            event_kinds = [item["kind"] for item in confirmed["events"]]
            self.assertIn("official_chrome_started", event_kinds)
            self.assertIn("executor_attach_succeeded", event_kinds)
            service._release_runtime(reviewed["id"])


class DeploymentIsolationTests(unittest.TestCase):
    def test_pilot_has_a_separate_image_service_display_and_volume(self):
        compose = (ROOT / "deploy/docker-compose.chrome-pilot.yml").read_text(
            encoding="utf-8"
        )
        dockerfile = (ROOT / "deploy/agent-v2-chrome.Dockerfile").read_text(
            encoding="utf-8"
        )
        production = (ROOT / "deploy/docker-compose.production.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("agent-v2-chrome:", compose)
        self.assertIn("DISPLAY: :102", compose)
        self.assertIn("docflow_agent_v2_chrome_data", compose)
        self.assertIn("127.0.0.1:", compose)
        self.assertIn("chrome_stable_human_first", compose)
        self.assertNotIn("agent-v2-chrome:", production)
        self.assertIn("https://dl.google.com/linux/chrome/deb/", dockerfile)
        self.assertIn("google-chrome-stable --version", dockerfile)
        self.assertNotIn("playwright install", dockerfile)


if __name__ == "__main__":
    unittest.main()
