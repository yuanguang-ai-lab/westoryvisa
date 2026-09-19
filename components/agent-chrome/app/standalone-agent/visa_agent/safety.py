"""Non-bypassable safety policy for visa form computer use."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

from .models import ActionKind, RiskLevel
from .page_plans import classify_ceac_page


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_human: bool = False
    reason: str = ""


class VisaFormSafetyPolicy:
    ALLOWED_HOSTS = {"ceac.state.gov"}
    ALLOWED_PATH_PREFIXES = ("/GenNIV/",)
    HUMAN_TEXT_PATTERNS = (
        r"\bcaptcha\b",
        r"\b(?:start|create|retrieve)\s+(?:an?\s+)?application\b",
        r"sign and submit",
        r"electronic signature",
        r"passport number.*confirm",
        r"\bpayment\b",
        r"visa fee",
        r"username",
        r"password",
        r"one[- ]time code",
        r"verification code",
    )
    ERROR_TEXT_PATTERNS = (
        r"\bvalidation error\b",
        r"\brequired field\b",
        r"\binvalid (?:value|entry|format)\b",
        r"\bplease correct\b",
    )
    UNTRUSTED_INSTRUCTION_PATTERNS = (
        r"ignore (?:all |the )?(?:previous|prior) instructions",
        r"reveal (?:the )?(?:system prompt|credentials|secret)",
        r"(?:upload|send|post) .{0,40} to https?://",
    )
    FORBIDDEN_TARGET_PATTERNS = (
        r"\bsubmit\b",
        r"\bsign\b",
        r"\bpay\b",
        r"\bdelete\b",
        r"\bconfirm booking\b",
        r"\bcaptcha\b",
    )
    SENSITIVE_FIELD_PREFIXES = (
        "security.",
        "history.refusal",
        "history.overstay",
        "history.criminal",
        "history.immigration",
        "history.removal",
    )
    SENSITIVE_QUERY_KEYS = {
        "password", "passwd", "token", "secret", "otp", "code", "ssn"
    }

    def inspect_page(self, observation):
        target = self.inspect_navigation_target(observation.url)
        if not target.allowed:
            return target
        classification = classify_ceac_page(observation)
        if classification.kind == "session_timeout":
            # A CEAC session timeout is a recoverable page boundary, not a
            # generic Agent failure.  Name it explicitly so the persistent
            # watcher and the visible cursor overlay tell the consultant what
            # changed and what will happen after the application is retrieved.
            return PolicyDecision(
                allowed=False,
                requires_human=True,
                reason=(
                    "CEAC 会话已超时。请重新进入原 DS-160 申请的正式表格；"
                    "进入后 Gemini 会自动继续，无需再次点击运行。"
                ),
            )
        if classification.kind == "captcha":
            return PolicyDecision(
                allowed=False,
                requires_human=True,
                reason="Human checkpoint detected: captcha",
            )
        if classification.kind in {"sign", "final_submit"}:
            return PolicyDecision(
                allowed=False,
                requires_human=True,
                reason=(
                    "DS-160 Review/Sign/final submission requires human review"
                ),
            )
        if classification.kind != "formal":
            return PolicyDecision(
                allowed=False,
                requires_human=True,
                reason=(
                    "Gemini may run only after the consultant manually enters "
                    "the existing DS-160 formal form"
                ),
            )
        visible = f"{observation.title}\n{observation.visible_text}".lower()
        for pattern in self.HUMAN_TEXT_PATTERNS:
            if re.search(pattern, visible, flags=re.IGNORECASE):
                return PolicyDecision(
                    allowed=False,
                    requires_human=True,
                    reason=f"Human checkpoint detected: {pattern}",
                )
        for pattern in self.UNTRUSTED_INSTRUCTION_PATTERNS:
            if re.search(pattern, visible, flags=re.IGNORECASE):
                return PolicyDecision(
                    allowed=False,
                    requires_human=True,
                    reason="Untrusted page instruction requires human review",
                )
        return PolicyDecision(allowed=True)

    def inspect_navigation_target(self, target_url):
        parsed = urlparse(str(target_url))
        if parsed.scheme != "https" or parsed.hostname not in self.ALLOWED_HOSTS:
            return PolicyDecision(
                allowed=False,
                requires_human=True,
                reason=f"Page is outside the allowed CEAC domain: {parsed.hostname or 'unknown'}",
            )
        if parsed.username or parsed.password:
            return PolicyDecision(False, True, "Credentials in navigation URLs are forbidden")
        if not any(parsed.path.startswith(prefix) for prefix in self.ALLOWED_PATH_PREFIXES):
            return PolicyDecision(False, True, "CEAC path is outside the allowed form area")
        query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query)}
        if query_keys.intersection(self.SENSITIVE_QUERY_KEYS):
            return PolicyDecision(False, True, "Sensitive data in navigation URL is forbidden")
        return PolicyDecision(allowed=True)

    def inspect_action(self, action, fields, page_plan=None):
        if page_plan is None:
            return PolicyDecision(False, True, "No approved page plan matches this page")
        if action.kind not in page_plan.allowed_action_kinds:
            return PolicyDecision(False, False, "Action kind is not allowed on this page")
        if action.kind in {
            ActionKind.COMPLETE,
            ActionKind.PAUSE,
            ActionKind.PRESS_KEY,
            ActionKind.SCROLL,
            ActionKind.WAIT,
        }:
            return PolicyDecision(allowed=True)
        if action.kind == ActionKind.NAVIGATE:
            # Opening/restoring the approved start URL and advancing with the
            # uniquely resolved CEAC Next control are system-owned operations.
            # A model-authored URL can skip required pages while still staying
            # on the allowed host, so it is never a form action.
            return PolicyDecision(
                False,
                False,
                "Form navigation is owned by the deterministic workflow",
            )

        field = fields.get(action.field_id) if action.field_id else None
        if action.kind in {ActionKind.TYPE, ActionKind.SELECT}:
            if field is None:
                return PolicyDecision(False, False, "Action references an unknown field")
            if not field.confirmed:
                return PolicyDecision(False, True, "Field is not human-confirmed")
            if not page_plan.allows_field(action.field_id):
                return PolicyDecision(False, False, "Field is not allowed on this page")

        target = f"{action.target_hint} {action.reason}".lower()
        for pattern in self.FORBIDDEN_TARGET_PATTERNS:
            if re.search(pattern, target, flags=re.IGNORECASE):
                return PolicyDecision(
                    False, True, f"Forbidden irreversible action detected: {pattern}"
                )
        if action.kind == ActionKind.CLICK:
            if action.field_id:
                if field is None:
                    return PolicyDecision(False, False, "Click references an unknown field")
                if not field.confirmed:
                    return PolicyDecision(False, True, "Field is not human-confirmed")
                if not page_plan.allows_field(action.field_id):
                    return PolicyDecision(False, False, "Field is not allowed on this page")
                return PolicyDecision(allowed=True)
            if not (
                action.dispatch_receipt_required
                and action.dispatch_receipt_scope
                and re.search(
                    r"\bnext\b",
                    action.target_hint,
                    re.IGNORECASE,
                )
            ):
                return PolicyDecision(
                    False,
                    False,
                    "Unbound page-control clicks are owned by the deterministic workflow",
                )
            if not page_plan.allows_click(action.target_hint):
                return PolicyDecision(
                    False, False, "Click target is not in the page allowlist"
                )
            if (
                re.search(r"\b(next|continue)\b", action.target_hint, re.IGNORECASE)
                and not page_plan.allow_next
            ):
                return PolicyDecision(False, False, "Next-page action is disabled")
        return PolicyDecision(allowed=True)

    def observation_has_errors(self, observation):
        if observation.errors:
            return True
        visible = observation.visible_text
        return any(
            re.search(pattern, visible, flags=re.IGNORECASE)
            for pattern in self.ERROR_TEXT_PATTERNS
        )
