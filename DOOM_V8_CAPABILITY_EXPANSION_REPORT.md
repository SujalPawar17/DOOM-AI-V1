# DOOM V8 Capability Expansion Report

Not V8.11. Planner expansion only. V8.10 guarantees unchanged.

## Verdict

**CAPABILITY EXPANSION READY WITH DOCUMENTED LIMITATIONS**

Five typed capabilities can be planned and executed only through `execute_plan()` → V7 public kernels. Vague language still fails closed. Production ASK does not invent UIA targets or browser sessions.

---

## 1. Exact capabilities added

| Capability | Plan action | Status |
|------------|-------------|--------|
| Computer observe | `computer.OBSERVE` | Supported (needs `computer_session_id`) |
| Computer click | `computer.CLICK` | Supported only with a unique structured UIA target |
| Computer type | `computer.TYPE` | Supported only with a unique structured field target + bounded text |
| Filesystem list | `filesystem.LIST_DIRECTORY` | Supported only with an explicit path |
| Filesystem read | `filesystem.READ_FILE` | Supported only with an explicit path |
| Browser navigate | `browser.NAVIGATE` | Supported only with an explicit `http`/`https` URL |
| Filesystem write/delete | — | Rejected |
| World actions | — | Still unplanned |
| Sequences / JS / shell | — | Not added |

## 2. Exact planner mappings

| User text (examples) | Result |
|----------------------|--------|
| `What is on my screen?` / `observe the screen` | `OBSERVE` if computer session present and observe flag on |
| `Click the OK button.` | `CLICK` only if `planner_context.structured_targets` has exactly one deterministic target named `OK` |
| `click something` / `open notepad` | `PLANNING_UNAVAILABLE` |
| `Type hello into the selected field.` | `TYPE` if exactly one selected/Edit target and payload is clean |
| `List files in "C:\...\safe"` | `LIST_DIRECTORY` |
| `Read this file "C:\...\a.txt"` | `READ_FILE` |
| `list this folder` / `list files in X` | `PLANNING_UNAVAILABLE` (no path) |
| `Write this file ...` / `Delete ...` | `PLANNING_UNAVAILABLE` |
| `Open https://example.com` | `NAVIGATE` |
| `open this website` / `go to Google` | Unplanned / unknown (no invented URL) |
| `file://` `javascript:` `data:` | Unknown / unplanned |
| `create a calendar hold` | `PLANNING_UNAVAILABLE` |

## 3. Exact parameter allowlists

Unchanged V8.2 keys, plus computer `OBSERVE`: `{session_id}`.

- CLICK: `automation_id`, `runtime_id`, `control_type`, `name`, `session_id`, `precondition_observation_hash`
- TYPE: those plus `text`, `sensitive`
- LIST/READ: `path`, `precondition_observation_hash`, `expected_exists`, `expected_type`
- NAVIGATE: `session_id`, `url`, `precondition_observation_hash`

No coordinates, no `command`/`shell`/`eval` keys.

## 4. Exact target-resolution rules

- Planner does **not** call V7.
- `structured_targets` must be a list of dicts with `control_type` and (`automation_id` or `runtime_id`).
- CLICK: extract `{name} button`; require exactly one name match. Labels `something`/`it`/`this`/`that` are rejected.
- TYPE: exactly one `selected=true` target, else exactly one `Edit` control type.
- Paths: quoted path, `C:\...`, or POSIX path with a directory segment. `..` rejected. Bare words are not paths.
- URLs: first `http://` or `https://` with a host. No scheme invention.
- Coordinates `x=`/`y=` → fail closed.

Production `plan_goal(goal)` passes **no** `structured_targets`, so ASK `CLICK`/`TYPE` remain fail-closed until a trusted observation snapshot is supplied by a future non-planner seam.

## 5. Risk mapping

| Action | Risk |
|--------|------|
| OBSERVE, LIST_DIRECTORY, READ_FILE | LOW (new OBSERVE is LOW; CLICK/TYPE not lowered) |
| CLICK, TYPE, NAVIGATE | MEDIUM (existing policy) |
| DELETE_FILE | HIGH (still not planned) |

Strongest-wins. Planner cannot claim below policy.

## 6. Approval behavior

Planner never sets `approved` or `execution_permitted`. MEDIUM+ sets `approval_required`. `execute_plan` still requires matching `authorized_plan_hash`. Identity is not authorization.

## 7. Verification behavior

