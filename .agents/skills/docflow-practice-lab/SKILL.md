---
name: docflow-practice-lab
description: Run an explicitly authorized DocFlow OpenCowork task through visible computer control in the local Visa Form Practice Lab. Use only for a supplied open-cowork job ID; validate the manifest, fill fixed demo fields one at a time, confirm visual acknowledgements, and stop before sensitive questions or submission.
---

# DocFlow Practice Lab

Use this workflow only for a DocFlow task whose ID begins with
`open-cowork-`. The task contains fixed, sanitized demo values rather than the
customer's original information.

## Required Workflow

1. Require a task ID matching `open-cowork-[0-9a-f]{24}`.
2. Ask the operator to confirm that visible screen control may begin now.
3. From the selected DocFlow project workspace, inspect the task:

   ```bash
   python3 .Codex/skills/docflow-practice-lab/scripts/job.py inspect --job-id <task-id>
   ```

4. Stop if inspection fails. Do not repair, bypass, or reinterpret a rejected
   task.
5. Use visible computer-use tools to open the exact `targetUrl` returned by
   inspection.
6. Confirm that the page visibly contains `VISA FORM PRACTICE LAB` before
   interacting with it.
7. Fill fields in the returned order. For every field:
   - visually locate the matching label;
   - click its input and type the supplied demo value;
   - verify that the page updates both `LAST FILLED <label>` and
     `FIELDS FILLED n OF m` before continuing.
8. If either acknowledgement is missing or inconsistent, stop and explain
   which field failed verification.
9. Stop when the page reaches `Security and Background`. Leave that section
   and every later action untouched.
10. After success or a controlled stop, redact the one-time task:

    ```bash
    python3 .Codex/skills/docflow-practice-lab/scripts/job.py complete --job-id <task-id>
    ```

## Hard Boundaries

- Never navigate to or operate `ceac.state.gov` or any non-local host.
- Never create accounts, enter credentials, handle CAPTCHA, evade bot
  controls, accept a legal declaration, make a payment, or submit a form.
- Never infer or select answers about refusal, overstay, criminal history,
  immigration history, health, security, or other sensitive background items.
- Never replace visible computer use with DOM injection, hidden JavaScript, or
  background form manipulation.
- Never use values other than those returned by the validated task inspector.
- Keep the operator able to observe and interrupt every action.
