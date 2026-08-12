#!/usr/bin/env python3
"""Visible backend Browser Use worker for the CEAC DS-160 Travel page.

The worker receives a private, page-scoped action plan. It never clicks Save or
Next and does not answer CAPTCHA, declarations, or security/background items.
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


TRAVEL_URL_FRAGMENT = "/GenNIV/General/complete/complete_travel.aspx"
CEAC_START_URL = "https://ceac.state.gov/GenNIV/Default.aspx"
STOP_REQUESTED = False


LOCATE_CONTROL_JS = r"""
(action) => {
  const normalize = (value) => String(value || "")
    .replace(/[\u00a0\s]+/g, " ").trim().toUpperCase();
  const visible = (element) => {
    if (!element || element.disabled || element.type === "hidden") return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && rect.width > 0 && rect.height > 0;
  };
  const controls = Array.from(document.querySelectorAll("input, select, textarea"))
    .filter(visible);
  const directLabel = (element) => {
    const values = [
      element.getAttribute("aria-label"), element.getAttribute("title"),
      element.getAttribute("placeholder"), element.id, element.name
    ];
    if (element.labels) {
      for (const label of element.labels) values.push(label.textContent);
    }
    const parentLabel = element.closest("label");
    if (parentLabel) values.push(parentLabel.textContent);
    return normalize(values.filter(Boolean).join(" "));
  };
  const groupFor = (element) => {
    const row = element.closest("tr");
    if (row) return row;
    let node = element.parentElement;
    for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
      const text = normalize(node.textContent);
      if (text.length >= 8 && text.length <= 900) return node;
    }
    return element.parentElement || element;
  };
  const contextFor = (element) => normalize(
    `${directLabel(element)} ${(groupFor(element).textContent || "")} ${element.id || ""} ${element.name || ""}`
  );
  const terms = (action.labelTerms || []).map(normalize).filter(Boolean);
  const optionTerms = (action.optionTerms || []).map(normalize).filter(Boolean);
  const termScore = (text, wanted) => wanted.reduce(
    (score, term) => score + (text.includes(term) ? 1 : 0), 0
  );
  const mark = (element, role) => {
    const token = `${action.id}-${role}-${Math.random().toString(36).slice(2, 9)}`;
    element.setAttribute("data-docflow-agent", token);
    return token;
  };
  document.querySelectorAll("[data-docflow-agent]").forEach((element) => {
    element.removeAttribute("data-docflow-agent");
  });

  if (action.kind === "select_text") {
    let best = null;
    for (const element of controls.filter((item) => item.tagName === "SELECT")) {
      const options = Array.from(element.options || []);
      for (const option of options) {
        const optionText = normalize(`${option.textContent || ""} ${option.value || ""}`);
        const matched = termScore(optionText, optionTerms);
        if (!matched || (optionTerms.length > 1 && matched < optionTerms.length - 1)) continue;
        const labelText = directLabel(element);
        const context = contextFor(element);
        const labelScore = termScore(labelText, terms) * 5 + termScore(context, terms);
        const score = matched * 20 + labelScore + (optionText === normalize(action.value) ? 8 : 0);
        if (!best || score > best.score) best = { element, option, score };
      }
    }
    if (!best) return { status: "not_found" };
    return {
      status: "found", role: "select", marker: mark(best.element, "select"),
      optionValue: best.option.value,
      alreadySet: String(best.element.value) === String(best.option.value)
    };
  }

  if (action.kind === "yes_no") {
    const desired = normalize(action.value);
    let best = null;
    for (const element of controls.filter((item) => item.type === "radio")) {
      const context = contextFor(element);
      const questionScore = termScore(context, terms);
      if (!questionScore) continue;
      const answerText = normalize(`${directLabel(element)} ${element.value || ""}`);
      const isYes = /(^|\s)(YES|Y|TRUE|1)(\s|$)/.test(answerText);
      const isNo = /(^|\s)(NO|N|FALSE|2)(\s|$)/.test(answerText);
      const answerMatch = desired === "YES" ? isYes : isNo;
      if (!answerMatch) continue;
      const score = questionScore * 10 + termScore(directLabel(element), terms) * 3;
      if (!best || score > best.score) best = { element, score };
    }
    if (!best) return { status: "not_found" };
    return {
      status: "found", role: "radio", marker: mark(best.element, "radio"),
      alreadySet: Boolean(best.element.checked)
    };
  }

  const candidateControls = controls.filter((element) => {
    if (action.kind === "text") {
      return element.tagName === "TEXTAREA"
        || (element.tagName === "INPUT" && ["", "text", "tel", "email", "number"].includes(element.type));
    }
    return element.tagName === "INPUT" || element.tagName === "SELECT";
  });
  let anchor = null;
  for (const element of candidateControls) {
    const direct = directLabel(element);
    const context = contextFor(element);
    const directMatches = termScore(direct, terms);
    const contextMatches = termScore(context, terms);
    if (!directMatches && !contextMatches) continue;
    const exactBonus = terms.some((term) => direct === term) ? 25 : 0;
    const score = directMatches * 8 + contextMatches * 2 + exactBonus;
    if (!anchor || score > anchor.score) anchor = { element, score };
  }
  if (!anchor) return { status: "not_found" };

  if (action.kind === "text") {
    return {
      status: "found", role: "text", marker: mark(anchor.element, "text"),
      alreadySet: normalize(anchor.element.value) === normalize(action.value)
    };
  }

  const group = groupFor(anchor.element);
  const grouped = Array.from(group.querySelectorAll("input, select, textarea")).filter(visible);
  if (action.kind === "date") {
    const output = [];
    for (const element of grouped) {
      const identity = normalize(`${element.id || ""} ${element.name || ""} ${directLabel(element)}`);
      let part = "";
      if (identity.includes("MONTH")) part = "month";
      else if (identity.includes("YEAR") || String(element.maxLength) === "4") part = "year";
      else if (identity.includes("DAY") || String(element.maxLength) === "2") part = "day";
      else if (element.tagName === "SELECT") {
        const optionText = normalize(Array.from(element.options || []).map((item) => item.textContent).join(" "));
        if (optionText.includes("JAN") && optionText.includes("DEC")) part = "month";
        else if (optionText.includes("31")) part = "day";
        else if (/\b20\d{2}\b/.test(optionText)) part = "year";
      }
      if (part && !output.some((item) => item.part === part)) {
        output.push({ part, tag: element.tagName.toLowerCase(), marker: mark(element, part) });
      }
    }
    if (!output.length && grouped.length === 1) {
      output.push({ part: "full", tag: grouped[0].tagName.toLowerCase(), marker: mark(grouped[0], "full") });
    }
    return output.length ? { status: "found", role: "date", controls: output } : { status: "not_found" };
  }

  if (action.kind === "duration") {
    const amount = grouped.find((element) => element.tagName === "INPUT" && element.type !== "hidden");
    const unit = grouped.find((element) => element.tagName === "SELECT");
    const controlsOut = [];
    if (amount) controlsOut.push({ part: "amount", tag: "input", marker: mark(amount, "amount") });
    if (unit) controlsOut.push({ part: "unit", tag: "select", marker: mark(unit, "unit") });
    return controlsOut.length ? { status: "found", role: "duration", controls: controlsOut } : { status: "not_found" };
  }
  return { status: "not_found" };
}
"""

SET_SELECT_JS = r"""
(payload) => {
  const element = document.querySelector(`[data-docflow-agent="${payload.marker}"]`);
  if (!element || element.tagName !== "SELECT") return { ok: false };
  const normalize = (value) => String(value || "").replace(/[\u00a0\s]+/g, " ").trim().toUpperCase();
  let option = null;
  if (payload.optionValue !== undefined) {
    option = Array.from(element.options).find((item) => String(item.value) === String(payload.optionValue));
  }
  if (!option && payload.optionText) {
    const wanted = normalize(payload.optionText);
    option = Array.from(element.options).find((item) => normalize(item.textContent).includes(wanted));
  }
  if (!option) return { ok: false };
  const changed = String(element.value) !== String(option.value);
  element.value = option.value;
  if (changed) {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }
  return { ok: true, changed };
}
"""

COMMIT_INPUT_JS = r"""
(marker) => {
  const element = document.querySelector(`[data-docflow-agent="${marker}"]`);
  if (!element) return false;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.blur();
  return true;
}
"""

PAGE_STATE_JS = r"""
() => ({
  url: location.href,
  title: document.title,
  travelForm: /Travel Information/i.test(document.title || "")
    && /Purpose of Trip to the U\.S\./i.test(document.body ? document.body.innerText : ""),
  sessionProblem: /session (?:has )?(?:timed out|expired)|application error/i.test(
    document.body ? document.body.innerText : ""
  )
})
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def write_private_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