CLICK/TYPE: `verification_required` + `TARGET_STATE_MATCH`. NAVIGATE: `URL_MATCH`. OBSERVE/LIST/READ: no verification claim.

Adapter SUCCESS + failed/missing verification → `NOT_VERIFIED`, not `SUCCESS`. No screenshot verification.

## 8. Computer-session behavior

Computer/browser/filesystem plans require `GoalSpec.computer_session_id` (from trusted identity context). ASK `session_id` is not copied. Planner does not start V7 sessions. Empty session → `PLANNING_UNAVAILABLE`. Executor still rejects missing plan computer session.

OBSERVE execution: `get_session` then `capture_observation` (V7.1). No screenshots stored by the V8 step.

## 9. Filesystem security

Planner only emits LIST/READ with an extracted path. V7.4 still owns canonicalize, allow/deny roots, symlink, sensitive files, size bounds. No `open()`/`pathlib`/`shutil` in the planner.

## 10. Browser security

Only `http`/`https`. `file`/`javascript`/`data`/`vbscript` rejected. Planner does not open Chromium, Playwright, or Selenium. NAVIGATE uses existing V7.3 `execute_browser_action`. If no in-process browser session exists for that id, V7 fails closed. Planner does not call `open_browser_session`.

## 11. Context behavior

`planner_context` is data-only. `approved`, memory, experience, audit, `task_id`, hashes do not authorize. Only `structured_targets` may supply typed UIA rows. Prompt-injection text is not executed.

## 12. Experience behavior

Unused for policy. No learning, no catalog mutation.

## 13. Identity behavior

Unchanged `ExecutionIdentity`. Not taken from user text, `OWNER_ID`, plan hash, or memory.

## 14. Legacy fallback protection

V8 ON: no CognitiveEngine / ALL_TOOLS / computer_tools / pyautogui path when planning fails or succeeds. Tests patch cognition and assert it is not called.

## 15. Test-hook isolation

Planner and production do not import `use_test_execution_hooks` / `_TEST_HOOKS`. `execute_plan` has no `adapters` argument.

## 16. Cost Guard

HARD $0. No LLM, HTTP planner, or paid API. Deterministic local rules only.

## 17. Voice/STT status

Untouched: `core/listen.py`, `core/cinematic_voice.py`, `core/commands.py`, `core/stt/`. Pre-existing working-tree diffs left as-is. Voice not connected to new capabilities.

## 18. Static security audit

Planner: no subprocess/ctypes/importlib/pyautogui/playwright/selenium/shutil/urllib/`eval`/`exec`/`open`. No `proactive.computer` import from planner or production. V7 remains behind executor defaults.

## 19. Manual validation

Not performed. Tests used spies and did not click real windows, write files, or open external sites.

## 20. Test counts

| Suite | Total | Passed | Failed | Errors |
|-------|------:|-------:|-------:|-------:|
| `test_v8_capability_expansion.py` | 15 | 15 | 0 | 0 |
| V8.1–V8.10 + identity + production + expansion | 261 | 261 | 0 | 0 |
| V7 + Cost Guard + V8 + expansion | 418 | 415 | 0 | 3 |

No **NEW REGRESSION**.

## 21. Known baseline errors

Unchanged FastAPI `Router.__init__() got an unexpected keyword argument 'on_startup'`:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

**KNOWN BASELINE ERROR.**

## 22. New limitations

- Production ASK does not attach live UIA `structured_targets`, so CLICK/TYPE stay unplanned in normal dashboard use.
- OBSERVE captures structured foreground UIA; it does not OCR or interpret pixels.
- Browser NAVIGATE needs an already-open V7 memory-browser session; this milestone does not create one.
- TYPE payload max 256 characters; executable tokens rejected.
- No filesystem write/delete, downloads, JS, world actions, or multi-step agents.

## 23. Remaining security debt

Prior V8.10 items (shared audit ring, etc.) unchanged. Shared ASK unlock. Voice still `IDENTITY_REQUIRED`. FastAPI `on_startup` baseline remains.

## 24. Exact next capability dependency

A **trusted observation snapshot seam** (not planner-invented, not memory text) so CLICK/TYPE can resolve UIA targets in production, plus an explicit V7 browser session bind if navigation should succeed beyond plan generation.

Do not add LLM planning, voice control, or world actions next unless separately specified.

---

Git commit: NOT PERFORMED  
Git push: NOT PERFORMED  
Git tag: NOT PERFORMED  
History rewrite: NOT PERFORMED  
