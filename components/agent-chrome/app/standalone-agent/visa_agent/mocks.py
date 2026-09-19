"""Deterministic providers used by tests and the offline demo."""

from collections import deque

from .models import (
    ActionKind,
    BrowserObservation,
    ComputerAction,
)


class EmptyExtractionModel:
    def extract(self, text, document_type, filename):
        return []


class ScriptedComputerUseModel:
    def __init__(self, actions):
        self.actions = deque(actions)

    def propose_action(self, observation, available_field_ids, completed_field_ids):
        if not self.actions:
            return ComputerAction(kind=ActionKind.COMPLETE)
        return self.actions.popleft()

    def verify_action(self, action, before, after):
        return after.visible_text != before.visible_text


class MockBrowserDriver:
    def __init__(
        self,
        url="https://ceac.state.gov/GenNIV/General/complete/complete_personal.aspx",
        title="Personal Information 1",
        visible_text="Personal Information 1",
    ):
        self.url = url
        self.title = title
        self.visible_text = visible_text
        self.executed = []
        self.control_values = {}
        self.acknowledged_action_ids = []

    def observe(self):
        return BrowserObservation(
            url=self.url,
            title=self.title,
            visible_text=self.visible_text,
            screenshot_ref=f"mock://step-{len(self.executed)}",
            control_values=dict(self.control_values),
            acknowledged_action_ids=list(self.acknowledged_action_ids),
        )

    def observe_lightweight(self):
        return self.observe()

    def execute(self, action):
        self.executed.append(action)
        if action.field_id:
            self.control_values[action.field_id] = action.value
        self.acknowledged_action_ids.append(action.id)
        self.visible_text = (
            f"{self.visible_text}\nverified:{action.field_id}:{action.value}"
        )