class StatusWriter:
    def __init__(self, job, path):
        self.job = job
        self.path = Path(path)
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        self.logs = list(existing.get("logs") or [])[-100:]
        self.completed = int(existing.get("completedFields") or 0)

    def update(self, state, message, log_type=None, log_message=None, **extra):
        if log_message:
            self.logs.append({
                "at": now_iso(), "type": log_type or "info", "message": log_message,
            })
            self.logs = self.logs[-100:]
        payload = {
            "jobId": self.job["jobId"],
            "caseId": self.job["caseId"],
            "state": state,
            "message": message,
            "completedFields": self.completed,
            "totalFields": len(self.job.get("actions") or []),
            "logs": self.logs,
            "updatedAt": now_iso(),
            **extra,
        }
        write_private_json(self.path, payload)


def read_json_result(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def date_parts(value):
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return None
    year, month, day = match.groups()
    months = [
        "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    ]
    return {
        "year": year, "month": months[int(month) - 1],
        "day": str(int(day)), "full": f"{int(day):02d}-{months[int(month) - 1]}-{year}",
    }


async def marked_element(page, marker):
    elements = await page.get_elements_by_css_selector(
        f'[data-docflow-agent="{marker}"]'
    )
    return elements[0] if len(elements) == 1 else None


async def set_select(page, marker, *, option_value=None, option_text=None):
    result = await page.evaluate(SET_SELECT_JS, {
        "marker": marker, "optionValue": option_value, "optionText": option_text,
    })
    return read_json_result(result)


async def fill_text(page, marker, value):
    element = await marked_element(page, marker)
    if not element:
        return False
    await element.fill(str(value))
    await page.evaluate(COMMIT_INPUT_JS, marker)
    return True


async def apply_action(page, action):
    located = read_json_result(await page.evaluate(LOCATE_CONTROL_JS, action))
    if located.get("status") != "found":
        return {"status": "not_found", "changed": False}
    if located.get("alreadySet"):
        return {"status": "already_set", "changed": False}

    role = located.get("role")
    if role == "text":
        ok = await fill_text(page, located["marker"], action["value"])
        return {"status": "filled" if ok else "not_found", "changed": ok}
    if role == "radio":
        element = await marked_element(page, located["marker"])
        if not element:
            return {"status": "not_found", "changed": False}
        await element.click()
        return {"status": "filled", "changed": True}
    if role == "select":
        result = await set_select(
            page, located["marker"], option_value=located.get("optionValue")
        )
        return {
            "status": "filled" if result.get("ok") else "not_found",
            "changed": bool(result.get("changed")),
        }
    if role == "date":
        values = date_parts(action.get("value"))
        if not values:
            return {"status": "invalid_value", "changed": False}
        changed = False
        for control in located.get("controls") or []:
            value = values.get(control.get("part"))
            if not value:
                continue
            if control.get("tag") == "select":
                result = await set_select(page, control["marker"], option_text=value)
                changed = changed or bool(result.get("ok"))
            else:
                changed = await fill_text(page, control["marker"], value) or changed
        return {"status": "filled" if changed else "not_found", "changed": changed}
    if role == "duration":
        duration = action.get("duration") or {}
        changed = False
        for control in located.get("controls") or []:
            if control.get("part") == "amount":
                changed = await fill_text(page, control["marker"], duration.get("amount")) or changed
            elif control.get("part") == "unit":
                result = await set_select(
                    page, control["marker"], option_text=duration.get("unit")
                )
                changed = changed or bool(result.get("ok"))
        return {"status": "filled" if changed else "not_found", "changed": changed}
    return {"status": "not_found", "changed": False}


async def wait_for_travel_page(browser, writer, stop_path, timeout_seconds=900):
    deadline = time.monotonic() + timeout_seconds
    start_redirected = False
    last_page_title = ""
    while time.monotonic() < deadline and not STOP_REQUESTED and not Path(stop_path).exists():
        page = await browser.get_current_page()
        if page:
            try:
                state = read_json_result(await page.evaluate(PAGE_STATE_JS))
                if state.get("travelForm") and TRAVEL_URL_FRAGMENT.lower() in str(state.get("url") or "").lower():
                    return page
                page_title = re.sub(r"\s+", " ", str(state.get("title") or "CEAC 页面")).strip()[:100]
                if page_title and page_title != last_page_title:
                    last_page_title = page_title
                    writer.update(
                        "waiting_for_travel_page",
                        f"受控 Chrome 当前位于：{page_title}。请在这个专用窗口进入 Travel Information",
                        "info", f"受控 Chrome 页面：{page_title}",
                    )
                if state.get("sessionProblem") and not start_redirected:
                    await page.goto(CEAC_START_URL)
                    start_redirected = True
                    writer.update(
                        "waiting_for_travel_page",
                        "CEAC 尚无有效会话，请在新窗口中恢复申请并进入 Travel Information",
                        "warning", "需要人工完成 CEAC 会话恢复或验证码",
                    )
            except Exception:
                pass
        await asyncio.sleep(1.2)
    return None


async def run_job(job_path, status_path, stop_path):
    global STOP_REQUESTED
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    writer = StatusWriter(job, status_path)
    if job.get("page") != "travel" or job.get("clickSave") or job.get("clickNext"):
        raise ValueError("任务范围无效：Travel v1 不允许 Save 或 Next")
    parsed_target = str(job.get("targetUrl") or "")
    if not parsed_target.startswith("https://ceac.state.gov/"):
        raise ValueError("任务目标必须是 ceac.state.gov")

    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
    os.environ.setdefault("BROWSER_USE_DISABLE_EXTENSIONS", "1")
    from browser_use import Browser

    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome_path).exists():
        raise RuntimeError("没有找到 Google Chrome")
    profile_root = Path(job_path).parent.parent / "browser-use" / "profile-seed"
    profile_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    browser = Browser(
        headless=False,
        executable_path=chrome_path,
        user_data_dir=str(profile_root),
        enable_default_extensions=False,
        allowed_domains=["ceac.state.gov"],
        keep_alive=False,
    )
    try:
        writer.update(
            "starting", "正在启动 Browser Use 专用 Chrome",
            "info", "Browser Use 正在启动专用 Chrome；普通 Chrome 标签不会被接管",
        )
        await browser.start()
        page = await browser.get_current_page()
        if page is None:
            page = await browser.new_page(job["targetUrl"])
        else:
            await page.goto(job["targetUrl"])
        writer.update(
            "waiting_for_travel_page",
            "请只在 Browser Use 新开的专用 Chrome 中恢复 CEAC 会话并进入 Travel Information",
            "info", "等待专用 Chrome 中的 Travel Information 页面就绪",
        )
        page = await wait_for_travel_page(browser, writer, stop_path)
        if page is None:
            if STOP_REQUESTED or Path(stop_path).exists():
                writer.update("stopped", "Browser Use 已停止", "warning", "任务已由顾问停止")
                return
            raise TimeoutError("15 分钟内未进入 Travel Information 页面")

        actions = job.get("actions") or []
        writer.update(
            "running", f"Travel Information 已就绪，开始填写 {len(actions)} 项",
            "info", "已识别真实 CEAC Travel Information 页面",
        )
        failed = []
        for action in actions:
            if STOP_REQUESTED or Path(stop_path).exists():
                writer.update("stopped", "Browser Use 已停止", "warning", "任务已由顾问停止")
                return
            try:
                result = await apply_action(page, action)
            except Exception as error:
                result = {"status": "error", "changed": False, "error": type(error).__name__}
            status = result.get("status")
            if status in {"filled", "already_set"}:
                writer.completed += 1
                writer.update(
                    "running", f"正在填写 {action['label']}",
                    "success", f"已处理 {action['label']}",
                )
            else:
                failed.append(action["label"])
                writer.update(
                    "running", f"未能定位 {action['label']}，已保留给人工",
                    "warning", f"未定位到 {action['label']}，未写入任何猜测值",
                )
            if action.get("causesRefresh") and result.get("changed"):
                await asyncio.sleep(2.2)
                current = await browser.get_current_page()
                if current is not None:
                    page = current
            else:
                await asyncio.sleep(0.18)

        message = (
            f"Travel 页已处理 {writer.completed}/{len(actions)} 项；请人工核对，未点击 Save 或 Next"
        )
        writer.update(
            "review_required", message, "success", "Travel 页填写结束，等待人工核对",
            failedFields=failed,
        )
        while not STOP_REQUESTED and not Path(stop_path).exists():
            try:
                current = await browser.get_current_page()
                if current is None:
                    break
                await current.get_url()
            except Exception:
                break
            await asyncio.sleep(1.5)
    finally:
        try:
            await browser.stop()
        except Exception:
            pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--stop", required=True)
    return parser.parse_args(argv)


def public_error_message(error):
    message = str(error)
    if isinstance(error, TimeoutError) and "BrowserStartEvent" in message:
        return (
            "Chrome 启动超时。请关闭残留的 Browser Use Chrome 窗口后重试；"
            "若当前网站由 Codex 启动，请改为在 Finder 中双击“启动Screen Agent演示.command”。"
        )
    if isinstance(error, PermissionError):
        return "系统阻止了 Chrome 启动，请从 Finder 运行启动脚本并允许 macOS 打开 Chrome。"
    return f"Browser Use 无法继续（{type(error).__name__}）。请查看本地运行日志后重试。"


def main(argv=None):
    global STOP_REQUESTED
    args = parse_args(argv)

    def request_stop(_signum, _frame):
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        asyncio.run(run_job(args.job, args.status, args.stop))
    except Exception as error:
        try:
            job = json.loads(Path(args.job).read_text(encoding="utf-8"))
            writer = StatusWriter(job, args.status)
            writer.update(
                "blocked", public_error_message(error),
                "error", f"Browser Use 任务失败：{type(error).__name__}",
            )
        except Exception:
            pass
        print(f"Browser Use worker failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
