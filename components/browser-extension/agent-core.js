(function initializeDocFlowAgentCore(global) {
  "use strict";

  const CORE_VERSION = "1.0.19";
  const CONTROL_STEP_DELAY = 90;
  if (global.DocFlowAgentCore?.version === CORE_VERSION) return;
  global.document?.documentElement?.setAttribute("data-docflow-core-version", CORE_VERSION);
  const MARKER_ATTRIBUTE = "data-docflow-page-agent";

  function normalize(value) {
    return String(value || "")
      .replace(/[\u00a0\s]+/g, " ")
      .trim()
      .toUpperCase();
  }

  function searchable(value) {
    return normalize(value)
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .trim();
  }

  function isVisible(element) {
    if (!element || element.disabled || element.type === "hidden") return false;
    const style = global.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && rect.width > 0 && rect.height > 0;
  }

  function dateGroupLabel(element) {
    const dateGroup = element?.closest?.(".date");
    if (!dateGroup) return "";
    const values = [];
    let sibling = dateGroup.previousElementSibling;
    for (let depth = 0; sibling && depth < 6; depth += 1) {
      values.push(sibling.textContent || "", sibling.id || "");
      const combined = normalize(values.join(" "));
      if (/DATE OF|DATE |DTE|FROM|TO|BIRTH|ISSUED|EXPIR/.test(combined)) break;
      sibling = sibling.previousElementSibling;
    }
    return normalize(values.join(" "));
  }

  function directLabel(element) {
    const values = [
      element.getAttribute("aria-label"),
      element.getAttribute("title"),
      element.getAttribute("placeholder"),
      element.id,
      element.name,
      dateGroupLabel(element)
    ];
    if (element.labels) {
      for (const label of element.labels) values.push(label.textContent);
    }
    const parentLabel = element.closest("label");
    if (parentLabel) values.push(parentLabel.textContent);
    return normalize(values.filter(Boolean).join(" "));
  }

  function groupFor(element) {
    const dateGroup = element.closest(".date");
    if (dateGroup) return dateGroup;
    const field = element.closest(".field");
    if (field) return field;
    const row = element.closest("tr");
    if (row) return row;
    let node = element.parentElement;
    for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
      const text = normalize(node.textContent);
      if (text.length >= 8 && text.length <= 1200) return node;
    }
    return element.parentElement || element;
  }

  function contextFor(element) {
    return normalize([
      directLabel(element),
      groupFor(element).textContent || "",
      element.id || "",
      element.name || ""
    ].join(" "));
  }

  function termScore(text, wanted) {
    const haystack = searchable(text);
    return wanted.reduce((score, term) => {
      const needle = searchable(term);
      return score + (needle && haystack.includes(needle) ? 1 : 0);
    }, 0);
  }

  function yesNoIntent(value) {
    const token = searchable(value);
    if (["YES", "Y", "TRUE", "1", "是"].includes(token)) return "yes";
    if (["NO", "N", "FALSE", "0", "2", "否"].includes(token)) return "no";
    return "";
  }

  function radioIntent(element) {
    const valueIntent = yesNoIntent(element.value);
    if (valueIntent) return valueIntent;
    const labelText = element.labels
      ? Array.from(element.labels).map((label) => label.textContent).join(" ")
      : "";
    return yesNoIntent(labelText);
  }

  function clearMarkers() {
    document.querySelectorAll(`[${MARKER_ATTRIBUTE}]`).forEach((element) => {
      element.removeAttribute(MARKER_ATTRIBUTE);
    });
  }

  function mark(element, actionId, role) {
    const token = `${actionId}-${role}-${Math.random().toString(36).slice(2, 9)}`;
    element.setAttribute(MARKER_ATTRIBUTE, token);
    return token;
  }

  function controls() {
    return Array.from(document.querySelectorAll("input, select, textarea"))
      .filter(isVisible);
  }

  function clickableControls() {
    return Array.from(document.querySelectorAll(
      "a, button, input[type='button'], input[type='submit'], input[type='image']"
    )).filter(isVisible);
  }

  const FRIENDLY_CONTROL_LABELS = [
    [/TRAVEL_LOS/i, "Intended Length of Stay in U.S."],
    [/PREV_US_TRAVEL_IND/i, "是否曾去过美国"],
    [/PREV_VISA_IND/i, "是否曾获得美国签证"],
    [/PREV_VISA_REFUSED_IND/i, "是否曾被拒签、拒绝入境或撤回入境申请"],
    [/IV_PETITION_IND/i, "是否有人为申请人提交过移民申请"]
  ];

  function readableText(value) {
    return String(value || "")
      .replace(/[\u00a0\s]+/g, " ")
      .replace(/^\*+|\*+$/g, "")
      .trim();
  }

  function controlLabel(element) {
    const identity = `${element.id || ""} ${element.name || ""}`;
    const friendly = FRIENDLY_CONTROL_LABELS.find(([pattern]) => pattern.test(identity));
    if (friendly) return friendly[1];

    const answerContainer = element.closest(".a");
    const questionText = readableText(answerContainer?.previousElementSibling?.textContent);
    if (questionText) return questionText;

    const labels = element.labels ? Array.from(element.labels) : [];
    const direct = readableText(labels.map((label) => label.textContent).join(" "));
    if (direct && !/^(YES|NO)$/i.test(direct)) return direct;

    const fieldText = readableText(groupFor(element).textContent)
      .replace(/\s+YES\s+NO(?:\s+|$)/i, " ")
      .trim();
    if (fieldText && !/^(YES|NO)$/i.test(fieldText)) return fieldText.slice(0, 180);

    return readableText(element.getAttribute("aria-label") || element.title
      || element.id || element.name || "未命名必填项");
  }

  function controlIdentity(element) {
    return normalize(`${element.id || ""} ${element.name || ""}`);
  }

  function questionContextFor(element) {
    const values = [controlIdentity(element), directLabel(element), controlLabel(element)];
    const answerContainer = element.closest(".a");
    if (answerContainer) {
      values.push(answerContainer.previousElementSibling?.textContent || "");
      values.push(answerContainer.parentElement?.querySelector(":scope > .q")?.textContent || "");
    }
    const field = element.closest(".field");
    if (field) values.push(field.textContent || "");
    return normalize(values.join(" "));
  }

  function controlHintScore(element, action) {
    const stableHints = {
      "passport.issuingAuthority": "ddlPPT_ISSUED_CNTRY",
      "passport.issueCity": "tbxPPT_ISSUED_IN_CITY",
      "passport.issueRegion": "tbxPPT_ISSUED_IN_STATE",
      "passport.issueCountry": "ddlPPT_ISSUED_IN_CNTRY"
    };
    const hints = [...(action.controlHints || []), stableHints[action.id]].filter(Boolean);
    return termScore(controlIdentity(element), hints.map(normalize));
  }

  function chooseCandidate(candidates, occurrence = 0) {
    if (!candidates.length) return null;
    const ranked = candidates
      .map((candidate, index) => ({ ...candidate, index }))
      .sort((left, right) => right.score - left.score || left.index - right.index);
    const bestScore = ranked[0].score;
    const closeMatches = ranked.filter((candidate) => candidate.score >= bestScore - 2);
    return closeMatches[Number(occurrence) || 0] || null;
  }

  function isWorkflowControl(element) {
    const identity = `${element.id || ""} ${element.name || ""}`;
    if (!/SiteContentPlaceHolder/i.test(identity)) return false;
    if (/UpdateButton|ddlLanguage/i.test(identity)) return false;
    if (["hidden", "submit", "button", "reset", "image", "file"].includes(element.type)) {
      return false;
    }
    return !element.readOnly;
  }

  function isBlankSelect(element) {
    if (element.selectedIndex < 0) return true;
    const option = element.options[element.selectedIndex];
    const value = readableText(element.value);
    const text = searchable(option?.textContent);
    return !value || !text || /^(SELECT|SELECT ONE|PLEASE SELECT)\b/.test(text);
  }

  function fieldHasCheckedNotApplicable(element) {
    const field = groupFor(element);
    return Array.from(field.querySelectorAll("input[type='checkbox']:checked"))
      .some((checkbox) => /DOES NOT APPLY|DO NOT KNOW|NOT APPLICABLE|N\/A/.test(
        contextFor(checkbox)
      ));
  }

  function requiredFieldAudit() {
    const available = controls().filter(isWorkflowControl);
    const missing = [];
    const seen = new Set();
    const addMissing = (key, element, kind) => {
      if (seen.has(key)) return;
      seen.add(key);
      missing.push({ key, kind, label: controlLabel(element) });
    };

    const radioGroups = new Map();
    for (const radio of available.filter((element) => element.type === "radio")) {
      const key = radio.name || radio.id.replace(/_\d+$/, "");
      if (!radioGroups.has(key)) radioGroups.set(key, []);
      radioGroups.get(key).push(radio);
    }
    for (const [key, radios] of radioGroups.entries()) {
      if (!radios.some((radio) => radio.checked)) {
        addMissing(`radio:${key}`, radios[0], "choice");
      }
    }

    for (const element of available) {
      if (["radio", "checkbox"].includes(element.type)) continue;
      if (fieldHasCheckedNotApplicable(element)) continue;
      const identity = `${element.id || ""} ${element.name || ""}`;
      if (/ADDR.*(?:LINE|LN)_?2|STREET.*(?:LINE|LN)_?2/i.test(identity)) continue;
      if (/(?:ADDR|ADDRESS)_?2(?:\b|_)/i.test(identity)) continue;
      if (/OPTIONAL|IF KNOWN/.test(questionContextFor(element))) continue;
      if (element.tagName === "SELECT" && isBlankSelect(element)) {
        addMissing(`select:${element.id || element.name}`, element, "select");
      } else if ((element.tagName === "TEXTAREA"
        || ["", "text", "tel", "email", "number", "date"].includes(element.type))
        && !readableText(element.value)) {
        addMissing(`text:${element.id || element.name}`, element, "text");
      }
    }

    return { complete: missing.length === 0, missing };
  }

  function findAnchor(action, candidates) {
    const terms = (action.labelTerms || []).map(normalize).filter(Boolean);
    const matches = [];
    const seenGroups = new Set();
    for (const element of candidates) {
      if (["date", "duration", "text_segments"].includes(action.kind)) {
        const group = groupFor(element);
        if (seenGroups.has(group)) continue;
        seenGroups.add(group);
      }
      const direct = directLabel(element);
      const context = questionContextFor(element);
      const directMatches = termScore(direct, terms);
      const contextMatches = termScore(context, terms);
      const labelMatches = termScore(controlLabel(element), terms);
      const hintMatches = controlHintScore(element, action);
      if (!directMatches && !contextMatches && !labelMatches && !hintMatches) continue;
      const exactBonus = terms.some((term) => direct === term) ? 25 : 0;
      const score = hintMatches * 80 + labelMatches * 20
        + directMatches * 10 + contextMatches * 2 + exactBonus;
      matches.push({ element, score });
    }
    return chooseCandidate(matches, action.occurrence);
  }

  function locateControl(action) {
    clearMarkers();
    const available = controls();
    const terms = (action.labelTerms || []).map(normalize).filter(Boolean);
    const optionTerms = (action.optionTerms || []).map(normalize).filter(Boolean);
    const optionAlternatives = (action.optionAlternatives || []).map(normalize).filter(Boolean);

    if (action.kind === "ensure_repeater") {
      const expectedCount = Math.max(1, Number(action.expectedCount || action.value || 1));
      const recordTerms = (action.recordLabelTerms || action.labelTerms || [])
        .map(normalize).filter((term) => term && !term.includes("ADD ANOTHER"));
      const existing = controls().filter((element) => {
        const direct = directLabel(element);
        const labelMatch = termScore(direct, recordTerms)
          || termScore(controlLabel(element), recordTerms);
        const hintMatch = controlHintScore(element, action);
        // When a record label is known, count only that record's primary
        // control. Broad section hints such as "EDUCATION" otherwise count
        // every field in one institution as a separate institution.
        const recordMatch = recordTerms.length ? labelMatch : hintMatch;
        return Boolean(recordMatch) && !/ADD|REMOVE/.test(controlIdentity(element));
      });
      if (existing.length >= expectedCount) {
        return { status: "found", role: "repeater", alreadySet: true };
      }
      const matches = [];
      const clickables = clickableControls();
      const addControlCount = clickables.filter((item) => (
        /ADD ANOTHER/.test(normalize(item.textContent || item.value || ""))
      )).length;
      for (const element of clickables) {
        const direct = normalize([
          element.textContent, element.value, element.getAttribute("aria-label"),
          element.title, element.id, element.name
        ].filter(Boolean).join(" "));
        if (!/ADD ANOTHER|ADD (?:A |AN )?(?:EMPLOYER|INSTITUTION|RELATIVE|LANGUAGE|COUNTRY|ORGANIZATION)/.test(direct)) {
          continue;
        }
        let ancestor = element.parentElement;
        const contextParts = [direct];
        for (let depth = 0; ancestor && depth < 6; depth += 1, ancestor = ancestor.parentElement) {
          contextParts.push(ancestor.textContent || "");
        }
        const context = normalize(contextParts.join(" "));
        const sectionScore = termScore(context, terms);
        const hintScore = termScore(controlIdentity(element), (action.controlHints || []).map(normalize));
        if (!sectionScore && !hintScore && addControlCount > 1) continue;
        matches.push({ element, score: hintScore * 100 + sectionScore * 12 + 5 });
      }
      const best = chooseCandidate(matches);
      if (!best) return { status: "not_found" };
      return {
        status: "found",
        role: "repeater",
        marker: mark(best.element, action.id, "repeater"),
        alreadySet: false
      };
    }

    if (action.kind === "select_text") {
      const matches = [];
      for (const element of available.filter((item) => item.tagName === "SELECT")) {
        for (const option of Array.from(element.options || [])) {
          const optionText = normalize(`${option.textContent || ""} ${option.value || ""}`);
          const matched = termScore(optionText, optionTerms);
          const alternativeMatched = optionAlternatives.some((term) => (
            searchable(optionText).includes(searchable(term))
          ));
          if ((!matched || matched < optionTerms.length) && !alternativeMatched) continue;
          const labelScore = termScore(controlLabel(element), terms) * 8
            + termScore(questionContextFor(element), terms);
          const hintScore = controlHintScore(element, action);
          if (!labelScore && !hintScore) continue;
          const score = hintScore * 100 + matched * 20 + (alternativeMatched ? 16 : 0) + labelScore
            + (optionText === normalize(action.value) ? 8 : 0);
          matches.push({ element, option, score });
        }
      }
      const best = chooseCandidate(matches, action.occurrence);
      if (!best) return { status: "not_found" };
      return {
        status: "found",
        role: "select",
        marker: mark(best.element, action.id, "select"),
        optionValue: best.option.value,
        alreadySet: String(best.element.value) === String(best.option.value)
      };
    }

    if (action.kind === "yes_no") {
      const desired = yesNoIntent(action.value);
      if (!desired) return { status: "invalid_value" };
      const groups = new Map();
      for (const element of available.filter((item) => item.type === "radio")) {
        const key = element.name || element.id.replace(/_\d+$/, "");
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(element);
      }
      const matches = [];
      for (const radios of groups.values()) {
        const context = normalize(radios.map(questionContextFor).join(" "));
        const questionScore = termScore(context, terms);
        const hintScore = Math.max(...radios.map((radio) => controlHintScore(radio, action)));
        if (!questionScore && !hintScore) continue;
        const element = radios.find((radio) => radioIntent(radio) === desired);
        if (!element) continue;
        const score = hintScore * 100 + questionScore * 12
          + termScore(controlLabel(element), terms) * 8;
        matches.push({ element, score });
      }
      const best = chooseCandidate(matches, action.occurrence);
      if (!best) return { status: "not_found" };
      return {
        status: "found",
        role: "radio",
        marker: mark(best.element, action.id, "radio"),
        alreadySet: Boolean(best.element.checked)
      };
    }

    if (action.kind === "does_not_apply") {
      const checkboxTerms = (action.checkboxTerms || ["DOES NOT APPLY", "DO NOT KNOW"])
        .map(normalize);
      const matches = [];
      for (const element of available.filter((item) => item.type === "checkbox")) {
        const context = questionContextFor(element);
        const questionScore = termScore(context, terms);
        const checkboxScore = termScore(context, checkboxTerms);
        const hintScore = controlHintScore(element, action);
        if ((!questionScore && !hintScore) || !checkboxScore) continue;
        const score = hintScore * 100 + questionScore * 10 + checkboxScore * 6;
        matches.push({ element, score });
      }
      const best = chooseCandidate(matches, action.occurrence);
      if (!best) return { status: "not_found" };
      return {
        status: "found",
        role: "checkbox",
        marker: mark(best.element, action.id, "checkbox"),
        alreadySet: Boolean(best.element.checked)
      };
    }

    const candidateControls = available.filter((element) => {
      if (["text", "text_segments"].includes(action.kind)) {
        return element.tagName === "TEXTAREA"
          || (element.tagName === "INPUT"
            && ["", "text", "tel", "email", "number"].includes(element.type));
      }
      return element.tagName === "INPUT" || element.tagName === "SELECT";
    });
    const anchor = findAnchor(action, candidateControls);
    if (!anchor) return { status: "not_found" };

    if (action.kind === "text") {
      return {
        status: "found",
        role: "text",
        marker: mark(anchor.element, action.id, "text"),
        alreadySet: normalize(anchor.element.value) === normalize(action.value)
      };
    }

    const group = groupFor(anchor.element);
    const grouped = Array.from(group.querySelectorAll("input, select, textarea"))
      .filter(isVisible);

    if (action.kind === "text_segments") {
      const inputs = grouped.filter((element) => element.tagName === "INPUT"
        && ["", "text", "tel", "number"].includes(element.type));
      return inputs.length ? {
        status: "found",
        role: "text_segments",
        controls: inputs.map((element, index) => ({
          index,
          marker: mark(element, action.id, `segment-${index}`),
          value: element.value
        }))
      } : { status: "not_found" };
    }

    if (action.kind === "date") {
      const output = [];
      for (const element of grouped) {
        // CEAC places image help buttons ending in "DTEMonth" before
        // the real date selectors. They are not editable date components.
        if (element.tagName !== "SELECT"
          && !(element.tagName === "INPUT"
            && ["", "text", "tel", "number", "date"].includes(element.type))) continue;
        const identity = normalize(`${element.id || ""} ${element.name || ""} ${directLabel(element)}`);
        let part = "";
        if (identity.includes("MONTH")) part = "month";
        else if (identity.includes("YEAR") || String(element.maxLength) === "4") part = "year";
        else if (identity.includes("DAY") || String(element.maxLength) === "2") part = "day";
        else if (element.tagName === "SELECT") {
          const options = normalize(Array.from(element.options || [])
            .map((item) => item.textContent).join(" "));
          if (options.includes("JAN") && options.includes("DEC")) part = "month";
          else if (options.includes("31")) part = "day";
          else if (/\b20\d{2}\b/.test(options)) part = "year";
        }
        if (part && !output.some((item) => item.part === part)) {
          output.push({
            part,
            tag: element.tagName.toLowerCase(),
            marker: mark(element, action.id, part),
            element
          });
        }
      }
      if (!output.length && grouped.length === 1) {
        output.push({
          part: "full",
          tag: grouped[0].tagName.toLowerCase(),
          marker: mark(grouped[0], action.id, "full"),
          element: grouped[0]
        });
      }
      return output.length
        ? {
          status: "found",
          role: "date",
          controls: output,
          alreadySet: dateControlsMatch(output, action.value)
        }
        : { status: "not_found" };
    }

    if (action.kind === "duration") {
      const amount = grouped.find((element) => element.tagName === "INPUT"
        && element.type !== "hidden");
      const unit = grouped.find((element) => element.tagName === "SELECT");
      const output = [];
      if (amount) output.push({
        part: "amount", tag: "input", marker: mark(amount, action.id, "amount"), element: amount
      });
      if (unit) output.push({
        part: "unit", tag: "select", marker: mark(unit, action.id, "unit"), element: unit
      });
      return output.length
        ? {
          status: "found",
          role: "duration",
          controls: output,
          alreadySet: durationControlsMatch(output, action.duration || {})
        }
        : { status: "not_found" };
    }
    return { status: "not_found" };
  }

  function marked(marker) {
    return document.querySelector(`[${MARKER_ATTRIBUTE}="${CSS.escape(marker)}"]`);
  }

  function commitInput(element) {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.blur();
  }

  function setNativeInputValue(element, value) {
    const prototype = element instanceof global.HTMLTextAreaElement
      ? global.HTMLTextAreaElement?.prototype
      : global.HTMLInputElement?.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype || {}, "value")?.set;
    if (setter) setter.call(element, value);
    else element.value = value;
  }

  function typingDelay(character, index) {
    const base = 24 + Math.floor(Math.random() * 22);
    if (/\s|[,./-]/.test(character)) return base + 18;
    if (index > 0 && index % 14 === 0) return base + 30;
    return base;
  }

  async function typeVisibleText(element, value, { blur = true } = {}) {
    if (!element || element.disabled || element.readOnly) return false;
    const target = String(value ?? "");
    element.focus();

    if (String(element.value || "")) {
      element.dispatchEvent(new InputEvent("beforeinput", {
        bubbles: true, inputType: "deleteContentBackward", data: null
      }));
      setNativeInputValue(element, "");
      element.dispatchEvent(new InputEvent("input", {
        bubbles: true, inputType: "deleteContentBackward", data: null
      }));
      await wait(90);
    }

    let current = "";
    for (let index = 0; index < target.length; index += 1) {
      const character = target[index];
      const keyCode = character.length ? character.charCodeAt(0) : 0;
      element.dispatchEvent(new KeyboardEvent("keydown", {
        key: character, code: "", keyCode, charCode: 0, bubbles: true
      }));
      element.dispatchEvent(new InputEvent("beforeinput", {
        bubbles: true, inputType: "insertText", data: character
      }));
      current += character;
      setNativeInputValue(element, current);
      element.dispatchEvent(new InputEvent("input", {
        bubbles: true, inputType: "insertText", data: character
      }));
      element.dispatchEvent(new KeyboardEvent("keyup", {
        key: character, code: "", keyCode, charCode: 0, bubbles: true
      }));
      await wait(typingDelay(character, index));
    }

    element.dispatchEvent(new Event("change", { bubbles: true }));
    if (blur) element.blur();
    return normalize(element.value) === normalize(target);
  }

  function visibleAssociatedLabel(element) {
    const labels = element.labels ? Array.from(element.labels) : [];
    return labels.find(isVisible) || null;
  }

  function setChoice(element) {
    if (element.checked) return true;
    element.focus();
    const nativeClick = global.HTMLInputElement?.prototype?.click;
    if (nativeClick) nativeClick.call(element);
    else element.click();
    if (!element.checked) {
      const label = visibleAssociatedLabel(element);
      if (label) label.click();
    }
    if (!element.checked) {
      const setter = Object.getOwnPropertyDescriptor(
        global.HTMLInputElement?.prototype || {}, "checked"
      )?.set;
      if (setter) setter.call(element, true);
      else element.checked = true;
      commitInput(element);
    }
    return Boolean(element.checked);
  }

  async function setText(element, value) {
    return typeVisibleText(element, value, { blur: true });
  }

  // The server execution layer commits composite duration controls without
  // blurring between the amount and unit. CEAC can clear the paired value if
  // either half loses focus before the second half is committed.
  async function setTextWithoutBlur(element, value) {
    return typeVisibleText(element, value, { blur: false });
  }

  function setSelect(element, optionValue) {
    const option = Array.from(element.options || [])
      .find((item) => String(item.value) === String(optionValue));
    if (!option) return false;
    const setter = Object.getOwnPropertyDescriptor(
      global.HTMLSelectElement?.prototype || {}, "value"
    )?.set;
    if (setter) setter.call(element, option.value);
    else element.value = option.value;
    element.focus();
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.blur();
    return String(element.value) === String(option.value);
  }

  function setSelectWithoutBlur(element, optionValue) {
    const option = Array.from(element.options || [])
      .find((item) => String(item.value) === String(optionValue));
    if (!option || option.disabled) return false;
    element.value = String(option.value);
    option.selected = true;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return String(element.value) === String(option.value);
  }

  function durationControlsMatch(controls, duration) {
    const amountControl = (controls || []).find(
      (item) => item.part === "amount"
    );
    const unitControl = (controls || []).find(
      (item) => item.part === "unit"
    );
    const amountElement = amountControl
      ? amountControl.element || marked(amountControl.marker)
      : null;
    const unitElement = unitControl
      ? unitControl.element || marked(unitControl.marker)
      : null;
    if (!amountElement || !unitElement) return false;
    const selectedUnit = searchable(
      unitElement.options?.[unitElement.selectedIndex]?.textContent
        || unitElement.value
    );
    return normalize(amountElement.value) === normalize(duration.amount)
      && selectedUnit.includes(searchable(duration.unit));
  }

  function durationMatches(action) {
    const located = locateControl(action);
    return located.status === "found"
      && durationControlsMatch(located.controls, action.duration || {});
  }

  function dateParts(value) {
    const months = [
      "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
    ];
    const source = String(value || "").trim().toUpperCase();
    let year = "";
    let month = "";
    let day = "";
    let match = /^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?$/.exec(source);
    if (match) {
      [, year, month, day] = match;
      month = months[Number(month) - 1];
    } else {
      match = /^(\d{1,2})[-/ ]([A-Z]{3,9}|\d{1,2})[-/ ](\d{4})$/.exec(source);
      if (!match) return null;
      day = match[1];
      year = match[3];
      const monthAliases = {
        JANUARY: "JAN", FEBRUARY: "FEB", MARCH: "MAR", APRIL: "APR",
        JUNE: "JUN", JULY: "JUL", AUGUST: "AUG", SEPTEMBER: "SEP",
        OCTOBER: "OCT", NOVEMBER: "NOV", DECEMBER: "DEC"
      };
      month = /^\d+$/.test(match[2])
        ? months[Number(match[2]) - 1]
        : (monthAliases[match[2]] || match[2].slice(0, 3));
    }
    if (!month || !months.includes(month) || Number(day) < 1 || Number(day) > 31) return null;
    return {
      year,
      month,
      day: String(Number(day)),
      full: `${Number(day).toString().padStart(2, "0")}-${month}-${year}`
    };
  }

  function dateControlsMatch(controls, value) {
    const expected = dateParts(value);
    if (!expected) return false;
    const parts = new Map();
    for (const control of controls || []) {
      const element = control.element || marked(control.marker);
      if (element) parts.set(control.part, element);
    }
    if (parts.has("full")) {
      const actual = dateParts(parts.get("full").value);
      return Boolean(actual && actual.full === expected.full);
    }
    if (!["day", "month", "year"].every((part) => parts.has(part))) return false;
    const day = String(Number(parts.get("day").value || 0));
    const monthElement = parts.get("month");
    const month = normalize(
      monthElement.options?.[monthElement.selectedIndex]?.textContent
        || monthElement.value
    );
    const year = String(parts.get("year").value || "").trim();
    return day === expected.day
      && month.includes(expected.month)
      && year === expected.year;
  }

  function selectByText(element, value) {
    const wanted = normalize(value);
    const option = Array.from(element.options || []).find((item) => {
      const text = normalize(`${item.textContent || ""} ${item.value || ""}`);
      return text === wanted || text.includes(wanted);
    });
    return option ? setSelect(element, option.value) : false;
  }

  async function applyAction(action) {
    const located = locateControl(action);
    if (located.status !== "found") return { status: "not_found", changed: false };
    if (located.alreadySet) return { status: "already_set", changed: false };

    if (located.role === "text") {
      const element = marked(located.marker);
      return element && await setText(element, action.value)
        ? { status: "filled", changed: true }
        : { status: "verification_failed", changed: false };
    }
    if (located.role === "radio" || located.role === "checkbox") {
      const element = marked(located.marker);
      if (!element) return { status: "not_found", changed: false };
      return setChoice(element)
        ? { status: "filled", changed: true }
        : { status: "verification_failed", changed: false };
    }
    if (located.role === "select") {
      const element = marked(located.marker);
      return element && setSelect(element, located.optionValue)
        ? { status: "filled", changed: true }
        : { status: "verification_failed", changed: false };
    }
    if (located.role === "repeater") {
      if (located.alreadySet) return { status: "already_set", changed: false };
      const element = marked(located.marker);
      if (!element) return { status: "not_found", changed: false };
      element.focus();
      // ASP.NET Add Another links use javascript:__doPostBack. Trigger their
      // native click in MAIN, just like Next, so the page-owned handler runs.
      if (!requestNativeNext(element)) {
        return { status: "verification_failed", changed: false };
      }
      return { status: "filled", changed: true };
    }
    if (located.role === "text_segments") {
      const compact = String(action.value || "").replace(/[^A-Za-z0-9]/g, "");
      let cursor = 0;
      let changed = false;
      let appliedControls = 0;
      for (const control of located.controls || []) {
        const element = marked(control.marker);
        if (!element) continue;
        if (appliedControls) await wait(CONTROL_STEP_DELAY);
        const remaining = compact.length - cursor;
        const expectedLength = Number(element.maxLength) > 0
          ? Math.min(Number(element.maxLength), remaining)
          : remaining;
        const part = compact.slice(cursor, cursor + expectedLength);
        cursor += expectedLength;
        changed = (await setText(element, part)) || changed;
        appliedControls += 1;
      }
      return changed && cursor >= compact.length
        ? { status: "filled", changed: true }
        : { status: "verification_failed", changed };
    }
    if (located.role === "date") {
      const values = dateParts(action.value);
      if (!values) return { status: "invalid_value", changed: false };
      let changed = false;
      let appliedControls = 0;
      for (const control of located.controls || []) {
        const element = marked(control.marker);
        const value = values[control.part];
        if (!element || !value) continue;
        if (appliedControls) await wait(CONTROL_STEP_DELAY);
        const ok = element.tagName === "SELECT"
          ? selectByText(element, value)
          : await setText(element, value);
        changed = ok || changed;
        appliedControls += 1;
      }
      if (!dateControlsMatch(located.controls, action.value)) {
        return { status: "verification_failed", changed };
      }
      return changed
        ? { status: "filled", changed: true }
        : { status: "already_set", changed: false };
    }
    if (located.role === "duration") {
      const duration = action.duration || {};
      const amountControl = (located.controls || []).find(
        (item) => item.part === "amount"
      );
      const unitControl = (located.controls || []).find(
        (item) => item.part === "unit"
      );
      const amountElement = amountControl ? marked(amountControl.marker) : null;
      const unitElement = unitControl ? marked(unitControl.marker) : null;
      if (!amountElement || !unitElement) {
        return { status: "not_found", changed: false };
      }

      // Keep the same composite sequence as the server adapter: amount first,
      // unit second, no blur between them, then verify the pair together.
      if (!await setTextWithoutBlur(amountElement, duration.amount)) {
        return { status: "verification_failed", changed: false };
      }
      const selectedUnit = searchable(
        unitElement.options?.[unitElement.selectedIndex]?.textContent
          || unitElement.value
      );
      const wantedUnit = searchable(duration.unit);
      if (!selectedUnit.includes(wantedUnit)) {
        const option = Array.from(unitElement.options || []).find((item) => {
          const text = searchable(`${item.textContent || ""} ${item.value || ""}`);
          return text === wantedUnit || text.includes(wantedUnit);
        });
        if (!option || !setSelectWithoutBlur(unitElement, option.value)) {
          return { status: "verification_failed", changed: true };
        }
      }

      if (!durationMatches(action)) {
        return { status: "verification_failed", changed: true };
      }

      // Re-check after any CEAC postback has settled. If CEAC rebuilt the
      // amount input, commit only that half again without a blur and verify.
      const settled = await waitForPageReady({
        timeout: 15000,
        minimumWait: 500,
        quietWindow: 500
      });
      if (!settled.ready) return { status: "verification_failed", changed: true };
      if (!durationMatches(action)) {
        const refreshed = locateControl(action);
        const refreshedAmount = (refreshed.controls || []).find(
          (item) => item.part === "amount"
        );
        const refreshedElement = refreshedAmount ? marked(refreshedAmount.marker) : null;
        if (!refreshedElement
          || !await setTextWithoutBlur(refreshedElement, duration.amount)
          || !durationMatches(action)) {
          return { status: "verification_failed", changed: true };
        }
      }
      return { status: "filled", changed: true };
    }
    return { status: "not_found", changed: false };
  }

  function isActionSet(action) {
    const located = locateControl(action);
    if (located.status !== "found") return false;
    if (located.role === "date") {
      return dateControlsMatch(located.controls, action.value);
    }
    if (located.role === "duration") {
      return durationControlsMatch(located.controls, action.duration || {});
    }
    return Boolean(located.alreadySet);
  }

  function pageSafetyState() {
    const url = String(global.location.href || "");
    const title = normalize(document.title);
    const body = normalize(document.body ? document.body.innerText.slice(0, 40000) : "");
    if (global.location.hostname !== "ceac.state.gov") {
      return { safe: false, reason: "当前页面不是 CEAC。", code: "wrong_domain" };
    }
    if (/ATTENTION REQUIRED|SORRY, YOU HAVE BEEN BLOCKED|WHY HAVE I BEEN BLOCKED|CLOUDFLARE RAY ID/.test(
      `${title} ${body}`
    )) {
      return {
        safe: false,
        reason: "CEAC 的安全服务已临时拦截当前请求。自动填写已立即停止；请勿连续刷新，稍后恢复申请后再继续。",
        code: "security_block"
      };
    }
    if (/APPLICATION ERROR/.test(`${title} ${body}`)) {
      return {
        safe: false,
        reason: "CEAC 返回了应用错误，自动填写已暂停。请使用 Application ID 恢复申请。",
        code: "application_error"
      };
    }
    if (/SESSION (?:HAS )?(?:TIMED OUT|EXPIRED)/.test(body)) {
      return {
        safe: false,
        reason: "CEAC 会话已过期，自动填写已暂停。请使用 Application ID 恢复申请。",
        code: "session_expired"
      };
    }
    if (document.querySelector("iframe[src*='recaptcha'], .g-recaptcha, input[name*='captcha' i]")
      || /\bCAPTCHA\b|ENTER THE CODE SHOWN/i.test(body)) {
      return { safe: false, reason: "检测到验证码，需要人工完成。", code: "captcha" };
    }
    const routeText = `${url} ${title}`;
    const hardStop = [
      /SIGNANDSUBMIT|SIGN AND SUBMIT/i,
      /ELECTRONIC SIGNATURE/i,
      /FINAL SUBMISSION|SUBMIT APPLICATION/i,
      /PAYMENT/i
    ];
    if (hardStop.some((pattern) => pattern.test(routeText))) {
      return {
        safe: false,
        reason: "已到声明、付款或最终提交边界，需要人工处理。",
        code: "hard_stop"
      };
    }
    return { safe: true, reason: "", code: "safe" };
  }

  function findNextButton() {
    const candidates = Array.from(document.querySelectorAll(
      "button, input[type='submit'], input[type='button'], a"
    )).filter(isVisible);
    let best = null;
    for (const element of candidates) {
      const text = normalize([
        element.textContent,
        element.value,
        element.title,
        element.getAttribute("aria-label"),
        element.id,
        element.name
      ].filter(Boolean).join(" "));
      if (!/(^|\s)NEXT(?::|\s|$)/.test(text) && !/UPDATEBUTTON3/.test(text)) continue;
      if (/SAVE ONLY|SAVE APPLICATION/.test(text)) continue;
      const score = text.startsWith("NEXT") ? 20 : 10;
      if (!best || score > best.score) best = { element, score };
    }
    return best ? best.element : null;
  }

  function requestNativeNext(element) {
    const root = document.documentElement;
    if (!root || !element?.id) return false;
    const token = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    root.removeAttribute("data-docflow-ceac-next-ack");
    root.setAttribute(
      "data-docflow-ceac-next-request",
      `${token}|${String(element.id).slice(0, 220)}`
    );
    document.dispatchEvent(new Event("docflow-ceac-native-next", { bubbles: false }));
    const acknowledged = root.getAttribute("data-docflow-ceac-next-ack") === token;
    root.removeAttribute("data-docflow-ceac-next-request");
    return acknowledged;
  }

  function visibleValidationErrors() {
    const candidates = Array.from(document.querySelectorAll(
      "[role='alert'], .error, .errors, .validation-summary-errors, [class*='error' i]"
    )).filter(isVisible);
    return candidates
      .map((item) => String(item.textContent || "").replace(/\s+/g, " ").trim())
      .filter((text) => text.length >= 4)
      .slice(0, 5);
  }

  function wait(milliseconds) {
    return new Promise((resolve) => global.setTimeout(resolve, milliseconds));
  }

  function ensureVisualMonitor() {
    if (!document.body) return null;
    let host = document.querySelector("#docflow-agent-visual-monitor");
    if (host) return host;
    host = document.createElement("div");
    host.id = "docflow-agent-visual-monitor";
    host.innerHTML = `
      <style>
        #docflow-agent-visual-monitor{position:fixed;inset:0;z-index:2147483647;pointer-events:none;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
        #docflow-agent-visible-cursor{position:fixed;left:24px;top:24px;width:19px;height:25px;z-index:2147483646;pointer-events:none;transition:left .34s ease,top .34s ease,transform 105ms ease;filter:drop-shadow(0 1px 2px rgba(0,0,0,.75));will-change:left,top,transform}
        #docflow-agent-visible-cursor::before{content:"";position:absolute;inset:0;background:#ff3b30;clip-path:polygon(0 0,0 88%,24% 66%,39% 100%,54% 92%,39% 60%,73% 58%)}
        #docflow-agent-visible-cursor[data-state="observing"]::before,#docflow-agent-visible-cursor[data-state="thinking"]::before,#docflow-agent-visible-cursor[data-state="navigating"]::before{animation:docflow-agent-cursor-breathe 1.4s ease-in-out infinite}
        #docflow-agent-visual-status{position:fixed;left:12px;top:12px;z-index:2147483647;min-width:150px;max-width:min(360px,calc(100vw - 36px));box-sizing:border-box;padding:10px 13px;border:1px solid rgba(255,255,255,.2);border-radius:13px;color:#fff;background:rgba(16,18,17,.94);box-shadow:0 8px 26px rgba(0,0,0,.28);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
        #docflow-agent-visual-status::before{content:"";display:inline-block;width:8px;height:8px;margin-right:7px;border-radius:50%;background:#34c759;box-shadow:0 0 0 4px rgba(52,199,89,.16)}
        #docflow-agent-visual-status[data-state="observing"]::before,#docflow-agent-visual-status[data-state="thinking"]::before,#docflow-agent-visual-status[data-state="navigating"]::before{background:#64d2ff;box-shadow:0 0 0 4px rgba(100,210,255,.16);animation:docflow-agent-pulse 1.1s infinite}
        #docflow-agent-visual-status[data-state="paused"]::before{background:#ff9f0a;box-shadow:0 0 0 4px rgba(255,159,10,.18)}
        #docflow-agent-visual-status[data-state="blocked"]::before,#docflow-agent-visual-status[data-state="error"]::before{background:#ff453a;box-shadow:0 0 0 4px rgba(255,69,58,.18)}
        #docflow-agent-visual-status [data-docflow-status-label]{font-size:13px;font-weight:700}
        #docflow-agent-visual-status [data-docflow-status-detail]{display:block;margin-top:5px;color:rgba(255,255,255,.74);font-size:11px;line-height:1.35}
        .docflow-agent-focus{outline:3px solid rgba(255,59,48,.9)!important;outline-offset:3px!important;box-shadow:0 0 0 7px rgba(255,59,48,.16)!important}
        @keyframes docflow-agent-pulse{50%{opacity:.38}}
        @keyframes docflow-agent-cursor-breathe{50%{filter:brightness(1.35);opacity:.72}}
      </style>
      <div id="docflow-agent-visible-cursor" data-state="observing" aria-hidden="true"></div>
      <section id="docflow-agent-visual-status" data-state="observing" role="status" aria-live="polite">
        <span data-docflow-status-label>Gemini · 读取页面</span>
        <span data-docflow-status-detail>正在读取当前页面</span>
      </section>`;
    document.body.appendChild(host);
    return host;
  }

  function visualLog(label, status) {
    const host = ensureVisualMonitor();
    const badge = host?.querySelector("#docflow-agent-visual-status");
    const cursor = host?.querySelector("#docflow-agent-visible-cursor");
    if (!badge) return;
    const safeLabel = String(label || "当前页面").replace(/[<>]/g, "").slice(0, 90);
    const safeStatus = String(status || "处理中").replace(/[<>]/g, "").slice(0, 32);
    const state = /失败|错误|未找到/.test(safeStatus) ? "blocked"
      : /进入下一页/.test(safeStatus) ? "navigating"
        : /准备|读取/.test(safeStatus) ? "observing" : "working";
    badge.dataset.state = state;
    if (cursor) cursor.dataset.state = state;
    const title = badge.querySelector("[data-docflow-status-label]");
    const detail = badge.querySelector("[data-docflow-status-detail]");
    if (title) title.textContent = state === "navigating" ? "Gemini · 正在进入下一页" : state === "blocked" ? "Gemini · 已暂停，需要处理" : "Gemini · 正在填写";
    if (detail) detail.textContent = `${safeLabel} · ${safeStatus}`;
  }

  function primaryElementForLocated(located) {
    if (!located || located.status !== "found") return null;
    const marker = located.marker || located.controls?.[0]?.marker;
    return marker ? marked(marker) : null;
  }

  function writeBindingAudit(action, located, resultStatus = "located") {
    const element = primaryElementForLocated(located);
    const payload = {
      at: Date.now(),
      actionId: String(action?.id || "").slice(0, 120),
      kind: String(action?.kind || "").slice(0, 40),
      controlId: String(element?.id || element?.name || "").slice(0, 180),
      expectedLength: String(action?.value ?? "").length,
      actualLength: element && "value" in element ? String(element.value || "").length : null,
      alreadySet: Boolean(located?.alreadySet),
      locatedStatus: String(located?.status || ""),
      resultStatus: String(resultStatus || "")
    };
    document.documentElement?.setAttribute(
      "data-docflow-binding-audit",
      JSON.stringify(payload)
    );
    const root = document.documentElement;
    if (root) {
      let bindings = {};
      try { bindings = JSON.parse(root.getAttribute("data-docflow-page-bindings") || "{}"); }
      catch (_error) { /* A previous document's audit is optional. */ }
      bindings[payload.actionId] = payload;
      root.setAttribute("data-docflow-page-bindings", JSON.stringify(bindings));
    }
  }

  async function previewAction(action) {
    const located = locateControl(action);
    writeBindingAudit(action, located);
    const element = primaryElementForLocated(located);
    visualLog(action?.label, located.status === "found" ? "准备操作" : "未找到控件");
    if (!element) return located;
    // The cursor is feedback, not a readiness check. Scroll synchronously
    // instead of charging every field for the smooth-scroll animation.
    element.scrollIntoView({ behavior: "instant", block: "center", inline: "nearest" });
    await wait(80);
    const rect = element.getBoundingClientRect();
    const cursor = ensureVisualMonitor()?.querySelector("#docflow-agent-visible-cursor");
    if (cursor) {
      cursor.style.left = `${Math.max(12, Math.min(global.innerWidth - 12, rect.left + rect.width / 2))}px`;
      cursor.style.top = `${Math.max(12, Math.min(global.innerHeight - 12, rect.top + rect.height / 2))}px`;
      cursor.style.transform = "scale(.78)";
      global.setTimeout(() => { cursor.style.transform = "scale(1)"; }, 180);
    }
    element.classList.add("docflow-agent-focus");
    global.setTimeout(() => element.classList.remove("docflow-agent-focus"), 1500);
    return located;
  }

  function visualActionResult(action, result) {
    const labels = {
      filled: "已填写",
      already_set: "已核对",
      not_found: "未找到",
      verification_failed: "校验失败",
      invalid_value: "数据无效",
      error: "执行错误"
    };
    const located = locateControl(action);
    writeBindingAudit(action, located, result?.status || "unknown");
    visualLog(action?.label, labels[result?.status] || String(result?.status || "已处理"));
  }

  function ceacRequestPending() {
    const root = document.documentElement;
    if (root?.getAttribute("data-docflow-ceac-request-pending") !== "true") {
      return false;
    }
    const since = Number(root.getAttribute("data-docflow-ceac-request-since") || 0);
    if (document.readyState === "complete" && since && Date.now() - since > 6000) {
      root.removeAttribute("data-docflow-ceac-request-pending");
      root.removeAttribute("data-docflow-ceac-request-since");
      root.removeAttribute("data-docflow-ceac-request-reason");
      return false;
    }
    return true;
  }

  function pageFingerprint() {
    const viewState = document.querySelector("input[name='__VIEWSTATE']")?.value || "";
    const eventValidation = document.querySelector("input[name='__EVENTVALIDATION']")?.value || "";
    const workflowControls = document.querySelectorAll(
      "input, select, textarea, button, input[type='submit']"
    ).length;
    return [
      global.location.href,
      document.readyState,
      viewState.length,
      viewState.slice(-32),
      eventValidation.length,
      eventValidation.slice(-24),
      workflowControls
    ].join("|");
  }

  async function waitForPageReady(options = {}) {
    const timeout = Math.max(1000, Number(options.timeout || 25000));
    const minimumWait = Math.max(0, Number(options.minimumWait || 900));
    const quietWindow = Math.max(200, Number(options.quietWindow || 650));
    const startedAt = Date.now();
    let lastFingerprint = pageFingerprint();
    let lastChangedAt = startedAt;

    while (Date.now() - startedAt < timeout) {
      const safety = pageSafetyState();
      if (!safety.safe) {
        return {
          ready: false,
          code: safety.code,
          reason: safety.reason,
          pending: ceacRequestPending()
        };
      }

      const fingerprint = pageFingerprint();
      if (fingerprint !== lastFingerprint) {
        lastFingerprint = fingerprint;
        lastChangedAt = Date.now();
      }
      const elapsed = Date.now() - startedAt;
      const quietFor = Date.now() - lastChangedAt;
      if (!ceacRequestPending()
        && document.readyState === "complete"
        && elapsed >= minimumWait
        && quietFor >= quietWindow) {
        await wait(120);
        if (!ceacRequestPending() && pageFingerprint() === lastFingerprint) {
          return { ready: true, code: "ready", reason: "", pending: false };
        }
      }
      await wait(100);
    }

    return {
      ready: false,
      code: "page_busy_timeout",
      reason: ceacRequestPending()
        ? "CEAC 仍在处理上一项选择，自动填写已暂停，避免重复提交。"
        : "CEAC 页面在等待时间内没有稳定下来，自动填写已暂停。",
      pending: ceacRequestPending()
    };
  }

  function navigationGuardActive(maxAge = 20000) {
    const root = document.documentElement;
    if (!root || root.getAttribute("data-docflow-navigation-pending") !== "true") {
      return false;
    }
    const since = Number(root.getAttribute("data-docflow-navigation-since") || 0);
    if (since && Date.now() - since > maxAge) {
      root.removeAttribute("data-docflow-navigation-pending");
      root.removeAttribute("data-docflow-navigation-since");
      return false;
    }
    return true;
  }

  function markNavigationPending() {
    const root = document.documentElement;
    if (!root) return;
    root.setAttribute("data-docflow-navigation-pending", "true");
    root.setAttribute("data-docflow-navigation-since", String(Date.now()));
  }

  function clearNavigationPending() {
    const root = document.documentElement;
    if (!root) return;
    root.removeAttribute("data-docflow-navigation-pending");
    root.removeAttribute("data-docflow-navigation-since");
  }

  async function waitForDomStable(timeout = 3000, quietWindow = 260) {
    if (!document.body || typeof MutationObserver === "undefined") {
      await wait(Math.min(timeout, quietWindow));
      return;
    }
    await new Promise((resolve) => {
      let quietTimer;
      const finish = () => {
        global.clearTimeout(quietTimer);
        global.clearTimeout(timeoutTimer);
        observer.disconnect();
        resolve();
      };
      const schedule = () => {
        global.clearTimeout(quietTimer);
        quietTimer = global.setTimeout(finish, quietWindow);
      };
      const observer = new MutationObserver(schedule);
      const timeoutTimer = global.setTimeout(finish, timeout);
      observer.observe(document.body, { childList: true, subtree: true, attributes: true });
      schedule();
    });
  }

  global.DocFlowAgentCore = Object.freeze({
    version: CORE_VERSION,
    normalize,
    yesNoIntent,
    radioIntent,
    controlLabel,
    questionContextFor,
    controlHintScore,
    isVisible,
    locateControl,
    previewAction,
    visualActionResult,
    visualLog,
    applyAction,
    isActionSet,
    pageSafetyState,
    findNextButton,
    requestNativeNext,
    requiredFieldAudit,
    visibleValidationErrors,
    wait,
    waitForDomStable,
    waitForPageReady,
    ceacRequestPending,
    navigationGuardActive,
    markNavigationPending,
    clearNavigationPending,
    dateParts,
    dateControlsMatch,
    durationControlsMatch,
    dateGroupLabel
  });
})(globalThis);
