---
name: docflow-ds160
description: Use when the user pastes a short-lived DocFlow task prepared by the local website and asks Codex to fill the corresponding visible CEAC or U.S. visa scheduling page with Computer Use. No Chrome extension, DOM injection, Playwright, Selenium, or RPA is used.
---

# DocFlow Computer Use Handoff

Use this skill only after the user explicitly prepares a task in DocFlow, enters the intended official form in Chrome, and pastes the generated local task instruction. The user remains the operator and reviewer. Never infer legal answers or submit an application.

## Required inputs

The instruction must contain both:

- a task URL on `http://127.0.0.1:<port>/api/codex-agent/jobs/<job-id>`
- a short-lived one-time access token

Reject any task URL whose scheme is not `http`, host is not exactly `127.0.0.1`, or job id is not `codex-agent-` followed by 24 lowercase hexadecimal characters. Never send the token anywhere except that exact task URL and the returned localhost `statusUrl`. Do not repeat the token or customer values in chat.

## Read and validate the task

Fetch the task from the local URL using `Authorization: Bearer <token>`. Keep the payload in memory. Do not write it to a new file, terminal log, or chat message.

Validate before operating Chrome:

- `executor` is `codex-computer-use`
- `workflowType` is `ds160` or `appointment`
- `targetUrl` is HTTPS and its host matches `safety.allowedDomain`
- `safety.visibleInteractionOnly` is true
- `safety.browserExtension` and `safety.domInjection` are `never`
- every action comes from the returned `pages[].actions` plan
- `interactionPolicy.maxActionsBeforeReinspect` is 1

Stop without modifying Chrome if validation fails.

## Computer Use boundary

1. Use the `computer-use:computer-use` capability and its persistent `node_repl` plus `@oai/sky` wrapper. Do not use the Chrome control plugin.
2. Do not use a browser extension, JavaScript injection, Playwright, Selenium, Browser Use, or third-party RPA.
3. Operate only the user's visible Google Chrome window and the matching official tab. Do not inspect unrelated tabs, cookies, browser storage, passwords, or extensions.
4. If the page requests CAPTCHA, login recovery, credentials, security verification, or a one-time code, stop and ask the user to complete it.
5. Before transmitting task data, ensure the user's pasted instruction explicitly authorizes the listed data categories and destination. Otherwise ask for confirmation at action time.

## Deliberate execution

Post `{"state":"running","completedFields":0}` to `statusUrl` before the first write.

Use one visible action per inspection cycle:

1. Read fresh Chrome accessibility state.
2. Match the current page to one task page using visible heading, route metadata, and planned labels. Never guess a page solely from a URL fragment.
3. Locate a visible, enabled control using the action label, `labelTerms`, nearby question text, and record occurrence.
4. Perform exactly one action with Computer Use.
5. Respect `interactionPolicy.betweenActionsMs`, choosing the pause from the configured interval according to how quickly the visible page settles. This is a stability delay, not an anti-detection technique.
6. Read fresh accessibility state again and verify the visible value.
7. If the action is Yes/No, a dropdown, Does Not Apply, Do Not Know, or has `causesRefresh`, wait at least `afterDynamicSelectionMs`, reacquire the entire visible form, and include any newly revealed controls in the next cycle.
8. Retry a failed action no more than `maxRetriesPerAction`; then stop and report the field rather than looping.

Action rules:

- `text`: enter the supplied value exactly; do not invent or translate a different value.
- `select_text`: select one unambiguous visible option matching `optionTerms`.
- `yes_no`: click Yes or No belonging to the exact visible question.
- `date`: use the supplied date parts; CEAC month selectors use three-letter English abbreviations.
- `duration`: fill the amount and select its unit, then verify both.
- repeaters: add only the records explicitly present in the plan and reacquire state after each added row.

Do not overwrite a conflicting non-empty value unless the user confirms the change.

## Navigation and stopping

For `ds160`, click Next only when all of these are true:

- task `autoNext` is true
- the current page is non-sensitive and mapped in the task
- every planned visible action verifies
- no visible required-field or validation error remains
- the page has been inspected again after the final action

After navigation, wait at least `afterNavigationMs`, reacquire Chrome state, and map the new page from scratch. Never reuse stale element indexes.

For `appointment`, never click Save, Continue, Next, payment controls, appointment slots, or final confirmation controls.

Always stop before CAPTCHA, credentials, refusal/overstay/criminal/security judgments, legal declarations, electronic signatures, payment, appointment confirmation, or final submission. Post `blocked` with sanitized missing labels when human input is required. Post `review_required` when all permitted actions are verified. Post `failed` only for a technical failure.

Do not echo passport numbers, dates of birth, addresses, tokens, or other customer values in the final response. Report only page names, completed counts, and labels that need human attention.
