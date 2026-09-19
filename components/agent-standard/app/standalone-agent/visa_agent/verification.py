"""Deterministic action verification independent of the proposing model."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .models import ActionKind


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    reason: str = ""


class DeterministicActionVerifier:
    """Verify browser state, not model confidence or narrative text."""

    def verify(self, action, before, after):
        # A legacy CEAC page can retain an unrelated validation message while
        # the Agent repairs another field.  Reject only errors introduced by
        # this action, or errors explicitly tagged to its deterministic field
        # marker.  Page navigation is intentionally stricter in workflow.py:
        # Next still refuses to advance while *any* page error is present.
        related_errors = self._action_errors(action, before, after)
        if related_errors:
            return VerificationResult(
                False,
                "Browser reported new or target-field validation errors",
            )
        if action.kind in {ActionKind.TYPE, ActionKind.SELECT}:
            actual = self._control_value(after, action)
            if actual is None:
                # Backward-compatible offline mock acknowledgement. Production
                # browser adapters should populate control_values instead.
                marker = f"verified:{action.field_id}:{action.value}"
                if marker in after.visible_text:
                    return VerificationResult(True)
                return VerificationResult(
                    False, "Browser did not expose the target control value"
                )
            if self._normalize(actual) != self._normalize(action.value):
                return VerificationResult(False, "Control value does not match approved value")
            return VerificationResult(True)
        if action.kind == ActionKind.CLICK:
            repeater = self._repeater_count(action, after)
            if repeater is not None:
                actual, expected = repeater
                if actual >= expected:
                    return VerificationResult(True)
                return VerificationResult(
                    False,
                    "Repeater record count is below the approved target",
                )
            if action.id in after.acknowledged_action_ids:
                return VerificationResult(True)
            if (after.url, after.title) != (before.url, before.title):
                return VerificationResult(True)
            return VerificationResult(False, "Click has no deterministic acknowledgement")
        if action.kind == ActionKind.NAVIGATE:
            expected = action.value or action.target_hint
            if self._url(after.url) == self._url(expected):
                return VerificationResult(True)
            return VerificationResult(False, "Browser did not reach the approved URL")
        if action.kind == ActionKind.WAIT:
            return VerificationResult(True)
        if action.kind == ActionKind.SCROLL:
            geometry_available = bool(
                max(
                    int(getattr(before, "scroll_height", 0) or 0),
                    int(getattr(after, "scroll_height", 0) or 0),
                    int(getattr(before, "viewport_height", 0) or 0),
                    int(getattr(after, "viewport_height", 0) or 0),
                )
            )
            if geometry_available:
                before_position = (
                    int(getattr(before, "scroll_x", 0) or 0),
                    int(getattr(before, "scroll_y", 0) or 0),
                )
                after_position = (
                    int(getattr(after, "scroll_x", 0) or 0),
                    int(getattr(after, "scroll_y", 0) or 0),
                )
                if after_position != before_position:
                    return VerificationResult(True)
                return VerificationResult(
                    False,
                    "Scroll did not move the rendered document",
                )
            if action.id in after.acknowledged_action_ids:
                return VerificationResult(True)
            return VerificationResult(False, "Scroll has no deterministic acknowledgement")
        if action.kind == ActionKind.PRESS_KEY:
            if action.id in after.acknowledged_action_ids:
                return VerificationResult(True)
            return VerificationResult(False, "Key press has no deterministic acknowledgement")
        return VerificationResult(False, "Action kind cannot be verified")

    def verify_current(self, action, observation):
        """Check a pending action after restart without executing it twice."""
        if action.kind in {ActionKind.TYPE, ActionKind.SELECT}:
            actual = self._control_value(observation, action)
            if actual is not None and self._normalize(actual) == self._normalize(action.value):
                return VerificationResult(True)
        if action.kind == ActionKind.NAVIGATE:
            expected = action.value or action.target_hint
            if self._url(observation.url) == self._url(expected):
                return VerificationResult(True)
        repeater = self._repeater_count(action, observation)
        if repeater is not None:
            actual, expected = repeater
            if actual >= expected:
                return VerificationResult(True)
            return VerificationResult(
                False,
                "Repeater record count is below the approved target",
            )
        if action.id in observation.acknowledged_action_ids:
            return VerificationResult(True)
        return VerificationResult(
            False,
            "Pending action outcome is uncertain; refusing to repeat it automatically",
        )

    @staticmethod
    def _repeater_count(action, observation):
        reason = str(getattr(action, "reason", "") or "")
        if not reason.startswith("Deterministic repeater ensure "):
            return None
        matched = re.search(
            r"\bexpected_count=(\d{1,3})\b",
            reason,
            flags=re.IGNORECASE,
        )
        if not matched:
            return None
        key = str(
            getattr(action, "field_id", "")
            or getattr(action, "id", "")
            or ""
        )
        counts = dict(
            getattr(observation, "repeater_counts", {}) or {}
        )
        if not key or key not in counts:
            return None
        try:
            actual = max(0, int(counts[key]))
        except (TypeError, ValueError):
            return None
        return actual, max(1, int(matched.group(1)))

    @staticmethod
    def _control_value(observation, action):
        for key in (action.field_id, action.target_hint):
            if key and key in observation.control_values:
                return observation.control_values[key]
        return None

    @staticmethod
    def _normalize(value):
        return " ".join(str(value).split()).casefold()

    @classmethod
    def _action_errors(cls, action, before, after):
        before_errors = {
            cls._normalize(error)
            for error in list(before.errors or [])
            if cls._normalize(error)
        }
        after_errors = [
            str(error)
            for error in list(after.errors or [])
            if cls._normalize(error)
        ]
        new_errors = [
            error for error in after_errors
            if cls._normalize(error) not in before_errors
        ]
        if new_errors:
            return new_errors
        field_id = str(action.field_id or "").strip()
        target_hint = str(action.target_hint or "").strip()
        markers = {
            item.casefold()
            for item in (field_id, target_hint)
            if item
        }
        if field_id:
            markers.add(field_id.rsplit(".", 1)[-1].casefold())
        return [
            error
            for error in after_errors
            if any(
                (
                    f"[field_id={marker}]" in error.casefold()
                    or (
                        len(marker) >= 5
                        and marker in error.casefold()
                    )
                )
                for marker in markers
            )
        ]

    @staticmethod
    def _url(value):
        parsed = urlsplit(str(value))
        return urlunsplit((
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path,
            parsed.query,
            "",
        ))
