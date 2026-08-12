(function initializeDocFlowAgentCore(global) {
  "use strict";

  const CORE_VERSION = "0.9.3";
  const CONTROL_STEP_DELAY = 260;
  if (global.DocFlowAgentCore?.version === CORE_VERSION) return;
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
    return termScore(controlIdentity(element), (action.controlHints || []).map(normalize));
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
    const text = normalize(option?.textContent);
    return !value || /^(SELECT|SELECT ONE|PLEASE SELECT|--)/.test(text);
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
            marker: mark(element, action.id, part)
          });
        }
      }
      if (!output.length && grouped.length === 1) {
        output.push({
          part: "full",
          tag: grouped[0].tagName.toLowerCase(),
          marker: mark(grouped[0], action.id, "full")
        });
      }
      return output.length
        ? { status: "found", role: "date", controls: output }
        : { status: "not_found" };
    }

    if (action.kind === "duration") {
      const amount = grouped.find((element) => element.tagName === "INPUT"
        && element.type !== "hidden");
      const unit = grouped.find((element) => element.tagName === "SELECT");
      const output = [];
      if (amount) output.push({
        part: "amount", tag: "input", marker: mark(amount, action.id, "amount")
      });
      if (unit) output.push({
        part: "unit", tag: "select", marker: mark(unit, action.id, "unit")
      });
      return output.length
        ? { status: "found", role: "duration", controls: output }
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

  function setText(element, value) {
    element.focus();
    element.value = String(value);
    commitInput(element);
    return normalize(element.value) === normalize(value);
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
      return element && setText(element, action.value)
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
      element.click();
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
        changed = setText(element, part) || changed;
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
          : setText(element, value);
        changed = ok || changed;
        appliedControls += 1;
      }
      return changed
        ? { status: "filled", changed: true }
        : { status: "verification_failed", changed: false };
    }
    if (located.role === "duration") {
      const duration = action.duration || {};
      let changed = false;
      let appliedControls = 0;
      for (const control of located.controls || []) {
        const element = marked(control.marker);
        if (!element) continue;
        if (appliedControls) await wait(CONTROL_STEP_DELAY);
        const ok = control.part === "amount"
          ? setText(element, duration.amount)
          : selectByText(element, duration.unit);
        changed = ok || changed;
        appliedControls += 1;
      }
      return changed
        ? { status: "filled", changed: true }
        : { status: "verification_failed", changed: false };
    }
    return { status: "not_found", changed: false };
  }

  function isActionSet(action) {
    const located = locateControl(action);
    return located.status === "found" && Boolean(located.alreadySet);
  }

  function pageSafetyState() {
    const url = String(global.location.href || "");
    const title = normalize(document.title);
    const body = normalize(document.body ? document.body.innerText.slice(0, 40000) : "");
    if (global.location.hostname !== "ceac.state.gov") {
      return { safe: false, reason: "当前页面不是 CEAC。", code: "wrong_domain" };
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
      /NODE=SECURITY/i,
      /SECURITY AND BACKGROUND/i,
      /SIGNANDSUBMIT|SIGN AND SUBMIT/i,
      /ELECTRONIC SIGNATURE/i,
      /FINAL SUBMISSION|SUBMIT APPLICATION/i,
      /PAYMENT/i
    ];
    if (hardStop.some((pattern) => pattern.test(routeText))) {
      return {
        safe: false,
        reason: "已到敏感背景、声明、付款或最终提交边界，必须由顾问处理。",
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

  function ceacRequestPending() {
    return document.documentElement?.getAttribute(
      "data-docflow-ceac-request-pending"
    ) === "true";
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
    applyAction,
    isActionSet,
    pageSafetyState,
    findNextButton,
    requiredFieldAudit,
    visibleValidationErrors,
    wait,
    waitForDomStable,
    waitForPageReady,
    ceacRequestPending,
    navigationGuardActive,
    markNavigationPending,
    dateParts,
    dateGroupLabel
  });
})(globalThis);
