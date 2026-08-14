"""Visible Playwright driver with event-driven ASP.NET settling."""

import re
import time
from uuid import uuid4

from visa_agent.adapters import PlaywrightBrowserDriver
from visa_agent.models import ActionKind, ComputerAction

# The unreachable forensic implementation below formerly referenced this
# exception type.  It is now a plain local alias and cannot acquire any OS
# permission or post any system event.
NativeInputUnavailable = RuntimeError

class ControlPostbackTimeout(RuntimeError):
    """The controller request started, but its final outcome stayed unknown."""


class FastVisiblePlaywrightBrowser(PlaywrightBrowserDriver):
    """Retain visible actions without forcing model-first planning."""

    FALSE_POSTBACK_GRACE_SECONDS = 0.75
    UNKNOWN_POSTBACK_GRACE_SECONDS = 2.5
    DYNAMIC_SETTLE_TIMEOUT_SECONDS = 4.0
    CONTROL_POSTBACK_SETTLE_TIMEOUT_SECONDS = 12.0
    def __init__(self, config):
        super().__init__(config)
        self._v2_payer_reopen_attempted = False
        self._v2_travel_purpose_reopen_attempted = False
        self._v2_work_reopen_attempted = False
        self._v2_us_contact_reopen_attempted = False
        self._v2_forced_travel_purpose_field_ids = set()
        self._v2_forced_postback_field_ids = set()
        self._v2_forced_us_contact_relationship_ids = set()
        self._v2_forced_refresh_receipt_field_ids = set()
        self._v2_async_before = {
            "begun": 0,
            "ended": 0,
            "inflight": 0,
            "available": False,
        }
        self._v2_network_page = None
        self._v2_network_started = 0
        self._v2_network_ended = 0
        self._v2_network_inflight = set()
        self._v2_network_before = {
            "started": 0,
            "ended": 0,
            "inflight": 0,
            "available": False,
        }
        self._last_control_postback_diagnostic = {}

    def start(self, url):
        """Open V2 without acquiring any OS-global input permission."""
        return super().start(url)

    def clear_page_state(self):
        super().clear_page_state()
        self._v2_payer_reopen_attempted = False
        self._v2_travel_purpose_reopen_attempted = False
        self._v2_work_reopen_attempted = False
        self._v2_us_contact_reopen_attempted = False
        self._v2_forced_travel_purpose_field_ids.clear()
        self._v2_forced_postback_field_ids.clear()
        self._v2_forced_us_contact_relationship_ids.clear()
        self._v2_forced_refresh_receipt_field_ids.clear()
        self._last_control_postback_diagnostic = {}

    @staticmethod
    def interrupted_action_retry_safe(_action, error):
        """Never replay a controller while its first POST may still commit."""
        return not isinstance(error, ControlPostbackTimeout)

    def _apply_structured_field_value(self, locator, action):
        """Keep Travel's stay amount numeric and set its unit separately.

        Production CEAC places the amount input and unit select in sibling
        wrappers.  Their nearest common ancestor can also contain the arrival
        date controls, so the legacy generic composite detector deliberately
        rejects that broad container.  Falling through to ordinary TYPE then
        writes the approved composite value (for example ``7 DAY``) into the
        three-character amount input, which is rendered as ``7 D``.

        On the exact Travel field, bind the visible same-row duration pair and
        commit the numeric amount and reviewed unit independently.  If the
        pair cannot be proven, consume the action after a numeric-only repair;
        never allow the generic text path to type unit letters into this box.
        """
        field_id = str(action.field_id or "")
        if (
            self._is_travel_page()
            and field_id.casefold().endswith((
                ".travel.purpose.primary",
                ".travel.purpose.secondary",
            ))
        ):
            # Match the successful native Computer Use path: commit the exact
            # option and let CEAC's own input/change handler own validation,
            # postback target selection, and async rendering.
            if self._select_native_ceac_option(
                locator,
                action.value,
            ):
                return True
        if (
            self._is_travel_page()
            and field_id.casefold().endswith("travel.stayduration")
        ):
            parsed = self._travel_stay_duration_parts(action.value)
            if parsed is not None:
                return self._fill_travel_stay_duration(
                    locator,
                    action,
                    parsed,
                )
        return super()._apply_structured_field_value(locator, action)

    @classmethod
    def _travel_stay_duration_parts(cls, value):
        parsed = cls._parse_duration(value)
        if parsed is not None:
            return parsed
        # A stale checkpoint or model repair may abbreviate the already
        # structured value as 7D/7 D.  Accept abbreviations only for this exact
        # semantic duration field; ordinary text controls remain untouched.
        matched = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*([DWMY])\s*",
            str(value or ""),
            flags=re.IGNORECASE,
        )
        if not matched:
            return None
        units = {
            "D": "DAY",
            "W": "WEEK",
            "M": "MONTH",
            "Y": "YEAR",
        }
        return matched.group(1), units[matched.group(2).upper()]

    def constrain_action_value(self, action):
        """Do not truncate Travel's composite duration to the amount width.

        The live CEAC amount input declares ``maxlength=3``, but the reviewed
        field value is a composite such as ``7 DAY``.  The V2 writer splits
        that value between the numeric input and its sibling unit select.
        Running the generic text constraint first changed the approved value
        to ``7 D`` and made the later page-wide audit compare two different
        representations of the same visible value.
        """
        field_id = str(getattr(action, "field_id", "") or "")
        if (
            self._is_travel_page()
            and field_id.casefold().endswith("travel.stayduration")
            and self._travel_stay_duration_parts(
                getattr(action, "value", "")
            ) is not None
        ):
            return None
        return super().constrain_action_value(action)

    def _live_control_value(self, field_id, selector, timeout):
        """Read Travel stay amount and unit as one canonical live value.

        CEAC can remove the temporary attributes that joined the two controls
        after another field fires an ASP.NET update.  Falling back to the base
        input reader then exposes only ``7`` and falsely reopens approved
        ``7 DAY``.  Reconstruct this exact code-owned field from the visible
        same-row amount/unit pair on every read; genuine differences such as
        ``8 DAY`` remain visible to the workflow verifier.
        """
        requested = str(field_id or "")
        if (
            self._is_travel_page()
            and requested.casefold().endswith("travel.stayduration")
        ):
            try:
                controls = self._page.locator(selector)
                if controls.count():
                    snapshot = controls.first.evaluate(
                        """anchor => {
                            const visible = element => {
                                if (!element || element.disabled) return false;
                                const style = getComputedStyle(element);
                                const box = element.getBoundingClientRect();
                                return style.display !== 'none'
                                    && style.visibility !== 'hidden'
                                    && box.width > 0
                                    && box.height > 0;
                            };
                            const amountInput = element => {
                                if (
                                    !visible(element)
                                    || element.tagName.toLowerCase() !== 'input'
                                ) return false;
                                return ![
                                    'hidden', 'radio', 'checkbox', 'button',
                                    'submit', 'reset', 'file', 'image',
                                    'password'
                                ].includes(String(
                                    element.type || ''
                                ).toLowerCase());
                            };
                            const durationSelect = element => {
                                if (
                                    !visible(element)
                                    || element.tagName.toLowerCase() !== 'select'
                                ) return false;
                                const choices = Array.from(
                                    element.options || []
                                ).map(option => `${option.text || ''} ${
                                    option.value || ''
                                }`.toUpperCase()).join(' ');
                                return /(?:^|[^A-Z])(?:DAY|WEEK|MONTH|YEAR)/
                                    .test(choices);
                            };
                            const root = anchor.form || document;
                            const inputs = Array.from(
                                root.querySelectorAll('input')
                            ).filter(amountInput);
                            const selects = Array.from(
                                root.querySelectorAll('select')
                            ).filter(durationSelect);
                            const pairs = [];
                            for (const input of inputs) {
                                for (const select of selects) {
                                    if (anchor !== input && anchor !== select) {
                                        continue;
                                    }
                                    const inputBox = input.getBoundingClientRect();
                                    const selectBox = select.getBoundingClientRect();
                                    const vertical = Math.abs(
                                        (inputBox.top + inputBox.height / 2)
                                        - (selectBox.top + selectBox.height / 2)
                                    );
                                    const horizontal = Math.abs(
                                        (inputBox.left + inputBox.width / 2)
                                        - (selectBox.left + selectBox.width / 2)
                                    );
                                    if (vertical > 32 || horizontal > 720) {
                                        continue;
                                    }
                                    pairs.push({
                                        input,
                                        select,
                                        score: vertical * 20 + horizontal
                                            + (
                                                selectBox.left < inputBox.left
                                                    ? 120 : 0
                                            ),
                                    });
                                }
                            }
                            pairs.sort((left, right) => (
                                left.score - right.score
                            ));
                            if (!pairs.length) return null;
                            const pair = pairs[0];
                            return {
                                amount: String(pair.input.value || ''),
                                unitValue: String(pair.select.value || ''),
                                unitText: pair.select.selectedIndex >= 0
                                    ? String(pair.select.options[
                                        pair.select.selectedIndex
                                    ].text || '')
                                    : '',
                            };
                        }""",
                        timeout=min(max(int(timeout or 500), 100), 1000),
                    )
                else:
                    snapshot = None
            except Exception:
                snapshot = None
            if isinstance(snapshot, dict):
                amount = str(snapshot.get("amount") or "").strip()
                unit_candidate = " ".join(filter(None, (
                    str(snapshot.get("unitText") or "").strip(),
                    str(snapshot.get("unitValue") or "").strip(),
                ))).upper()
                unit_match = re.search(
                    r"(?<![A-Z])(DAY|DAYS|WEEK|WEEKS|MONTH|MONTHS|"
                    r"YEAR|YEARS)(?![A-Z])",
                    unit_candidate,
                )
                if unit_match is not None:
                    unit = unit_match.group(1).rstrip("S")
                else:
                    compact_unit = re.sub(r"[^A-Z]", "", unit_candidate)
                    unit = {
                        "D": "DAY",
                        "W": "WEEK",
                        "M": "MONTH",
                        "Y": "YEAR",
                    }.get(compact_unit, "")
                if re.fullmatch(r"\d+(?:\.\d+)?", amount) and unit:
                    return f"{self._normalize_number(amount)} {unit}"
        return super()._live_control_value(field_id, selector, timeout)

    def _fill_travel_stay_duration(self, locator, action, parsed_duration):
        amount, unit = parsed_duration
        token = f"v2-travel-duration-{uuid4().hex}"
        try:
            paired = bool(locator.evaluate(
                """(anchor, token) => {
                    const visible = element => {
                        if (!element || element.disabled) return false;
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const amountInput = element => {
                        if (!visible(element)) return false;
                        if (element.tagName.toLowerCase() !== 'input') {
                            return false;
                        }
                        return ![
                            'hidden', 'radio', 'checkbox', 'button',
                            'submit', 'reset', 'file', 'image', 'password'
                        ].includes(String(element.type || '').toLowerCase());
                    };
                    const durationSelect = element => {
                        if (
                            !visible(element)
                            || element.tagName.toLowerCase() !== 'select'
                        ) return false;
                        const choices = Array.from(element.options || [])
                            .map(option => `${option.text || ''} ${
                                option.value || ''
                            }`.toUpperCase())
                            .join(' ');
                        return /(?:^|[^A-Z])(DAY|WEEK|MONTH|YEAR)/
                            .test(choices);
                    };
                    const root = anchor.form || document;
                    const inputs = Array.from(
                        root.querySelectorAll('input')
                    ).filter(amountInput);
                    const selects = Array.from(
                        root.querySelectorAll('select')
                    ).filter(durationSelect);
                    let pairs = [];
                    for (const input of inputs) {
                        for (const select of selects) {
                            if (anchor !== input && anchor !== select) {
                                continue;
                            }
                            const inputBox = input.getBoundingClientRect();
                            const selectBox = select.getBoundingClientRect();
                            const vertical = Math.abs(
                                (inputBox.top + inputBox.height / 2)
                                - (selectBox.top + selectBox.height / 2)
                            );
                            const horizontal = Math.abs(
                                (inputBox.left + inputBox.width / 2)
                                - (selectBox.left + selectBox.width / 2)
                            );
                            if (vertical > 32 || horizontal > 720) continue;
                            const backwards = selectBox.left < inputBox.left
                                ? 120 : 0;
                            pairs.push({
                                input,
                                select,
                                score: vertical * 20 + horizontal + backwards,
                            });
                        }
                    }
                    pairs.sort((left, right) => left.score - right.score);
                    if (!pairs.length) return false;
                    const selected = pairs[0];
                    selected.input.setAttribute(
                        'data-docflow-duration-group', token
                    );
                    selected.input.setAttribute(
                        'data-docflow-v2-duration-part', 'amount'
                    );
                    selected.select.setAttribute(
                        'data-docflow-duration-group', token
                    );
                    selected.select.setAttribute(
                        'data-docflow-v2-duration-part', 'unit'
                    );
                    return true;
                }""",
                token,
                timeout=1000,
            ))
        except Exception:
            paired = False

        if not paired:
            # Failure-safe behavior: repair a proven input with digits only,
            # but do not cache the composite as verified without its unit.
            try:
                if self._visual_execution:
                    self._move_pointer_to_locator(locator, clicking=True)
                locator.evaluate(
                    """(el, amount) => {
                        if (
                            el.disabled || el.readOnly
                            || el.tagName.toLowerCase() !== 'input'
                        ) return false;
                        el.value = String(amount);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return /^\d+(?:\.\d+)?$/.test(String(el.value));
                    }""",
                    str(amount),
                    timeout=1000,
                )
            except Exception:
                pass
            return True

        group_selector = f'[data-docflow-duration-group="{token}"]'
        amount_target = self._page.locator(
            f'{group_selector}[data-docflow-v2-duration-part="amount"]'
        ).first
        unit_target = self._page.locator(
            f'{group_selector}[data-docflow-v2-duration-part="unit"]'
        ).first
        if self._visual_execution:
            self._move_pointer_to_locator(amount_target, clicking=True)
        try:
            amount_ok = bool(amount_target.evaluate(
                """(el, amount) => {
                    if (el.disabled || el.readOnly) return false;
                    el.value = String(amount);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return /^\d+(?:\.\d+)?$/.test(String(el.value))
                        && String(el.value) === String(amount);
                }""",
                str(amount),
                timeout=1000,
            ))
        except Exception:
            amount_ok = False
        if not amount_ok:
            return True

        try:
            current_unit = unit_target.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '') : ''
                })""",
                timeout=800,
            )
        except Exception:
            return True
        current_candidate = " ".join(filter(None, (
            str((current_unit or {}).get("text") or ""),
            str((current_unit or {}).get("value") or ""),
        )))
        if not self._choice_matches(unit, current_candidate):
            if self._visual_execution:
                self._move_pointer_to_locator(unit_target, clicking=True)
            if not self._select_approved_option(unit_target, unit):
                return True

        try:
            final = self._page.locator(group_selector).evaluate_all(
                """items => ({
                    amount: String(
                        items.find(item => (
                            item.getAttribute('data-docflow-v2-duration-part')
                            === 'amount'
                        ))?.value || ''
                    ),
                    unitValue: String(
                        items.find(item => (
                            item.getAttribute('data-docflow-v2-duration-part')
                            === 'unit'
                        ))?.value || ''
                    ),
                    unitText: (() => {
                        const select = items.find(item => (
                            item.getAttribute('data-docflow-v2-duration-part')
                            === 'unit'
                        ));
                        return select && select.selectedIndex >= 0
                            ? String(select.options[select.selectedIndex].text || '')
                            : '';
                    })(),
                })"""
            )
        except Exception:
            return True
        if (
            self._normalize_number(final.get("amount"))
            != self._normalize_number(amount)
            or not self._choice_matches(
                unit,
                f"{final.get('unitText', '')} {final.get('unitValue', '')}",
            )
        ):
            return True

        # Keep the field selector on the numeric input so later live
        # verification reconstructs the composite from the tagged pair.
        try:
            self._mark_field(amount_target, action)
        except Exception:
            return True
        self._verified_field_values[action.field_id] = action.value
        return True

    def _rebind_travel_stay_duration_for_revalidation(
        self,
        field_id,
        labels,
    ):
        """Reconstruct a replaced amount/unit pair without changing values.

        CEAC may preserve the visible controls while replacing the attributes
        that joined them during the original write.  A page-wide audit must
        rebuild that relationship before reading the field; otherwise the
        generic reader exposes only ``7`` and incorrectly compares it with the
        approved composite value ``7 DAY``.
        """
        approved = self._descriptor_approved_value(labels)
        parsed = self._travel_stay_duration_parts(approved)
        selector = self._field_selectors.get(str(field_id or ""))
        if parsed is None or not selector:
            return False
        locator = self._page.locator(selector).first
        token = f"v2-travel-duration-audit-{uuid4().hex}"
        try:
            snapshot = locator.evaluate(
                """(anchor, token) => {
                    const visible = element => {
                        if (!element || element.disabled) return false;
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const amountInput = element => {
                        if (
                            !visible(element)
                            || element.tagName.toLowerCase() !== 'input'
                        ) return false;
                        return ![
                            'hidden', 'radio', 'checkbox', 'button',
                            'submit', 'reset', 'file', 'image', 'password'
                        ].includes(String(element.type || '').toLowerCase());
                    };
                    const durationSelect = element => {
                        if (
                            !visible(element)
                            || element.tagName.toLowerCase() !== 'select'
                        ) return false;
                        const choices = Array.from(element.options || [])
                            .map(option => `${option.text || ''} ${
                                option.value || ''
                            }`.toUpperCase())
                            .join(' ');
                        return /(?:^|[^A-Z])(DAY|WEEK|MONTH|YEAR)/
                            .test(choices);
                    };
                    const root = anchor.form || document;
                    const inputs = Array.from(
                        root.querySelectorAll('input')
                    ).filter(amountInput);
                    const selects = Array.from(
                        root.querySelectorAll('select')
                    ).filter(durationSelect);
                    const pairs = [];
                    for (const input of inputs) {
                        for (const select of selects) {
                            if (anchor !== input && anchor !== select) {
                                continue;
                            }
                            const inputBox = input.getBoundingClientRect();
                            const selectBox = select.getBoundingClientRect();
                            const vertical = Math.abs(
                                (inputBox.top + inputBox.height / 2)
                                - (selectBox.top + selectBox.height / 2)
                            );
                            const horizontal = Math.abs(
                                (inputBox.left + inputBox.width / 2)
                                - (selectBox.left + selectBox.width / 2)
                            );
                            if (vertical > 32 || horizontal > 720) continue;
                            pairs.push({
                                input,
                                select,
                                score: vertical * 20 + horizontal
                                    + (selectBox.left < inputBox.left ? 120 : 0),
                            });
                        }
                    }
                    pairs.sort((left, right) => left.score - right.score);
                    if (!pairs.length) return null;
                    const selected = pairs[0];
                    selected.input.setAttribute(
                        'data-docflow-duration-group', token
                    );
                    selected.input.setAttribute(
                        'data-docflow-v2-duration-part', 'amount'
                    );
                    selected.select.setAttribute(
                        'data-docflow-duration-group', token
                    );
                    selected.select.setAttribute(
                        'data-docflow-v2-duration-part', 'unit'
                    );
                    return {
                        amount: String(selected.input.value || ''),
                        unitValue: String(selected.select.value || ''),
                        unitText: selected.select.selectedIndex >= 0
                            ? String(
                                selected.select.options[
                                    selected.select.selectedIndex
                                ].text || ''
                            ) : '',
                    };
                }""",
                token,
                timeout=1000,
            )
        except Exception:
            return False
        if not isinstance(snapshot, dict):
            return False
        expected_amount, expected_unit = parsed
        if (
            self._normalize_number(snapshot.get("amount"))
            != self._normalize_number(expected_amount)
            or not self._choice_matches(
                expected_unit,
                f"{snapshot.get('unitText', '')} "
                f"{snapshot.get('unitValue', '')}",
            )
        ):
            # The pair is authoritative but genuinely differs, so leave the
            # selector exposed for the ordinary one-time repair path.
            return True
        self._verified_field_values[str(field_id or "")] = approved
        return True

    def execute(self, action):
        """Replay one verified CEAC controller postback when its panel is absent.

        Chromium can restore the selected occupation after a retained-session
        navigation without firing the select's ASP.NET ``onchange`` handler.
        In that state the visible value is correct, but CEAC never renders the
        employer/school controls.  A normal select of the same option is a
        no-op, so reset through the code-owned placeholder and then reselect
        the reviewed value after proving the live option already matches it.
        """
        self._last_control_postback_diagnostic = {}
        field_id = str(action.field_id or "")
        if (
            action.kind == ActionKind.SELECT
            and field_id in self._v2_forced_travel_purpose_field_ids
        ):
            return self._execute_travel_purpose_branch_reset(action)
        if (
            action.kind == ActionKind.SELECT
            and field_id.casefold().endswith(
                ".travel.specific_plans"
            )
        ):
            return self._execute_travel_specific_plans(action)
        if (
            action.kind == ActionKind.SELECT
            and self._is_work_education2_page()
            and self._work_education2_choice_terms(field_id)
        ):
            return self._execute_work_education2_choice(action)
        if (
            action.kind == ActionKind.SELECT
            and self._is_us_contact_page()
            and field_id.casefold().endswith(
                ".us_contact.person.does_not_know"
            )
        ):
            return self._execute_us_contact_person_unknown(action)
        if (
            action.kind == ActionKind.SELECT
            and self._is_us_contact_page()
            and field_id.casefold().endswith(".us_contact.email")
            and self._boolean_choice(action.value) is True
        ):
            return self._execute_us_contact_email_dna(action)
        if (
            action.kind == ActionKind.SELECT
            and self._is_address_phone_page()
            and self._address_phone_exact_rule(field_id) is not None
            and self._address_phone_dna_requested(action.value)
        ):
            return self._execute_address_phone_dna(action)
        if (
            action.kind == ActionKind.SELECT
            and field_id in self._v2_forced_us_contact_relationship_ids
        ):
            return self._execute_us_contact_branch_reset(action)
        if (
            action.kind in {ActionKind.TYPE, ActionKind.SELECT}
            and field_id.casefold().endswith(".travel.stayduration")
        ):
            return self._execute_travel_stay_duration(action)
        if (
            action.kind == ActionKind.SELECT
            and field_id.casefold().endswith(".travel.payer")
        ):
            return self._execute_travel_payer_branch(action)
        if (
            action.kind != ActionKind.SELECT
            or field_id not in self._v2_forced_postback_field_ids
        ):
            return super().execute(action)

        locator = self._action_locator(action)
        try:
            selected = locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    value: String(el.value || ''),
                    text: (
                        el.selectedIndex >= 0 && el.options
                        ? String(el.options[el.selectedIndex].text || '')
                        : ''
                    ),
                    options: Array.from(el.options || []).map(
                        (option, index) => ({
                            index,
                            value: String(option.value || ''),
                            text: String(option.text || '').trim()
                        })
                    )
                })"""
            )
        except Exception:
            self._v2_forced_postback_field_ids.discard(field_id)
            return super().execute(action)
        candidate = " ".join(filter(None, (
            str((selected or {}).get("text") or ""),
            str((selected or {}).get("value") or ""),
        )))
        if (
            not isinstance(selected, dict)
            or selected.get("tag") != "select"
            or not self._choice_matches(action.value, candidate)
        ):
            # If the browser did not restore the approved value, use the
            # ordinary verified select path.  Never replay a stale option.
            self._v2_forced_postback_field_ids.discard(field_id)
            return super().execute(action)

        options = list(selected.get("options") or ())
        selected_index = next((
            int(item.get("index") or 0)
            for item in options
            if str(item.get("value") or "")
            == str(selected.get("value") or "")
        ), -1)
        placeholder = next((
            option for option in options
            if int(option.get("index") or 0) != selected_index
            and (
                not str(option.get("value") or "").strip()
                or "select one" in str(
                    option.get("text") or ""
                ).casefold()
            )
        ), None)
        reviewed = next((
            option for option in options
            if str(option.get("value") or "")
            == str(selected.get("value") or "")
        ), None)

        self._require_page()
        self._mark_field(locator, action)
        if placeholder is not None:
            # CEAC can restore BUSINESS in both the control and ViewState while
            # omitting the dependent panel. Reposting BUSINESS is then a
            # server-side no-op. Post the code-owned placeholder first and wait
            # for that response; selecting the approved occupation on the new
            # document now guarantees a real SelectedIndexChanged transition.
            self._begin_action_dom_watch()
            if not self._activate_select_option(locator, placeholder):
                raise RuntimeError(
                    "CEAC Primary Occupation placeholder could not be "
                    "selected with native input"
                )
            if self.dynamic_refresh_detected(action):
                self._wait_for_watched_dom_replacement()
            rebound = self._travel_semantic_control(
                ("Primary Occupation",),
                "select_text",
                section="",
                prefer_last=False,
            )
            if rebound is None:
                raise RuntimeError(
                    "CEAC Primary Occupation disappeared after branch reset"
                )
            locator = rebound
            self._mark_field(locator, action)

        self._begin_action_dom_watch()
        if placeholder is not None:
            if reviewed is None or not self._activate_select_option(
                locator, reviewed,
            ):
                raise RuntimeError(
                    "CEAC Primary Occupation could not be restored with "
                    "native input after reset"
                )
            if self.dynamic_refresh_detected(action):
                self._wait_for_watched_dom_replacement()
            panel = self._travel_semantic_control(
                ("Present Employer or School Name",),
                "text",
                section="",
                prefer_last=False,
            )
            if panel is None:
                # Some retained CEAC documents accept both postbacks but keep
                # rendering the stale conditional tree. A single ordinary GET
                # of the same formal page rebuilds that tree from the now
                # updated server ViewState/session without submitting or
                # advancing the application.
                self._page.reload(
                    wait_until="domcontentloaded",
                    timeout=self.NAVIGATION_TIMEOUT_MS,
                )
                self._configure_timeout_target(self._page)
                # ``reload`` replaces the select that was just posted.  Live
                # CEAC can correctly rebuild the employer panel from server
                # state while rendering Primary Occupation back at its
                # placeholder.  Keeping the old detached marker made the
                # workflow later revoke the already verified controller and
                # ask Gemini to locate it again.  Rebind the fresh select and
                # restore the reviewed option without another postback: the
                # server branch is already proven by the visible panel, and
                # the selected value must now be present in the eventual Next
                # form payload as well.
                rebound = self._travel_semantic_control(
                    ("Primary Occupation",),
                    "select_text",
                    section="",
                    prefer_last=False,
                )
                if rebound is None:
                    raise RuntimeError(
                        "CEAC Primary Occupation disappeared after Work reload"
                    )
                if reviewed is None or not self._activate_select_option(
                    rebound, reviewed,
                ):
                    raise RuntimeError(
                        "CEAC Primary Occupation could not be restored with "
                        "native input after Work reload"
                    )
                locator = rebound
                self._mark_field(locator, action)
                panel = self._travel_semantic_control(
                    ("Present Employer or School Name",),
                    "text",
                    section="",
                    prefer_last=False,
                )
                if panel is None:
                    raise RuntimeError(
                        "CEAC Work branch remained absent after one safe reload"
                    )
        elif not self._apply_structured_field_value(locator, action):
            if not self._select_approved_option(locator, action.value):
                raise RuntimeError(
                    "CEAC Primary Occupation option could not be selected "
                    "with native input"
                )
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)
        self._v2_forced_postback_field_ids.discard(field_id)
        self._v2_forced_refresh_receipt_field_ids.add(field_id)
        if self._visual_execution:
            self._select_best_page()
            self._configure_timeout_target(self._page)
            self._ensure_visible_cursor()

    def _us_contact_person_unknown_control(self):
        try:
            controls = self._page.locator(
                'input[type="checkbox"][id$="_cbxUS_POC_NAME_NA"]'
            )
            if controls.count() == 1 and controls.first.is_visible():
                return controls.first
        except Exception:
            pass
        return None

    def _us_contact_person_name_state(self):
        try:
            return dict(self._page.evaluate(
                """() => {
                    const checkbox = document.querySelector(
                        'input[type="checkbox"][id$="_cbxUS_POC_NAME_NA"]'
                    );
                    const surname = document.querySelector(
                        'input[id$="_tbxUS_POC_SURNAME"]'
                    );
                    const given = document.querySelector(
                        'input[id$="_tbxUS_POC_GIVEN_NAME"]'
                    );
                    const unavailable = element => Boolean(
                        element && (element.disabled || element.readOnly)
                    );
                    return {
                        checked: Boolean(checkbox?.checked),
                        surnameUnavailable: unavailable(surname),
                        givenUnavailable: unavailable(given),
                    };
                }"""
            ) or {})
        except Exception:
            return {}

    def _us_contact_person_toggle_consistent(self, desired):
        state = self._us_contact_person_name_state()
        if not state or bool(state.get("checked")) != bool(desired):
            return False
        unavailable = bool(
            state.get("surnameUnavailable")
            and state.get("givenUnavailable")
        )
        return unavailable if desired else not unavailable

    def _trusted_us_contact_checkbox_click(self, locator):
        if self._visual_execution:
            self._move_pointer_to_locator(locator, clicking=True)
        locator.click(timeout=3000)
        try:
            self._page.wait_for_timeout(180)
        except Exception:
            pass

    def _execute_us_contact_person_unknown(self, action):
        """Use Chromium's trusted click and prove CEAC's name lockout.

        The generic checkbox writer historically used ``HTMLElement.click``.
        CEAC can accept the resulting checked bit while skipping the page's
        own name-disable controller, leaving ViewState and the visible form in
        conflict.  A real Playwright input click plus the dependent-state
        assertion makes that impossible to acknowledge as complete.
        """
        desired = self._boolean_choice(action.value)
        locator = self._us_contact_person_unknown_control()
        if desired is None or locator is None:
            self.invalidate_field_binding(str(action.field_id or ""))
            return
        self._require_page()
        self._mark_field(locator, action)
        if not self._us_contact_person_toggle_consistent(desired):
            try:
                current = bool(locator.is_checked(timeout=1000))
            except Exception:
                current = not bool(desired)
            if current == bool(desired):
                self._trusted_us_contact_checkbox_click(locator)
                locator = self._us_contact_person_unknown_control()
                if locator is None:
                    self.invalidate_field_binding(str(action.field_id or ""))
                    return
            self._trusted_us_contact_checkbox_click(locator)
        if not self._us_contact_person_toggle_consistent(desired):
            self.invalidate_field_binding(str(action.field_id or ""))
            self._verified_field_values.pop(str(action.field_id or ""), None)
            return
        field_id = str(action.field_id or "")
        rebound = self._us_contact_person_unknown_control()
        if rebound is not None:
            self._mark_field(rebound, action)
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)

    def _us_contact_address_rendered(self):
        try:
            exact = self._page.locator(
                'input[id*="US_POC_ADDR_LN1" i], '
                'input[name*="US_POC_ADDR_LN1" i]'
            )
            if exact.count() and exact.first.is_visible():
                return True
        except Exception:
            pass
        return self._travel_semantic_control(
            ("U.S. Street Address (Line 1)", "Street Address (Line 1)"),
            "text",
            section="",
            prefer_last=False,
        ) is not None

    def _us_contact_relationship_control(self):
        try:
            controls = self._page.locator(
                'select[id$="_ddlUS_POC_REL_TO_APP"]'
            )
            if controls.count() == 1 and controls.first.is_visible():
                return controls.first
        except Exception:
            pass
        return self._travel_semantic_control(
            ("Relationship to You",),
            "select_text",
            section="",
            prefer_last=False,
        )

    def _us_contact_email_dna_control(self):
        try:
            controls = self._page.locator(
                'input[type="checkbox"][id$="_cbexUS_POC_EMAIL_ADDR_NA"]'
            )
            if controls.count() == 1 and controls.first.is_visible():
                return controls.first
        except Exception:
            pass
        return None

    def _us_contact_email_dna_state(self):
        try:
            return dict(self._page.evaluate(
                """() => {
                    const checkbox = document.querySelector(
                        'input[type="checkbox"]'
                        + '[id$="_cbexUS_POC_EMAIL_ADDR_NA"]'
                    );
                    const hidden = document.querySelector(
                        'input[type="hidden"]'
                        + '[id$="_tbxUS_POC_EMAIL_ADDR_NA"]'
                    );
                    const text = document.querySelector(
                        'input[id$="_tbxUS_POC_EMAIL_ADDR"]'
                    );
                    return {
                        found: Boolean(checkbox),
                        checked: Boolean(checkbox?.checked),
                        hiddenValue: String(hidden?.value || ''),
                        textDisabled: Boolean(text?.disabled || text?.readOnly),
                        textValue: String(text?.value || ''),
                    };
                }"""
            ) or {})
        except Exception:
            return {}

    @staticmethod
    def _us_contact_email_dna_consistent(state):
        return bool(
            state.get("found")
            and state.get("checked")
            and str(state.get("hiddenValue") or "").strip().casefold()
            in {"y", "yes", "true", "1", "on"}
            and state.get("textDisabled")
            and not str(state.get("textValue") or "").strip()
        )

    def _execute_us_contact_email_dna(self, action):
        """Select Contact Email D/N/A with a trusted click and exact proof."""
        field_id = str(action.field_id or "")
        locator = self._us_contact_email_dna_control()
        if locator is None:
            self.invalidate_field_binding(field_id)
            return
        self._require_page()
        self._mark_field(locator, action)
        state = self._us_contact_email_dna_state()
        if not self._us_contact_email_dna_consistent(state):
            if bool(state.get("checked")):
                self._trusted_us_contact_checkbox_click(locator)
                locator = self._us_contact_email_dna_control()
                if locator is None:
                    self.invalidate_field_binding(field_id)
                    return
            self._trusted_us_contact_checkbox_click(locator)
        if not self._us_contact_email_dna_consistent(
            self._us_contact_email_dna_state()
        ):
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            return
        rebound = self._us_contact_email_dna_control()
        if rebound is not None:
            self._mark_field(rebound, action)
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)

    @staticmethod
    def _canonical_us_contact_relationship(value):
        """Translate legacy intake institution labels to CEAC's enum."""
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        normalized = cleaned.casefold()
        if re.search(
            r"(?:\bhotel\b|\bhostel\b|\bmotel\b|\blodging\b|"
            r"\baccommodation\b|\bresort\b|酒店|旅馆|住宿)",
            normalized,
        ):
            return "OTHER"
        return cleaned

    def us_contact_relationship_matches_approved(self, field_id, approved):
        """Read the exact CEAC relationship using legacy-value equivalence."""
        if (
            not self._is_us_contact_page()
            or not str(field_id or "").casefold().endswith(
                ".us_contact.relationship"
            )
        ):
            return False
        relationship = self._us_contact_relationship_control()
        if relationship is None:
            return False
        snapshot = self._selected_option_snapshot(relationship)
        candidate = " ".join(filter(None, (
            str(snapshot.get("text") or ""),
            str(snapshot.get("value") or ""),
        )))
        return bool(candidate.strip() and self._choice_matches(
            self._canonical_us_contact_relationship(approved),
            candidate,
        ))

    @staticmethod
    def _selected_option_snapshot(locator):
        try:
            return dict(locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '').trim()
                        : '',
                    options: Array.from(el.options || []).map(
                        (option, index) => ({
                            index,
                            value: String(option.value || ''),
                            text: String(option.text || '').trim(),
                            disabled: Boolean(option.disabled)
                        })
                    )
                })"""
            ) or {})
        except Exception:
            return {}

    def _wait_for_us_contact_address(self):
        for delay_ms in (0, 160, 280, 450, 700, 1000):
            if delay_ms:
                try:
                    self._page.wait_for_timeout(delay_ms)
                except Exception:
                    return False
            if self._us_contact_address_rendered():
                return True
        return False

    def _prepare_us_contact_relationship_retry(self, action, reason):
        """Make an uncertain Relationship postback safe to re-verify.

        CEAC replaces the U.S. Contact address block through an ASP.NET
        postback.  If that request outlives the bounded network watcher, the
        selected relationship may be only local browser state or may already
        have committed on the server.  Reloading the same formal page makes
        the server response authoritative before the workflow is allowed to
        perform its existing bounded, idempotent field repair.
        """
        field_id = str(getattr(action, "field_id", "") or "")
        self._acknowledged = [
            item for item in self._acknowledged if item != action.id
        ]
        self._verified_field_values.pop(field_id, None)
        self._v2_forced_us_contact_relationship_ids.add(field_id)
        if not self._restore_fresh_ceac_page(reason):
            return False
        self._prune_detached_field_bindings()
        rebound = self._us_contact_relationship_control()
        if rebound is None:
            self.invalidate_field_binding(field_id)
            return True
        self._mark_field(rebound, action)
        return True

    def _execute_us_contact_branch_reset(self, action):
        """Replay the exact U.S. Contact controllers through trusted input."""
        field_id = str(action.field_id or "")
        relationship = self._us_contact_relationship_control()
        snapshot = (
            self._selected_option_snapshot(relationship)
            if relationship is not None else {}
        )
        options = list(snapshot.get("options") or ())
        reviewed = next((
            option for option in options
            if str(option.get("value") or "")
            == str(snapshot.get("value") or "")
            and str(option.get("value") or "").strip()
        ), None)
        if reviewed is None:
            desired = self._canonical_us_contact_relationship(action.value)
            reviewed = next((
                option for option in options
                if str(option.get("value") or "").strip()
                and self._choice_matches(
                    desired,
                    " ".join((
                        str(option.get("text") or ""),
                        str(option.get("value") or ""),
                    )),
                )
            ), None)
        placeholder = next((
            option for option in options
            if not str(option.get("value") or "").strip()
            or "select one" in str(option.get("text") or "").casefold()
        ), None)
        if relationship is None or reviewed is None or placeholder is None:
            self.invalidate_field_binding(field_id)
            return

        current_value = str(snapshot.get("value") or "").strip()
        current_candidate = " ".join(filter(None, (
            str(snapshot.get("text") or ""),
            current_value,
        )))
        if (
            current_value
            and self._us_contact_address_rendered()
            and self._choice_matches(
                self._canonical_us_contact_relationship(action.value),
                current_candidate,
            )
        ):
            # The reviewed relationship and its server-owned address branch
            # already prove that CEAC accepted the selection. Replaying the
            # select through its placeholder here can start a redundant POST
            # whose network bookkeeping never reaches idle even though the
            # visible branch is complete, producing a false hard boundary.
            self._mark_field(relationship, action)
            self._verified_field_values[field_id] = str(
                snapshot.get("text") or action.value
            ).strip()
            self._acknowledged.append(action.id)
            self._v2_forced_us_contact_relationship_ids.discard(field_id)
            self._v2_forced_refresh_receipt_field_ids.add(field_id)
            return

        checkbox = self._us_contact_person_unknown_control()
        if checkbox is not None:
            try:
                desired_unknown = bool(checkbox.is_checked(timeout=1000))
            except Exception:
                desired_unknown = False
            if not self._us_contact_person_toggle_consistent(desired_unknown):
                self._trusted_us_contact_checkbox_click(checkbox)
                checkbox = self._us_contact_person_unknown_control()
                if checkbox is None:
                    self.invalidate_field_binding(field_id)
                    return
                self._trusted_us_contact_checkbox_click(checkbox)
            if not self._us_contact_person_toggle_consistent(desired_unknown):
                self.invalidate_field_binding(field_id)
                return

        self._require_page()
        if current_value:
            self._mark_field(relationship, action)
            self._begin_action_dom_watch()
            if not self._activate_select_option(relationship, placeholder):
                self.invalidate_field_binding(field_id)
                return
            try:
                placeholder_posted = self._ensure_travel_control_postback(
                    relationship,
                    action,
                    require_dependent=False,
                )
            except ControlPostbackTimeout:
                if self._prepare_us_contact_relationship_retry(
                    action,
                    "us-contact-relationship-reset-timeout",
                ):
                    raise RuntimeError(
                        "CEAC U.S. Contact Relationship reset POST timed "
                        "out; the same page was safely reloaded for bounded "
                        "re-verification"
                    ) from None
                self.invalidate_field_binding(field_id)
                raise
            if not placeholder_posted:
                self.invalidate_field_binding(field_id)
                return
            relationship = self._us_contact_relationship_control()
            if relationship is None:
                self.invalidate_field_binding(field_id)
                return
        self._mark_field(relationship, action)
        self._begin_action_dom_watch()
        if not self._activate_select_option(relationship, reviewed):
            self.invalidate_field_binding(field_id)
            return
        try:
            reviewed_posted = self._ensure_travel_control_postback(
                relationship,
                action,
                require_dependent=True,
                dependent_probe=self._us_contact_address_rendered,
            )
        except ControlPostbackTimeout:
            if self._prepare_us_contact_relationship_retry(
                action,
                "us-contact-relationship-postback-timeout",
            ):
                raise RuntimeError(
                    "CEAC U.S. Contact Relationship POST timed out; the "
                    "same page was safely reloaded for bounded "
                    "re-verification"
                ) from None
            self.invalidate_field_binding(field_id)
            raise
        if not reviewed_posted:
            self.invalidate_field_binding(field_id)
            return
        if not self._wait_for_us_contact_address():
            self._page.reload(
                wait_until="domcontentloaded",
                timeout=self.NAVIGATION_TIMEOUT_MS,
            )
            self._configure_timeout_target(self._page)
        if not self._wait_for_us_contact_address():
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            return
        rebound = self._us_contact_relationship_control()
        if rebound is None:
            self.invalidate_field_binding(field_id)
            return
        self._mark_field(rebound, action)
        self._verified_field_values[field_id] = str(
            reviewed.get("text") or action.value
        ).strip()
        self._acknowledged.append(action.id)
        self._v2_forced_us_contact_relationship_ids.discard(field_id)
        self._v2_forced_refresh_receipt_field_ids.add(field_id)

    def action_postcondition(self, action):
        """Require Travel primary to materialize its dependent select.

        A value-only verifier cannot distinguish a genuine CEAC WebForms
        postback from a select whose rendered text changed locally.  For the
        Travel primary purpose, the reviewed value is complete only when the
        exact dependent ``Specify visa class`` control exists in the live DOM.
        """
        field_id = str(getattr(action, "field_id", "") or "")
        if (
            field_id.casefold().endswith(".travel.stayduration")
            and getattr(action, "kind", None) in {
                ActionKind.TYPE, ActionKind.SELECT,
            }
        ):
            if self._travel_us_address_rendered():
                return True, ""
            return (
                False,
                "Travel 停留时长虽然显示了已审核值，"
                "但 CEAC 未生成美国住址区域；该组合控件"
                "不会被记为完成。",
            )
        if getattr(action, "kind", None) != ActionKind.SELECT:
            return True, ""
        if field_id.casefold().endswith(".travel.specific_plans"):
            if not self._is_travel_page():
                return (
                    False,
                    "Travel 行程计划选择后页面状态不可确认，"
                    "系统未将该单选题记为完成。",
                )
            if not self._travel_specific_plans_branch_rendered(action.value):
                return (
                    False,
                    "Travel 行程计划虽然显示了已审核选项，"
                    "但 CEAC 未生成对应的日期、停留时长和"
                    "美国住址区域；该单选题不会被记为完成。",
                )
            return True, ""
        if field_id.casefold().endswith(
            ".work.education_secondary_or_above"
        ):
            if not self._is_work_education2_page():
                return (
                    False,
                    "Work/Education 2 教育经历选择后页面状态"
                    "不可确认，该字段不会被记为完成。",
                )
            if self._boolean_choice(action.value) is not True:
                return True, ""
            if self._work_education2_school_rendered():
                return True, ""
            return (
                False,
                "Work/Education 2 虽然显示了“受过中学以上"
                "教育”，但 CEAC 尚未生成 Name of Institution "
                "学校区域；该控制器不会被记为完成。",
            )
        if not field_id.casefold().endswith(".travel.purpose.primary"):
            return True, ""
        if not self._is_travel_page():
            return (
                False,
                "Travel 主用途选择后页面状态不可确认，系统未将一级框记为完成。",
            )
        secondary = self._travel_purpose_control(
            "secondary",
            ("Specify", "Specify visa class"),
        )
        if secondary is None:
            return (
                False,
                "Travel 主用途虽然显示了已审核值，但 CEAC 未生成必填的 "
                "Specify visa class 二级框；一级框不会被记为完成。",
            )
        return True, ""

    def action_postcondition_requires_hard_boundary(self, action):
        """Stop a failed Travel controller once instead of refill-looping."""
        field_id = str(getattr(action, "field_id", "") or "")
        if (
            field_id.casefold().endswith(".travel.stayduration")
            and getattr(action, "kind", None) in {
                ActionKind.TYPE, ActionKind.SELECT,
            }
        ):
            return not self._travel_us_address_rendered()
        if getattr(action, "kind", None) != ActionKind.SELECT:
            return False
        if field_id.casefold().endswith(".travel.specific_plans"):
            return not self._travel_specific_plans_branch_rendered(
                action.value
            )
        if field_id.casefold().endswith(
            ".work.education_secondary_or_above"
        ):
            return bool(
                self._boolean_choice(action.value) is True
                and not self._work_education2_school_rendered()
            )
        return bool(
            field_id.casefold().endswith(".travel.purpose.primary")
            and self._travel_purpose_control(
                "secondary",
                ("Specify", "Specify visa class"),
            ) is None
        )

    def _execute_travel_purpose_branch_reset(self, action):
        """Force one real CEAC value transition for a stale Travel branch.

        A restored ASP.NET document can show the reviewed primary purpose B
        while omitting its dependent Specify select.  Posting B again is a
        server-side no-op because ViewState already contains B.  Move through
        CEAC's own placeholder first, wait for that postback, then post the
        reviewed B option and require the dependent select to exist before the
        action is acknowledged.  This is bounded to one repair per page.
        """
        field_id = str(action.field_id or "")
        locator = self._action_locator(action)
        try:
            selected = locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    value: String(el.value || ''),
                    text: (
                        el.selectedIndex >= 0 && el.options
                        ? String(el.options[el.selectedIndex].text || '')
                        : ''
                    ),
                    options: Array.from(el.options || []).map(
                        (option, index) => ({
                            index,
                            value: String(option.value || ''),
                            text: String(option.text || '').trim()
                        })
                    )
                })"""
            )
        except Exception as error:
            raise RuntimeError(
                "CEAC Travel purpose control disappeared before branch reset"
            ) from error
        if not isinstance(selected, dict) or selected.get("tag") != "select":
            raise RuntimeError(
                "CEAC Travel purpose branch reset requires a select control"
            )
        options = list(selected.get("options") or ())
        placeholder = next((
            option for option in options
            if (
                not str(option.get("value") or "").strip()
                or "select one" in str(
                    option.get("text") or ""
                ).casefold()
            )
        ), None)
        reviewed = next((
            option for option in options
            if self._choice_matches(
                action.value,
                f"{option.get('text', '')} {option.get('value', '')}",
            )
        ), None)
        if placeholder is None or reviewed is None:
            raise RuntimeError(
                "CEAC Travel purpose placeholder/reviewed option unavailable"
            )

        self._require_page()
        self._mark_field(locator, action)
        current_candidate = " ".join(filter(None, (
            str(selected.get("text") or ""),
            str(selected.get("value") or ""),
        )))
        current_matches = self._choice_matches(
            action.value,
            current_candidate,
        )
        if (
            current_matches
            and self._travel_purpose_control(
                "secondary",
                ("Specify", "Specify visa class"),
            ) is not None
        ):
            # A retained page can expose both hierarchy levels before this
            # action is replayed.  Exact value plus exact dependent control is
            # already the complete postcondition; avoid another postback.
            self._mark_field(locator, action)
            self._verified_field_values[field_id] = action.value
            self._acknowledged.append(action.id)
            self._v2_forced_travel_purpose_field_ids.discard(field_id)
            return
        if current_matches:
            # The primary already displays the reviewed value but its
            # dependent Specify control is missing.  Only this stale-branch
            # repair needs the controlled placeholder -> reviewed transition.
            locator = self._post_travel_purpose_value(
                locator,
                action,
                str(placeholder.get("value") or ""),
                require_primary=True,
            )
        # On a fresh Travel page the primary visibly starts at CEAC's
        # ``PLEASE SELECT A VISA CLASS`` placeholder.  Post the reviewed
        # primary option once; do not first post the placeholder again.
        self._post_travel_purpose_value(
            locator,
            action,
            str(reviewed.get("value") or ""),
            require_primary=False,
        )

        secondary = self._wait_for_travel_purpose_secondary()
        if secondary is None:
            # A displayed B value is not success without its dependent
            # control.  Leave the action unacknowledged so the workflow's
            # browser postcondition stops once at a resumable hard boundary.
            self._v2_travel_purpose_reopen_attempted = True
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            self._v2_forced_travel_purpose_field_ids.discard(field_id)
            return

        rebound = self._travel_purpose_control(
            "primary",
            ("Purpose of Trip to the U.S.",),
        )
        if rebound is None:
            if secondary is None:
                raise RuntimeError(
                    "CEAC Travel primary disappeared during branch refresh"
                )
            # Production CEAC may replace the primary purpose select with the
            # dependent visa-class select instead of rendering both controls.
            # The exact secondary selector above proves the reviewed primary
            # branch.  Do not write the primary B value into the B1/B2 select.
            self.invalidate_field_binding(field_id)
            self._verified_field_values[field_id] = action.value
            self._acknowledged.append(action.id)
            self._v2_forced_travel_purpose_field_ids.discard(field_id)
            self._v2_forced_refresh_receipt_field_ids.add(field_id)
            return
        current = rebound.evaluate(
            """el => ({
                value: String(el.value || ''),
                text: (
                    el.selectedIndex >= 0 && el.options
                    ? String(el.options[el.selectedIndex].text || '')
                    : ''
                )
            })"""
        )
        current_candidate = " ".join(filter(None, (
            str((current or {}).get("text") or ""),
            str((current or {}).get("value") or ""),
        )))
        if not self._choice_matches(action.value, current_candidate):
            raise RuntimeError(
                "CEAC Travel purpose server response did not retain the "
                "reviewed primary option"
            )
        self._mark_field(rebound, action)
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)
        self._v2_forced_travel_purpose_field_ids.discard(field_id)
        self._v2_forced_refresh_receipt_field_ids.add(field_id)
        if self._visual_execution:
            self._select_best_page()
            self._configure_timeout_target(self._page)
            self._ensure_visible_cursor()

    def _wait_for_travel_purpose_secondary(self):
        """Wait for the exact second-level Travel select after postback."""
        secondary = None
        for delay_ms in (0, 150, 250, 400, 650, 1000, 1500):
            if delay_ms:
                try:
                    self._page.wait_for_timeout(delay_ms)
                except Exception:
                    break
            secondary = self._travel_purpose_control(
                "secondary",
                ("Specify", "Specify visa class"),
            )
            if secondary is not None:
                break
        return secondary

    def _post_travel_purpose_value(
        self,
        locator,
        action,
        value,
        *,
        require_primary,
    ):
        """Select through CEAC's own change handler and rebind the primary."""
        self._begin_action_dom_watch()
        if self._visual_execution:
            # Keep the pointer visible over the exact system-owned target.
            # The helper below validates the live option before scheduling the
            # select's own ASP.NET postback and never presses the default Next.
            self._move_pointer_to_locator(locator, clicking=False)
        if not self._select_native_ceac_option(
            locator,
            str(value or ""),
            exact_value=True,
        ):
            raise RuntimeError(
                "CEAC Travel purpose option postback could not be sent: "
                f"{getattr(self, '_last_native_select_failure', '') or 'unknown'}"
            )
        try:
            self._page.wait_for_timeout(150)
            audit = locator.evaluate(
                "el => el.__docflowScopedSelectAudit || null",
                timeout=500,
            )
        except Exception:
            audit = None
        self._last_native_select_audit = audit
        if isinstance(audit, dict) and int(audit.get("change") or 0) < 1:
            raise RuntimeError(
                "Page-scoped select event audit did not prove CEAC postback "
                "wiring: "
                f"change={int(audit.get('change') or 0)},"
                f"changeTrusted={audit.get('changeTrusted')},"
                f"input={int(audit.get('input') or 0)},"
                f"inputTrusted={audit.get('inputTrusted')},"
                f"hasInlineChange={audit.get('hasInlineChange')},"
                f"onchange={str(audit.get('onchangeAttribute') or '')[:100]}"
            )
        try:
            self._ensure_travel_control_postback(
                locator,
                action,
                require_dependent=not require_primary,
                dependent_probe=self._travel_purpose_secondary_rendered,
            )
        except ControlPostbackTimeout:
            # Preserve the exact visible value and retained page. Reloading
            # here erased a correct primary selection when CEAC omitted its
            # dependent control, making Continue repeat the same work.
            self._last_control_postback_diagnostic[
                "retainedPageAfterUnknownPostback"
            ] = True
            raise
        try:
            self._page.wait_for_load_state(
                "domcontentloaded", timeout=5000,
            )
        except Exception:
            pass
        try:
            self._page.wait_for_timeout(250)
        except Exception:
            pass
        self._prune_detached_field_bindings()
        rebound = self._travel_purpose_control(
            "primary",
            ("Purpose of Trip to the U.S.",),
        )
        if rebound is None:
            if (
                not require_primary
                and self._travel_purpose_control(
                    "secondary",
                    ("Specify", "Specify visa class"),
                ) is not None
            ):
                return None
            raise RuntimeError(
                "CEAC Travel purpose did not return after postback"
            )
        self._mark_field(rebound, action)
        return rebound

    def _travel_purpose_secondary_rendered(self):
        return self._travel_purpose_control(
            "secondary",
            ("Specify", "Specify visa class"),
        ) is not None

    def _travel_specific_plans_branch_rendered(self, value):
        """Prove CEAC processed the selected travel-plans radio.

        A checked radio is only local browser state.  For ``No``, CEAC's
        authoritative server response contains all three downstream regions:
        intended arrival, intended stay, and the U.S. stay address.  Requiring
        the address region as well prevents the earlier partial-refresh false
        positive where only the upper controls appeared.
        """
        if self._boolean_choice(value) is False:
            arrival = self._travel_semantic_control(
                ("Intended Date of Arrival",),
                "date",
                section="",
                prefer_last=False,
            )
            stay = self._travel_semantic_control(
                (
                    "Intended Length of Stay in U.S.",
                    "Intended Length of Stay",
                ),
                "duration",
                section="",
                prefer_last=False,
            )
            return arrival is not None and stay is not None

        # The positive branch uses detailed itinerary controls instead of the
        # estimated arrival/stay pair.  The U.S. address is a later branch and
        # must not be required while acknowledging this immediate controller.
        for terms in (
            ("Arrival Flight",),
            ("Arrival City",),
            ("Departure Flight",),
            ("Date of Departure", "Departure Date"),
        ):
            if self._travel_semantic_control(
                terms,
                "text",
                section="",
                prefer_last=False,
            ) is not None:
                return True
        return False

    def _travel_us_address_rendered(self):
        return self._travel_us_address_control_by_order(
            "ceac.travel.travel.usstreet1"
        ) is not None

    def _work_education2_school_rendered(self):
        """Prove the education Yes postback produced its required panel."""
        rule = self._work_education2_semantic_rule(
            "ceac.work_education2.work.education.record.school.probe"
        )
        if rule is None:
            return False
        for delay_ms in (0, 120, 180, 250, 350):
            if delay_ms:
                try:
                    self._page.wait_for_timeout(delay_ms)
                except Exception:
                    return False
            if self._work_education2_semantic_control(rule, "text") is not None:
                return True
        return False

    def _travel_specific_plans_choice_control(self, locator, approved_value):
        """Return the exact Yes/No radio, never merely the group leader."""
        token = f"v2-specific-plans-{uuid4().hex}"
        try:
            count = int(locator.evaluate(
                """(el, token) => {
                    if (
                        el.tagName.toLowerCase() !== 'input'
                        || String(el.type || '').toLowerCase() !== 'radio'
                        || !String(el.name || '')
                    ) return 0;
                    const scope = el.form || document;
                    const radios = Array.from(scope.querySelectorAll(
                        'input[type="radio"][name]'
                    )).filter(item => String(item.name) === String(el.name));
                    radios.forEach(item => item.setAttribute(
                        'data-docflow-v2-specific-plans', token
                    ));
                    return radios.length;
                }""",
                token,
                timeout=1000,
            ))
        except Exception:
            return None
        radios = self._page.locator(
            f'[data-docflow-v2-specific-plans="{token}"]'
        )
        for index in range(min(count, 6)):
            item = radios.nth(index)
            try:
                details = item.evaluate(
                    """el => ({
                        value: String(el.value || ''),
                        label: Array.from(el.labels || []).map(
                            label => String(label.innerText || '')
                        ).join(' '),
                        nearby: String(el.parentElement?.innerText || '')
                    })""",
                    timeout=800,
                )
            except Exception:
                continue
            candidate = " ".join((
                str(details.get("label") or ""),
                str(details.get("value") or ""),
                str(details.get("nearby") or ""),
            ))
            if self._choice_matches(approved_value, candidate):
                return item
        return None

    def _wait_for_travel_specific_plans_branch(self, value):
        for delay_ms in (0, 150, 250, 400, 650, 1000, 1500):
            if delay_ms:
                try:
                    self._page.wait_for_timeout(delay_ms)
                except Exception:
                    return False
            if self._travel_specific_plans_branch_rendered(value):
                return True
        return False

    def _execute_travel_specific_plans(self, action):
        """Click the exact radio and require its complete CEAC response."""
        field_id = str(action.field_id or "")
        group = self._action_locator(action)
        target = self._travel_specific_plans_choice_control(
            group, action.value
        )
        if target is None:
            self.invalidate_field_binding(field_id)
            return

        try:
            already_checked = bool(target.is_checked(timeout=1000))
        except Exception:
            already_checked = False
        if already_checked and self._travel_specific_plans_branch_rendered(
            action.value
        ):
            self._mark_field(target, action)
            self._verified_field_values[field_id] = action.value
            self._acknowledged.append(action.id)
            return

        self._require_page()
        self._mark_field(target, action)
        self._begin_action_dom_watch()
        if not already_checked:
            if self._visual_execution:
                self._move_pointer_to_locator(target, clicking=True)
            # Playwright's input pipeline produces a trusted click/change;
            # unlike HTMLElement.click(), this is the same browser path as a
            # user click and permits CEAC's AutoPostBack handler to run.
            target.click(timeout=3000)

        try:
            posted = self._ensure_travel_control_postback(
                target,
                action,
                require_dependent=True,
                dependent_probe=lambda: (
                    self._travel_specific_plans_branch_rendered(action.value)
                ),
            )
        except ControlPostbackTimeout:
            self._restore_fresh_travel_page(
                "specific-plans-postback-timeout"
            )
            raise
        if not posted or not self._wait_for_travel_specific_plans_branch(
            action.value
        ):
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            self._restore_fresh_travel_page(
                "specific-plans-dependent-branch-missing"
            )
            return

        self._prune_detached_field_bindings()
        rebound_group = self._prompt_scoped_choice_group(
            self._travel_choice_terms(field_id)
        )
        rebound = (
            self._travel_specific_plans_choice_control(
                rebound_group, action.value
            )
            if rebound_group is not None else None
        )
        if rebound is None or not rebound.is_checked(timeout=1000):
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            return
        self._mark_field(rebound, action)
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)
        self._v2_forced_refresh_receipt_field_ids.add(field_id)

    def _execute_work_education2_choice(self, action):
        """Require CEAC to receive each Work/Education 2 radio postback.

        A trusted click can leave ``rblOtherEduc`` visibly checked while its
        WebForms callback never starts. Value verification alone would then
        accept a local-only state with no school panel. Reuse the bounded
        exact-control postback path and require the Name of Institution panel
        for the reviewed education Yes answer.
        """
        field_id = str(action.field_id or "")
        group = self._action_locator(action)
        target = self._travel_specific_plans_choice_control(
            group,
            action.value,
        )
        if target is None:
            self.invalidate_field_binding(field_id)
            return

        requires_school = bool(
            field_id.casefold().endswith(
                ".work.education_secondary_or_above"
            )
            and self._boolean_choice(action.value) is True
        )
        try:
            already_checked = bool(target.is_checked(timeout=1000))
        except Exception:
            already_checked = False
        if (
            already_checked
            and (
                not requires_school
                or self._work_education2_school_rendered()
            )
        ):
            self._mark_field(target, action)
            self._verified_field_values[field_id] = action.value
            self._acknowledged.append(action.id)
            return

        self._require_page()
        self._mark_field(target, action)
        self._begin_action_dom_watch()
        if not already_checked:
            if self._visual_execution:
                self._move_pointer_to_locator(target, clicking=True)
            target.click(timeout=3000)

        try:
            posted = self._ensure_travel_control_postback(
                target,
                action,
                require_dependent=requires_school,
                dependent_probe=(
                    self._work_education2_school_rendered
                    if requires_school else None
                ),
            )
        except ControlPostbackTimeout:
            self.invalidate_field_binding(field_id)
            raise
        if not posted or (
            requires_school
            and not self._work_education2_school_rendered()
        ):
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            return

        self._prune_detached_field_bindings()
        rebound_group = self._prompt_scoped_choice_group(
            self._work_education2_choice_terms(field_id)
        )
        rebound = (
            self._travel_specific_plans_choice_control(
                rebound_group,
                action.value,
            )
            if rebound_group is not None else None
        )
        if rebound is None or not rebound.is_checked(timeout=1000):
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            return
        self._mark_field(rebound, action)
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)
        self._v2_forced_refresh_receipt_field_ids.add(field_id)

    def _travel_stay_duration_unit_control(self, anchor):
        """Resolve the exact unit select paired with the duration amount."""
        tagged = self._page.locator(
            '[data-docflow-v2-duration-part="unit"]'
        )
        try:
            visible = [
                tagged.nth(index)
                for index in range(min(tagged.count(), 8))
                if tagged.nth(index).is_visible()
            ]
        except Exception:
            visible = []
        if len(visible) == 1:
            return visible[0]

        token = f"v2-travel-duration-unit-{uuid4().hex}"
        try:
            found = bool(anchor.evaluate(
                """(anchor, token) => {
                    const visible = element => {
                        if (!element || element.disabled) return false;
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const candidates = Array.from(document.querySelectorAll(
                        'select'
                    )).filter(select => {
                        if (!visible(select)) return false;
                        const choices = Array.from(select.options || [])
                            .map(option => `${option.text || ''} ${
                                option.value || ''
                            }`.toUpperCase()).join(' ');
                        if (!/(?:^|[^A-Z])(DAY|WEEK|MONTH|YEAR)/
                            .test(choices)) return false;
                        const a = anchor.getBoundingClientRect();
                        const b = select.getBoundingClientRect();
                        return Math.abs(
                            (a.top + a.height / 2) - (b.top + b.height / 2)
                        ) <= 32 && Math.abs(
                            (a.left + a.width / 2) - (b.left + b.width / 2)
                        ) <= 720;
                    });
                    if (candidates.length !== 1) return false;
                    candidates[0].setAttribute(
                        'data-docflow-v2-duration-postback', token
                    );
                    return true;
                }""",
                token,
                timeout=1000,
            ))
        except Exception:
            found = False
        if not found:
            return None
        locator = self._page.locator(
            f'[data-docflow-v2-duration-postback="{token}"]'
        )
        return locator.first if locator.count() == 1 else None

    def _wait_for_travel_us_address(self):
        for delay_ms in (0, 150, 250, 400, 650, 1000, 1500):
            if delay_ms:
                try:
                    self._page.wait_for_timeout(delay_ms)
                except Exception:
                    return False
            if self._travel_us_address_rendered():
                return True
        return False

    def _prepare_travel_stay_duration_retry(self, action, reason):
        """Turn an uncertain duration POST into a proven safe replan.

        A slow CEAC document POST can outlive the bounded network watcher.
        Replaying the unit select while that response is unresolved would be
        unsafe.  Reload the same formal Travel URL first: the GET both
        detaches the pending response from the visible document and exposes
        the server's final selected value/branch.  The generic workflow may
        then verify success or perform its already-bounded idempotent field
        replan from this fresh page.
        """
        field_id = str(getattr(action, "field_id", "") or "")
        self._acknowledged = [
            item for item in self._acknowledged if item != action.id
        ]
        self._verified_field_values.pop(field_id, None)
        if not self._restore_fresh_travel_page(reason):
            return False
        self._prune_detached_field_bindings()
        rebound = self._travel_semantic_control(
            (
                "Intended Length of Stay in U.S.",
                "Intended Length of Stay",
            ),
            "duration",
            section="",
            prefer_last=False,
        )
        if rebound is None:
            self.invalidate_field_binding(field_id)
            # The reload itself is the safety proof.  A missing immediate
            # semantic rebind is not a reason to resurrect the original
            # unknown outcome: the workflow will discard the stale binding
            # and plan the visible field again from this fresh document.
            return True
        self._mark_field(rebound, action)
        return True

    def _execute_travel_stay_duration(self, action):
        """Fill amount/unit, then require the address branch from CEAC."""
        field_id = str(action.field_id or "")
        # If an earlier run wrote the reviewed unit locally but missed its
        # dependent panel, choosing the same unit again cannot fire a real
        # change.  Reset through CEAC's own placeholder first, then let the
        # ordinary composite writer reselect the approved unit.
        try:
            initial_amount = self._action_locator(action)
        except Exception:
            initial_amount = None
        initial_unit = (
            self._travel_stay_duration_unit_control(initial_amount)
            if initial_amount is not None else None
        )
        expected_parts = self._travel_stay_duration_parts(action.value)
        if initial_amount is not None and expected_parts is not None:
            try:
                raw_amount = str(initial_amount.input_value() or "").strip()
            except Exception:
                raw_amount = ""
            if (
                raw_amount
                and not re.fullmatch(r"\d+(?:\.\d+)?", raw_amount)
            ):
                # Repair checkpoints created by the old maxlength bug (for
                # example ``7 D`` in the numeric box) even when canonical
                # readback can infer ``7 DAY`` from the sibling unit select.
                # A genuinely blank first-run field is not legacy damage:
                # pre-filling it here also selected the unit, after which the
                # stale-branch guard immediately reset that brand-new choice
                # to the placeholder and posted an unnecessary request.
                self._fill_travel_stay_duration(
                    initial_amount,
                    action,
                    expected_parts,
                )
        if (
            initial_unit is not None
            and expected_parts is not None
            and not self._travel_us_address_rendered()
        ):
            try:
                unit_state = initial_unit.evaluate(
                    """el => ({
                        value: String(el.value || ''),
                        text: el.selectedIndex >= 0
                            ? String(el.options[el.selectedIndex].text || '')
                            : '',
                        options: Array.from(el.options || []).map(option => ({
                            value: String(option.value || ''),
                            text: String(option.text || '').trim()
                        }))
                    })""",
                    timeout=1000,
                )
            except Exception:
                unit_state = {}
            candidate = " ".join((
                str(unit_state.get("text") or ""),
                str(unit_state.get("value") or ""),
            ))
            current_matches = self._choice_matches(
                expected_parts[1], candidate
            )
            placeholder = next((
                option for option in unit_state.get("options", ())
                if (
                    not str(option.get("value") or "").strip()
                    or "select one" in str(
                        option.get("text") or ""
                    ).casefold()
                )
            ), None)
            if current_matches and placeholder is not None:
                self._begin_action_dom_watch()
                if not self._select_native_ceac_option(
                    initial_unit,
                    str(placeholder.get("value") or ""),
                    exact_value=True,
                ):
                    raise RuntimeError(
                        "CEAC stay-duration placeholder could not be "
                        "selected through native input"
                    )
                if not self._ensure_travel_control_postback(
                    initial_unit,
                    action,
                    require_dependent=False,
                ):
                    self._restore_fresh_travel_page(
                        "stay-duration-reset-not-posted"
                    )
                    return
                try:
                    self._page.wait_for_load_state(
                        "domcontentloaded", timeout=5000
                    )
                    self._page.wait_for_timeout(250)
                except Exception:
                    pass
                self._prune_detached_field_bindings()
                rebound_amount = self._travel_semantic_control(
                    (
                        "Intended Length of Stay in U.S.",
                        "Intended Length of Stay",
                    ),
                    "duration",
                    section="",
                    prefer_last=False,
                )
                if rebound_amount is None:
                    self.invalidate_field_binding(field_id)
                    return
                self._mark_field(rebound_amount, action)

        super().execute(action)
        if self._travel_us_address_rendered():
            self._v2_forced_refresh_receipt_field_ids.add(field_id)
            return

        try:
            amount = self._action_locator(action)
        except Exception:
            amount = None
        unit = (
            self._travel_stay_duration_unit_control(amount)
            if amount is not None else None
        )
        if unit is None:
            self._acknowledged = [
                item for item in self._acknowledged if item != action.id
            ]
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            return

        try:
            posted = self._ensure_travel_control_postback(
                unit,
                action,
                require_dependent=True,
                dependent_probe=self._travel_us_address_rendered,
            )
        except ControlPostbackTimeout:
            if self._prepare_travel_stay_duration_retry(
                action,
                "stay-duration-postback-timeout",
            ):
                # Use a different exception type only after the same-page GET
                # has made the live DOM authoritative again.  The workflow's
                # ordinary idempotent-value repair is bounded and will first
                # verify this fresh page, so it cannot blindly replay the
                # unresolved POST.
                raise RuntimeError(
                    "CEAC stay-duration POST timed out; the same Travel "
                    "page was safely reloaded for bounded re-verification"
                ) from None
            self._acknowledged = [
                item for item in self._acknowledged if item != action.id
            ]
            self._verified_field_values.pop(field_id, None)
            raise
        if not posted or not self._wait_for_travel_us_address():
            self._acknowledged = [
                item for item in self._acknowledged if item != action.id
            ]
            self._verified_field_values.pop(field_id, None)
            return
        self._v2_forced_refresh_receipt_field_ids.add(field_id)

    def _travel_payer_dependent_rendered(self):
        """Prove that CEAC rendered the branch below the payer controller."""
        for terms in (
            ("Surnames of Person Paying for Trip",),
            ("Organization Name",),
            ("Telephone Number",),
            ("Email Address",),
            ("Relationship to You",),
        ):
            if self._travel_semantic_control(
                terms,
                "text",
                section="payer",
                prefer_last=False,
            ) is not None:
                return True
        return False

    def _travel_payer_control(self):
        return self._travel_semantic_control(
            ("Person/Entity Paying for Your Trip",),
            "select_text",
            section="",
            prefer_last=False,
        )

    @classmethod
    def _travel_payer_requires_details(cls, value):
        """Return whether CEAC expands payer identity/contact controls.

        CEAC deliberately asks no follow-up payer questions for SELF,
        PRESENT EMPLOYER, or EMPLOYER IN THE U.S.  Only the two explicit
        external-payer choices expand a person/company detail branch.
        """
        compact = re.sub(
            r"[^a-z0-9]",
            "",
            str(value or "").casefold(),
        )
        if compact in {
            "otherperson",
            "otherorganization",
            "othercompanyorganization",
        }:
            return True
        return any(
            cls._choice_matches(value, choice)
            for choice in (
                "OTHER PERSON",
                "OTHER COMPANY/ORGANIZATION",
            )
        )

    def _wait_for_travel_payer_dependent(self):
        for delay_ms in (0, 150, 250, 400, 650, 1000, 1500):
            if delay_ms:
                try:
                    self._page.wait_for_timeout(delay_ms)
                except Exception:
                    return False
            if self._travel_payer_dependent_rendered():
                return True
        return False

    def _restore_fresh_ceac_page(self, reason):
        """Reload the same CEAC form so server state becomes authoritative."""
        def record(**outcome):
            diagnostic = dict(
                self._last_control_postback_diagnostic or {}
            )
            diagnostic.update(outcome)
            # A navigation callback may clear page-owned state while reload
            # is in progress. Publish the receipt after that transition
            # instead of updating a stale dictionary by reference.
            self._last_control_postback_diagnostic = diagnostic

        try:
            self._page.reload(
                wait_until="domcontentloaded",
                timeout=self.NAVIGATION_TIMEOUT_MS,
            )
            self._configure_timeout_target(self._page)
            record(
                safeReloadAfterUnknownPostback=True,
                safeReloadReason=str(reason or "")[:120],
            )
            return True
        except Exception as error:
            record(
                safeReloadAfterUnknownPostback=False,
                safeReloadReason=str(reason or "")[:120],
                safeReloadErrorType=type(error).__name__,
            )
            return False

    def _restore_fresh_travel_page(self, reason):
        """Backward-compatible Travel wrapper for same-page recovery."""
        return self._restore_fresh_ceac_page(reason)

    def _execute_travel_payer_branch(self, action):
        """Commit the payer controller only after its CEAC branch exists."""
        field_id = str(action.field_id or "")
        locator = self._action_locator(action)
        try:
            selected = locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '') : '',
                    options: Array.from(el.options || []).map(
                        (option, index) => ({
                            index,
                            value: String(option.value || ''),
                            text: String(option.text || '').trim()
                        })
                    )
                })""",
                timeout=1000,
            )
        except Exception:
            return super().execute(action)
        if not isinstance(selected, dict) or selected.get("tag") != "select":
            return super().execute(action)

        reviewed = next((
            option for option in list(selected.get("options") or ())
            if self._choice_matches(
                action.value,
                f"{option.get('text', '')} {option.get('value', '')}",
            )
        ), None)
        placeholder = next((
            option for option in list(selected.get("options") or ())
            if (
                not str(option.get("value") or "").strip()
                or "select one" in str(option.get("text") or "").casefold()
            )
        ), None)
        if reviewed is None:
            return super().execute(action)

        needs_details = self._travel_payer_requires_details(action.value)
        current = " ".join(filter(None, (
            str(selected.get("text") or ""),
            str(selected.get("value") or ""),
        )))
        current_matches = self._choice_matches(action.value, current)
        if current_matches and (
            not needs_details or self._travel_payer_dependent_rendered()
        ):
            self._mark_field(locator, action)
            self._verified_field_values[field_id] = action.value
            self._acknowledged.append(action.id)
            return

        # A local-only same value cannot emit another trusted change event.
        # Reset it through CEAC's own placeholder first, then choose the
        # reviewed option again and demand the dependent branch postcondition.
        if current_matches:
            if placeholder is None:
                self._restore_fresh_travel_page("payer-placeholder-missing")
                return
            reset_posted = self._post_travel_payer_value(
                locator,
                action,
                str(placeholder.get("value") or ""),
                require_dependent=False,
            )
            if not reset_posted:
                self._restore_fresh_travel_page("payer-reset-not-posted")
                return
            locator = self._travel_payer_control()
            if locator is None:
                locator = self._action_locator(action)

        reviewed_posted = self._post_travel_payer_value(
            locator,
            action,
            str(reviewed.get("value") or ""),
            require_dependent=needs_details,
            accept_post_start=not needs_details,
        )
        if not reviewed_posted:
            self._restore_fresh_travel_page("payer-selection-not-posted")
            return
        if needs_details and not self._wait_for_travel_payer_dependent():
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            self._v2_payer_reopen_attempted = True
            self._restore_fresh_travel_page("payer-dependent-panel-missing")
            return

        self._prune_detached_field_bindings()
        try:
            rebound = self._travel_payer_control()
            if rebound is None:
                rebound = self._action_locator(action)
            live = rebound.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '') : ''
                })""",
                timeout=1000,
            )
        except Exception:
            self.invalidate_field_binding(field_id)
            return
        candidate = f"{live.get('text', '')} {live.get('value', '')}"
        if not self._choice_matches(action.value, candidate):
            self.invalidate_field_binding(field_id)
            return
        self._mark_field(rebound, action)
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)
        self._v2_forced_refresh_receipt_field_ids.add(field_id)

    def _post_travel_payer_value(
        self,
        locator,
        action,
        value,
        *,
        require_dependent,
        accept_post_start=False,
    ):
        self._begin_action_dom_watch()
        if not self._select_native_ceac_option(
            locator,
            str(value or ""),
            exact_value=True,
        ):
            raise RuntimeError(
                "CEAC Travel payer option could not be selected through "
                "native input"
            )
        try:
            posted = self._ensure_travel_control_postback(
                locator,
                action,
                require_dependent=require_dependent,
                dependent_probe=self._travel_payer_dependent_rendered,
                accept_post_start=accept_post_start,
            )
        except ControlPostbackTimeout:
            self._restore_fresh_travel_page("payer-postback-timeout")
            raise
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        self._prune_detached_field_bindings()
        return posted

    def _select_native_ceac_option(
        self,
        locator,
        desired,
        *,
        exact_value=False,
    ):
        """Resolve one exact CEAC option, then commit it as real user input."""
        self._last_native_select_failure = ""
        try:
            snapshot = locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    options: Array.from(el.options || []).map(
                        (option) => ({
                            value: String(option.value || ''),
                            text: String(option.text || '').trim()
                        })
                    )
                })""",
                timeout=1000,
            )
        except Exception as error:
            self._last_native_select_failure = (
                f"option-snapshot-read:{type(error).__name__}"
            )
            return False
        if not isinstance(snapshot, dict) or snapshot.get("tag") != "select":
            self._last_native_select_failure = "option-snapshot-not-select"
            return False
        target = next((
            option
            for option in list(snapshot.get("options") or ())
            if (
                str(option.get("value") or "") == str(desired or "")
                if exact_value
                else self._choice_matches(
                    desired,
                    f"{option.get('text', '')} {option.get('value', '')}",
                )
            )
        ), None)
        if target is None:
            self._last_native_select_failure = (
                f"exact-option-not-found:{str(desired or '')[:80]}"
            )
            return False
        activated = self._activate_select_option(locator, target)
        if not activated and not self._last_native_select_failure:
            self._last_native_select_failure = "native-option-activation-false"
        return activated

    def _activate_select_option(self, locator, selected):
        """Choose an option only through its verified in-page locator.

        The former V2 implementation raised Chrome with AppleScript and then
        posted global macOS mouse/keyboard events.  A global event can land in
        another application when focus or screen geometry changes.  Delegate
        to the repair runtime's bounded Playwright hook instead: it mutates
        only the exact semantic ``select`` locator and cannot address desktop
        coordinates, menus, windows, or other applications.
        """
        self._last_native_select_failure = ""
        desired_value = str((selected or {}).get("value") or "")
        desired_text = str(
            (selected or {}).get("text")
            or (selected or {}).get("label")
            or ""
        ).strip()
        try:
            current = locator.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '').trim()
                        : ''
                })""",
                timeout=600,
            )
        except Exception:
            current = None
        if isinstance(current, dict) and (
            str(current.get("value") or "") == desired_value
            and (
                not desired_text
                or str(current.get("text") or "").strip() == desired_text
            )
        ):
            # A same-value write must be a true no-op. Dispatching another
            # synthetic change after a safe reload produced duplicate ASP.NET
            # postbacks and occasionally erased a freshly rendered branch.
            return True
        try:
            locator.evaluate(
                """el => {
                    el.__docflowScopedSelectAudit = {
                        input: 0,
                        change: 0,
                        inputTrusted: null,
                        changeTrusted: null,
                        hasInlineChange: typeof el.onchange === 'function',
                        onchangeAttribute: String(
                            el.getAttribute('onchange') || ''
                        ).slice(0, 160)
                    };
                    if (!el.__docflowScopedSelectAuditInstalled) {
                        el.addEventListener('input', event => {
                            const audit = el.__docflowScopedSelectAudit;
                            if (!audit) return;
                            audit.input += 1;
                            audit.inputTrusted = event.isTrusted;
                        }, true);
                        el.addEventListener('change', event => {
                            const audit = el.__docflowScopedSelectAudit;
                            if (!audit) return;
                            audit.change += 1;
                            audit.changeTrusted = event.isTrusted;
                        }, true);
                        el.__docflowScopedSelectAuditInstalled = true;
                    }
                }""",
                timeout=800,
            )
        except Exception:
            pass
        activated = super()._activate_select_option(locator, selected)
        if activated:
            try:
                self._last_native_select_audit = locator.evaluate(
                    "el => el.__docflowScopedSelectAudit || null",
                    timeout=500,
                )
            except Exception:
                self._last_native_select_audit = None
        return activated

        # Unreachable forensic prototype: scoped locator keyboard type-ahead
        # was evaluated but emitted multiple change events for some Chromium
        # selects. The exact one-change DOM hook above is deterministic and
        # its ASP.NET transport is separately proved before acknowledgement.
        desired_value = str((selected or {}).get("value") or "")
        desired_text = str((selected or {}).get("text") or "").strip()
        try:
            snapshot = locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    disabled: Boolean(el.disabled),
                    multiple: Boolean(el.multiple),
                    selectedIndex: Number(el.selectedIndex),
                    options: Array.from(el.options || []).map(
                        (option, index) => ({
                            index,
                            value: String(option.value || ''),
                            text: String(option.text || '').trim(),
                            disabled: Boolean(
                                option.disabled
                                || option.parentElement?.disabled
                            )
                        })
                    )
                })""",
                timeout=1000,
            )
        except Exception as error:
            self._last_native_select_failure = (
                f"scoped-snapshot-read:{type(error).__name__}"
            )
            return False
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("tag") != "select"
            or snapshot.get("disabled")
            or snapshot.get("multiple")
        ):
            self._last_native_select_failure = "scoped-control-ineligible"
            return False
        options = list(snapshot.get("options") or ())
        matches = [
            option for option in options
            if str(option.get("value") or "") == desired_value
            and (
                not desired_text
                or str(option.get("text") or "").strip() == desired_text
            )
        ]
        if len(matches) != 1 or matches[0].get("disabled"):
            self._last_native_select_failure = (
                f"scoped-option-match-count:{len(matches)}"
            )
            return False
        target = matches[0]
        target_index = int(target.get("index", -1))
        if int(snapshot.get("selectedIndex", -1)) == target_index:
            return True

        label = re.sub(r"\s+", " ", desired_text).strip()
        other_labels = [
            re.sub(r"\s+", " ", str(option.get("text") or "")).strip()
            for option in options
            if int(option.get("index", -1)) != target_index
            and not option.get("disabled")
        ]
        selection_text = label
        for end in range(1, len(label) + 1):
            prefix = label[:end]
            if (
                prefix[-1].isalnum()
                and all(
                    not other.casefold().startswith(prefix.casefold())
                    for other in other_labels
                )
            ):
                selection_text = prefix
                break

        try:
            locator.evaluate(
                """el => {
                    el.__docflowScopedSelectAudit = {
                        input: 0,
                        change: 0,
                        inputTrusted: null,
                        changeTrusted: null,
                        hasInlineChange: typeof el.onchange === 'function',
                        onchangeAttribute: String(
                            el.getAttribute('onchange') || ''
                        ).slice(0, 160)
                    };
                    if (!el.__docflowScopedSelectAuditInstalled) {
                        el.addEventListener('input', event => {
                            const audit = el.__docflowScopedSelectAudit;
                            if (!audit) return;
                            audit.input += 1;
                            audit.inputTrusted = event.isTrusted;
                        }, true);
                        el.addEventListener('change', event => {
                            const audit = el.__docflowScopedSelectAudit;
                            if (!audit) return;
                            audit.change += 1;
                            audit.changeTrusted = event.isTrusted;
                        }, true);
                        el.__docflowScopedSelectAuditInstalled = true;
                    }
                }""",
                timeout=800,
            )
            # Locator keyboard events travel over this page's DevTools target;
            # they never use macOS focus, coordinates, Accessibility, or HID.
            # Native select type-ahead gives CEAC a real browser input/change
            # event while remaining strictly scoped to the verified element.
            if label:
                locator.focus(timeout=3000)
                locator.press_sequentially(
                    selection_text,
                    delay=0,
                    timeout=3000,
                )
                self._page.wait_for_timeout(40)
            current = locator.evaluate(
                """el => ({
                    value: String(el.value || ''),
                    text: el.selectedIndex >= 0
                        ? String(el.options[el.selectedIndex].text || '').trim()
                        : '',
                    audit: el.__docflowScopedSelectAudit || null
                })""",
                timeout=800,
            )
            if not (
                isinstance(current, dict)
                and str(current.get("value") or "") == desired_value
                and (
                    not desired_text
                    or str(current.get("text") or "").strip() == desired_text
                )
            ):
                # Exact Playwright select is a page-scoped final fallback for
                # labels that browser type-ahead cannot represent.
                result = locator.select_option(
                    value=desired_value,
                    timeout=3000,
                )
                if desired_value not in [str(item) for item in result or ()]:
                    self._last_native_select_failure = (
                        "scoped-select-option-mismatch"
                    )
                    return False
                current = locator.evaluate(
                    """el => ({
                        value: String(el.value || ''),
                        text: el.selectedIndex >= 0
                            ? String(el.options[el.selectedIndex].text || '')
                                .trim()
                            : '',
                        audit: el.__docflowScopedSelectAudit || null
                    })""",
                    timeout=800,
                )
            self._last_native_select_audit = (
                current.get("audit") if isinstance(current, dict) else None
            )
            return bool(
                isinstance(current, dict)
                and str(current.get("value") or "") == desired_value
                and (
                    not desired_text
                    or str(current.get("text") or "").strip() == desired_text
                )
            )
        except Exception as error:
            self._last_native_select_failure = (
                f"scoped-activation:{type(error).__name__}"
            )
            return False

    def _disabled_global_activate_select_option(self, locator, selected):
        """Retained only as unreachable forensic history; always disabled.

        DOM access is read-only here: it proves the exact option and derives
        how many enabled choices lie before it.  The only state-changing input
        is Chromium's mouse/keyboard pipeline, so CEAC receives the same
        trusted change event as it does from a person using the dropdown.
        """
        raise RuntimeError("OS-global select input is permanently disabled")
        desired_value = str((selected or {}).get("value") or "")
        desired_text = str((selected or {}).get("text") or "").strip()
        try:
            snapshot = locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    disabled: Boolean(el.disabled),
                    multiple: Boolean(el.multiple),
                    selectedIndex: Number(el.selectedIndex),
                    options: Array.from(el.options || []).map(
                        (option, index) => ({
                            index,
                            value: String(option.value || ''),
                            text: String(option.text || '').trim(),
                            disabled: Boolean(
                                option.disabled
                                || option.parentElement?.disabled
                            )
                        })
                    )
                })""",
                timeout=1000,
            )
        except Exception as error:
            self._last_native_select_failure = (
                f"activation-snapshot-read:{type(error).__name__}"
            )
            return False
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("tag") != "select"
            or snapshot.get("disabled")
            or snapshot.get("multiple")
        ):
            self._last_native_select_failure = "activation-control-ineligible"
            return False
        options = list(snapshot.get("options") or ())
        matches = [
            option for option in options
            if str(option.get("value") or "") == desired_value
            and (
                not desired_text
                or str(option.get("text") or "").strip() == desired_text
            )
        ]
        if len(matches) != 1:
            self._last_native_select_failure = (
                f"activation-option-match-count:{len(matches)}"
            )
            return False
        target = matches[0]
        if target.get("disabled"):
            self._last_native_select_failure = "activation-option-disabled"
            return False
        target_index = int(target.get("index", -1))
        if int(snapshot.get("selectedIndex", -1)) == target_index:
            return True
        if target_index < 0 or target_index > 80:
            self._last_native_select_failure = (
                f"activation-option-index:{target_index}"
            )
            return False
        option_steps = sum(
            1
            for option in options[:target_index]
            if not option.get("disabled")
        )

        label = re.sub(r"\s+", " ", desired_text).strip()
        if not label:
            self._last_native_select_failure = "activation-empty-label"
            return False
        other_labels = [
            re.sub(r"\s+", " ", str(option.get("text") or "")).strip()
            for option in options
            if int(option.get("index", -1)) != target_index
            and not option.get("disabled")
        ]
        # Chrome's macOS native menu reliably supports type-ahead, but long
        # labels containing punctuation can terminate its search buffer.  The
        # DOM has already resolved one exact target, so type only the shortest
        # alphanumeric-ending prefix that is unique among live enabled options.
        selection_text = label
        for end in range(1, len(label) + 1):
            prefix = label[:end]
            if (
                prefix[-1].isalnum()
                and all(
                    not other.casefold().startswith(prefix.casefold())
                    for other in other_labels
                )
            ):
                selection_text = prefix
                break
        if self.headless:
            raise NativeInputUnavailable(
                "V2 真实下拉框需要可见 Chrome；当前为 headless，未执行 JS 降级。"
            )
        self.focus()
        if self._native_input is None:
            raise self._native_input_error or NativeInputUnavailable(
                "V2 系统级鼠标键盘通道不可用；下拉框未改写。"
            )
        launch_source = str(self.browser_launch_source or "")
        preferred_process = (
            "Google Chrome for Testing"
            if launch_source.startswith("playwright-")
            else "Google Chrome"
        )
        window_bounds = self._controlled_browser_window_bounds()
        self._native_content_origin = self._native_input.activate_browser_window(
            self._page.title(),
            preferred_process=preferred_process,
            window_bounds=window_bounds,
        )
        try:
            self._page.bring_to_front()
        except Exception:
            pass
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
            box = locator.bounding_box(timeout=3000)
            if not box or box["width"] <= 0 or box["height"] <= 0:
                return False
            locator.evaluate(
                """el => {
                    el.__docflowNativeSelectAudit = {
                        input: 0,
                        change: 0,
                        inputTrusted: null,
                        changeTrusted: null,
                        hasInlineChange: typeof el.onchange === 'function',
                        onchangeAttribute: String(
                            el.getAttribute('onchange') || ''
                        ).slice(0, 160)
                    };
                    if (!el.__docflowNativeSelectAuditInstalled) {
                        el.addEventListener('input', event => {
                            const audit = el.__docflowNativeSelectAudit;
                            if (!audit) return;
                            audit.input += 1;
                            audit.inputTrusted = event.isTrusted;
                        }, true);
                        el.addEventListener('change', event => {
                            const audit = el.__docflowNativeSelectAudit;
                            if (!audit) return;
                            audit.change += 1;
                            audit.changeTrusted = event.isTrusted;
                        }, true);
                        el.__docflowNativeSelectAuditInstalled = true;
                    }
                }""",
                timeout=800,
            )
            x, y = self._screen_point_for_box(box)
            self._native_input.select_option(
                x,
                y,
                selection_text,
                option_steps=option_steps,
            )
            for delay_ms in (80, 100, 150, 220, 300):
                self._page.wait_for_timeout(delay_ms)
                current = locator.evaluate(
                    """el => ({
                        value: String(el.value || ''),
                        text: el.selectedIndex >= 0
                            ? String(el.options[el.selectedIndex].text || '') : ''
                    })""",
                    timeout=800,
                )
                if isinstance(current, dict) and (
                    str(current.get("value") or "") == desired_value
                    and (
                        not desired_text
                        or str(current.get("text") or "").strip()
                            == desired_text
                    )
                ):
                    try:
                        self._last_native_select_audit = locator.evaluate(
                            "el => el.__docflowNativeSelectAudit || null",
                            timeout=500,
                        )
                    except Exception:
                        self._last_native_select_audit = None
                    return True
                if isinstance(current, dict):
                    self._last_native_select_failure = (
                        "post-input-value-mismatch:"
                        f"expected={desired_value[:60]},"
                        f"actual={str(current.get('value') or '')[:60]};"
                        f"{getattr(self, '_last_screen_point_diagnostics', '')}"
                    )
        except NativeInputUnavailable:
            raise
        except Exception:
            # ASP.NET is allowed to synchronously replace the select after the
            # trusted Enter/change.  The generation watcher and the action's
            # DOM postcondition perform the final proof on the new document.
            return True
        return False

    def _controlled_browser_window_bounds(self, metrics=None):
        """Return the exact CDP window bounds used for OS-level targeting."""
        metrics = metrics or self._page.evaluate(
            """() => ({
                screenX: Number(window.screenX),
                screenY: Number(window.screenY),
                outerWidth: Number(window.outerWidth),
                outerHeight: Number(window.outerHeight),
                innerWidth: Number(window.innerWidth),
                innerHeight: Number(window.innerHeight)
            })"""
        )
        bounds = {
            "left": float(metrics.get("screenX") or 0),
            "top": float(metrics.get("screenY") or 0),
            "width": float(metrics.get("outerWidth") or 0),
            "height": float(metrics.get("outerHeight") or 0),
        }
        session = None
        try:
            if self._context is not None and "chrom" in self.engine_name:
                session = self._context.new_cdp_session(self._page)
                window = session.send("Browser.getWindowForTarget")
                reported = dict(window.get("bounds") or {})
                for key in ("left", "top", "width", "height"):
                    if reported.get(key) is not None:
                        bounds[key] = float(reported[key])
        except Exception:
            pass
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass
        return bounds

    def _screen_point_for_box(self, box):
        """Convert a Playwright viewport box to a global macOS screen point."""
        metrics = self._page.evaluate(
            """() => ({
                screenX: Number(window.screenX),
                screenY: Number(window.screenY),
                outerWidth: Number(window.outerWidth),
                outerHeight: Number(window.outerHeight),
                innerWidth: Number(window.innerWidth),
                innerHeight: Number(window.innerHeight)
            })"""
        )
        outer_width = max(0.0, float(metrics.get("outerWidth") or 0))
        outer_height = max(0.0, float(metrics.get("outerHeight") or 0))
        inner_width = max(0.0, float(metrics.get("innerWidth") or 0))
        inner_height = max(0.0, float(metrics.get("innerHeight") or 0))
        side_inset = max(0.0, (outer_width - inner_width) / 2.0)
        top_inset = max(0.0, outer_height - inner_height - side_inset)
        bounds = self._controlled_browser_window_bounds(metrics)
        window_x = float(bounds.get("left") or 0)
        window_y = float(bounds.get("top") or 0)
        bound_width = float(bounds.get("width") or outer_width)
        bound_height = float(bounds.get("height") or outer_height)
        if bound_width > 0:
            side_inset = max(0.0, (bound_width - inner_width) / 2.0)
        if bound_height > 0:
            top_inset = max(
                0.0,
                bound_height - inner_height - side_inset,
            )
        content_origin = getattr(self, "_native_content_origin", None)
        if (
            isinstance(content_origin, tuple)
            and len(content_origin) == 2
        ):
            point = (
                float(content_origin[0])
                + float(box["x"]) + float(box["width"]) / 2.0,
                float(content_origin[1])
                + float(box["y"]) + float(box["height"]) / 2.0,
            )
            origin_detail = (
                f"content={float(content_origin[0]):.0f},"
                f"{float(content_origin[1]):.0f}"
            )
        else:
            point = (
                window_x + side_inset
                + float(box["x"]) + float(box["width"]) / 2.0,
                window_y + top_inset
                + float(box["y"]) + float(box["height"]) / 2.0,
            )
            origin_detail = f"inset={side_inset:.0f},{top_inset:.0f}"
        self._last_screen_point_diagnostics = (
            f"point={point[0]:.0f},{point[1]:.0f};"
            f"box={float(box['x']):.0f},{float(box['y']):.0f},"
            f"{float(box['width']):.0f},{float(box['height']):.0f};"
            f"window={window_x:.0f},{window_y:.0f},"
            f"{bound_width:.0f},{bound_height:.0f};"
            f"inner={inner_width:.0f},{inner_height:.0f};"
            f"{origin_detail}"
        )
        return point

    def dynamic_refresh_detected(self, action=None):
        """Publish the refresh already completed inside a Work repair action."""
        field_id = str(getattr(action, "field_id", "") or "")
        if field_id in self._v2_forced_refresh_receipt_field_ids:
            self._v2_forced_refresh_receipt_field_ids.discard(field_id)
            self._last_dynamic_refresh_evidence = {
                "generationChanged": True,
                "v2ForcedBranchRepairCompleted": True,
            }
            self._action_watch_active = False
            return True
        return super().dynamic_refresh_detected(action)

    def settle_after_dynamic_refresh(self, field_id, labels=(), hints=()):
        """Give the positive Work/Education 2 branch time to materialize."""
        if (
            self._is_us_contact_page()
            and str(field_id or "").casefold().endswith(
                ".us_contact.relationship"
            )
            and self._us_contact_address_rendered()
        ):
            # A missing-address repair is a synthetic replay of an already
            # completed field, so the workflow has no pending-field labels to
            # pass here.  The base rebinder cannot resolve an empty descriptor
            # and discards the fresh selector.  Rebind the exact replacement
            # select directly and preserve only the value already proven by
            # the native placeholder -> reviewed transition.
            relationship = self._us_contact_relationship_control()
            approved = self._verified_field_values.get(str(field_id or ""))
            snapshot = (
                self._selected_option_snapshot(relationship)
                if relationship is not None else {}
            )
            candidate = " ".join(filter(None, (
                str(snapshot.get("text") or ""),
                str(snapshot.get("value") or ""),
            )))
            if (
                relationship is not None
                and approved
                and self._choice_matches(approved, candidate)
            ):
                self._mark_field(
                    relationship,
                    ComputerAction(
                        kind=ActionKind.SELECT,
                        field_id=str(field_id or ""),
                        target_hint=str(field_id or ""),
                        value=approved,
                        reason=(
                            "U.S. Contact replacement-control verification"
                        ),
                    ),
                )
                return True
        settled = super().settle_after_dynamic_refresh(
            field_id,
            labels,
            hints,
        )
        if not (
            self._is_work_education2_page()
            and str(field_id or "").casefold().endswith(
                ".work.education_secondary_or_above"
            )
            and self._boolean_choice(
                self._descriptor_approved_value(labels)
            ) is True
        ):
            return settled
        for delay_ms in (150, 250, 400, 650, 1000):
            school = self._travel_semantic_control(
                ("Name of Institution",),
                "text",
                section="",
                prefer_last=False,
            )
            if school is not None:
                break
            try:
                self._page.wait_for_timeout(delay_ms)
            except Exception:
                break
        return settled

    def _ensure_travel_control_postback(
        self,
        locator,
        action,
        *,
        require_dependent=False,
        dependent_probe=None,
        accept_post_start=False,
    ):
        """Prove the page-scoped change reached CEAC, or submit its target.

        Production CEAC can leave a value local when its inline WebForms
        callback does not start traffic.  First give that callback a bounded
        chance to produce DOM/async/network proof.
        If it does not, invoke the select's own ``__doPostBack`` target once.
        A final same-origin native form POST is allowed only when WebForms'
        exact hidden event fields and a POST form are present.
        """
        diagnostic = {
            "fieldId": str(getattr(action, "field_id", "") or ""),
            "nativeChangeObserved": True,
            "ordinaryPostbackScheduled": False,
            "forcedNativeFormSubmit": False,
        }
        self._last_control_postback_diagnostic = diagnostic

        if self._await_control_postback_receipt(
            action,
            dispatch_kind="native-change",
            require_dependent=require_dependent,
            dependent_probe=dependent_probe,
            accept_post_start=accept_post_start,
        ):
            diagnostic.update(self._last_dynamic_refresh_evidence or {})
            diagnostic["result"] = "native-change-received"
            return True

        try:
            self._begin_action_dom_watch()
            ordinary = self._schedule_aspnet_control_postback(locator)
        except Exception as error:
            ordinary = {
                "scheduled": False,
                "errorType": type(error).__name__,
            }
        diagnostic["ordinaryPostback"] = ordinary
        diagnostic["ordinaryPostbackScheduled"] = bool(
            ordinary.get("scheduled")
        )
        if ordinary.get("scheduled") and self._await_control_postback_receipt(
            action,
            dispatch_kind="control-do-postback",
            require_dependent=require_dependent,
            dependent_probe=dependent_probe,
            accept_post_start=accept_post_start,
        ):
            diagnostic.update(self._last_dynamic_refresh_evidence or {})
            diagnostic["result"] = "control-do-postback-received"
            return True

        try:
            self._begin_action_dom_watch()
            forced = self._submit_aspnet_control_form(locator)
        except Exception as error:
            forced = {
                "dispatched": False,
                "errorType": type(error).__name__,
            }
        diagnostic["forcedPostback"] = forced
        diagnostic["forcedNativeFormSubmit"] = bool(
            forced.get("forcedNativeFormSubmit")
        )
        if forced.get("dispatched") and self._await_control_postback_receipt(
            action,
            dispatch_kind="native-form-submit",
            require_dependent=require_dependent,
            dependent_probe=dependent_probe,
            accept_post_start=accept_post_start,
        ):
            diagnostic.update(self._last_dynamic_refresh_evidence or {})
            diagnostic["result"] = "native-form-post-received"
            return True

        diagnostic["result"] = (
            "dependent-branch-missing"
            if require_dependent
            else "no-postback-request"
        )
        if require_dependent:
            return False
        if not (
            diagnostic.get("ordinaryPostbackScheduled")
            or diagnostic.get("forcedNativeFormSubmit")
        ):
            # A non-WebForms fixture or unexpected page cannot be repaired by
            # inventing a transport. Preserve the original safe behavior:
            # leave the primary unacknowledged so deterministic verification
            # opens the resumable hard boundary without clicking Next.
            return False
        raise RuntimeError(
            "CEAC control changed through page-scoped input, but its "
            "ASP.NET postback did not start: "
            f"{str(diagnostic)[:700]}"
        )

    def _await_control_postback_receipt(
        self,
        action,
        *,
        dispatch_kind,
        require_dependent=False,
        dependent_probe=None,
        accept_post_start=False,
    ):
        """Wait for exact DOM, async-manager, or network postback evidence."""
        def dependent_ready():
            if not require_dependent or not callable(dependent_probe):
                return False
            try:
                return bool(dependent_probe())
            except Exception:
                return False

        def accept_dependent_branch():
            self._last_dynamic_refresh_evidence.update({
                "postbackDispatch": dispatch_kind,
                "dependentBranchRendered": True,
            })
            return True

        network_before = max(
            0,
            int((self._v2_network_before or {}).get("started") or 0),
        )
        detected = self.dynamic_refresh_detected(action)
        if dependent_ready():
            return accept_dependent_branch()
        if detected and not dependent_ready():
            self._wait_for_watched_dom_replacement()
        if dependent_ready():
            return accept_dependent_branch()

        deadline = time.monotonic() + self.FALSE_POSTBACK_GRACE_SECONDS
        completed_post = False
        while time.monotonic() < deadline:
            # A rendered semantic child is the controller's authoritative
            # postcondition. Do not wait for Playwright's request bookkeeping
            # to become idle after CEAC has already replaced the branch DOM.
            if dependent_ready():
                return accept_dependent_branch()
            if self._v2_network_started > network_before:
                if accept_post_start:
                    self._last_dynamic_refresh_evidence.update({
                        "postbackDispatch": dispatch_kind,
                        "networkRequestStarted": True,
                        "networkCompletionNotRequired": True,
                        "noDependentBranchExpected": True,
                    })
                    return True
                network_outcome = self._wait_for_dynamic_network_idle(
                    network_before,
                    completion_probe=(
                        dependent_probe if require_dependent else None
                    ),
                )
                if network_outcome == "dependent-rendered":
                    self._last_dynamic_refresh_evidence.update({
                        "networkRequestStarted": True,
                    })
                    return accept_dependent_branch()
                if network_outcome != "network-idle":
                    # CEAC can render the dependent FormView in the same tick
                    # that the bounded network watcher expires. Recheck the
                    # semantic postcondition before escalating an uncertain
                    # transport receipt into a manual hard boundary.
                    if dependent_ready():
                        self._last_dynamic_refresh_evidence.update({
                            "networkRequestStarted": True,
                            "networkCompletionNotRequired": True,
                        })
                        return accept_dependent_branch()
                    field_id = str(
                        getattr(action, "field_id", "") or "unknown-control"
                    )
                    raise ControlPostbackTimeout(
                        f"CEAC controller postback for {field_id} started "
                        "but did not finish before the bounded settle timeout"
                    )
                self._last_dynamic_refresh_evidence.update({
                    "postbackDispatch": dispatch_kind,
                    "networkRequestStarted": True,
                    "networkRequestCompleted": True,
                })
                completed_post = True
                if not require_dependent:
                    return True
            if dependent_ready():
                return accept_dependent_branch()
            try:
                self._page.wait_for_timeout(50)
            except Exception:
                break

        evidence = dict(self._last_dynamic_refresh_evidence or {})
        if require_dependent and completed_post:
            evidence.update({
                "postbackDispatch": dispatch_kind,
                "dependentBranchRendered": False,
                "postbackCompletedWithoutDependentBranch": True,
            })
            self._last_dynamic_refresh_evidence = evidence
            return False
        if any((
            evidence.get("generationChanged"),
            evidence.get("markedControlRemoved"),
            evidence.get("missingFieldTokens"),
        )):
            if require_dependent:
                evidence.update({
                    "postbackDispatch": dispatch_kind,
                    "dependentBranchRendered": False,
                })
                self._last_dynamic_refresh_evidence = evidence
                return False
            evidence["postbackDispatch"] = dispatch_kind
            self._last_dynamic_refresh_evidence = evidence
            return True
        return False

    def _wait_for_dynamic_network_idle(
        self,
        started_before,
        *,
        completion_probe=None,
    ):
        """Wait for POST idle, unless its semantic branch renders first."""
        network_before = dict(self._v2_network_before or {})
        inflight_before = set(
            network_before.get("inflightTokens") or ()
        )
        deadline = (
            time.monotonic()
            + self.CONTROL_POSTBACK_SETTLE_TIMEOUT_SECONDS
        )
        while time.monotonic() < deadline:
            if callable(completion_probe):
                try:
                    if completion_probe():
                        return "dependent-rendered"
                except Exception:
                    pass
            started = self._v2_network_started > started_before
            ended = self._v2_network_ended > max(
                0,
                int(network_before.get("ended") or 0),
            )
            new_inflight = set(self._v2_network_inflight).difference(
                inflight_before
            )
            if started and ended and not new_inflight:
                try:
                    self._page.wait_for_timeout(120)
                except Exception:
                    pass
                return "network-idle"
            try:
                self._page.wait_for_timeout(60)
            except Exception:
                break
        return "timeout"

    @staticmethod
    def _schedule_aspnet_control_postback(locator):
        """Schedule the exact WebForms target without changing its value."""
        return dict(locator.evaluate(
            """el => {
                const target = String(el.getAttribute('name') || '').trim();
                const form = el.form || el.closest('form');
                const result = {
                    scheduled: false,
                    target: target.slice(0, 200),
                    formFound: Boolean(form),
                    doPostBackFound: typeof window.__doPostBack === 'function',
                    currentValue: String(el.value || '').slice(0, 120)
                };
                if (!target || !result.doPostBackFound) return result;
                window.setTimeout(() => window.__doPostBack(target, ''), 0);
                result.scheduled = true;
                return result;
            }"""
        ) or {})

    @staticmethod
    def _submit_aspnet_control_form(locator):
        """Submit one exact same-origin WebForms POST as a bounded fallback."""
        return dict(locator.evaluate(
            """el => {
                const target = String(el.getAttribute('name') || '').trim();
                const form = el.form || el.closest('form');
                const result = {
                    dispatched: false,
                    target: target.slice(0, 200),
                    formFound: Boolean(form),
                    forcedNativeFormSubmit: false,
                    currentValue: String(el.value || '').slice(0, 120)
                };
                if (!target || !form) return result;
                const method = String(form.method || '').toLowerCase();
                result.formMethod = method;
                if (method !== 'post') return result;
                const targetInput = form.querySelector(
                    'input[name="__EVENTTARGET"]'
                );
                const argumentInput = form.querySelector(
                    'input[name="__EVENTARGUMENT"]'
                );
                result.eventFieldsFound = Boolean(
                    targetInput && argumentInput
                );
                if (!targetInput || !argumentInput) return result;
                let action;
                try {
                    action = new URL(
                        form.getAttribute('action') || location.href,
                        location.href
                    );
                } catch (_error) {
                    return result;
                }
                result.sameOrigin = action.origin === location.origin;
                if (!result.sameOrigin) return result;
                const submit = (
                    form.ownerDocument.defaultView.HTMLFormElement
                        .prototype.submit
                );
                if (typeof submit !== 'function') return result;
                targetInput.value = target;
                argumentInput.value = '';
                window.setTimeout(() => submit.call(form), 0);
                result.dispatched = true;
                result.forcedNativeFormSubmit = true;
                return result;
            }"""
        ) or {})

    def classify_field_presence(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        """Classify ambiguous Travel fields inside their actual CEAC section.

        The legacy presence probe treats any visible ``Street Address`` or
        ``City`` label as evidence for every same-named field.  On Travel this
        keeps non-rendered payer details pending merely because the U.S.
        address is visible, which then sends Gemini into an unnecessary retry
        loop.  Preserve the generic result everywhere else, but replace the
        known Travel fields with section-scoped evidence.
        """
        result = dict(super().classify_field_presence(
            field_ids,
            field_labels,
            control_hints,
        ) or {})
        if self._is_personal2_page():
            return self._classify_personal2_field_presence(
                result,
                field_ids,
            )
        if self._is_passport_page():
            return self._classify_passport_field_presence(
                result,
                field_ids,
                dict(field_labels or {}),
            )
        if self._is_relatives_page():
            return self._classify_relatives_field_presence(
                result,
                field_ids,
                dict(field_labels or {}),
            )
        if self._is_work_education1_page():
            return self._classify_work_field_presence(
                result,
                field_ids,
                dict(field_labels or {}),
            )
        if self._is_work_education2_page():
            return self._classify_work_education2_field_presence(
                result,
                field_ids,
                dict(field_labels or {}),
            )
        if self._is_us_contact_page():
            return self._classify_us_contact_field_presence(
                result,
                field_ids,
                dict(field_labels or {}),
            )
        if not self._is_travel_page():
            return result

        labels = dict(field_labels or {})
        scoped_ids = [
            str(field_id)
            for field_id in field_ids or ()
            if self._travel_semantic_rule(field_id) is not None
        ]
        if not scoped_ids:
            return result

        resolved = {}
        for field_id in scoped_ids:
            rule = self._travel_semantic_rule(field_id)
            locator = self._travel_rule_control(
                field_id,
                rule,
                labels.get(field_id) or (),
            )
            resolved[field_id] = locator is not None

        payer_ids = [
            field_id
            for field_id in scoped_ids
            if self._travel_semantic_rule(field_id)["section"] == "payer"
        ]
        payer_branch_is_absent = bool(
            payer_ids
            and not any(resolved[field_id] for field_id in payer_ids)
        )
        secondary_purpose_is_visible = self._travel_purpose_control(
            "secondary",
            ("Specify", "Specify visa class"),
        ) is not None

        present = set(result.get("present") or ())
        absent = set(result.get("absent") or ())
        unresolved = set(result.get("unresolved") or ())
        for field_id in scoped_ids:
            present.discard(field_id)
            absent.discard(field_id)
            unresolved.discard(field_id)
            rule = self._travel_semantic_rule(field_id)
            if resolved[field_id]:
                present.add(field_id)
            elif (
                field_id.casefold().endswith(
                    ".travel.purpose.primary"
                )
                and secondary_purpose_is_visible
            ):
                # A real, explicitly labelled Specify control is server-owned
                # proof that the primary branch has already been accepted.
                # Treat a physically absent primary as out of live scope so it
                # can never be sent to Gemini as a ghost pending field.
                absent.add(field_id)
            elif (
                rule["section"] == "payer"
                and payer_branch_is_absent
            ):
                absent.add(field_id)
            else:
                unresolved.add(field_id)
        ordered = [str(field_id) for field_id in field_ids or ()]
        return {
            "present": [item for item in ordered if item in present],
            "absent": [item for item in ordered if item in absent],
            "unresolved": [
                item for item in ordered if item in unresolved
            ],
        }

    def stale_completed_branch_controller_fields(
        self,
        field_ids,
        field_values=None,
        field_labels=None,
    ):
        """Detect Travel controllers reset by a revisit or later postback.

        The durable job can revisit Travel after the user or CEAC navigates
        away.  A fresh server document then shows placeholders and unchecked
        radios, while the job still carries completions from the earlier page
        generation.  Read only the four exact, visible Travel controllers and
        reopen a completion when their live state is authoritative.  Missing
        controls remain inconclusive and never revoke a verified completion.
        """
        if self._is_relatives_page():
            return self._stale_family_choice_controllers(
                field_ids,
                field_values,
                field_labels,
            )
        if self._is_work_education2_page():
            return self._stale_work_education2_choice_controllers(
                field_ids,
                field_values,
                field_labels,
            )
        if self._is_us_contact_page():
            return self._stale_us_contact_controllers(
                field_ids,
                field_values,
                field_labels,
            )
        if not self._is_travel_page():
            return []
        values = dict(field_values or {})
        labels = dict(field_labels or {})
        stale = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            normalized = field_id.casefold()
            locator = None
            if normalized.endswith((
                ".travel.arrivaldate",
                ".travel.stayduration",
            )):
                approved = (
                    str(values.get(field_id) or "").strip()
                    or self._descriptor_approved_value(
                        labels.get(field_id) or ()
                    )
                )
                try:
                    self.rebind_page_fields_for_revalidation(
                        [field_id], labels
                    )
                    selector = self._field_selectors.get(field_id)
                    actual = (
                        self._live_control_value(field_id, selector, 1000)
                        if selector else ""
                    )
                except Exception:
                    # Failure to reconstruct a control is inconclusive.  Only
                    # an exact visible blank/different value may revoke the
                    # durable completion.
                    continue
                if normalized.endswith(".travel.arrivaldate"):
                    expected_parts = self._parse_iso_date(approved)
                    actual_parts = self._parse_iso_date(actual)
                    matches = bool(
                        expected_parts is not None
                        and actual_parts == expected_parts
                    )
                else:
                    expected_parts = self._travel_stay_duration_parts(
                        approved
                    )
                    actual_parts = self._travel_stay_duration_parts(actual)
                    matches = bool(
                        expected_parts is not None
                        and actual_parts is not None
                        and self._normalize_number(actual_parts[0])
                            == self._normalize_number(expected_parts[0])
                        and self._choice_matches(
                            expected_parts[1], actual_parts[1]
                        )
                        and self._travel_us_address_rendered()
                    )
                if not matches:
                    stale.append(field_id)
                continue
            if normalized.endswith(".travel.purpose.primary"):
                locator = self._travel_purpose_control(
                    "primary",
                    ("Purpose of Trip to the U.S.",),
                )
            elif normalized.endswith(".travel.purpose.secondary"):
                locator = self._travel_purpose_control(
                    "secondary",
                    ("Specify", "Specify visa class"),
                )
            elif normalized.endswith(".travel.payer"):
                locator = self._travel_payer_control()
            elif normalized.endswith(".travel.specific_plans"):
                group = self._prompt_scoped_choice_group(
                    self._travel_choice_terms(field_id)
                )
                approved = (
                    str(values.get(field_id) or "").strip()
                    or self._descriptor_approved_value(
                        labels.get(field_id) or ()
                    )
                )
                locator = (
                    self._travel_specific_plans_choice_control(
                        group, approved
                    )
                    if group is not None else None
                )
                if locator is not None:
                    try:
                        checked = bool(locator.is_checked(timeout=800))
                    except Exception:
                        checked = False
                    if (
                        not checked
                        or not self._travel_specific_plans_branch_rendered(
                            approved
                        )
                    ):
                        stale.append(field_id)
                continue
            else:
                continue
            if locator is None:
                continue
            try:
                selected = locator.evaluate(
                    """select => ({
                        value: String(select.value || ''),
                        text: (
                            select.selectedIndex >= 0 && select.options
                            ? String(
                                select.options[select.selectedIndex].text || ''
                            ) : ''
                        )
                    })"""
                )
            except Exception:
                continue
            approved = (
                str(values.get(field_id) or "").strip()
                or self._descriptor_approved_value(
                    labels.get(field_id) or ()
                )
            )
            candidate = " ".join(filter(None, (
                str((selected or {}).get("text") or ""),
                str((selected or {}).get("value") or ""),
            )))
            if approved and not self._choice_matches(approved, candidate):
                stale.append(field_id)
        return stale

    def _stale_us_contact_controllers(
        self,
        field_ids,
        field_values=None,
        field_labels=None,
    ):
        """Reopen U.S. Contact controllers reset by runtime reconnection."""
        values = dict(field_values or {})
        labels = dict(field_labels or {})
        available = {str(field_id) for field_id in field_ids or ()}
        suffixes = (
            ".us_contact.person.does_not_know",
            ".us_contact.organization",
            ".us_contact.relationship",
        )
        by_suffix = {
            suffix: next((
                field_id for field_id in available
                if field_id.casefold().endswith(suffix)
            ), "")
            for suffix in suffixes
        }
        stale = []
        unknown_id = by_suffix[suffixes[0]]
        if unknown_id:
            approved = (
                str(values.get(unknown_id) or "").strip()
                or self._descriptor_approved_value(
                    labels.get(unknown_id) or ()
                )
            )
            desired = self._boolean_choice(approved)
            if (
                desired is not None
                and not self._us_contact_person_toggle_consistent(desired)
            ):
                stale.append(unknown_id)

        organization_id = by_suffix[suffixes[1]]
        if organization_id:
            approved = (
                str(values.get(organization_id) or "").strip()
                or self._descriptor_approved_value(
                    labels.get(organization_id) or ()
                )
            )
            try:
                controls = self._page.locator(
                    'input[id$="_tbxUS_POC_ORGANIZATION"]'
                )
                actual = (
                    str(controls.first.input_value(timeout=800) or "").strip()
                    if controls.count() == 1 and controls.first.is_visible()
                    else None
                )
            except Exception:
                actual = None
            if actual is not None and re.sub(
                r"\s+", " ", actual
            ).strip().casefold() != re.sub(
                r"\s+", " ", approved
            ).strip().casefold():
                stale.append(organization_id)

        relationship_id = by_suffix[suffixes[2]]
        if relationship_id:
            approved = (
                str(values.get(relationship_id) or "").strip()
                or self._descriptor_approved_value(
                    labels.get(relationship_id) or ()
                )
            )
            relationship = self._us_contact_relationship_control()
            snapshot = (
                self._selected_option_snapshot(relationship)
                if relationship is not None else {}
            )
            candidate = " ".join(filter(None, (
                str(snapshot.get("text") or ""),
                str(snapshot.get("value") or ""),
            )))
            approved = self._canonical_us_contact_relationship(approved)
            if relationship is not None and (
                not approved
                or not self._choice_matches(approved, candidate)
            ):
                stale.append(relationship_id)
        return stale

    def _stale_family_choice_controllers(
        self,
        field_ids,
        field_values=None,
        field_labels=None,
    ):
        """Reopen Family radio completions lost after page restoration.

        CEAC can restore parent text/date values while leaving the Father,
        Mother and Immediate-Relatives radio groups unchecked. Retaining the
        durable completion in that state hides the final Other-Relatives
        group forever. A visible exact group with no checked option (or a
        different checked option) is authoritative stale evidence; a missing
        dependent group remains inconclusive.
        """
        values = dict(field_values or {})
        labels = dict(field_labels or {})
        stale = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            terms = self._family_choice_terms(field_id)
            if not terms:
                continue
            locator = self._family_choice_group(field_id, terms)
            if locator is None:
                continue
            try:
                selected = locator.evaluate(
                    """first => {
                        const name = String(first.name || '');
                        const items = Array.from(document.getElementsByName(name))
                            .filter(item => item.type === 'radio');
                        const checked = items.find(item => item.checked);
                        if (!checked) return {checked: false, candidate: ''};
                        const label = checked.id
                            ? document.querySelector(
                                'label[for="' + CSS.escape(checked.id) + '"]'
                            ) : null;
                        return {
                            checked: true,
                            candidate: [
                                String(checked.value || ''),
                                String(label ? label.innerText : '')
                            ].join(' ')
                        };
                    }"""
                )
            except Exception:
                continue
            approved = (
                str(values.get(field_id) or "").strip()
                or self._descriptor_approved_value(
                    labels.get(field_id) or ()
                )
            )
            if (
                not bool(dict(selected or {}).get("checked"))
                or (
                    approved
                    and not self._choice_matches(
                        approved,
                        dict(selected or {}).get("candidate"),
                    )
                )
            ):
                stale.append(field_id)
        return stale

    def _stale_work_education2_choice_controllers(
        self,
        field_ids,
        field_values=None,
        field_labels=None,
    ):
        """Reopen Work/Education 2 radios reset by browser restoration."""
        values = dict(field_values or {})
        labels = dict(field_labels or {})
        stale = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            terms = self._work_education2_choice_terms(field_id)
            if not terms:
                continue
            locator = self._prompt_scoped_choice_group(terms)
            if locator is None:
                continue
            try:
                selected = locator.evaluate(
                    """first => {
                        const items = Array.from(document.getElementsByName(
                            String(first.name || '')
                        )).filter(item => item.type === 'radio');
                        const checked = items.find(item => item.checked);
                        if (!checked) return {checked: false, candidate: ''};
                        const label = checked.id
                            ? document.querySelector(
                                'label[for="' + CSS.escape(checked.id) + '"]'
                            ) : null;
                        return {
                            checked: true,
                            candidate: [
                                String(checked.value || ''),
                                String(label ? label.innerText : '')
                            ].join(' ')
                        };
                    }"""
                )
            except Exception:
                continue
            approved = (
                str(values.get(field_id) or "").strip()
                or self._descriptor_approved_value(
                    labels.get(field_id) or ()
                )
            )
            if (
                not bool(dict(selected or {}).get("checked"))
                or (
                    approved
                    and not self._choice_matches(
                        approved,
                        dict(selected or {}).get("candidate"),
                    )
                )
            ):
                stale.append(field_id)
        return stale

    def _classify_personal2_field_presence(
        self,
        generic_result,
        field_ids,
    ):
        """Never hide Personal 2's required permanent-resident question.

        DocFlow's older descriptor says ``country of nationality`` while the
        live CEAC prompt says ``country/region of origin (nationality)
        indicated above``.  That wording difference previously classified the
        visible required radio group as inapplicable and unlocked Next.
        """
        scoped_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._personal2_choice_terms(field_id)
        ]
        if not scoped_ids:
            return generic_result
        present = set(generic_result.get("present") or ())
        absent = set(generic_result.get("absent") or ())
        unresolved = set(generic_result.get("unresolved") or ())
        for field_id in scoped_ids:
            present.discard(field_id)
            absent.discard(field_id)
            unresolved.discard(field_id)
            locator = self._prompt_scoped_choice_group(
                self._personal2_choice_terms(field_id)
            )
            if locator is None:
                # This is an unconditional required Personal 2 question.  A
                # failed read must keep it pending, never declare it absent.
                unresolved.add(field_id)
            else:
                present.add(field_id)
        ordered = [str(field_id) for field_id in field_ids or ()]
        return {
            "present": [item for item in ordered if item in present],
            "absent": [item for item in ordered if item in absent],
            "unresolved": [item for item in ordered if item in unresolved],
        }

    def _classify_passport_field_presence(
        self,
        generic_result,
        field_ids,
        field_labels,
    ):
        """Keep required Passport controls pending until exactly rebound."""
        scoped_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._passport_semantic_rule(field_id) is not None
        ]
        if not scoped_ids:
            return generic_result
        present = set(generic_result.get("present") or ())
        absent = set(generic_result.get("absent") or ())
        unresolved = set(generic_result.get("unresolved") or ())
        for field_id in scoped_ids:
            present.discard(field_id)
            absent.discard(field_id)
            unresolved.discard(field_id)
            rule = self._passport_semantic_rule(field_id)
            labels = tuple((field_labels or {}).get(field_id) or ())
            kind = self._control_kind(labels)
            if kind not in rule["kinds"]:
                # A stale provider checkpoint may still claim that Passport
                # City is a Does-Not-Apply checkbox. Production has no such
                # control. Never reinterpret that boolean as text.
                unresolved.add(field_id)
                continue
            locator = self._travel_semantic_control(
                rule["terms"], kind, section="", prefer_last=False,
            )
            if locator is None:
                unresolved.add(field_id)
            else:
                present.add(field_id)
        ordered = [str(field_id) for field_id in field_ids or ()]
        return {
            "present": [item for item in ordered if item in present],
            "absent": [item for item in ordered if item in absent],
            "unresolved": [item for item in ordered if item in unresolved],
        }

    def _classify_relatives_field_presence(
        self,
        generic_result,
        field_ids,
        field_labels,
    ):
        """Scope duplicate parent fields to the Father/Mother panels.

        Production CEAC labels both panels with the leaf text ``Surnames``,
        ``Given Names`` and ``Date of Birth``.  DocFlow's reviewed descriptors
        deliberately include ``Father's``/``Mother's`` for identity, so the
        generic page-wide presence probe could find neither exact label and
        incorrectly declare four visible required controls inapplicable.  A
        missing section-scoped binding is unresolved, never absent.
        """
        parent_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._family_semantic_rule(field_id) is not None
        ]
        choice_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._family_choice_terms(field_id)
        ]
        scoped_ids = list(dict.fromkeys([*parent_ids, *choice_ids]))
        if not scoped_ids:
            return generic_result
        present = set(generic_result.get("present") or ())
        absent = set(generic_result.get("absent") or ())
        unresolved = set(generic_result.get("unresolved") or ())
        for field_id in scoped_ids:
            present.discard(field_id)
            absent.discard(field_id)
            unresolved.discard(field_id)
            labels = tuple((field_labels or {}).get(field_id) or ())
            kind = self._control_kind(labels)
            rule = self._family_semantic_rule(field_id)
            choice_terms = self._family_choice_terms(field_id)
            if rule is not None:
                if kind not in rule["kinds"]:
                    unresolved.add(field_id)
                    continue
                locator = self._travel_semantic_control(
                    rule["terms"],
                    kind,
                    section=rule["section"],
                    prefer_last=False,
                )
            else:
                if kind != "yes_no" or not choice_terms:
                    unresolved.add(field_id)
                    continue
                locator = self._family_choice_group(
                    field_id,
                    choice_terms,
                )
            if locator is None:
                if (
                    self._is_dependent_family_choice(field_id)
                    and self._family_immediate_choice_answered()
                ):
                    # CEAC variants differ here. Some render a separate
                    # Other-Relatives radio shortly after the immediate-
                    # relatives controller; the current production page does
                    # not render that fourth question at all. Give the client
                    # branch a bounded chance to appear, then treat a still-
                    # absent exact control as authoritative inapplicability.
                    for delay_ms in (150, 250, 400, 700):
                        try:
                            self._page.wait_for_timeout(delay_ms)
                        except Exception:
                            break
                        locator = self._family_choice_group(
                            field_id,
                            choice_terms,
                        )
                        if locator is not None:
                            break
                    if locator is not None:
                        present.add(field_id)
                    else:
                        absent.add(field_id)
                else:
                    # Before the prerequisite controller is answered, a
                    # missing dependent question is inconclusive and must
                    # remain pending.
                    unresolved.add(field_id)
            else:
                present.add(field_id)
        ordered = [str(field_id) for field_id in field_ids or ()]
        return {
            "present": [item for item in ordered if item in present],
            "absent": [item for item in ordered if item in absent],
            "unresolved": [item for item in ordered if item in unresolved],
        }

    def _classify_work_field_presence(
        self,
        generic_result,
        field_ids,
        field_labels,
    ):
        """Keep employer fields pending across the occupation postback.

        CEAC initially renders only ``Primary Occupation`` and reveals the
        employer/school panel after that select is posted back.  The reveal can
        finish after the first new-page observation, so absence at that moment
        is not proof that reviewed employer fields are inapplicable.
        """
        scoped_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._work_semantic_rule(field_id) is not None
        ]
        if not scoped_ids:
            return generic_result
        present = set(generic_result.get("present") or ())
        absent = set(generic_result.get("absent") or ())
        unresolved = set(generic_result.get("unresolved") or ())
        for field_id in scoped_ids:
            present.discard(field_id)
            absent.discard(field_id)
            unresolved.discard(field_id)
            rule = self._work_semantic_rule(field_id)
            labels = tuple((field_labels or {}).get(field_id) or ())
            kind = self._control_kind(labels)
            if kind not in rule["kinds"]:
                unresolved.add(field_id)
                continue
            # These CEAC controls are explicitly optional.  A reviewed blank
            # value means there is intentionally nothing to transmit, even
            # though the empty input is visibly rendered.  Treating its mere
            # visibility as pending kept Work/Education 1 permanently locked
            # on Street Address (Line 2) and sent Gemini into a coordinate
            # repair loop for an empty value.
            approved = self._descriptor_approved_value(labels)
            if rule.get("optional_on_live_page") and not approved:
                absent.add(field_id)
                continue
            locator = self._travel_semantic_control(
                rule["terms"],
                kind,
                section="",
                prefer_last=False,
            )
            if locator is not None:
                present.add(field_id)
            elif rule.get("ignore_when_missing"):
                # Some reviewed profile fields are not rendered for every
                # CEAC occupation branch.  In particular Business does not
                # expose Job Title on the Present Work page.  Missing exact
                # label/control evidence is authoritative for this narrowly
                # allow-listed field and must not be sent to Gemini, where a
                # stale visual action could land on the next page's similarly
                # shaped Course of Study input.
                absent.add(field_id)
            else:
                # A non-empty reviewed optional value remains mandatory for
                # this run.  Missing live control evidence may never discard
                # it as inapplicable.
                unresolved.add(field_id)
        ordered = [str(field_id) for field_id in field_ids or ()]
        return {
            "present": [item for item in ordered if item in present],
            "absent": [item for item in ordered if item in absent],
            "unresolved": [item for item in ordered if item in unresolved],
        }

    def _classify_work_education2_field_presence(
        self,
        generic_result,
        field_ids,
        field_labels,
    ):
        """Do not submit Work/Education 2 before its Yes branch renders.

        CEAC may acknowledge the ``secondary level or above`` radio before
        the nine school controls have been inserted into the replacement DOM.
        The generic probe quite reasonably reports them absent in that short
        interval, but absence is not inapplicability when the reviewed answer
        is Yes.  Keep every school field pending (unresolved) until the live
        controls exist; the workflow's ordinary pending-field gate then makes
        it impossible to plan Next prematurely.
        """
        education_ids = [
            str(field_id)
            for field_id in field_ids or ()
            if ".work.education.record." in str(field_id).casefold()
        ]
        if not education_ids:
            return generic_result
        controller_id = next((
            str(field_id)
            for field_id in (field_labels or {})
            if str(field_id).casefold().endswith(
                ".work.education_secondary_or_above"
            )
        ), "")
        approved = self._descriptor_approved_value(
            (field_labels or {}).get(controller_id) or ()
        )
        if self._boolean_choice(approved) is not True:
            return generic_result

        present = set(generic_result.get("present") or ())
        absent = set(generic_result.get("absent") or ())
        unresolved = set(generic_result.get("unresolved") or ())
        for field_id in education_ids:
            present.discard(field_id)
            absent.discard(field_id)
            unresolved.discard(field_id)
            labels = tuple((field_labels or {}).get(field_id) or ())
            rule = self._work_education2_semantic_rule(field_id)
            kind = self._control_kind(labels)
            if rule is None or kind not in rule["kinds"]:
                unresolved.add(field_id)
                continue
            locator = self._work_education2_semantic_control(rule, kind)
            if locator is None:
                unresolved.add(field_id)
            else:
                present.add(field_id)
        ordered = [str(field_id) for field_id in field_ids or ()]
        return {
            "present": [item for item in ordered if item in present],
            "absent": [item for item in ordered if item in absent],
            "unresolved": [
                item for item in ordered if item in unresolved
            ],
        }

    def _classify_us_contact_field_presence(
        self,
        generic_result,
        field_ids,
        field_labels,
    ):
        """Never classify U.S. Contact's required address block as absent.

        Reviewed labels include a section prefix (``U.S. Contact Address``)
        while CEAC renders shorter leaf labels such as ``U.S. Street Address
        (Line 1)``.  The generic exact-label probe can therefore miss all
        seven controls and incorrectly unlock Next.  These controls are not a
        conditional branch on this page: a failed exact bind must remain
        unresolved and pending, never become inapplicable.
        """
        scoped_ids = [
            str(field_id)
            for field_id in field_ids or ()
            if self._us_contact_semantic_rule(field_id) is not None
        ]
        if not scoped_ids:
            return generic_result
        present = set(generic_result.get("present") or ())
        absent = set(generic_result.get("absent") or ())
        unresolved = set(generic_result.get("unresolved") or ())
        for field_id in scoped_ids:
            present.discard(field_id)
            absent.discard(field_id)
            unresolved.discard(field_id)
            rule = self._us_contact_semantic_rule(field_id)
            labels = tuple((field_labels or {}).get(field_id) or ())
            kind = self._control_kind(labels)
            if kind not in rule["kinds"]:
                unresolved.add(field_id)
                continue
            locator = self._us_contact_semantic_control(field_id, kind)
            if locator is None:
                unresolved.add(field_id)
            else:
                present.add(field_id)
        ordered = [str(field_id) for field_id in field_ids or ()]
        return {
            "present": [item for item in ordered if item in present],
            "absent": [item for item in ordered if item in absent],
            "unresolved": [item for item in ordered if item in unresolved],
        }

    def rebind_page_fields_for_revalidation(
        self,
        field_ids,
        field_labels=None,
    ):
        """Expose live Travel values through the new scoped selectors.

        This is intentionally read-only: it creates deterministic bindings but
        never executes the returned value actions.  The workflow verifier can
        then detect and repair a stale value left by an older V2 runtime.
        """
        labels = dict(field_labels or {})
        if self._is_travel_page():
            travel_ids = [
                str(field_id)
                for field_id in field_ids or ()
                if self._travel_semantic_rule(field_id) is not None
            ]
            _actions, unresolved = self._plan_travel_semantic_fallback(
                travel_ids,
                labels,
            )
            for field_id in travel_ids:
                if not field_id.casefold().endswith(
                    "travel.stayduration"
                ):
                    continue
                if self._rebind_travel_stay_duration_for_revalidation(
                    field_id,
                    tuple(labels.get(field_id) or ()),
                ):
                    unresolved = [
                        item for item in unresolved if item != field_id
                    ]
                    continue
                # A lone amount is not authoritative evidence for a duration
                # mismatch.  Remove its selector so the base audit classifies
                # this read as inconclusive instead of reopening the field.
                self.invalidate_field_binding(field_id)
                if field_id not in unresolved:
                    unresolved.append(field_id)
        elif self._is_passport_page():
            _actions, unresolved = self._plan_passport_semantic_fallback(
                [
                    str(field_id)
                    for field_id in field_ids or ()
                    if self._passport_semantic_rule(field_id) is not None
                ],
                labels,
            )
        elif self._is_relatives_page():
            parent_ids = [
                str(field_id)
                for field_id in field_ids or ()
                if self._family_semantic_rule(field_id) is not None
            ]
            choice_ids = [
                str(field_id)
                for field_id in field_ids or ()
                if self._family_choice_terms(field_id)
            ]
            _parent_actions, parent_unresolved = (
                self._plan_family_semantic_fallback(parent_ids, labels)
            )
            _choice_actions, choice_unresolved = (
                self._plan_family_choice_fallback(choice_ids, labels)
            )
            unresolved = list(dict.fromkeys([
                *parent_unresolved,
                *choice_unresolved,
            ]))
        elif self._is_work_education1_page():
            _actions, unresolved = self._plan_work_semantic_fallback(
                [
                    str(field_id)
                    for field_id in field_ids or ()
                    if self._work_semantic_rule(field_id) is not None
                    and not self._work_semantic_rule(field_id).get(
                        "optional_on_live_page"
                    )
                ],
                labels,
            )
        elif self._is_work_education2_page():
            school_ids = [
                str(field_id)
                for field_id in field_ids or ()
                if self._work_education2_semantic_rule(field_id) is not None
            ]
            _actions, school_unresolved = (
                self._plan_work_education2_semantic_fallback(
                    school_ids,
                    labels,
                )
            )
            # The two radio controllers retain their earlier exact checked
            # verification. Rebind only school fields here; stale-controller
            # auditing independently owns radio resets and postback replay.
            unresolved = list(dict.fromkeys([
                *school_unresolved,
                *[
                    str(field_id)
                    for field_id in field_ids or ()
                    if str(field_id) not in set(school_ids)
                ],
            ]))
        elif self._is_us_contact_page():
            _actions, unresolved = self._plan_us_contact_semantic_fallback(
                [
                    str(field_id)
                    for field_id in field_ids or ()
                    if self._us_contact_semantic_rule(field_id) is not None
                ],
                labels,
            )
        elif self._is_address_phone_page():
            exact_ids = [
                str(field_id)
                for field_id in field_ids or ()
                if self._address_phone_exact_rule(field_id) is not None
            ]
            _actions, exact_unresolved = (
                self._plan_address_phone_semantic_fallback(
                    exact_ids,
                    labels,
                )
            )
            unresolved = list(dict.fromkeys([
                *exact_unresolved,
                *(
                    str(field_id)
                    for field_id in field_ids or ()
                    if str(field_id) not in set(exact_ids)
                ),
            ]))
        else:
            return []
        return unresolved

    def _is_travel_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            "complete_travel.aspx" in current_url
            and "node=travel" in current_url
        )

    def _is_address_phone_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            "complete_contact.aspx" in current_url
            and "node=addressphone" in current_url
        )

    @staticmethod
    def _address_phone_exact_rule(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".address_phone.contact.homeregion",
                "APP_ADDR_STATE",
            ),
            (
                ".address_phone.contact.homepostalcode",
                "APP_ADDR_POSTAL_CD",
            ),
            (
                ".address_phone.contact.secondaryphone",
                "APP_MOBILE_TEL",
            ),
            (
                ".address_phone.contact.workphone",
                "APP_BUS_TEL",
            ),
        )
        for suffix, control_token in rules:
            if normalized.endswith(suffix):
                return {
                    "text": f"tbx{control_token}",
                    "checkbox": f"cbex{control_token}_NA",
                    "hidden": f"tbx{control_token}_NA",
                }
        return None

    @classmethod
    def _address_phone_dna_requested(cls, value):
        normalized = " ".join(str(value or "").strip().upper().split())
        return normalized in {
            "1", "TRUE", "Y", "YES", "D", "DNA", "N/A",
            "DOES NOT APPLY", "NOT APPLICABLE", "DO NOT KNOW", "UNKNOWN",
        }

    def _address_phone_exact_control(self, field_id, *, dna=False):
        rule = self._address_phone_exact_rule(field_id)
        if rule is None:
            return None
        selector = (
            'input[type="checkbox"][id$="' + rule["checkbox"] + '"]'
            if dna
            else 'input[type="text"][id$="' + rule["text"] + '"]'
        )
        try:
            controls = self._page.locator(selector)
            if controls.count() == 1 and controls.first.is_visible(timeout=800):
                return controls.first
        except Exception:
            pass
        return None

    def _address_phone_exact_dna_state(self, field_id):
        rule = self._address_phone_exact_rule(field_id)
        if rule is None:
            return {}
        try:
            return dict(self._page.evaluate(
                """rule => {
                    const checkbox = document.querySelector(
                        'input[type="checkbox"][id$="' + rule.checkbox + '"]'
                    );
                    const hidden = document.querySelector(
                        'input[type="hidden"][id$="' + rule.hidden + '"]'
                    );
                    const text = document.querySelector(
                        'input[type="text"][id$="' + rule.text + '"]'
                    );
                    return {
                        found: Boolean(checkbox),
                        checked: Boolean(checkbox?.checked),
                        hiddenFound: Boolean(hidden),
                        hiddenValue: String(hidden?.value || ''),
                        textFound: Boolean(text),
                        textDisabled: Boolean(text?.disabled),
                        textValue: String(text?.value || ''),
                    };
                }""",
                rule,
            ) or {})
        except Exception:
            return {}

    def _address_phone_postal_dna_control(self):
        return self._address_phone_exact_control(
            "ceac.address_phone.contact.homepostalcode",
            dna=True,
        )

    def _address_phone_postal_dna_state(self):
        return self._address_phone_exact_dna_state(
            "ceac.address_phone.contact.homepostalcode"
        )

    @classmethod
    def _address_phone_postal_dna_consistent(cls, state, desired):
        snapshot = dict(state or {})
        if not snapshot.get("found"):
            return False
        checked = bool(snapshot.get("checked"))
        if snapshot.get("hiddenFound"):
            hidden = cls._boolean_choice(snapshot.get("hiddenValue"))
            if hidden is None or hidden != bool(desired):
                return False
            if desired:
                # CEAC's ExtendedCheckBox occasionally leaves its visual bit
                # false while its server-bound hidden value is Y and the
                # paired text input is disabled.  That pair is the actual
                # submitted D/N/A state and is stronger evidence than the
                # transient renderer bit.
                return bool(
                    checked
                    or (
                        snapshot.get("textFound")
                        and snapshot.get("textDisabled")
                    )
                )
            return not checked
        return checked == bool(desired)

    def _execute_address_phone_dna(self, action):
        """Use a trusted click and prove CEAC's companion hidden value.

        ``HTMLElement.click()`` can toggle the transient DOM bit without
        making CEAC's D/N/A controller persist the companion Y/N field.  A
        Playwright input click follows the real mouse event path; completion
        is acknowledged only when both states agree with the reviewed value.
        """
        field_id = str(action.field_id or "")
        desired = self._address_phone_dna_requested(action.value)
        locator = self._address_phone_exact_control(field_id, dna=True)
        if not desired or locator is None:
            self.invalidate_field_binding(field_id)
            return
        self._require_page()
        self._mark_field(locator, action)
        state = self._address_phone_exact_dna_state(field_id)
        hidden = self._boolean_choice(state.get("hiddenValue"))
        if (
            desired
            and state.get("found")
            and state.get("checked")
            and state.get("hiddenFound")
            and hidden is False
        ):
            # CEAC can restore the renderer's checked bit without restoring
            # the ExtendedCheckBox hidden Y value. One click only clears the
            # stale visual bit; the second normal click is then required to
            # produce the real checked/Y/disabled state.
            if self._visual_execution:
                self._move_pointer_to_locator(locator, clicking=True)
            locator.click(timeout=3000)
            try:
                self._page.wait_for_timeout(120)
            except Exception:
                pass
            locator = self._address_phone_exact_control(field_id, dna=True)
            state = self._address_phone_exact_dna_state(field_id)
        if not self._address_phone_postal_dna_consistent(state, desired):
            if locator is None:
                self.invalidate_field_binding(field_id)
                return
            if self._visual_execution:
                self._move_pointer_to_locator(locator, clicking=True)
            locator.click(timeout=3000)
            try:
                self._page.wait_for_timeout(180)
            except Exception:
                pass
        state = self._address_phone_exact_dna_state(field_id)
        if not self._address_phone_postal_dna_consistent(state, desired):
            self.invalidate_field_binding(field_id)
            self._verified_field_values.pop(field_id, None)
            return
        rebound = self._address_phone_exact_control(field_id, dna=True)
        if rebound is not None:
            self._mark_field(rebound, action)
        self._verified_field_values[field_id] = action.value
        self._acknowledged.append(action.id)

    # Backward-compatible name retained for focused tests and older callers.
    def _execute_address_phone_postal_dna(self, action):
        return self._execute_address_phone_dna(action)

    def address_phone_exact_value_matches(self, field_id, approved_value):
        if (
            not self._is_address_phone_page()
            or self._address_phone_exact_rule(field_id) is None
        ):
            return None
        if self._address_phone_dna_requested(approved_value):
            state = self._address_phone_exact_dna_state(field_id)
            if not state.get("found"):
                return None
            return self._address_phone_postal_dna_consistent(state, True)
        locator = self._address_phone_exact_control(field_id, dna=False)
        if locator is None:
            return None
        try:
            return str(locator.input_value(timeout=800) or "").strip() == str(
                approved_value or ""
            ).strip()
        except Exception:
            return None

    def address_phone_dna_value_matches(self, field_id, approved_value):
        """Read the stable home-postal D/N/A checkbox after postbacks.

        CEAC replaces the surrounding Address/Phone markup after several
        unrelated Yes/No postbacks.  The generic verified-field marker is
        therefore no longer resolvable even though the native checkbox keeps
        a stable ASP.NET id.  Return ``None`` unless this exact control can be
        proved; callers may safely treat ``False`` as an authoritative reset.
        """
        normalized = str(field_id or "").casefold()
        if (
            not self._is_address_phone_page()
            or not normalized.endswith(
                ".address_phone.contact.homepostalcode"
            )
        ):
            return None
        return self.address_phone_exact_value_matches(
            field_id,
            approved_value,
        )

    def address_phone_exact_dna_state(self, field_id):
        """Expose a read-only stable D/N/A snapshot to V2 workflow audits."""
        if (
            not self._is_address_phone_page()
            or self._address_phone_exact_rule(field_id) is None
        ):
            return {}
        return self._address_phone_exact_dna_state(field_id)

    def _is_us_contact_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            "complete_uscontact.aspx" in current_url
            and "node=uscontact" in current_url
        )

    def _is_passport_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            (
                "complete_pptvisa.aspx" in current_url
                # The live CEAC deployment currently uses this physical
                # filename while the synthetic acceptance site and older
                # deployments use ``complete_pptvisa.aspx``.  The former was
                # still classified as the Passport page by its ``node`` but
                # missed V2's deterministic composite binder, sending City,
                # Authority and Issuance Date to slow Gemini coordinates.
                or "passport_visa_info.aspx" in current_url
            )
            and (
                "node=pptvisa" in current_url
                or "node=passport" in current_url
            )
        )

    def _is_relatives_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            "complete_family1.aspx" in current_url
            and "node=relatives" in current_url
        )

    def _is_personal2_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            "complete_personalcont.aspx" in current_url
            and "node=personal2" in current_url
        )

    def _is_work_education1_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            "complete_workeducation1.aspx" in current_url
            and "node=workeducation1" in current_url
        )

    def _is_work_education2_page(self):
        try:
            current_url = str(self._page.url or "").casefold()
        except Exception:
            return False
        return bool(
            "complete_workeducation2.aspx" in current_url
            and "node=workeducation2" in current_url
        )

    def plan_choice_fields(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        """Bind repeated CEAC questions to their exact radio group.

        CEAC renders several adjacent Yes/No groups whose individual controls
        are labelled only ``Yes`` and ``No``.  A visual coordinate therefore
        cannot prove which reviewed question owns the group.  Production also
        says ``other phone numbers`` while older metadata said ``telephone``.
        On the exact Address/Phone route, bind these stable questions by their
        complete prompt and vertical group relationship before allowing the
        generic resolver to handle every other choice field.
        """
        labels = dict(field_labels or {})
        hints = dict(control_hints or {})
        approved = [str(field_id) for field_id in field_ids or ()]
        if self._is_personal2_page():
            scoped_ids = [
                field_id for field_id in approved
                if self._personal2_choice_terms(field_id)
            ]
            scoped = set(scoped_ids)
            generic_ids = [
                field_id for field_id in approved if field_id not in scoped
            ]
            actions, unresolved = super().plan_choice_fields(
                generic_ids, labels, hints,
            )
            for field_id in scoped_ids:
                locator = self._prompt_scoped_choice_group(
                    self._personal2_choice_terms(field_id)
                )
                if locator is None:
                    unresolved.append(field_id)
                    continue
                action = ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC Personal 2 prompt-scoped radio match "
                        f"[field_id={field_id}]"
                    ),
                )
                try:
                    self._mark_field(locator, action)
                except Exception:
                    unresolved.append(field_id)
                    continue
                actions.append(action)
            return actions, unresolved
        if self._is_relatives_page():
            scoped_ids = [
                field_id for field_id in approved
                if self._family_choice_terms(field_id)
            ]
            scoped = set(scoped_ids)
            generic_ids = [
                field_id for field_id in approved if field_id not in scoped
            ]
            generic_actions, generic_unresolved = (
                super().plan_choice_fields(generic_ids, labels, hints)
            )
            scoped_actions, scoped_unresolved = (
                self._plan_family_choice_fallback(scoped_ids, labels)
            )
            return (
                [*generic_actions, *scoped_actions],
                [*generic_unresolved, *scoped_unresolved],
            )
        if self._is_travel_page():
            scoped_ids = [
                field_id for field_id in approved
                if self._travel_choice_terms(field_id)
            ]
            scoped = set(scoped_ids)
            generic_ids = [
                field_id for field_id in approved if field_id not in scoped
            ]
            actions, unresolved = super().plan_choice_fields(
                generic_ids, labels, hints,
            )
            for field_id in scoped_ids:
                locator = self._prompt_scoped_choice_group(
                    self._travel_choice_terms(field_id)
                )
                if locator is None:
                    unresolved.append(field_id)
                    continue
                action = ComputerAction(
                    kind=ActionKind.SELECT,
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC Travel prompt-scoped radio match "
                        f"[field_id={field_id}]"
                    ),
                )
                try:
                    self._mark_field(locator, action)
                except Exception:
                    unresolved.append(field_id)
                    continue
                actions.append(action)
            return actions, unresolved
        if not self._is_address_phone_page():
            return super().plan_choice_fields(approved, labels, hints)

        scoped_ids = [
            field_id for field_id in approved
            if self._address_phone_choice_terms(field_id)
        ]
        scoped = set(scoped_ids)
        generic_ids = [
            field_id for field_id in approved if field_id not in scoped
        ]
        generic_actions, generic_unresolved = super().plan_choice_fields(
            generic_ids,
            labels,
            hints,
        )

        actions = list(generic_actions)
        unresolved = list(generic_unresolved)
        for field_id in scoped_ids:
            locator = self._prompt_scoped_choice_group(
                self._address_phone_choice_terms(field_id)
            )
            if locator is None:
                unresolved.append(field_id)
                continue
            action = ComputerAction(
                kind=ActionKind.SELECT,
                field_id=field_id,
                target_hint=field_id,
                reason=(
                    "V2 CEAC Address/Phone prompt-scoped radio match "
                    f"[field_id={field_id}]"
                ),
            )
            try:
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    @staticmethod
    def _personal2_choice_terms(field_id):
        normalized = str(field_id or "").casefold()
        if ".personal.permanent_resident_other_country" not in normalized:
            return ()
        return (
            "are you a permanent resident of a country region other than "
            "your country region of origin nationality indicated above",
            "permanent resident of a country region other than your country "
            "region of origin",
            "permanent resident of a country region other than your country "
            "of nationality",
        )

    @staticmethod
    def _travel_choice_terms(field_id):
        normalized = str(field_id or "").casefold()
        if not normalized.endswith(".travel.specific_plans"):
            return ()
        return (
            "have you made specific travel plans",
        )

    def unanswered_visible_choice_fields(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        """Return exact visible approved radio groups with no selected value.

        This is the final read-only gate before Next.  It does not infer a
        field from position: only descriptor-bound groups returned by the
        normal semantic choice planner are inspected.
        """
        labels = dict(field_labels or {})
        hints = dict(control_hints or {})
        choice_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._control_kind(labels.get(str(field_id)) or ())
            == "yes_no"
        ]
        if not choice_ids:
            return []
        actions, _unresolved = self.plan_choice_fields(
            choice_ids, labels, hints,
        )
        unanswered = []
        for action in actions:
            selector = self._field_selectors.get(str(action.field_id or ""))
            if not selector:
                continue
            try:
                selected = bool(self._page.locator(selector).first.evaluate(
                    """el => {
                        const name = String(el.name || '');
                        if (!name) return false;
                        const scope = el.form || document;
                        return Array.from(scope.querySelectorAll(
                            'input[type="radio"][name]'
                        )).some(item => (
                            String(item.name || '') === name
                            && item.checked
                        ));
                    }"""
                ))
            except Exception:
                continue
            if not selected:
                unanswered.append(str(action.field_id))
        return list(dict.fromkeys(unanswered))

    @staticmethod
    def _address_phone_choice_terms(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".contact.other_phones",
                (
                    "have you used any other phone numbers in the last five years",
                    "have you used any other telephone numbers in the last five years",
                    "other phone numbers in the last five years",
                    "other telephone numbers in the last five years",
                ),
            ),
            (
                ".contact.other_emails",
                (
                    "have you used any other email addresses in the last five years",
                    "other email addresses in the last five years",
                ),
            ),
            (
                ".contact.social_media",
                (
                    "do you have a social media presence",
                    "social media presence",
                ),
            ),
            (
                ".contact.other_platforms",
                (
                    "presence on any other websites or applications",
                    "other websites or applications you have used within the last five years",
                    "other websites or applications",
                ),
            ),
        )
        for token, terms in rules:
            if token in normalized:
                return terms
        return ()

    def _prompt_scoped_choice_group(self, terms):
        """Return the first radio in the one group owned by an exact prompt."""
        token = f"v2-address-phone-{uuid4().hex}"
        try:
            found = bool(self._page.evaluate(
                """([rawTerms, token]) => {
                    const norm = value => String(value || '')
                        .toLowerCase()
                        .replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ')
                        .replace(/\s+/g, ' ').trim();
                    const terms = rawTerms.map(norm).filter(Boolean);
                    const visible = item => {
                        if (!item || item.disabled) return false;
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const radios = Array.from(document.querySelectorAll(
                        'input[type="radio"][name]'
                    )).filter(visible);
                    const grouped = new Map();
                    for (const radio of radios) {
                        const name = String(radio.name || '');
                        if (!grouped.has(name)) grouped.set(name, []);
                        grouped.get(name).push(radio);
                    }
                    const groups = Array.from(grouped.values()).map(items => {
                        const boxes = items.map(
                            item => item.getBoundingClientRect()
                        );
                        return {
                            items,
                            box: {
                                left: Math.min(...boxes.map(box => box.left)),
                                top: Math.min(...boxes.map(box => box.top)),
                                right: Math.max(...boxes.map(box => box.right)),
                                bottom: Math.max(...boxes.map(box => box.bottom))
                            }
                        };
                    });
                    if (!groups.length) return false;
                    const groupNamesWithin = element => new Set(
                        Array.from(element.querySelectorAll(
                            'input[type="radio"][name]'
                        )).filter(visible).map(item => item.name)
                    );
                    const candidates = Array.from(document.querySelectorAll(
                        'label, legend, span, div, td, th, p, strong, b'
                    )).filter(visible).map(element => ({
                        element,
                        text: norm(element.innerText),
                        box: element.getBoundingClientRect()
                    })).filter(item => (
                        item.text.length >= 12
                        && item.text.length <= 1400
                        && terms.some(term => item.text.includes(term))
                    )).sort((left, right) => (
                        left.text.length - right.text.length
                        || left.box.top - right.box.top
                    ));
                    const ranked = [];
                    for (const prompt of candidates) {
                        for (const group of groups) {
                            let structure = 0;
                            let current = prompt.element;
                            for (let depth = 0; depth <= 6 && current; depth += 1) {
                                if (
                                    current.contains(group.items[0])
                                    && groupNamesWithin(current).size === 1
                                ) {
                                    structure = Math.max(700, 1100 - depth * 55);
                                    break;
                                }
                                current = current.parentElement;
                            }
                            const below = group.box.top - prompt.box.bottom;
                            const horizontal = Math.abs(
                                group.box.left - prompt.box.left
                            );
                            let geometry = 0;
                            if (
                                below >= -24 && below <= 260
                                && horizontal <= 900
                            ) {
                                geometry = 520 - Math.max(0, below) * 0.8
                                    - horizontal * 0.04;
                            }
                            const score = Math.max(structure, geometry)
                                + Math.max(0, 260 - prompt.text.length * 0.1);
                            if (score > 300) ranked.push({group, score});
                        }
                    }
                    ranked.sort((left, right) => right.score - left.score);
                    if (!ranked.length) return false;
                    const best = ranked[0];
                    const runner = ranked.find(
                        item => item.group !== best.group
                    );
                    if (runner && best.score - runner.score < 45) return false;
                    best.group.items[0].setAttribute(
                        'data-docflow-v2-address-phone-choice', token
                    );
                    return true;
                }""",
                [list(terms or ()), token],
            ))
        except Exception:
            return None
        if not found:
            return None
        locator = self._page.locator(
            '[data-docflow-v2-address-phone-choice="' + token + '"]'
        )
        return locator.first if locator.count() == 1 else None

    def _family_choice_group(self, field_id, terms):
        """Bind a Family radio by prompt, then exact CEAC group identity.

        Production can render the final ``other relatives`` prompt inside a
        large table cell whose text also contains the preceding immediate-
        relatives question.  The generic prompt scorer correctly rejects that
        ambiguous geometry.  CEAC still gives the final radio group a stable
        OtherRelatives identity, so use that identity only for this one exact
        dependent field.  A page exposing only the immediate-relative group
        never satisfies this fallback.
        """
        locator = self._prompt_scoped_choice_group(terms)
        if locator is not None:
            return locator
        if not self._is_dependent_family_choice(field_id):
            return None
        token = f"v2-family-other-relative-{uuid4().hex}"
        try:
            found = bool(self._page.evaluate(
                """token => {
                    const visible = item => {
                        if (!item || item.disabled) return false;
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const compact = value => String(value || '')
                        .toLowerCase().replace(/[^a-z0-9]+/g, '');
                    const grouped = new Map();
                    for (const radio of Array.from(
                        document.querySelectorAll('input[type="radio"][name]')
                    ).filter(visible)) {
                        const name = String(radio.name || '');
                        if (!grouped.has(name)) grouped.set(name, []);
                        grouped.get(name).push(radio);
                    }
                    const matches = Array.from(grouped.values()).filter(items => {
                        const identity = compact([
                            items[0].name,
                            ...items.map(item => item.id)
                        ].join(' '));
                        if (identity.includes('immediate')) return false;
                        return identity.includes('otherrelatives')
                            || identity.includes('otherrelativefollowup');
                    });
                    if (matches.length !== 1) return false;
                    matches[0][0].setAttribute(
                        'data-docflow-v2-family-other-relative', token
                    );
                    return true;
                }""",
                token,
            ))
        except Exception:
            return None
        if not found:
            return None
        locator = self._page.locator(
            '[data-docflow-v2-family-other-relative="' + token + '"]'
        )
        return locator.first if locator.count() == 1 else None

    def _family_immediate_choice_answered(self):
        field_id = "ceac.relatives.family.immediate_relatives_us"
        locator = self._family_choice_group(
            field_id,
            self._family_choice_terms(field_id),
        )
        if locator is None:
            return False
        try:
            return bool(locator.evaluate(
                """first => Array.from(
                    document.getElementsByName(String(first.name || ''))
                ).some(item => item.type === 'radio' && item.checked)"""
            ))
        except Exception:
            return False

    def family_other_relative_value_matches(self, action):
        """Prove the late-rendered Family radio from its live checked input."""
        field_id = str(getattr(action, "field_id", "") or "")
        if not self._is_dependent_family_choice(field_id):
            return False
        locator = self._family_choice_group(
            field_id,
            self._family_choice_terms(field_id),
        )
        if locator is None:
            return False
        try:
            candidate = locator.evaluate(
                """first => {
                    const items = Array.from(document.getElementsByName(
                        String(first.name || '')
                    )).filter(item => item.type === 'radio');
                    const checked = items.find(item => item.checked);
                    if (!checked) return '';
                    const label = checked.id
                        ? document.querySelector(
                            `label[for="${CSS.escape(checked.id)}"]`
                        )
                        : null;
                    return [checked.value, checked.id, label?.textContent || '']
                        .join(' ');
                }"""
            )
        except Exception:
            return False
        return self._choice_matches(
            getattr(action, "value", ""),
            candidate,
        )

    # Kept for callers outside V2 that still use the old page-specific name.
    def _address_phone_choice_group(self, terms):
        return self._prompt_scoped_choice_group(terms)

    def plan_fields(self, field_ids, field_labels=None, control_hints=None):
        """Give a just-rendered CEAC branch one cheap semantic settle window.

        ASP.NET can finish the network request before dependent selects and
        inputs become visible to Playwright. Falling through immediately used
        to start a 30-second Gemini request for controls that appeared a few
        hundred milliseconds later. Poll the deterministic binder for less
        than one second and bring the first unresolved field into view before
        paying any provider latency.
        """
        actions, unresolved = self._plan_semantic_fields_once(
            field_ids,
            field_labels,
            control_hints,
        )
        if actions or not unresolved:
            return actions, unresolved

        labels = dict(field_labels or {})
        hints = dict(control_hints or {})
        self._scroll_to_pending_semantic_evidence(
            unresolved[0],
            labels.get(unresolved[0]) or (),
            hints.get(unresolved[0]) or (),
        )
        delays = (120, 180, 250, 350)
        if (
            self._is_relatives_page()
            and any(
                self._is_dependent_family_choice(field_id)
                for field_id in unresolved
            )
        ):
            # The live Relatives page may reveal its final Yes/No group with
            # delayed client-side script and no observable postback.  Spend a
            # bounded extra ~2 seconds only at that exact dependency instead
            # of falling through to slow Gemini coordinates or clicking Next
            # before CEAC has exposed the control.
            delays = (*delays, 500, 700, 900)
        for delay_ms in delays:
            try:
                self._page.wait_for_timeout(delay_ms)
            except Exception:
                break
            retry_actions, retry_unresolved = self._plan_semantic_fields_once(
                unresolved,
                labels,
                hints,
            )
            if retry_actions or not retry_unresolved:
                return retry_actions, retry_unresolved
            unresolved = retry_unresolved
        replay_actions = self._plan_missing_work_branch_replay(
            unresolved,
            labels,
        )
        if replay_actions:
            return replay_actions, unresolved
        replay_actions = self._plan_missing_travel_branch_replay(
            unresolved,
            labels,
            hints,
        )
        if replay_actions:
            return replay_actions, unresolved
        replay_actions = self._plan_missing_us_contact_branch_replay(
            unresolved,
        )
        if replay_actions:
            return replay_actions, unresolved
        return [], unresolved

    def _plan_missing_us_contact_branch_replay(self, unresolved):
        """Rebuild the mandatory U.S. Contact address block once.

        A retained CEAC page can show a selected Relationship and checked
        Contact Person ``Do Not Know`` bit while the checkbox's dependent UI
        script never ran.  The following relationship postback then returns a
        truncated FormView with no address/phone/email controls.  Replaying
        that exact already-selected relationship through the placeholder is
        bounded, idempotent, and uses the same native select path as a person.
        """
        if (
            not self._is_us_contact_page()
            or self._v2_us_contact_reopen_attempted
            or self._us_contact_address_rendered()
            or not any(
                self._us_contact_semantic_rule(field_id) is not None
                for field_id in unresolved or ()
            )
        ):
            return []
        relationship = self._us_contact_relationship_control()
        if relationship is None:
            return []
        snapshot = self._selected_option_snapshot(relationship)
        if (
            snapshot.get("tag") != "select"
            or not str(snapshot.get("value") or "").strip()
            or not str(snapshot.get("text") or "").strip()
        ):
            return []
        field_id = "ceac.us_contact.us_contact.relationship"
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value=str(snapshot.get("text") or "").strip(),
            reason=(
                "V2 CEAC U.S. Contact missing-address-branch controller "
                f"replay [field_id={field_id}]"
            ),
        )
        self._mark_field(relationship, action)
        self._v2_us_contact_reopen_attempted = True
        self._v2_forced_us_contact_relationship_ids.add(field_id)
        return [action]

    def _plan_missing_travel_branch_replay(
        self,
        unresolved,
        labels,
        hints,
    ):
        """Repost Travel Purpose once when its reviewed branch is missing.

        The first Travel purpose select owns CEAC's dependent ``Specify visa
        class`` control.  If an older parallel V2 process interrupts that
        ASP.NET transition, the primary select can visibly retain ``B`` while
        the Specify/date/address branch is absent.  Re-selecting the same
        reviewed primary option is idempotent and replays its real change
        postback; a per-page flag prevents a loop if CEAC still refuses to
        render the branch.
        """
        if (
            not self._is_travel_page()
            or self._v2_travel_purpose_reopen_attempted
        ):
            return []
        secondary_id = next((
            str(field_id)
            for field_id in labels
            if str(field_id).casefold().endswith(
                ".travel.purpose.secondary"
            )
        ), "")
        if (
            not secondary_id
            or secondary_id not in set(str(item) for item in unresolved or ())
            or not self._descriptor_approved_value(
                labels.get(secondary_id) or ()
            )
        ):
            return []
        primary_id = next((
            str(field_id)
            for field_id in labels
            if str(field_id).casefold().endswith(
                ".travel.purpose.primary"
            )
        ), "")
        if not primary_id:
            return []
        actions, _controller_unresolved = (
            self._plan_travel_semantic_fallback(
                [primary_id],
                labels,
            )
        )
        if len(actions) != 1:
            return []
        actions[0].reason = (
            "V2 CEAC Travel missing-purpose-branch controller replay "
            f"[field_id={primary_id}]"
        )
        self._v2_travel_purpose_reopen_attempted = True
        self._v2_forced_travel_purpose_field_ids.add(primary_id)
        return actions

    def model_fallback_block_reason(self, field_ids):
        """Keep a missing required Travel branch out of visual guessing."""
        if (
            self._is_us_contact_page()
            and any(
                self._us_contact_semantic_rule(field_id) is not None
                for field_id in field_ids or ()
            )
        ):
            return (
                "U.S. Contact 的地址、电话或邮箱属于必填的"
                "精确 DOM 字段，但当前页面未建立可核验的"
                "控件绑定；系统已停止 Gemini 滚动/坐标猜测，"
                "不会点击 Next。"
            )
        if (
            self._is_travel_page()
            and self._v2_travel_purpose_reopen_attempted
            and any(
                str(field_id).casefold().endswith(
                    ".travel.purpose.secondary"
                )
                for field_id in field_ids or ()
            )
        ):
            return (
                "V2 已尝试用系统级鼠标和键盘重放 Travel 主用途，"
                "但尚未核验到 CEAC 生成必填的 Specify visa class；"
                "系统已停止，"
                "不会点击 Next，也不会交替填写日期、地址或付款人字段。"
            )
        return ""

    def model_fallback_diagnostics(self, field_ids):
        """Expose bounded read-only evidence for unresolved Family radios."""
        if not (
            self._is_relatives_page()
            and any(
                self._is_dependent_family_choice(field_id)
                for field_id in field_ids or ()
            )
        ):
            return {}
        try:
            diagnostics = self._page.evaluate(
                """() => {
                    const visible = item => {
                        if (!item || item.disabled) return false;
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const grouped = new Map();
                    for (const radio of Array.from(
                        document.querySelectorAll('input[type="radio"][name]')
                    ).filter(visible)) {
                        const name = String(radio.name || '');
                        if (!grouped.has(name)) grouped.set(name, []);
                        grouped.get(name).push(radio);
                    }
                    const visibleGroups = Array.from(grouped.entries()).slice(-6).map(
                        ([name, items]) => {
                            const first = items[0];
                            let scope = first.parentElement;
                            let text = '';
                            for (let depth = 0; depth < 5 && scope; depth += 1) {
                                text = String(scope.innerText || '')
                                    .replace(/\s+/g, ' ').trim();
                                if (text.length >= 12 && text.length <= 500) break;
                                scope = scope.parentElement;
                            }
                            return {
                                name: String(name || ''),
                                ids: items.map(item => String(item.id || '')),
                                values: items.map(item => String(item.value || '')),
                                checked: items.map(item => Boolean(item.checked)),
                                nearbyText: text.slice(0, 500)
                            };
                        }
                    );
                    const relativeControls = Array.from(
                        document.querySelectorAll('input, select')
                    ).filter(item => {
                        const identity = [item.id, item.name].join(' ')
                            .toLowerCase();
                        return identity.includes('relat');
                    }).slice(0, 30).map(item => {
                        const style = getComputedStyle(item);
                        const box = item.getBoundingClientRect();
                        return {
                            tag: String(item.tagName || '').toLowerCase(),
                            type: String(item.type || ''),
                            id: String(item.id || ''),
                            name: String(item.name || ''),
                            value: String(item.value || ''),
                            checked: Boolean(item.checked),
                            display: String(style.display || ''),
                            visibility: String(style.visibility || ''),
                            width: Number(box.width || 0),
                            height: Number(box.height || 0)
                        };
                    });
                    const relativePrompts = Array.from(
                        document.querySelectorAll('span, label, legend, td, th')
                    ).map(item => ({
                        id: String(item.id || ''),
                        text: String(item.innerText || '')
                            .replace(/\s+/g, ' ').trim()
                    })).filter(item => (
                        item.text.toLowerCase().includes('relativ')
                        && item.text.length <= 500
                    )).slice(0, 20);
                    return {visibleGroups, relativeControls, relativePrompts};
                }"""
            )
        except Exception as error:
            return {"diagnosticError": type(error).__name__}
        diagnostics = dict(diagnostics or {})
        return {
            "familyRadioGroups": list(
                diagnostics.get("visibleGroups") or ()
            ),
            "familyRelativeControls": list(
                diagnostics.get("relativeControls") or ()
            ),
            "familyRelativePrompts": list(
                diagnostics.get("relativePrompts") or ()
            ),
        }

    def _plan_missing_work_branch_replay(self, unresolved, labels):
        """Re-fire a restored Work controller only when its branch is missing."""
        if (
            not self._is_work_education1_page()
            or self._v2_work_reopen_attempted
        ):
            return []
        dependent_pending = any(
            self._is_required_work_dependent(field_id)
            for field_id in unresolved or ()
        )
        if not dependent_pending:
            return []
        primary_id = next(
            (
                str(field_id)
                for field_id in labels
                if ".work.primary_occupation" in str(field_id).casefold()
            ),
            "",
        )
        if not primary_id:
            # After a process restart the provider correctly sends only the
            # ten still-pending employer fields. The already verified
            # controller label is no longer in that subset, but every semantic
            # Work field retains the same provider-owned page prefix. Recover
            # the exact controller ID from that prefix instead of falling back
            # to Gemini merely because a completed field was omitted.
            pending_work_id = next((
                str(field_id)
                for field_id in unresolved or ()
                if ".work." in str(field_id).casefold()
            ), "")
            if pending_work_id:
                primary_id = (
                    pending_work_id.split(".work.", 1)[0]
                    + ".work.primary_occupation"
                )
        if not primary_id:
            return []
        controller_labels = dict(labels)
        controller_labels.setdefault(
            primary_id,
            (
                "Primary Occupation [control=select_text; "
                "refresh_after_change=true; "
                "repair_missing_branch=aspnet-reset-reload-v7]",
            ),
        )
        actions, _unresolved = self._plan_work_semantic_fallback(
            [primary_id],
            controller_labels,
        )
        if len(actions) != 1:
            return []
        action = actions[0]
        action.reason = (
            "V2 CEAC Work missing-branch controller replay "
            f"[field_id={primary_id}]"
        )
        self._v2_work_reopen_attempted = True
        self._v2_forced_postback_field_ids.add(primary_id)
        return [action]

    @classmethod
    def _is_required_work_dependent(cls, field_id):
        normalized = str(field_id or "").casefold()
        rule = cls._work_semantic_rule(field_id)
        return bool(
            rule is not None
            and ".work.primary_occupation" not in normalized
            and not rule.get("optional_on_live_page")
            and not rule.get("ignore_when_missing")
        )

    def _plan_us_contact_relationship_stage(
        self,
        stage_ids,
        all_ids,
        labels,
    ):
        """Bind the exact relationship select with legacy-value migration.

        Older DocFlow jobs persisted an institution category such as
        ``Hotel Hotel Hostel`` as the relationship value.  That string is
        not a CEAC option.  Resolve it to CEAC ``OTHER`` before any native
        click and carry only the live option text into verification.
        """
        field_id = str(stage_ids[0]) if stage_ids else ""
        approved = self._descriptor_approved_value(labels.get(field_id) or ())
        desired = self._canonical_us_contact_relationship(approved)
        relationship = self._us_contact_relationship_control()
        snapshot = (
            self._selected_option_snapshot(relationship)
            if relationship is not None else {}
        )
        target = next((
            option for option in list(snapshot.get("options") or ())
            if str(option.get("value") or "").strip()
            and self._choice_matches(
                desired,
                " ".join((
                    str(option.get("text") or ""),
                    str(option.get("value") or ""),
                )),
            )
        ), None)
        if not field_id or not desired or relationship is None or target is None:
            return [], list(dict.fromkeys(str(item) for item in all_ids))
        action = ComputerAction(
            kind=ActionKind.SELECT,
            field_id=field_id,
            target_hint=field_id,
            value=str(target.get("text") or desired).strip(),
            reason=(
                "V2 exact CEAC U.S. Contact relationship selection "
                f"[field_id={field_id}]"
            ),
        )
        self._mark_field(relationship, action)
        self._v2_forced_us_contact_relationship_ids.add(field_id)
        return [action], [
            str(item) for item in all_ids if str(item) != field_id
        ]

    def _plan_semantic_fields_once(
        self,
        field_ids,
        field_labels=None,
        control_hints=None,
    ):
        """Keep ambiguous Travel address fields out of the generic binder."""
        labels = dict(field_labels or {})
        hints = dict(control_hints or {})
        if self._is_passport_page():
            return self._plan_passport_page_once(
                field_ids,
                labels,
                hints,
            )
        if self._is_relatives_page():
            return self._plan_relatives_page_once(
                field_ids,
                labels,
                hints,
            )
        if self._is_work_education1_page():
            return self._plan_work_page_once(
                field_ids,
                labels,
                hints,
            )
        if self._is_work_education2_page():
            return self._plan_work_education2_page_once(
                field_ids,
                labels,
                hints,
            )
        if self._is_us_contact_page():
            all_ids = [str(field_id) for field_id in field_ids or ()]
            # These three controls build the server-side FormView that owns
            # address/phone/email.  Execute one controller stage at a time so
            # a checkbox or select postback can never invalidate an earlier
            # text write or let lower fields race a missing branch.
            for suffix in (
                ".us_contact.person.does_not_know",
                ".us_contact.organization",
                ".us_contact.relationship",
            ):
                stage_ids = [
                    field_id for field_id in all_ids
                    if field_id.casefold().endswith(suffix)
                ]
                if not stage_ids:
                    continue
                if suffix == ".us_contact.relationship":
                    return self._plan_us_contact_relationship_stage(
                        stage_ids,
                        all_ids,
                        labels,
                    )
                stage_actions, stage_unresolved = super().plan_fields(
                    stage_ids,
                    labels,
                    hints,
                )
                acted = {action.field_id for action in stage_actions}
                return stage_actions, list(dict.fromkeys([
                    *stage_unresolved,
                    *(
                        field_id for field_id in all_ids
                        if field_id not in acted
                    ),
                ]))
            semantic_ids = [
                str(field_id)
                for field_id in all_ids
                if self._us_contact_semantic_rule(field_id) is not None
            ]
            ordinary_ids = [
                str(field_id)
                for field_id in all_ids
                if str(field_id) not in set(semantic_ids)
            ]
            ordinary_actions, ordinary_unresolved = (
                super().plan_fields(ordinary_ids, labels, hints)
                if ordinary_ids else ([], [])
            )
            semantic_actions, semantic_unresolved = (
                self._plan_us_contact_semantic_fallback(
                    semantic_ids,
                    labels,
                )
            )
            return (
                [*ordinary_actions, *semantic_actions],
                list(dict.fromkeys([
                    *ordinary_unresolved,
                    *semantic_unresolved,
                ])),
            )
        if self._is_address_phone_page():
            all_ids = [str(field_id) for field_id in field_ids or ()]
            exact_ids = [
                field_id for field_id in all_ids
                if self._address_phone_exact_rule(field_id) is not None
            ]
            exact = set(exact_ids)
            ordinary_ids = [
                field_id for field_id in all_ids if field_id not in exact
            ]
            ordinary_actions, ordinary_unresolved = (
                super().plan_fields(ordinary_ids, labels, hints)
                if ordinary_ids else ([], [])
            )
            if ordinary_actions:
                return ordinary_actions, list(dict.fromkeys([
                    *ordinary_unresolved,
                    *exact_ids,
                ]))
            exact_actions, exact_unresolved = (
                self._plan_address_phone_semantic_fallback(
                    exact_ids,
                    labels,
                )
            )
            return exact_actions, list(dict.fromkeys([
                *ordinary_unresolved,
                *exact_unresolved,
            ]))
        if not self._is_travel_page():
            return super().plan_fields(field_ids, labels, hints)

        all_ids = [str(field_id) for field_id in field_ids or ()]
        # Purpose is a strict two-level controller.  No date, address, payer,
        # or Specific Plans action is safe until CEAC has rendered and accepted
        # the dependent Specify select.  This stage barrier prevents a failed
        # primary repair from alternating with one lower-field write per resume.
        primary_ids = [
            field_id for field_id in all_ids
            if field_id.casefold().endswith(
                ".travel.purpose.primary"
            )
        ]
        if primary_ids:
            primary_actions, primary_unresolved = (
                self._plan_travel_semantic_fallback(
                    primary_ids,
                    labels,
                )
            )
            if primary_actions:
                self._v2_forced_travel_purpose_field_ids.update(
                    action.field_id for action in primary_actions
                )
                acted = {action.field_id for action in primary_actions}
                return primary_actions, list(dict.fromkeys([
                    *primary_unresolved,
                    *(field_id for field_id in all_ids if field_id not in acted),
                ]))

        secondary_ids = [
            field_id for field_id in all_ids
            if field_id.casefold().endswith(
                ".travel.purpose.secondary"
            )
        ]
        secondary_unresolved = []
        if secondary_ids:
            secondary_actions, secondary_unresolved = (
                self._plan_travel_semantic_fallback(
                    secondary_ids,
                    labels,
                )
            )
            if secondary_actions:
                acted = {action.field_id for action in secondary_actions}
                return secondary_actions, list(dict.fromkeys([
                    *secondary_unresolved,
                    *(field_id for field_id in all_ids if field_id not in acted),
                ]))
            if secondary_unresolved:
                replay_actions = self._plan_missing_travel_branch_replay(
                    secondary_unresolved,
                    labels,
                    hints,
                )
                if replay_actions:
                    return replay_actions, list(dict.fromkeys(all_ids))
                return [], list(dict.fromkeys([
                    *secondary_unresolved,
                    *(field_id for field_id in all_ids
                      if field_id not in set(secondary_unresolved)),
                ]))

        scoped_ids = [
            str(field_id)
            for field_id in field_ids or ()
            if self._travel_semantic_rule(field_id) is not None
        ]
        scoped = set(scoped_ids)
        generic_ids = [
            str(field_id)
            for field_id in field_ids or ()
            if str(field_id) not in scoped
        ]
        generic_actions, generic_unresolved = super().plan_fields(
            generic_ids,
            labels,
            hints,
        )
        # Controllers such as Purpose, Specific Plans, and Payer can replace a
        # large branch. Execute and verify them before binding dependent
        # address/date controls against a possibly stale DOM.
        if generic_actions:
            return generic_actions, [
                *generic_unresolved,
                *scoped_ids,
            ]

        scoped_actions, scoped_unresolved = (
            self._plan_travel_semantic_fallback(
                scoped_ids,
                labels,
            )
        )
        if scoped_actions:
            return scoped_actions, [
                *generic_unresolved,
                *scoped_unresolved,
            ]

        payer_pending = any(
            self._travel_semantic_rule(field_id).get("section") == "payer"
            for field_id in scoped_unresolved
            if self._travel_semantic_rule(field_id) is not None
        )
        if payer_pending and not self._v2_payer_reopen_attempted:
            payer_id = next(
                (
                    field_id
                    for field_id in labels
                    if str(field_id).casefold().endswith(
                        ".travel.payer"
                    )
                ),
                "",
            )
            if payer_id:
                payer_value = self._descriptor_approved_value(
                    labels.get(payer_id) or ()
                )
                if not self._travel_payer_requires_details(payer_value):
                    return [], [
                        *generic_unresolved,
                        *scoped_unresolved,
                    ]
                controller_actions, _controller_unresolved = (
                    super().plan_fields(
                        [payer_id],
                        labels,
                        hints,
                    )
                )
                if not controller_actions:
                    payer_control = self._travel_payer_control()
                    if payer_control is None:
                        payer_control = self._unique_actionable_control(
                            self._page.get_by_label(re.compile(
                                r"^\s*Person/Entity Paying for Your Trip"
                                r"\s*(?:\*|:)?\s*$",
                                re.IGNORECASE,
                            ))
                        )
                    if payer_control is not None:
                        controller = ComputerAction(
                            kind=ActionKind.SELECT,
                            field_id=payer_id,
                            target_hint=payer_id,
                            value=payer_value,
                            reason=(
                                "V2 CEAC Travel missing-payer-branch "
                                "controller replay "
                                f"[field_id={payer_id}]"
                            ),
                        )
                        try:
                            self._mark_field(payer_control, controller)
                        except Exception:
                            controller_actions = []
                        else:
                            controller_actions = [controller]
                if controller_actions:
                    self._v2_payer_reopen_attempted = True
                    return controller_actions, [
                        *generic_unresolved,
                        *scoped_unresolved,
                    ]
        return [], [
            *generic_unresolved,
            *scoped_unresolved,
        ]

    def _plan_passport_page_once(self, field_ids, labels, hints):
        """Plan Passport composites after ordinary controller actions."""
        scoped_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._passport_semantic_rule(field_id) is not None
        ]
        scoped = set(scoped_ids)
        generic_ids = [
            str(field_id) for field_id in field_ids or ()
            if str(field_id) not in scoped
        ]
        generic_actions, generic_unresolved = super().plan_fields(
            generic_ids,
            labels,
            hints,
        )
        # Passport Type and checkbox branches can post back. Let those generic
        # controllers finish before retaining any dependent composite binding.
        if generic_actions:
            return generic_actions, [*generic_unresolved, *scoped_ids]
        scoped_actions, scoped_unresolved = (
            self._plan_passport_semantic_fallback(scoped_ids, labels)
        )
        return scoped_actions, [*generic_unresolved, *scoped_unresolved]

    def _plan_relatives_page_once(self, field_ids, labels, hints):
        """Fill both parent identity panels before postback controllers.

        Parent surname/given-name/date labels repeat verbatim.  Bind all six
        values to their panel first, then let the generic planner handle the
        Yes/No questions whose postbacks can replace parts of the page.
        """
        scoped_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._family_semantic_rule(field_id) is not None
        ]
        scoped = set(scoped_ids)
        choice_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._family_choice_terms(field_id)
        ]
        choices = set(choice_ids)
        generic_ids = [
            str(field_id) for field_id in field_ids or ()
            if str(field_id) not in scoped
            and str(field_id) not in choices
        ]
        scoped_actions, scoped_unresolved = (
            self._plan_family_semantic_fallback(scoped_ids, labels)
        )
        if scoped_actions:
            return scoped_actions, [
                *generic_ids,
                *choice_ids,
                *scoped_unresolved,
            ]
        choice_actions, choice_unresolved = (
            self._plan_family_choice_fallback(choice_ids, labels)
        )
        if choice_actions:
            return choice_actions, [
                *generic_ids,
                *scoped_unresolved,
                *choice_unresolved,
            ]
        generic_actions, generic_unresolved = super().plan_fields(
            generic_ids,
            labels,
            hints,
        )
        return generic_actions, [
            *generic_unresolved,
            *scoped_unresolved,
            *choice_unresolved,
        ]

    def _plan_work_page_once(self, field_ids, labels, hints):
        """Post the occupation controller before binding its late panel."""
        scoped_ids = [
            str(field_id) for field_id in field_ids or ()
            if self._work_semantic_rule(field_id) is not None
        ]
        scoped = set(scoped_ids)
        generic_ids = [
            str(field_id) for field_id in field_ids or ()
            if str(field_id) not in scoped
        ]
        generic_actions, generic_unresolved = super().plan_fields(
            generic_ids,
            labels,
            hints,
        )
        if generic_actions:
            return generic_actions, [*generic_unresolved, *scoped_ids]
        scoped_actions, scoped_unresolved = (
            self._plan_work_semantic_fallback(scoped_ids, labels)
        )
        return scoped_actions, [*generic_unresolved, *scoped_unresolved]

    def _plan_work_education2_page_once(self, field_ids, labels, hints):
        """Post one branch radio at a time before binding the school record.

        CEAC replaces parts of this WebForms page after each radio postback.
        Selecting Education first and Previously Employed second can discard
        the newly rendered school panel.  Always settle employment first,
        then education, and only then bind school controls.
        """
        ordered_ids = [str(field_id) for field_id in field_ids or ()]
        choice_ids = {
            field_id
            for field_id in ordered_ids
            if self._work_education2_choice_terms(field_id)
        }
        for controller_suffix in (
            ".work.previously_employed",
            ".work.education_secondary_or_above",
        ):
            controller_id = next(
                (
                    field_id for field_id in ordered_ids
                    if field_id in choice_ids
                    and field_id.casefold().endswith(controller_suffix)
                ),
                "",
            )
            if not controller_id:
                continue
            controller_actions, controller_unresolved = super().plan_fields(
                [controller_id],
                labels,
                hints,
            )
            remaining = [
                field_id for field_id in ordered_ids
                if field_id != controller_id
            ]
            return controller_actions, list(dict.fromkeys([
                *controller_unresolved,
                *remaining,
            ]))

        scoped_ids = [
            field_id for field_id in ordered_ids
            if self._work_education2_semantic_rule(field_id) is not None
        ]
        scoped = set(scoped_ids)
        generic_ids = [
            field_id for field_id in ordered_ids
            if str(field_id) not in scoped
        ]
        generic_actions, generic_unresolved = super().plan_fields(
            generic_ids,
            labels,
            hints,
        )
        if generic_actions:
            return generic_actions, [*generic_unresolved, *scoped_ids]
        scoped_actions, scoped_unresolved = (
            self._plan_work_education2_semantic_fallback(
                scoped_ids,
                labels,
            )
        )
        return scoped_actions, [*generic_unresolved, *scoped_unresolved]

    def _plan_address_phone_semantic_fallback(
        self,
        field_ids,
        field_labels,
    ):
        """Bind Address/Phone text/DNA pairs by their stable CEAC ids."""
        if not self._is_address_phone_page():
            return [], list(field_ids or ())
        actions = []
        unresolved = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            labels = tuple((field_labels or {}).get(field_id) or ())
            approved = self._descriptor_approved_value(labels)
            dna = self._address_phone_dna_requested(approved)
            locator = self._address_phone_exact_control(field_id, dna=dna)
            if locator is None:
                unresolved.append(field_id)
                continue
            action = ComputerAction(
                kind=ActionKind.SELECT if dna else ActionKind.TYPE,
                field_id=field_id,
                target_hint=field_id,
                reason=(
                    "V2 CEAC Address/Phone stable-id text/DNA match "
                    f"[field_id={field_id}]"
                ),
            )
            try:
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def _plan_passport_semantic_fallback(self, field_ids, field_labels):
        """Resolve the stable Passport page without Gemini coordinates."""
        if not self._is_passport_page():
            return [], list(field_ids or ())
        actions = []
        unresolved = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            labels = tuple((field_labels or {}).get(field_id) or ())
            rule = self._passport_semantic_rule(field_id)
            kind = self._control_kind(labels)
            if rule is None or kind not in rule["kinds"]:
                unresolved.append(field_id)
                continue
            locator = self._travel_semantic_control(
                rule["terms"], kind, section="", prefer_last=False,
            )
            if locator is None:
                unresolved.append(field_id)
                continue
            try:
                metadata = locator.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        type: String(el.getAttribute('type') || '')
                            .toLowerCase()
                    })"""
                )
                action = ComputerAction(
                    kind=(
                        ActionKind.SELECT
                        if (
                            str(metadata.get("tag") or "") == "select"
                            or (
                                str(metadata.get("tag") or "") == "input"
                                and str(metadata.get("type") or "") in {
                                    "radio", "checkbox",
                                }
                            )
                        )
                        else ActionKind.TYPE
                    ),
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC Passport prompt-scoped deterministic match "
                        f"[field_id={field_id}]"
                    ),
                )
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def _plan_family_semantic_fallback(self, field_ids, field_labels):
        """Resolve Father/Mother repeated controls without Gemini."""
        if not self._is_relatives_page():
            return [], list(field_ids or ())
        actions = []
        unresolved = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            labels = tuple((field_labels or {}).get(field_id) or ())
            rule = self._family_semantic_rule(field_id)
            kind = self._control_kind(labels)
            if rule is None or kind not in rule["kinds"]:
                unresolved.append(field_id)
                continue
            locator = self._travel_semantic_control(
                rule["terms"],
                kind,
                section=rule["section"],
                prefer_last=False,
            )
            if locator is None:
                unresolved.append(field_id)
                continue
            try:
                metadata = locator.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        type: String(el.getAttribute('type') || '')
                            .toLowerCase()
                    })"""
                )
                action = ComputerAction(
                    kind=(
                        ActionKind.SELECT
                        if (
                            str(metadata.get("tag") or "") == "select"
                            or (
                                str(metadata.get("tag") or "") == "input"
                                and str(metadata.get("type") or "") in {
                                    "radio", "checkbox",
                                }
                            )
                        )
                        else ActionKind.TYPE
                    ),
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC Family panel-scoped deterministic match "
                        f"[field_id={field_id}]"
                    ),
                )
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def _plan_family_choice_fallback(self, field_ids, field_labels):
        """Bind each Relatives Yes/No group to its complete visible prompt."""
        if not self._is_relatives_page():
            return [], list(field_ids or ())
        actions = []
        unresolved = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            labels = tuple((field_labels or {}).get(field_id) or ())
            terms = self._family_choice_terms(field_id)
            if self._control_kind(labels) != "yes_no" or not terms:
                unresolved.append(field_id)
                continue
            locator = self._family_choice_group(field_id, terms)
            if locator is None:
                unresolved.append(field_id)
                continue
            action = ComputerAction(
                kind=ActionKind.SELECT,
                field_id=field_id,
                target_hint=field_id,
                reason=(
                    "V2 CEAC Family prompt-scoped radio match "
                    f"[field_id={field_id}]"
                ),
            )
            try:
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def _plan_work_semantic_fallback(self, field_ids, field_labels):
        """Resolve the visible Present Employer/School panel without Gemini."""
        if not self._is_work_education1_page():
            return [], list(field_ids or ())
        actions = []
        unresolved = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            labels = tuple((field_labels or {}).get(field_id) or ())
            rule = self._work_semantic_rule(field_id)
            kind = self._control_kind(labels)
            if rule is None or kind not in rule["kinds"]:
                unresolved.append(field_id)
                continue
            approved = self._descriptor_approved_value(labels)
            if rule.get("optional_on_live_page") and not approved:
                # Presence classification normally removes reviewed blanks
                # before planning.  Keep the semantic fallback independently
                # safe for direct/recovery callers as well.
                continue
            locator = self._travel_semantic_control(
                rule["terms"],
                kind,
                section="",
                prefer_last=False,
            )
            if locator is None:
                if rule.get("ignore_when_missing"):
                    continue
                unresolved.append(field_id)
                continue
            try:
                metadata = locator.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        type: String(el.getAttribute('type') || '')
                            .toLowerCase()
                    })"""
                )
                action = ComputerAction(
                    kind=(
                        ActionKind.SELECT
                        if (
                            str(metadata.get("tag") or "") == "select"
                            or (
                                str(metadata.get("tag") or "") == "input"
                                and str(metadata.get("type") or "") in {
                                    "radio", "checkbox",
                                }
                            )
                        )
                        else ActionKind.TYPE
                    ),
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC Present Work prompt-scoped match "
                        f"[field_id={field_id}]"
                    ),
                )
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def _plan_work_education2_semantic_fallback(
        self,
        field_ids,
        field_labels,
    ):
        """Fill the one rendered school record without Gemini coordinates.

        CEAC posts the school State/Province and Postal Code ``Does Not
        Apply`` checkboxes and replaces the record DOM.  Plan those structural
        controls before all ordinary school values so their postbacks cannot
        erase an already typed Course of Study or institution name.
        """
        if not self._is_work_education2_page():
            return [], list(field_ids or ())
        actions = []
        unresolved = []
        ordered_field_ids = sorted(
            (str(field_id or "") for field_id in field_ids or ()),
            key=lambda field_id: (
                0
                if self._control_kind(
                    tuple((field_labels or {}).get(field_id) or ())
                ) == "does_not_apply"
                else 1
            ),
        )
        for raw_field_id in ordered_field_ids:
            field_id = str(raw_field_id or "")
            labels = tuple((field_labels or {}).get(field_id) or ())
            rule = self._work_education2_semantic_rule(field_id)
            kind = self._control_kind(labels)
            if rule is None or kind not in rule["kinds"]:
                unresolved.append(field_id)
                continue
            locator = self._work_education2_semantic_control(rule, kind)
            if locator is None:
                unresolved.append(field_id)
                continue
            try:
                metadata = locator.evaluate(
                    """el => ({
                        tag: String(el.tagName || '').toLowerCase(),
                        type: String(el.getAttribute('type') || '')
                            .toLowerCase()
                    })"""
                )
                action = ComputerAction(
                    kind=(
                        ActionKind.SELECT
                        if (
                            str(metadata.get("tag") or "") == "select"
                            or str(metadata.get("type") or "") == "checkbox"
                        )
                        else ActionKind.TYPE
                    ),
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC Work/Education 2 exact school match "
                        f"[field_id={field_id}]"
                    ),
                )
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    def _plan_us_contact_semantic_fallback(
        self,
        field_ids,
        field_labels,
    ):
        """Bind the unconditional U.S. Contact address/phone block exactly."""
        if not self._is_us_contact_page():
            return [], list(field_ids or ())
        actions = []
        unresolved = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            rule = self._us_contact_semantic_rule(field_id)
            labels = tuple((field_labels or {}).get(field_id) or ())
            kind = self._control_kind(labels)
            if rule is None or kind not in rule["kinds"]:
                unresolved.append(field_id)
                continue
            if (
                kind == "does_not_apply"
                and field_id.casefold().endswith(".us_contact.email")
            ):
                locator = self._us_contact_email_dna_control()
            else:
                locator = self._travel_semantic_control(
                    rule["terms"],
                    kind,
                    section="",
                    prefer_last=False,
                )
            if locator is None:
                unresolved.append(field_id)
                continue
            try:
                metadata = locator.evaluate(
                    """el => ({
                        tag: String(el.tagName || '').toLowerCase(),
                        type: String(el.getAttribute('type') || '')
                            .toLowerCase()
                    })"""
                )
                action = ComputerAction(
                    kind=(
                        ActionKind.SELECT
                        if (
                            str(metadata.get("tag") or "") == "select"
                            or str(metadata.get("type") or "") == "checkbox"
                        )
                        else ActionKind.TYPE
                    ),
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC U.S. Contact exact prompt match "
                        f"[field_id={field_id}]"
                    ),
                )
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    @staticmethod
    def _us_contact_semantic_rule(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".us_contact.phone",
                ("Phone Number",),
                {"text"},
                "US_POC_HOME_TEL",
            ),
            (
                ".us_contact.email",
                ("Email Address",),
                {"text", "does_not_apply"},
                "US_POC_EMAIL_ADDR",
            ),
            (
                ".us_contact.address.street1",
                (
                    "U.S. Street Address (Line 1)",
                    "Street Address (Line 1)",
                ),
                {"text"},
                "US_POC_ADDR_LN1",
            ),
            (
                ".us_contact.address.street2",
                (
                    "U.S. Street Address (Line 2)",
                    "Street Address (Line 2)",
                ),
                {"text"},
                "US_POC_ADDR_LN2",
            ),
            (
                ".us_contact.address.city",
                ("City",),
                {"text"},
                "US_POC_ADDR_CITY",
            ),
            (
                ".us_contact.address.state",
                ("State",),
                {"select", "select_text"},
                "US_POC_ADDR_STATE",
            ),
            (
                ".us_contact.address.postalcode",
                ("ZIP Code", "Postal Code"),
                {"text"},
                "US_POC_ADDR_POSTAL_CD",
            ),
        )
        for token, terms, kinds, id_token in rules:
            if token in normalized:
                return {
                    "terms": terms,
                    "kinds": kinds,
                    "id_token": id_token,
                }
        return None

    def _us_contact_semantic_control(self, field_id, kind):
        """Bind one mandatory U.S. Contact field by its stable CEAC ID."""
        rule = self._us_contact_semantic_rule(field_id)
        if rule is None:
            return None
        id_token = str(rule.get("id_token") or "")
        tag = "select" if kind in {"select", "select_text"} else "input"
        if id_token:
            try:
                controls = self._page.locator(
                    f'{tag}[id*="{id_token}" i], '
                    f'{tag}[name*="{id_token}" i]'
                )
                visible = [
                    controls.nth(index)
                    for index in range(min(controls.count(), 8))
                    if controls.nth(index).is_visible()
                ]
                if len(visible) == 1:
                    return visible[0]
            except Exception:
                pass
        return self._travel_semantic_control(
            rule["terms"],
            kind,
            section="",
            prefer_last=False,
        )

    @staticmethod
    def _passport_semantic_rule(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".passport.issuingauthority",
                (
                    "Country/Authority that Issued Passport/Travel Document",
                    "Country/Authority that Issued Passport",
                ),
                {"select", "select_text"},
            ),
            (
                ".passport.issuecity",
                ("City", "City Where Issued"),
                {"text"},
            ),
            (
                ".passport.issueregion",
                (
                    "State/Province *If shown on passport",
                    "State/Province If shown on passport",
                    "State/Province Where Issued",
                ),
                {"text"},
            ),
            (
                ".passport.issuecountry",
                ("Country/Region", "Country/Region Where Issued"),
                {"select", "select_text"},
            ),
            (
                ".passport.issuedate",
                ("Issuance Date",),
                {"date"},
            ),
            (
                ".passport.expiration",
                ("Expiration Date",),
                {"date"},
            ),
        )
        for token, terms, kinds in rules:
            if token in normalized:
                return {"terms": terms, "kinds": kinds}
        return None

    @staticmethod
    def _family_semantic_rule(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".family.father.surname",
                ("Surnames",),
                "father",
                {"text"},
            ),
            (
                ".family.father.givennames",
                ("Given Names",),
                "father",
                {"text"},
            ),
            (
                ".family.father.dateofbirth",
                ("Date of Birth",),
                "father",
                {"date"},
            ),
            (
                ".family.mother.surname",
                ("Surnames",),
                "mother",
                {"text"},
            ),
            (
                ".family.mother.givennames",
                ("Given Names",),
                "mother",
                {"text"},
            ),
            (
                ".family.mother.dateofbirth",
                ("Date of Birth",),
                "mother",
                {"date"},
            ),
        )
        for token, terms, section, kinds in rules:
            if token in normalized:
                return {
                    "terms": terms,
                    "section": section,
                    "kinds": kinds,
                }
        return None

    @staticmethod
    def _family_choice_terms(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".family.father_in_us",
                ("is your father in the u s",),
            ),
            (
                ".family.mother_in_us",
                ("is your mother in the u s",),
            ),
            (
                ".family.immediate_relatives_us",
                (
                    "do you have any immediate relatives not including "
                    "parents in the united states",
                    "immediate relatives not including parents",
                ),
            ),
            (
                ".family.other_relatives_us",
                (
                    "do you have any other relatives in the united states",
                    "any other relatives in the united states",
                ),
            ),
        )
        for token, terms in rules:
            if token in normalized:
                return terms
        return ()

    @staticmethod
    def _work_semantic_rule(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".work.primary_occupation",
                ("Primary Occupation",),
                {"select", "select_text"},
                False,
            ),
            (
                ".work.organization",
                ("Present Employer or School Name",),
                {"text"},
                False,
            ),
            (
                ".work.phone",
                ("Phone Number",),
                {"text"},
                False,
            ),
            (
                ".work.startdate",
                ("Start Date",),
                {"date"},
                False,
            ),
            (
                ".work.monthlyincome",
                (
                    "Monthly Income in Local Currency (if employed)",
                    "Monthly Income in Local Currency",
                    "Monthly Income",
                ),
                {"text"},
                False,
            ),
            (
                ".work.duties",
                (
                    "Briefly describe your duties",
                    "Briefly Describe your Duties",
                ),
                {"text", "textarea"},
                False,
            ),
            (
                ".work.jobtitle",
                ("Job Title",),
                {"text"},
                True,
                True,
            ),
            (
                ".work.present.address.record.line1.",
                ("Street Address (Line 1)",),
                {"text"},
                False,
            ),
            (
                ".work.present.address.record.line2.",
                ("Street Address (Line 2)",),
                {"text"},
                True,
            ),
            (
                ".work.present.address.record.city.",
                ("City",),
                {"text"},
                False,
            ),
            (
                ".work.present.address.record.region.",
                ("State/Province",),
                {"text"},
                False,
            ),
            (
                ".work.present.address.record.postalcode.",
                ("Postal Zone/ZIP Code",),
                {"text", "does_not_apply"},
                False,
            ),
            (
                ".work.present.address.record.country.",
                ("Country/Region",),
                {"select", "select_text"},
                False,
            ),
        )
        for rule in rules:
            token, terms, kinds, optional_on_live_page = rule[:4]
            if token in normalized:
                return {
                    "terms": terms,
                    "kinds": kinds,
                    "optional_on_live_page": optional_on_live_page,
                    "ignore_when_missing": bool(
                        len(rule) > 4 and rule[4]
                    ),
                }
        return None

    @staticmethod
    def _work_education2_choice_terms(field_id):
        normalized = str(field_id or "").casefold()
        if normalized.endswith(".work.previously_employed"):
            return ("were you previously employed",)
        if normalized.endswith(
            ".work.education_secondary_or_above"
        ):
            return (
                "have you attended any educational institutions at a "
                "secondary level or above",
                "attended any educational institutions",
            )
        return ()

    @staticmethod
    def _work_education2_semantic_rule(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".work.education.record.school.",
                ("Name of Institution",),
                {"text"},
            ),
            (
                ".work.education.record.course.",
                ("Course of Study",),
                {"text"},
            ),
            (
                ".work.education.record.startdate.",
                ("Date of Attendance From",),
                {"date"},
            ),
            (
                ".work.education.record.enddate.",
                ("Date of Attendance To",),
                {"date"},
            ),
            (
                ".work.education.record.line1.",
                ("Street Address (Line 1)",),
                {"text"},
            ),
            (
                ".work.education.record.line2.",
                ("Street Address (Line 2)",),
                {"text"},
            ),
            (
                ".work.education.record.city.",
                ("City",),
                {"text"},
            ),
            (
                ".work.education.record.region.",
                ("State/Province",),
                {"text", "does_not_apply"},
            ),
            (
                ".work.education.record.postalcode.",
                ("Postal Zone/ZIP Code",),
                {"text", "does_not_apply"},
            ),
            (
                ".work.education.record.country.",
                ("Country/Region",),
                {"select", "select_text"},
            ),
        )
        for token, terms, kinds in rules:
            if token in normalized:
                return {"terms": terms, "kinds": kinds}
        return None

    def _work_education2_semantic_control(self, rule, kind):
        # CEAC's previous-education panel keeps stable native control ids.
        # Prefer those ids before proximity-based label matching: Course of
        # Study sits immediately below State/Province, and a visual/label
        # binder can otherwise type the course into the province control and
        # then incorrectly verify the marked element as complete.
        terms = tuple(rule.get("terms") or ())
        exact_selectors = {
            ("Name of Institution",): {
                "text": 'input[id$="_tbxSchoolName"]',
            },
            ("Course of Study",): {
                "text": 'input[id$="_tbxSchoolCourseOfStudy"]',
            },
            ("Street Address (Line 1)",): {
                "text": 'input[id$="_tbxSchoolAddr1"]',
            },
            ("Street Address (Line 2)",): {
                "text": 'input[id$="_tbxSchoolAddr2"]',
            },
            ("City",): {
                "text": 'input[id$="_tbxSchoolCity"]',
            },
            ("State/Province",): {
                "text": 'input[id$="_tbxEDUC_INST_ADDR_STATE"]',
                "does_not_apply": (
                    'input[type="checkbox"]'
                    '[id$="_cbxEDUC_INST_ADDR_STATE_NA"]'
                ),
            },
            ("Postal Zone/ZIP Code",): {
                "text": 'input[id$="_tbxEDUC_INST_POSTAL_CD"]',
                "does_not_apply": (
                    'input[type="checkbox"]'
                    '[id$="_cbxEDUC_INST_POSTAL_CD_NA"]'
                ),
            },
            ("Country/Region",): {
                "select": 'select[id$="_ddlSchoolCountry"]',
                "select_text": 'select[id$="_ddlSchoolCountry"]',
            },
        }
        exact_selector = exact_selectors.get(terms, {}).get(kind)
        if exact_selector:
            try:
                locator = self._page.locator(exact_selector)
                if locator.count() == 1:
                    return locator.first
            except Exception:
                pass
        if kind != "does_not_apply":
            return self._travel_semantic_control(
                rule["terms"],
                kind,
                section="",
                prefer_last=False,
            )
        token = f"v2-work-education2-dna-{uuid4().hex}"
        try:
            found = bool(self._page.evaluate(
                """([rawTerms, token]) => {
                    const norm = value => String(value || '')
                        .toLowerCase()
                        .replace(/[\\s:*?]+/g, ' ')
                        .replace(/[^a-z0-9\\u4e00-\\u9fff /'().-]/g, '')
                        .trim();
                    const terms = rawTerms.map(norm).filter(Boolean);
                    const visible = element => {
                        if (!element) return false;
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const anchors = Array.from(document.querySelectorAll(
                        'label, span, td, th, p, strong, div'
                    )).filter(node => {
                        if (!visible(node) || !terms.includes(norm(node.innerText))) {
                            return false;
                        }
                        return !Array.from(node.children || []).some(
                            child => visible(child)
                                && terms.includes(norm(child.innerText))
                        );
                    });
                    const candidates = [];
                    for (const anchor of anchors) {
                        const anchorBox = anchor.getBoundingClientRect();
                        for (const checkbox of Array.from(
                            document.querySelectorAll('input[type="checkbox"]')
                        ).filter(visible)) {
                            const box = checkbox.getBoundingClientRect();
                            const below = box.top - anchorBox.bottom;
                            const horizontal = Math.abs(box.left - anchorBox.left);
                            const container = checkbox.closest('tr, td, div');
                            const text = norm(container?.innerText || '');
                            if (!text.includes('does not apply')) continue;
                            if (below < -28 || below > 150 || horizontal > 780) {
                                continue;
                            }
                            candidates.push({
                                checkbox,
                                score: Math.max(0, below) + horizontal * 0.02,
                            });
                        }
                    }
                    candidates.sort((left, right) => left.score - right.score);
                    if (!candidates.length) return false;
                    if (
                        candidates.length > 1
                        && candidates[1].score - candidates[0].score < 8
                    ) return false;
                    candidates[0].checkbox.setAttribute(
                        'data-docflow-v2-work-education2-dna', token
                    );
                    return true;
                }""",
                [list(rule["terms"]), token],
            ))
        except Exception:
            return None
        if not found:
            return None
        locator = self._page.locator(
            '[data-docflow-v2-work-education2-dna="' + token + '"]'
        )
        return locator.first if locator.count() == 1 else None

    @staticmethod
    def _is_dependent_family_choice(field_id):
        return ".family.other_relatives_us" in str(
            field_id or ""
        ).casefold()

    def _plan_travel_semantic_fallback(self, field_ids, field_labels):
        """Resolve the stable CEAC Travel layout without asking Gemini.

        CEAC repeats generic labels such as ``Street Address (Line 1)`` for
        both the U.S. stay address and the paying party. Its real production
        control ids do not consistently contain the aliases used by older
        deployments, so the generic deterministic binder can leave otherwise
        visible, already populated fields unresolved. The Travel page has a
        stable top-to-bottom structure: the U.S. address is first and the
        paying-party branch is last. Use that structure only for this exact
        code-owned page and only for the known reviewed Travel fields.
        """
        if not self._is_travel_page():
            return [], list(field_ids or ())

        actions = []
        unresolved = []
        for raw_field_id in field_ids or ():
            field_id = str(raw_field_id or "")
            labels = tuple((field_labels or {}).get(field_id) or ())
            rule = self._travel_semantic_rule(field_id)
            if rule is None:
                unresolved.append(field_id)
                continue
            locator = self._travel_rule_control(
                field_id,
                rule,
                labels,
            )
            if locator is None:
                unresolved.append(field_id)
                continue
            try:
                metadata = locator.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        type: String(el.getAttribute('type') || '')
                            .toLowerCase()
                    })"""
                )
                action = ComputerAction(
                    kind=(
                        ActionKind.SELECT
                        if (
                            str(metadata.get("tag") or "") == "select"
                            or (
                                str(metadata.get("tag") or "") == "input"
                                and str(metadata.get("type") or "") in {
                                    "radio", "checkbox",
                                }
                            )
                        )
                        else ActionKind.TYPE
                    ),
                    field_id=field_id,
                    target_hint=field_id,
                    reason=(
                        "V2 CEAC Travel section/label deterministic match "
                        f"[field_id={field_id}]"
                    ),
                )
                self._mark_field(locator, action)
            except Exception:
                unresolved.append(field_id)
                continue
            actions.append(action)
        return actions, unresolved

    @staticmethod
    def _travel_semantic_rule(field_id):
        normalized = str(field_id or "").casefold()
        rules = (
            (
                ".purpose.primary",
                ("Purpose of Trip to the U.S.",),
                "",
                False,
            ),
            (
                ".purpose.secondary",
                ("Specify", "Specify visa class"),
                "",
                False,
            ),
            (
                ".arrivaldate",
                ("Intended Date of Arrival",),
                "",
                False,
            ),
            (
                ".stayduration",
                (
                    "Intended Length of Stay in U.S.",
                    "Intended Length of Stay",
                ),
                "",
                False,
            ),
            (
                ".usstreet1",
                ("Street Address (Line 1)",),
                "us",
                False,
            ),
            (
                ".usstreet2",
                ("Street Address (Line 2)",),
                "us",
                False,
            ),
            (
                ".uscity",
                ("City",),
                "us",
                False,
            ),
            (
                ".usstate",
                ("State",),
                "us",
                False,
            ),
            (
                ".uspostalcode",
                ("ZIP Code",),
                "us",
                False,
            ),
            (
                ".payerphone",
                ("Telephone Number",),
                "payer",
                False,
            ),
            (
                ".payerorganization",
                ("Organization Name",),
                "payer",
                False,
            ),
            (
                ".payeraddress.record.line1.",
                ("Street Address (Line 1)",),
                "payer",
                False,
            ),
            (
                ".payeraddress.record.line2.",
                ("Street Address (Line 2)",),
                "payer",
                False,
            ),
            (
                ".payeraddress.record.city.",
                ("City",),
                "payer",
                False,
            ),
            (
                ".payeraddress.record.region.",
                ("State/Province",),
                "payer",
                False,
            ),
            (
                ".payeraddress.record.postalcode.",
                ("Postal Zone/ZIP Code",),
                "payer",
                False,
            ),
            (
                ".payeraddress.record.country.",
                ("Country/Region",),
                "payer",
                False,
            ),
        )
        for token, terms, section, prefer_last in rules:
            if token in normalized:
                return {
                    "terms": terms,
                    "section": section,
                    "prefer_last": prefer_last,
                }
        return None

    def _travel_rule_control(self, field_id, rule, labels=()):
        """Resolve Travel purpose controls without confusing both levels."""
        normalized = str(field_id or "").casefold()
        if normalized.endswith(".travel.purpose.primary"):
            return self._travel_purpose_control("primary", rule["terms"])
        if normalized.endswith(".travel.purpose.secondary"):
            return self._travel_purpose_control("secondary", rule["terms"])
        locator = self._travel_semantic_control(
            rule["terms"],
            self._control_kind(labels),
            section=rule["section"],
            prefer_last=rule["prefer_last"],
        )
        if locator is not None or rule["section"] != "us":
            return locator
        return self._travel_us_address_control_by_order(field_id)

    def _travel_us_address_control_by_order(self, field_id):
        """Bind CEAC's fixed five-control U.S. stay-address panel.

        Production CEAC decorates these labels with required-field images and
        nests the section heading differently from the acceptance fixtures.
        When exact nearest-label binding cannot find the panel, accept only
        the unambiguous control signature between the exact address heading
        and the payer controller: text, text, text, select, text.
        """
        normalized = str(field_id or "").casefold()
        position_by_suffix = {
            ".usstreet1": 0,
            ".usstreet2": 1,
            ".uscity": 2,
            ".usstate": 3,
            ".uspostalcode": 4,
        }
        position = next((
            index for suffix, index in position_by_suffix.items()
            if suffix in normalized
        ), None)
        if position is None:
            return None
        token = f"v2-travel-us-address-{uuid4().hex}"
        try:
            found = bool(self._page.evaluate(
                """([position, token]) => {
                    const norm = value => String(value || '')
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, ' ')
                        .replace(/\s+/g, ' ').trim();
                    const visible = element => {
                        if (!element || element.disabled) return false;
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0 && box.height > 0;
                    };
                    const textNodes = Array.from(document.querySelectorAll(
                        'legend, h1, h2, h3, h4, h5, h6, label, span, '
                        + 'td, th, p, strong, div'
                    )).filter(visible).map(element => ({
                        element,
                        text: norm(element.innerText),
                        box: element.getBoundingClientRect()
                    }));
                    const addressText = norm(
                        'Address Where You Will Stay in the U.S.'
                    );
                    const payerText = norm(
                        'Person/Entity Paying for Your Trip'
                    );
                    const pickAnchor = wanted => textNodes.filter(item => (
                        item.text === wanted
                        || item.text.startsWith(`${wanted} `)
                    )).sort((left, right) => (
                        left.text.length - right.text.length
                        || left.box.top - right.box.top
                    ))[0] || null;
                    const address = pickAnchor(addressText);
                    const payer = pickAnchor(payerText);
                    if (!address || !payer) return false;
                    const top = address.box.top + 2;
                    const bottom = payer.box.top - 2;
                    if (!(bottom > top)) return false;
                    const controls = Array.from(document.querySelectorAll(
                        'input, select'
                    )).filter(control => {
                        if (!visible(control)) return false;
                        const box = control.getBoundingClientRect();
                        if (!(box.top > top && box.top < bottom)) return false;
                        const tag = control.tagName.toLowerCase();
                        const type = String(
                            control.getAttribute('type') || 'text'
                        ).toLowerCase();
                        return tag === 'select' || (
                            tag === 'input'
                            && ![
                                'hidden', 'radio', 'checkbox', 'button',
                                'submit', 'reset', 'file', 'image', 'password'
                            ].includes(type)
                        );
                    }).map(control => ({
                        control,
                        box: control.getBoundingClientRect(),
                        tag: control.tagName.toLowerCase()
                    })).sort((left, right) => (
                        left.box.top - right.box.top
                        || left.box.left - right.box.left
                    ));
                    if (controls.length !== 5) return false;
                    const signature = controls.map(item => item.tag);
                    if (
                        signature.join(',') !==
                        'input,input,input,select,input'
                    ) return false;
                    const selected = controls[Number(position)];
                    if (!selected) return false;
                    selected.control.setAttribute(
                        'data-docflow-v2-travel-us-address', token
                    );
                    return true;
                }""",
                [position, token],
            ))
        except Exception:
            return None
        if not found:
            return None
        locator = self._page.locator(
            f'[data-docflow-v2-travel-us-address="{token}"]'
        )
        return locator.first if locator.count() == 1 else None

    def _travel_purpose_control(self, part, terms):
        """Bind Travel purpose levels by their actual CEAC labels.

        ``PLEASE SELECT A VISA CLASS`` is the placeholder of the *primary*
        ``Purpose of Trip to the U.S.`` select.  The secondary B1/B2 select is
        distinct and appears only after the primary postback under an explicit
        ``Specify`` label.  A placeholder option can therefore never identify
        the secondary control.
        """
        locator = self._travel_semantic_control(
            terms,
            "select_text",
            section="",
            prefer_last=False,
        )
        if locator is None:
            return None
        return locator

    def travel_purpose_matches_approved(self, field_id, approved):
        """Read back a Travel purpose using CEAC option equivalence.

        The reviewed wording and CEAC's rendered option can differ only in
        harmless connectors, for example ``BUSINESS & TOURISM`` versus
        ``BUSINESS OR TOURISM``.  The action-time selector already treats
        those labels as the same option.  Page-wide revalidation must use the
        same narrow rule or it will reopen a select that was just verified.
        This hook is deliberately limited to the two exact Travel-purpose
        controls; ordinary text and date values keep strict verification.
        """
        if not self._is_travel_page():
            return False
        normalized_id = str(field_id or "").casefold()
        if normalized_id.endswith(".travel.purpose.primary"):
            locator = self._travel_purpose_control(
                "primary",
                ("Purpose of Trip to the U.S.", "Purpose of Trip"),
            )
        elif normalized_id.endswith(".travel.purpose.secondary"):
            locator = self._travel_purpose_control(
                "secondary",
                ("Specify", "Specify visa class"),
            )
        else:
            return False
        if locator is None:
            return False
        try:
            selected = locator.evaluate(
                """el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    value: String(el.value || ''),
                    text: (
                        el.selectedIndex >= 0 && el.options
                        ? String(el.options[el.selectedIndex].text || '')
                        : ''
                    )
                })"""
            )
        except Exception:
            return False
        if str(dict(selected or {}).get("tag") or "") != "select":
            return False
        candidate = " ".join(
            str(dict(selected or {}).get(key) or "")
            for key in ("text", "value")
        ).strip()
        return self._choice_matches(approved, candidate)

    def _travel_semantic_control(
        self,
        terms,
        control_kind,
        *,
        section,
        prefer_last,
    ):
        """Return one visible control nearest the first/last exact label."""
        token = f"v2-travel-{uuid4().hex}"
        try:
            found = bool(self._page.evaluate(
                """([rawTerms, kind, token, section, preferLast]) => {
                    const norm = value => String(value || '')
                        .toLowerCase()
                        .replace(/[\\s:*?]+/g, ' ')
                        .replace(/[^a-z0-9\\u4e00-\\u9fff /'().-]/g, '')
                        .trim();
                    const terms = rawTerms.map(norm).filter(Boolean);
                    const canonicalLabel = value => norm(value)
                        .replace(/\\s+optional$/, '')
                        .replace(/\\s+\\(if known\\)$/, '')
                        .trim();
                    const termMatch = value => terms.includes(
                        canonicalLabel(value)
                    );
                    const visible = element => {
                        if (!element) return false;
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && box.width > 0
                            && box.height > 0;
                    };
                    const allowed = control => {
                        if (!visible(control) || control.disabled) return false;
                        const tag = control.tagName.toLowerCase();
                        const type = String(
                            control.getAttribute('type') || 'text'
                        ).toLowerCase();
                        if (['radio', 'checkbox', 'hidden', 'button', 'submit',
                            'reset', 'file', 'image', 'password'].includes(type)) {
                            return false;
                        }
                        if (['select', 'select_text'].includes(kind)) {
                            return tag === 'select';
                        }
                        if (['date', 'duration'].includes(kind)) {
                            return tag === 'select' || tag === 'input';
                        }
                        return tag === 'textarea' || (
                            tag === 'input' && ![
                                'radio', 'checkbox', 'hidden', 'button',
                                'submit', 'reset', 'file', 'image', 'password'
                            ].includes(type)
                        );
                    };
                    const controls = Array.from(document.querySelectorAll(
                        'input, textarea, select'
                    )).filter(allowed);
                    if (!controls.length) return false;
                    const allTextNodes = Array.from(
                        document.querySelectorAll(
                            'label, legend, h1, h2, h3, h4, h5, h6, '
                            + 'span, td, th, p, strong, div'
                        )
                    ).filter(visible);
                    const leafTextNodes = allTextNodes.filter(node => (
                        !Array.from(node.children || []).some(
                            child => visible(child)
                                && norm(child.innerText)
                                    === norm(node.innerText)
                        )
                    ));
                    const sectionAnchor = wanted => {
                        const normalizedWanted = norm(wanted);
                        const candidates = leafTextNodes.filter(node => {
                            const text = norm(node.innerText);
                            return text === normalizedWanted
                                || text.startsWith(
                                    `${normalizedWanted} `
                                );
                        }).sort((a, b) => {
                            const left = a.getBoundingClientRect();
                            const right = b.getBoundingClientRect();
                            return left.top - right.top
                                || left.left - right.left;
                        });
                        return candidates[0] || null;
                    };
                    const usAnchor = sectionAnchor(
                        'Address Where You Will Stay in the U.S.'
                    );
                    const payerAnchor = sectionAnchor(
                        'Person/Entity Paying for Your Trip'
                    );
                    const fatherAnchor = sectionAnchor(
                        "Father's Full Name and Date of Birth"
                    );
                    const motherAnchor = sectionAnchor(
                        "Mother's Full Name and Date of Birth"
                    );
                    if (
                        (section === 'us' && !usAnchor)
                        || (section === 'payer' && !payerAnchor)
                        || (section === 'father' && !fatherAnchor)
                        || (section === 'mother' && !motherAnchor)
                    ) {
                        return false;
                    }
                    const usTop = usAnchor
                        ? usAnchor.getBoundingClientRect().top
                        : -Infinity;
                    const payerTop = payerAnchor
                        ? payerAnchor.getBoundingClientRect().top
                        : Infinity;
                    const fatherTop = fatherAnchor
                        ? fatherAnchor.getBoundingClientRect().top
                        : -Infinity;
                    const motherTop = motherAnchor
                        ? motherAnchor.getBoundingClientRect().top
                        : Infinity;
                    const inSection = box => {
                        if (section === 'us') {
                            return box.top > usTop + 2
                                && box.top < payerTop - 2;
                        }
                        if (section === 'payer') {
                            return box.top > payerTop + 2;
                        }
                        if (section === 'father') {
                            return box.top > fatherTop + 2
                                && box.top < motherTop - 2;
                        }
                        if (section === 'mother') {
                            return box.top > motherTop + 2;
                        }
                        return true;
                    };
                    const nodes = Array.from(document.querySelectorAll(
                        'label, legend, h1, h2, h3, h4, h5, h6, '
                        + 'span, td, th, p, strong, div'
                    )).filter(node => {
                        if (!visible(node)) return false;
                        if (!inSection(node.getBoundingClientRect())) {
                            return false;
                        }
                        if (!termMatch(node.innerText)) return false;
                        return !Array.from(node.children || []).some(
                            child => visible(child)
                                && termMatch(child.innerText)
                        );
                    });
                    const candidates = [];
                    for (const anchor of nodes) {
                        const anchorBox = anchor.getBoundingClientRect();
                        const associated = anchor.tagName.toLowerCase()
                            === 'label' ? anchor.control : null;
                        const ranked = [];
                        for (const control of controls) {
                            const box = control.getBoundingClientRect();
                            if (!inSection(box)) continue;
                            const verticalCenter = Math.abs(
                                (box.top + box.height / 2)
                                - (anchorBox.top + anchorBox.height / 2)
                            );
                            const below = box.top - anchorBox.bottom;
                            const right = box.left - anchorBox.right;
                            let score = Infinity;
                            if (associated === control) {
                                score = 0;
                            } else if (anchor.contains(control)) {
                                score = 3 + verticalCenter * 0.03;
                            } else if (
                                right >= -28 && right <= 780
                                && verticalCenter <= 72
                            ) {
                                score = 12 + verticalCenter * 0.8
                                    + Math.max(0, right) * 0.02;
                            } else if (
                                below >= -12 && below <= 250
                                && Math.abs(box.left - anchorBox.left) <= 580
                            ) {
                                score = 20 + Math.max(0, below) * 0.45
                                    + Math.abs(
                                        box.left - anchorBox.left
                                    ) * 0.02;
                            }
                            if (Number.isFinite(score)) {
                                ranked.push({control, score, box});
                            }
                        }
                        ranked.sort((a, b) => (
                            a.score - b.score
                            || a.box.left - b.box.left
                        ));
                        if (!ranked.length || ranked[0].score > 125) continue;
                        const best = ranked[0];
                        const competing = ranked.find(
                            item => item.control !== best.control
                        );
                        const sameCompositeRow = (
                            ['date', 'duration'].includes(kind)
                            && competing
                            && Math.abs(
                                competing.box.top - best.box.top
                            ) <= 12
                        );
                        if (
                            competing
                            && competing.score - best.score < 10
                            && !sameCompositeRow
                        ) {
                            continue;
                        }
                        if (!candidates.some(
                            item => item.control === best.control
                        )) {
                            candidates.push({
                                control: best.control,
                                top: best.box.top,
                                left: best.box.left
                            });
                        }
                    }
                    candidates.sort((a, b) => (
                        a.top - b.top || a.left - b.left
                    ));
                    if (!candidates.length) return false;
                    const selected = preferLast
                        ? candidates[candidates.length - 1]
                        : candidates[0];
                    selected.control.setAttribute(
                        'data-docflow-v2-travel-control',
                        token
                    );
                    return true;
                }""",
                [
                    list(terms or ()),
                    str(control_kind or ""),
                    token,
                    str(section or ""),
                    bool(prefer_last),
                ],
            ))
        except Exception:
            return None
        if not found:
            return None
        locator = self._page.locator(
            f'[data-docflow-v2-travel-control="{token}"]'
        )
        return locator.first if locator.count() == 1 else None

    def _scroll_to_pending_semantic_evidence(
        self,
        field_id,
        labels,
        hints,
    ):
        """Best-effort viewport preparation; never chooses or writes a value."""
        terms = []
        for raw in labels or ():
            term = str(raw or "").split("[control=", 1)[0].strip()
            if len(term) >= 4 and term not in terms:
                terms.append(term)
        for term in terms[:8]:
            try:
                candidates = self._page.get_by_text(
                    re.compile(
                        rf"^\s*{re.escape(term)}\s*(?:\*|:)?\s*$",
                        re.IGNORECASE,
                    )
                )
                visible = [
                    candidates.nth(index)
                    for index in range(min(candidates.count(), 10))
                    if candidates.nth(index).is_visible()
                ]
                if len(visible) == 1:
                    visible[0].scroll_into_view_if_needed(timeout=700)
                    return True
            except Exception:
                continue
        for raw_hint in hints or ():
            hint = re.sub(r"[^A-Za-z0-9_-]", "", str(raw_hint or ""))
            if len(hint) < 3:
                continue
            try:
                candidates = self._page.locator(
                    f'input[id*="{hint}" i], textarea[id*="{hint}" i], '
                    f'select[id*="{hint}" i], input[name*="{hint}" i], '
                    f'textarea[name*="{hint}" i], select[name*="{hint}" i]'
                )
                visible = [
                    candidates.nth(index)
                    for index in range(min(candidates.count(), 20))
                    if candidates.nth(index).is_visible()
                ]
                if visible:
                    visible[0].scroll_into_view_if_needed(timeout=700)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _is_dynamic_request(request):
        try:
            method = str(request.method or "").upper()
        except Exception:
            method = ""
        # CEAC is an ASP.NET WebForms application. Branch controllers, Next,
        # and repeaters post the current form; unrelated GET/fetch traffic is
        # not a dispatch receipt and must never satisfy a controller wait.
        if method != "POST":
            return False
        try:
            resource_type = str(request.resource_type or "").lower()
        except Exception:
            resource_type = ""
        try:
            navigation = bool(request.is_navigation_request())
        except Exception:
            navigation = False
        return navigation or resource_type in {
            "document",
            "xhr",
            "fetch",
        }

    @staticmethod
    def _network_request_token(request):
        """Return one stable token across Playwright request callbacks."""
        implementation = getattr(request, "_impl_obj", None)
        guid = str(getattr(implementation, "_guid", "") or "")
        if guid:
            return f"guid:{guid}"
        return id(request)

    def _ensure_network_watch(self):
        """Track real postback traffic without waiting on ordinary actions."""
        page = self._page
        if page is None:
            return False
        if self._v2_network_page is page:
            return True

        def request_started(request):
            if not self._is_dynamic_request(request):
                return
            token = self._network_request_token(request)
            self._v2_network_started += 1
            self._v2_network_inflight.add(token)

        def request_ended(request):
            if not self._is_dynamic_request(request):
                return
            token = self._network_request_token(request)
            self._v2_network_ended += 1
            self._v2_network_inflight.discard(token)

        try:
            page.on("request", request_started)
            page.on("requestfinished", request_ended)
            page.on("requestfailed", request_ended)
        except Exception:
            self._v2_network_page = None
            return False
        self._v2_network_page = page
        return True

    def _dispatch_repeater_postback(self, locator):
        """Escalate a false ``__doPostBack`` receipt to one exact form POST.

        CEAC's language LinkButton can expose a valid ``__doPostBack`` href
        while the page-level function returns without starting a request.  A
        scheduled callback is therefore not a dispatch receipt.  V2 already
        owns a page-scoped network watcher, so first give the ordinary
        WebForms path a short chance to start real traffic.  Only when it
        starts no document/XHR/fetch request do we populate WebForms' two
        event fields from that same exact href and invoke the native form
        submit implementation once.  The server still performs its normal
        validation, and the caller still requires monotonic repeater growth.
        """
        network_before = max(0, int(self._v2_network_started))
        dispatched = super()._dispatch_repeater_postback(locator)
        ordinary = dict(
            getattr(self, "_last_repeater_dispatch_diagnostic", {}) or {}
        )
        if not dispatched:
            return False

        deadline = time.monotonic() + self.FALSE_POSTBACK_GRACE_SECONDS
        while time.monotonic() < deadline:
            if self._v2_network_started > network_before:
                ordinary["requestStarted"] = True
                ordinary["forcedNativeFormSubmit"] = False
                self._last_repeater_dispatch_diagnostic = ordinary
                return True
            try:
                self._page.wait_for_timeout(50)
            except Exception:
                break

        # If the ordinary callback replaced the document before Playwright
        # published its request event, the old locator will be detached and
        # this inspection fails closed.  The outer growth wait can still
        # recognize the resulting row; no second submission is attempted.
        try:
            forced = dict(locator.evaluate(
                """el => {
                    const href = String(
                        el.getAttribute('href') || ''
                    ).trim();
                    const matched = href.match(
                        /^javascript:\\s*__doPostBack\\(\\s*'([^']+)'\\s*,\\s*'([^']*)'\\s*\\)\\s*;?$/i
                    );
                    const form = el.form || el.closest('form');
                    const result = {
                        dispatched: false,
                        href: href.slice(0, 240),
                        matched: Boolean(matched),
                        formFound: Boolean(form),
                        forcedNativeFormSubmit: false
                    };
                    if (!matched || !form) return result;
                    const method = String(form.method || '').toLowerCase();
                    if (method !== 'post') {
                        result.formMethod = method;
                        return result;
                    }
                    const targetInput = form.querySelector(
                        'input[name="__EVENTTARGET"]'
                    );
                    const argumentInput = form.querySelector(
                        'input[name="__EVENTARGUMENT"]'
                    );
                    result.eventFieldsFound = Boolean(
                        targetInput && argumentInput
                    );
                    if (!targetInput || !argumentInput) return result;
                    let action;
                    try {
                        action = new URL(
                            form.getAttribute('action') || location.href,
                            location.href
                        );
                    } catch (_error) {
                        return result;
                    }
                    if (action.origin !== location.origin) {
                        result.sameOrigin = false;
                        return result;
                    }
                    const submit = (
                        form.ownerDocument.defaultView.HTMLFormElement
                            .prototype.submit
                    );
                    if (typeof submit !== 'function') return result;
                    const target = matched[1];
                    const argument = matched[2];
                    targetInput.value = target;
                    argumentInput.value = argument;
                    window.setTimeout(() => submit.call(form), 0);
                    return {
                        ...result,
                        dispatched: true,
                        eventFieldsFound: true,
                        sameOrigin: true,
                        forcedNativeFormSubmit: true,
                        target: target.slice(0, 200)
                    };
                }"""
            ) or {})
        except Exception as error:
            ordinary.update({
                "requestStarted": False,
                "forcedNativeFormSubmit": False,
                "forcedSubmitErrorType": type(error).__name__,
            })
            self._last_repeater_dispatch_diagnostic = ordinary
            return True

        forced["ordinaryPostback"] = ordinary
        forced["requestStarted"] = False
        self._last_repeater_dispatch_diagnostic = forced
        return bool(forced.get("dispatched"))

    def _begin_action_dom_watch(self):
        network_available = self._ensure_network_watch()
        super()._begin_action_dom_watch()
        try:
            state = self._page.evaluate(
                """() => {
                    const state = window.__docflowV2AsyncState ||= {
                        installed: false,
                        available: false,
                        begun: 0,
                        ended: 0,
                        inflight: 0,
                        lastEndAt: 0
                    };
                    if (!state.installed) {
                        state.installed = true;
                        try {
                            const manager = (
                                window.Sys
                                && window.Sys.WebForms
                                && window.Sys.WebForms.PageRequestManager
                                && window.Sys.WebForms.PageRequestManager
                                    .getInstance()
                            );
                            if (manager) {
                                state.available = true;
                                manager.add_beginRequest(() => {
                                    state.begun += 1;
                                    state.inflight += 1;
                                });
                                manager.add_endRequest(() => {
                                    state.ended += 1;
                                    state.inflight = Math.max(
                                        0,
                                        state.inflight - 1
                                    );
                                    state.lastEndAt = Date.now();
                                });
                            }
                        } catch (_error) {
                            state.available = false;
                        }
                    }
                    return {
                        available: Boolean(state.available),
                        begun: Number(state.begun || 0),
                        ended: Number(state.ended || 0),
                        inflight: Number(state.inflight || 0)
                    };
                }"""
            )
        except Exception:
            state = {}
        self._v2_async_before = {
            "available": bool((state or {}).get("available")),
            "begun": max(0, int((state or {}).get("begun") or 0)),
            "ended": max(0, int((state or {}).get("ended") or 0)),
            "inflight": max(0, int((state or {}).get("inflight") or 0)),
        }
        self._v2_network_before = {
            "available": bool(network_available),
            "started": max(0, int(self._v2_network_started)),
            "ended": max(0, int(self._v2_network_ended)),
            "inflight": len(self._v2_network_inflight),
            "inflightTokens": set(self._v2_network_inflight),
        }

    def _wait_for_watched_dom_replacement(self):
        evidence = dict(self._last_dynamic_refresh_evidence or {})
        if not evidence.get("postbackStarted"):
            return

        started = time.monotonic()
        deadline = started + self.DYNAMIC_SETTLE_TIMEOUT_SECONDS
        false_signal_deadline = (
            started + self.FALSE_POSTBACK_GRACE_SECONDS
        )
        unknown_signal_deadline = (
            started + self.UNKNOWN_POSTBACK_GRACE_SECONDS
        )
        async_before = dict(self._v2_async_before or {})
        network_before = dict(self._v2_network_before or {})
        network_inflight_before = set(
            network_before.get("inflightTokens") or ()
        )

        while time.monotonic() < deadline:
            token = f"document-{uuid4().hex}"
            try:
                state = self._page.evaluate(
                    """token => {
                        if (!window.__docflowAgentDocumentGeneration) {
                            window.__docflowAgentDocumentGeneration = token;
                        }
                        const asyncState = (
                            window.__docflowV2AsyncState || {}
                        );
                        return {
                            generation:
                                window.__docflowAgentDocumentGeneration,
                            fields: Array.from(document.querySelectorAll(
                                '[data-docflow-field]'
                            )).map(item => String(
                                item.getAttribute('data-docflow-field') || ''
                            )).filter(Boolean),
                            async: {
                                available: Boolean(asyncState.available),
                                begun: Number(asyncState.begun || 0),
                                ended: Number(asyncState.ended || 0),
                                inflight: Number(asyncState.inflight || 0)
                            }
                        };
                    }""",
                    token,
                )
            except Exception:
                state = None
            if not isinstance(state, dict):
                self._page.wait_for_timeout(80)
                continue

            async_state = dict(state.get("async") or {})
            begun = max(0, int(async_state.get("begun") or 0))
            ended = max(0, int(async_state.get("ended") or 0))
            inflight = max(0, int(async_state.get("inflight") or 0))
            request_began = begun > max(
                0, int(async_before.get("begun") or 0)
            )
            request_ended = ended > max(
                0, int(async_before.get("ended") or 0)
            )
            network_started = self._v2_network_started > max(
                0, int(network_before.get("started") or 0)
            )
            network_ended = self._v2_network_ended > max(
                0, int(network_before.get("ended") or 0)
            )
            new_network_inflight = set(
                self._v2_network_inflight
            ).difference(network_inflight_before)
            if (
                network_started
                and network_ended
                and not new_network_inflight
            ):
                # The response has completed. Give its synchronous render
                # callbacks one short turn before semantic replanning.
                self._page.wait_for_timeout(120)
                return
            if network_started and new_network_inflight:
                self._page.wait_for_timeout(80)
                continue
            if request_ended and inflight == 0:
                self._page.wait_for_timeout(120)
                return
            signal_deadline = (
                false_signal_deadline
                if async_before.get("available")
                else unknown_signal_deadline
            )
            if (
                not request_began
                and not network_started
                and time.monotonic() >= signal_deadline
            ):
                self._last_dynamic_refresh_evidence[
                    "v2FalsePostbackSignal"
                ] = True
                return
            self._page.wait_for_timeout(80)

        self._last_dynamic_refresh_evidence[
            "v2DynamicSettleTimedOut"
        ] = True
