import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "docflow-chrome-extension"


class ChromeExtensionTests(unittest.TestCase):
    def test_frontend_accepts_current_or_newer_backend_revision(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        server_source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('const REQUIRED_API_REVISION = 20;', app)
        self.assertIn('state.apiRevision >= REQUIRED_API_REVISION', app)
        self.assertNotIn('state.apiVersion === REQUIRED_API_VERSION', app)
        self.assertIn('"apiRevision": 20', server_source)

    def test_manifest_has_narrow_hosts_and_no_all_urls_permission(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertNotIn("<all_urls>", manifest["host_permissions"])
        self.assertIn("https://ceac.state.gov/*", manifest["host_permissions"])
        self.assertIn("https://www.usvisascheduling.com/*", manifest["host_permissions"])
        self.assertIn("http://127.0.0.1/*", manifest["host_permissions"])
        self.assertNotIn("webRequest", manifest["permissions"])
        self.assertIn("scripting", manifest["permissions"])

    def test_extension_contains_hard_stops_and_no_captcha_bypass(self):
        core = (EXTENSION / "agent-core.js").read_text(encoding="utf-8")
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        combined = f"{core}\n{background}"
        self.assertIn("SIGNANDSUBMIT", core)
        self.assertIn("ELECTRONIC SIGNATURE", core)
        self.assertIn("CAPTCHA", core)
        self.assertIn("SENSITIVE_ID_PATTERN", background)
        self.assertNotIn("<all_urls>", combined)
        self.assertNotIn("eval(", combined)

    def test_access_token_is_not_forwarded_to_ceac_content_script(self):
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        assignment_block = background.split("async function assignmentForTab", 1)[1]
        assignment_block = assignment_block.split("async function dispatchToCeacTab", 1)[0]
        self.assertNotIn("accessToken", assignment_block)
        self.assertNotIn("taskUrl", assignment_block)

    def test_extension_waits_for_explicit_resume_before_filling(self):
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        bridge = (EXTENSION / "docflow-bridge.js").read_text(encoding="utf-8")
        self.assertIn('armed: false', background)
        self.assertIn('status: "waiting_for_entry"', background)
        self.assertIn('message.type === "docflow.resumeTask"', background)
        self.assertIn('message.type === "DOCFLOW_RESUME_TASK"', bridge)
        self.assertIn("CEAC_START_URL", background)

    def test_docflow_exposes_manual_start_gate_without_offensive_questionnaire_copy(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="codexStartGate"', app)
        self.assertIn("我已进入表格，交给 Computer Use", app)
        self.assertIn("再次复制 Codex 启动指令", app)
        self.assertIn("请在 CEAC 点击 Next", app)
        self.assertIn("系统级可见操作 · 无需 Chrome 扩展", app)
        self.assertIn("Authorization: Bearer <一次性令牌>", app)
        self.assertIn("executor=codex-computer-use", app)
        self.assertNotIn("initializeChromeExtensionBridge", app)
        self.assertNotIn("DOCFLOW_EXTENSION_PING", app)
        self.assertNotIn("顾问将重点复核", app)

    def test_public_intake_keeps_social_records_and_hides_nested_companion_branches(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderedSocialQuestions", app)
        self.assertIn("publicQuestionVisible(parent, questionsById, nextVisited)", app)
        self.assertIn("只填一个完整账号即可", app)
        self.assertIn("没有同行人", app)
        self.assertNotIn(
            'if (questionId === "contact.social_media" && !socialRecords[questionId]) values.records = [];',
            app,
        )

    def test_public_intake_preserves_spaces_while_typing_and_review_fields_are_editable(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("function normalizePublicDs160Value(value, finalize = false)", app)
        self.assertIn("if (!finalize) return raw;", app)
        self.assertIn("publicIntakeState.values.fields[input.dataset.publicField] = finalize", app)
        self.assertIn("capturePublicIntakeValues({ finalize: true })", app)
        self.assertIn("data-system-field-value", app)
        self.assertIn("data-save-system-field", app)
        self.assertIn('reviewReason = "顾问已修改系统识别结果"', app)
        self.assertIn("state.translationService = healthData.translation", app)
        self.assertIn("LibreTranslate 中译英尚未连接", app)

    def test_agent_audits_required_fields_before_next_and_supports_manual_continue(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        core = (EXTENSION / "agent-core.js").read_text(encoding="utf-8")
        ceac_agent = (EXTENSION / "ceac-agent.js").read_text(encoding="utf-8")
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("requiredFieldAudit", core)
        self.assertIn("const field = element.closest(\".field\")", core)
        self.assertIn('code: "required_fields_missing"', ceac_agent)
        self.assertIn('assignment.resumeState !== "manual_continue"', ceac_agent)
        self.assertIn('{ manualContinue: true }', background)
        self.assertIn("chooseMappedCeacTab", background)
        self.assertIn("重新准备 Computer Use 任务", app)
        self.assertIn("browserWorkflowBlockingQuestions", app)
        self.assertIn("browserWorkflowMissingFields", app)
        self.assertIn("BROWSER_WORKFLOW_RUNTIME_ONLY_QUESTION_IDS", app)
        self.assertIn("field.required && !browserWorkflowFieldValueIsUsable", app)
        self.assertIn('"photo.upload_result"', app)
        self.assertIn("!BROWSER_WORKFLOW_RUNTIME_ONLY_QUESTION_IDS.has(question.id)", app)
        self.assertIn("返回客户问题补充", app)
        self.assertIn("renderClientIntakePanel(application, clientIntakePending)", app)
        self.assertIn("资料字段待客户补充", app)
        self.assertNotIn("renderClientIntakePanel(application, customerPending.length)", app)
        self.assertIn("yesNoIntent", core)
        self.assertIn("visibleAssociatedLabel", core)
        self.assertIn("verifyChoiceAfterRefresh", ceac_agent)
        self.assertIn("waitForDomStable", ceac_agent)
        self.assertIn("waitForPageReady", ceac_agent)
        self.assertIn("markNavigationPending", ceac_agent)
        self.assertIn('code: "application_error"', core)
        self.assertIn('code: "session_expired"', core)
        self.assertIn("ceac-postback-monitor.js", background)
        self.assertIn('action.kind === "ensure_repeater"', core)
        self.assertIn("for (let pass = 0; pass < 4", ceac_agent)
        self.assertIn("ensureCeacAgent", background)
        self.assertIn("observeCeacRoute", background)
        self.assertIn("observedRoutes", background)
        self.assertIn('code: "auto_next_disabled"', ceac_agent)
        self.assertEqual(manifest["version"], "0.9.3")

    def test_ceac_agent_uses_conservative_adaptive_pacing(self):
        core = (EXTENSION / "agent-core.js").read_text(encoding="utf-8")
        agent = (EXTENSION / "ceac-agent.js").read_text(encoding="utf-8")
        self.assertIn("const PACING = Object.freeze", agent)
        self.assertIn("normalField: 780", agent)
        self.assertIn("branchMinimumWait: 1900", agent)
        self.assertIn("beforeNext: 1900", agent)
        self.assertIn("PACING.pageTimeout", agent)
        self.assertIn("低速稳定模式", agent)
        self.assertIn("CONTROL_STEP_DELAY = 260", core)
        self.assertNotIn("Math.random", agent)

    def test_ceac_postbacks_are_observed_in_main_world(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        monitor = (EXTENSION / "ceac-postback-monitor.js").read_text(encoding="utf-8")
        monitor_entry = next(
            entry for entry in manifest["content_scripts"]
            if "ceac-postback-monitor.js" in entry.get("js", [])
        )
        self.assertEqual(monitor_entry["world"], "MAIN")
        self.assertEqual(monitor_entry["run_at"], "document_start")
        self.assertIn("PageRequestManager", monitor)
        self.assertIn("add_beginRequest", monitor)
        self.assertIn("add_endRequest", monitor)
        self.assertIn("HTMLFormElement", monitor)
        self.assertIn("data-docflow-ceac-request-pending", monitor)

    def test_page_is_completed_only_after_the_route_changes(self):
        ceac_agent = (EXTENSION / "ceac-agent.js").read_text(encoding="utf-8")
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        advance_block = ceac_agent.split("async function advanceToNext", 1)[1]
        advance_block = advance_block.split("async function executeAssignment", 1)[0]

        self.assertNotIn("pageCompleted: true", advance_block)
        self.assertIn("const previousMappedKey = state.currentRoute?.mappedKey", background)
        self.assertIn("previousMappedKey !== detectedPage.key", background)
        self.assertIn("[previousMappedKey]: true", background)

    def test_date_parts_normalize_numeric_and_chinese_months_to_ceac_abbreviations(self):
        script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("docflow-chrome-extension/agent-core.js", "utf8");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const core = sandbox.DocFlowAgentCore;
console.log(JSON.stringify({
  iso: core.dateParts("1994-3-14"),
  chinese: core.dateParts("1994年3月14日"),
  english: core.dateParts("14 March 1994")
}));
'''
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=True,
            capture_output=True, text=True,
        )
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["iso"]["month"], "MAR")
        self.assertEqual(parsed["chinese"]["month"], "MAR")
        self.assertEqual(parsed["english"]["full"], "14-MAR-1994")

    def test_date_locator_uses_ceac_date_group_label_and_record_occurrence(self):
        script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("docflow-chrome-extension/agent-core.js", "utf8");
let allControls = [];

function dateGroup(label, recordIndex, direction) {
  const heading = { textContent: label, id: `heading-${recordIndex}-${direction}`, previousElementSibling: null };
  const group = {
    textContent: "",
    previousElementSibling: heading,
    controls: [],
    querySelectorAll() { return this.controls; }
  };
  for (const [part, tag] of [["Day", "SELECT"], ["Month", "SELECT"], ["Year", "INPUT"]]) {
    const attributes = {};
    const control = {
      tagName: tag,
      type: tag === "INPUT" ? "text" : "select-one",
      id: `SiteContentPlaceHolder_FormView1_dtlPrevEduc_ctl0${recordIndex}_${direction}${part}`,
      name: "",
      value: "",
      labels: [],
      disabled: false,
      maxLength: part === "Year" ? 4 : 0,
      options: part === "Month" ? [{ textContent: "JAN" }, { textContent: "SEP" }, { textContent: "DEC" }] : [],
      parentElement: group,
      attributes,
      getAttribute(key) { return attributes[key] || null; },
      setAttribute(key, value) { attributes[key] = value; },
      removeAttribute(key) { delete attributes[key]; },
      getBoundingClientRect() { return { width: 100, height: 24 }; },
      closest(selector) {
        if (selector === ".date") return group;
        return null;
      }
    };
    group.controls.push(control);
    allControls.push(control);
  }
  return group;
}

dateGroup("Date of Attendance From", 0, "SchoolFrom");
dateGroup("Date of Attendance To", 0, "SchoolTo");
dateGroup("Date of Attendance From", 1, "SchoolFrom");
dateGroup("Date of Attendance To", 1, "SchoolTo");

const sandbox = {
  getComputedStyle: () => ({ display: "block", visibility: "visible" }),
  document: {
    querySelectorAll(selector) {
      if (selector.startsWith("[data-docflow-page-agent")) {
        return allControls.filter((item) => item.attributes["data-docflow-page-agent"]);
      }
      return allControls;
    }
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const located = sandbox.DocFlowAgentCore.locateControl({
  id: "work.education.1.startDate",
  kind: "date",
  value: "2021.9.10",
  labelTerms: ["Date of Attendance From"],
  controlHints: ["SCHOOLFROM"],
  occurrence: 1
});
const markedIds = allControls
  .filter((item) => item.attributes["data-docflow-page-agent"])
  .map((item) => item.id);
console.log(JSON.stringify({ status: located.status, markedIds }));
'''
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=True,
            capture_output=True, text=True,
        )
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["status"], "found")
        self.assertEqual(len(parsed["markedIds"]), 3)
        self.assertTrue(all("ctl01_SchoolFrom" in item for item in parsed["markedIds"]))

    def test_appointment_agent_is_fill_only_and_stops_at_transaction_pages(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        agent = (EXTENSION / "appointment-agent.js").read_text(encoding="utf-8")
        appointment_script = next(
            item for item in manifest["content_scripts"]
            if "https://www.usvisascheduling.com/*" in item["matches"]
        )
        self.assertIn("appointment-agent.js", appointment_script["js"])
        self.assertIn('workflowType: "appointment"', agent)
        self.assertIn("appointment_hard_stop", agent)
        self.assertIn("APPOINTMENT_URL_PATTERN", background)
        self.assertIn('allowedDomain: "www.usvisascheduling.com"', background)
        self.assertNotIn("findNextButton", agent)
        self.assertNotIn("nextButton.click", agent)
        self.assertNotIn("submitButton.click", agent)
        self.assertNotIn("paymentButton.click", agent)

    def test_failure_details_are_forwarded_without_field_values(self):
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        self.assertIn("failedActionIds: state.failedActionIds || []", background)
        self.assertIn("missingFields: state.missingFields || []", background)
        self.assertIn("allowedActionIds.has(actionId)", background)

    def test_yes_no_intent_uses_radio_value_before_full_control_label(self):
        script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("docflow-chrome-extension/agent-core.js", "utf8");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const fakeRadio = (value, label) => ({
  value,
  id: "radio-id",
  name: "radio-name",
  labels: [{ textContent: label }],
  getAttribute: () => null,
  closest: () => null
});
const core = sandbox.DocFlowAgentCore;
console.log(JSON.stringify({
  yes: core.radioIntent(fakeRadio("Y", "Yes")),
  no: core.radioIntent(fakeRadio("N", "No")),
  wordYes: core.yesNoIntent("yes"),
  wordNo: core.yesNoIntent("no")
}));
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {"yes": "yes", "no": "no", "wordYes": "yes", "wordNo": "no"},
        )

    def test_agent_targets_the_correct_radio_group_and_select_by_control_hint(self):
        script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("docflow-chrome-extension/agent-core.js", "utf8");
let allControls = [];

function field(question) {
  const q = { textContent: question };
  const parent = { querySelector: (selector) => selector === ":scope > .q" ? q : null };
  const answer = { previousElementSibling: q, parentElement: parent };
  return {
    textContent: question,
    controls: [],
    answer,
    querySelectorAll() { return this.controls; }
  };
}

function control({ tag = "INPUT", type = "text", id, name = "", value = "", question, options = [] }) {
  const owner = field(question);
  const attributes = {};
  const element = {
    tagName: tag,
    type,
    id,
    name,
    value,
    options,
    labels: [],
    checked: false,
    disabled: false,
    readOnly: false,
    parentElement: owner,
    getAttribute(key) { return attributes[key] || null; },
    setAttribute(key, next) { attributes[key] = next; },
    removeAttribute(key) { delete attributes[key]; },
    getBoundingClientRect() { return { width: 120, height: 24 }; },
    closest(selector) {
      if (selector === ".field") return owner;
      if (selector === ".a") return owner.answer;
      return null;
    },
    focus() {},
    blur() {},
    dispatchEvent() {},
    click() {
      if (this.type === "radio") {
        allControls.filter((item) => item.type === "radio" && item.name === this.name)
          .forEach((item) => { item.checked = false; });
        this.checked = true;
      }
    }
  };
  owner.controls.push(element);
  return element;
}

const specificYes = control({type:"radio", id:"ctl_rblSpecificTravel_0", name:"specific", value:"Y", question:"Have you made specific travel plans?"});
const specificNo = control({type:"radio", id:"ctl_rblSpecificTravel_1", name:"specific", value:"N", question:"Have you made specific travel plans?"});
const companionYes = control({type:"radio", id:"ctl_rblOtherPersonsTraveling_0", name:"companions", value:"Y", question:"Are there other persons traveling with you?"});
const companionNo = control({type:"radio", id:"ctl_rblOtherPersonsTraveling_1", name:"companions", value:"N", question:"Are there other persons traveling with you?"});
specificYes.labels = [{textContent:"Yes"}]; specificNo.labels = [{textContent:"No"}];
companionYes.labels = [{textContent:"Yes"}]; companionNo.labels = [{textContent:"No"}];

const birthCountry = control({tag:"SELECT", id:"ctl_ddlAPP_POB_CNTRY", value:"", question:"Country/Region of Birth", options:[{value:"",textContent:"- SELECT ONE -"},{value:"CHINA",textContent:"CHINA"}]});
const nationality = control({tag:"SELECT", id:"ctl_ddlAPP_NATL", value:"", question:"Country/Region of Origin (Nationality)", options:[{value:"",textContent:"- SELECT ONE -"},{value:"CHINA",textContent:"CHINA"}]});
allControls = [specificYes, specificNo, companionYes, companionNo, birthCountry, nationality];

const sandbox = {
  getComputedStyle: () => ({display:"block", visibility:"visible"}),
  CSS: {escape: (value) => value},
  Event: class Event { constructor(type) { this.type = type; } },
  document: {
    querySelectorAll(selector) {
      if (selector === "input, select, textarea") return allControls;
      if (selector.startsWith("[data-docflow-page-agent]")) {
        return allControls.filter((item) => item.getAttribute("data-docflow-page-agent"));
      }
      return [];
    },
    querySelector(selector) {
      const match = selector.match(/data-docflow-page-agent="([^"]+)"/);
      return match ? allControls.find((item) => item.getAttribute("data-docflow-page-agent") === match[1]) || null : null;
    }
  }
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
  const core = sandbox.DocFlowAgentCore;
  const radioResult = await core.applyAction({
    id:"companions.has_companions", kind:"yes_no", value:"no",
    labelTerms:["Are there other persons traveling with you"],
    controlHints:["OtherPersonsTraveling"]
  });
  const selectResult = await core.applyAction({
    id:"personal.nationality", kind:"select_text", value:"CHINA",
    labelTerms:["Nationality"], optionTerms:["CHINA"], controlHints:["APP_NATL"]
  });
  console.log(JSON.stringify({
    radioStatus: radioResult.status,
    companionNo: companionNo.checked,
    specificNo: specificNo.checked,
    selectStatus: selectResult.status,
    nationality: nationality.value,
    birthCountry: birthCountry.value
  }));
})().catch((error) => { console.error(error); process.exit(1); });
'''
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), {
            "radioStatus": "filled",
            "companionNo": True,
            "specificNo": False,
            "selectStatus": "filled",
            "nationality": "CHINA",
            "birthCountry": "",
        })


if __name__ == "__main__":
    unittest.main()
