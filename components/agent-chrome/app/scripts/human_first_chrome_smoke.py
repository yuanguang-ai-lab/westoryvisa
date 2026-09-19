#!/usr/bin/env python3
"""Run the official-Chrome detach/attach experiment outside production.

With no ``--auto-confirm`` this script intentionally pauses for an operator to
finish the visible human steps before Playwright is imported or connected.
It never enters credentials, solves a challenge, navigates after attach, or
clicks a page control.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "standalone-agent-v2"))

from visa_agent_v2.chrome_process import ChromeProcessManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=(
            "data:text/html,<title>DocFlow human first smoke</title>"
            "<h1>Manual phase</h1>"
        ),
    )
    parser.add_argument("--chrome-executable", default="")
    parser.add_argument("--auto-confirm", action="store_true")
    arguments = parser.parse_args()

    if "playwright" in sys.modules:
        raise SystemExit("Playwright was imported before Chrome startup")

    with tempfile.TemporaryDirectory(prefix="docflow-human-first-smoke-") as root:
        profile = Path(root) / "agent-job-local-smoke"
        profile.mkdir(mode=0o700)
        manager = ChromeProcessManager(
            job_id="agent-job-local-smoke",
            profile_dir=profile,
            executable=arguments.chrome_executable or None,
            port_min=9450,
            port_max=9499,
        )
        try:
            info = manager.start(arguments.url)
            print(json.dumps({
                "phase": "WAITING_HUMAN_ENTRY",
                "pid": info.pid,
                "chromeAlive": manager.is_alive,
                "playwrightImported": "playwright" in sys.modules,
                "profileIsPrivate": info.profile_dir == profile.resolve(),
            }, ensure_ascii=False))
            if not arguments.auto_confirm:
                input(
                    "Complete the visible human steps, enter the formal page, "
                    "then press Enter to perform a read-only CDP attach: "
                )

            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            browser = None
            try:
                browser = playwright.chromium.connect_over_cdp(
                    manager.cdp_url,
                    timeout=18000,
                )
                pages = [
                    page
                    for context in browser.contexts
                    for page in context.pages
                    if not page.is_closed()
                ]
                print(json.dumps({
                    "phase": "ATTACHED_READ_ONLY",
                    "sameChromePid": manager.is_alive,
                    "existingPageCount": len(pages),
                    "urls": [str(page.url)[:160] for page in pages],
                    "titles": [str(page.title())[:160] for page in pages],
                }, ensure_ascii=False))
            finally:
                if browser is not None:
                    browser.close()
                playwright.stop()
        finally:
            manager.stop()


if __name__ == "__main__":
    main()
