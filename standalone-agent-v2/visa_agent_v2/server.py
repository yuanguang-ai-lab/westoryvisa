"""Independent HTTP server entry point for the V2 runtime."""

import signal
import re
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from visa_agent.service import (
    Handler,
    _interrupt_server_on_sigterm,
)

from .factory import build_fast_service
from .native_input import browser_scoped_input_readiness
from .preflight import require_job_preflight
from .settings import load_v2_config


class V2Handler(Handler):
    """Expose the legacy-compatible API with an unambiguous V2 identity."""

    service = None
    JOB_DIAGNOSTICS_PATH = re.compile(
        r"^/v1/jobs/(?P<job_id>agent-job-[A-Za-z0-9-]+)/diagnostics$"
    )

    def _browser_diagnostics(self, job_id):
        """Read the retained V2 page without moving or mutating it."""
        with self.service._runtime_lock:
            runtime = self.service._runtimes.get(job_id)
        if runtime is None or not runtime.is_available:
            raise ValueError("Browser runtime is not open")

        def capture(computer_use_agent):
            browser = computer_use_agent.browser
            page = getattr(browser, "_page", None)
            family_diagnostics = browser.model_fallback_diagnostics([
                "ceac.relatives.family.other_relatives_us",
            ])
            visible_controls = (
                page.evaluate(
                    """() => {
                        const visible = element => {
                            const style = getComputedStyle(element);
                            const box = element.getBoundingClientRect();
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && box.width > 0 && box.height > 0;
                        };
                        return Array.from(document.querySelectorAll(
                            'input, select, textarea'
                        )).filter(visible).slice(0, 100).map(element => {
                            const id = String(element.id || '');
                            const explicit = id
                                ? document.querySelector(
                                    `label[for="${CSS.escape(id)}"]`
                                )
                                : null;
                            const container = element.closest(
                                'tr, td, fieldset, section, div'
                            );
                            return {
                                tag: element.tagName.toLowerCase(),
                                type: String(
                                    element.getAttribute('type') || ''
                                ).toLowerCase(),
                                id,
                                name: String(element.name || ''),
                                value: String(element.value || ''),
                                checked: Boolean(element.checked),
                                label: String(
                                    explicit?.innerText
                                    || explicit?.textContent
                                    || ''
                                ).trim().slice(0, 180),
                                nearbyText: String(
                                    container?.innerText || ''
                                ).replace(/\\s+/g, ' ').trim().slice(0, 240),
                            };
                        });
                    }"""
                )
                if page is not None else []
            )
            all_control_states = (
                page.evaluate(
                    """() => Array.from(document.querySelectorAll(
                        'input, select, textarea'
                    )).slice(0, 180).map(element => {
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        const hiddenAncestor = element.closest(
                            '[hidden], [aria-hidden="true"]'
                        );
                        return {
                            tag: element.tagName.toLowerCase(),
                            type: String(
                                element.getAttribute('type') || ''
                            ).toLowerCase(),
                            id: String(element.id || ''),
                            name: String(element.name || ''),
                            value: String(element.value || ''),
                            checked: Boolean(element.checked),
                            disabled: Boolean(element.disabled),
                            display: String(style.display || ''),
                            visibility: String(style.visibility || ''),
                            width: Math.round(box.width),
                            height: Math.round(box.height),
                            hiddenAncestor: String(
                                hiddenAncestor?.id
                                || hiddenAncestor?.getAttribute('name')
                                || ''
                            ),
                        };
                    })"""
                )
                if page is not None else []
            )
            return {
                "url": str(getattr(page, "url", "") or ""),
                "title": page.title() if page is not None else "",
                "validationErrors": list(browser._validation_errors()),
                "familyDiagnostics": family_diagnostics,
                "visibleControls": visible_controls,
                "allControlStates": all_control_states,
                "usContactDiagnostics": {
                    "isPage": bool(browser._is_us_contact_page()),
                    "addressRendered": bool(
                        browser._us_contact_address_rendered()
                    ),
                    "reopenAttempted": bool(getattr(
                        browser,
                        "_v2_us_contact_reopen_attempted",
                        False,
                    )),
                    "relationship": browser._selected_option_snapshot(
                        browser._us_contact_relationship_control()
                    ),
                    "personNameState": (
                        browser._us_contact_person_name_state()
                    ),
                },
            }

        return runtime.try_call(capture, timeout=5)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            payload = dict(self.service.health())
            scoped_input = browser_scoped_input_readiness()
            payload.update({
                "service": "docflow-computer-use-v2",
                "version": "0.1.0-v2",
                "runtime": "computer-use-v2",
                "planningStrategy": "semantic-first",
                "interactionStyle": "visible",
                # The existing DocFlow readiness contract uses this field for
                # the browser interaction style. Planning ownership is
                # reported separately above.
                "computerUseExecution": "visual",
                "selectInputBackend": scoped_input["backend"],
                "selectInputReady": scoped_input["authorized"],
                "selectInputReason": scoped_input["reason"],
                # Compatibility keys remain present, but explicitly describe
                # that no native/global channel is used by V2.
                "nativeInputReady": False,
                "nativeInput": {
                    **scoped_input,
                    "authorized": False,
                    "reason": "OS-global input is disabled",
                },
                "globalInputDisabled": True,
            })
            if "ready" in payload:
                payload["ready"] = bool(
                    payload["ready"] and scoped_input["authorized"]
                )
            return self.json_response(payload)
        matched = self.JOB_DIAGNOSTICS_PATH.fullmatch(path)
        if matched:
            return self._call(
                self._browser_diagnostics,
                matched.group("job_id"),
            )
        return super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/v1/jobs":
            payload = self.read_json()
            try:
                require_job_preflight(payload)
            except ValueError as error:
                return self.json_response({"error": str(error)}, 400)
            return self._call(
                self.service.create_job,
                payload,
                status=201,
            )
        matched = self.JOB_PATH.fullmatch(path)
        if matched and matched.group("action") == "sync":
            payload = self.read_json()
            try:
                require_job_preflight(payload)
            except ValueError as error:
                return self.json_response({"error": str(error)}, 400)
            return self._call(
                self.service.sync_job,
                matched.group("job_id"),
                payload,
            )
        return super().do_POST()


def run_fast_server(host=None, port=None):
    config = load_v2_config()
    V2Handler.service = build_fast_service(config)
    server = ThreadingHTTPServer(
        (host or config.host, port or config.port),
        V2Handler,
    )
    previous_sigterm_handler = None
    install_sigterm_handler = bool(
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "SIGTERM")
    )
    if install_sigterm_handler:
        previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _interrupt_server_on_sigterm)
    try:
        V2Handler.service.recover_durable_continuous_runs()
        print(
            "DocFlow Computer Use V2 API: "
            f"http://{server.server_address[0]}:{server.server_port}"
        )
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            V2Handler.service.shutdown()
        finally:
            try:
                server.server_close()
            finally:
                if install_sigterm_handler:
                    signal.signal(
                        signal.SIGTERM,
                        previous_sigterm_handler,
                    )
