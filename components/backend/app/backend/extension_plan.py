"""Verified Chrome-specific plan; independent of the legacy server executor."""
import re
from .extension_workflow import build_browser_workflow

def build_plan(payload):
    plan = build_browser_workflow(payload)
    raw_pages = plan.get("pages") or []
    sensitive_action_id = re.compile(
        r"^(security|immigration|inadmissibility)\."
        r"|refusal|immigrant_petition|specialized_skills"
        r"|military_service|paramilitary",
        re.IGNORECASE,
    )
    pages = []
    for source_page in raw_pages:
        page_key = str(source_page.get("key") or "")
        if source_page.get("manualReview"):
            continue

        # Copy explicit source answers through ordinary questionnaire pages.
        # Negative answers have no explanation branch; unresolved or positive
        # sensitive pages retain the workflow builder's manual-review gate.
        reviewed_negative_history_ids = {
            "us_history.refusal_or_admission",
            "us_history.immigrant_petition",
        }

        def chrome_action_allowed(action):
            action_id = str(action.get("id") or "")
            # Present Work has a duties box but no separate Job Title input.
            # Job Title belongs to the previous-employment records page.
            if page_key == "work_education1" and action_id == "work.jobTitle":
                return False
            # The current CEAC Part 4 has fraud/misrepresentation and removal
            # questions. These two legacy intake questions have no separate
            # controls and must not be mapped onto either remaining question.
            if page_key == "security_background4" and action_id in {
                "immigration.status_violation", "immigration.assisted_illegal_entry"
            }:
                return False
            if (
                (action_id in reviewed_negative_history_ids
                 or action.get("sourceAnswerExplicit") is True)
                and action.get("kind") == "yes_no"
                and str(action.get("value") or "").strip().lower() == "no"
            ):
                return True
            return not sensitive_action_id.search(action_id)

        actions = [
            action for action in (source_page.get("actions") or [])
            if chrome_action_allowed(action)
        ]
        if not actions:
            continue
        page = dict(source_page)
        page["actions"] = actions
        pages.append(page)
    total_fields = sum(len(page.get("actions") or []) for page in pages)
    if not pages or not total_fields:
        raise ValueError("当前客户档案没有可交给 Chrome 插件的已确认字段")

    return pages
