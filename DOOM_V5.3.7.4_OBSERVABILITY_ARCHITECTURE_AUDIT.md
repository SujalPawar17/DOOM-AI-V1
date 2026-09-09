# DOOM V5.3.7.4 — Operational Observability & Telemetry
# Architecture Audit (Phase 1 — Read-Only)

**Audit date:** 2026-09-09  
**Mode:** inspect / propose only  
**Production code modified:** no  
**Commit / tag / push:** none  
**V5.3.7.4 implementation:** **NOT PRESENT**

---

## 1. Executive Summary

DOOM already has **fragments** of operational observability: in-memory `CognitiveTelemetry`, stdout `[PERF]` lines, PostgreSQL `command_logs` / `system_telemetry` / `task_checkpoints`, WebSocket cognitive events, a V4.2 `CorrelationContext`, provider circuit-breaker state, and per-call embedding latency. These pieces are **not a system**.

There is **no canonical telemetry event bus**, **no metrics registry**, **no end-to-end correlation on the production request path**, **no bounded retention**, and **no consistent privacy boundary**. The most serious issues are **content-bearing logs and broadcasts** (full user prompts on stdout and WebSocket; full command/response text in `command_logs`) and **unbounded persistence**. Telemetry failure is *mostly* isolated (`try/except` around DB and WS), but stdout `print` of exceptions and Gemini putting the API key in the request URL remain risk surfaces.

**Recommendation:** Implement V5.3.7.4 as a **fail-open, redacting, correlated operational event + metrics layer** that *observes* the released provider router and existing cognitive/task/memory engines. Do **not** treat V4.2 correlation or `command_logs` as that layer without redesign.

**DOOM advantage:** Ordinary chatbot logging dumps prompts. DOOM should answer *why a stage was slow, which provider failed, why fallback ran, which step verified* — structured metadata, **never** raw chain-of-thought or private memory bodies.

---

## 2. Exact Git Baseline

| Check | Result |
|--------|--------|
| Branch | `DOOM-V5.2` |
| HEAD | `086582edf93a405862f84f275a4d6512a35a756c` `DOOM Provider Routing Correction` |
| Tag on HEAD | `v5.3.7.3-provider-routing` |
| Governance tag | `v5.3.7.3` → `73be7fffeef7baab32e8f938badfcbd8c69fd5fa` (unchanged) |
| Expected baseline | **MATCH** (`086582e` / `v5.3.7.3-provider-routing`) |
| `observability/` on disk | **Absent** (`Test-Path observability` → False) |
| `observability/` in HEAD | **Absent** |
| `test_v5374_*` | **Absent** |
| `postgres_db.py` v5374 tables | **Absent** (no `telemetry_events` / `audit_events`) |
| Working tree | **Not clean** — untracked local markdown audits/reports only (including prior provider-routing docs). **Not modified, not cleaned.** |
| Tracked product dirty | No `M` product files at audit time |

No reset, checkout, or delete was performed.

**V5.3.7.4 implementation already exists?** **No** (not in HEAD, not in the working tree as source).

---

## 3. Existing Observability Inventory

| Component | Current mechanism | Location | Data captured | Persistence | Retention | Correlation | Security/privacy | Production path? | Duplicate? | Missing? | Risk |
|-----------|-------------------|----------|---------------|-------------|-----------|-------------|------------------|------------------|------------|----------|------|
| A Request | `print` full prompt; `log_command` | `orchestrator.py`, `postgres_db.py` | prompt, spoken response, tools, total ms | PG `command_logs` + stdout | **None** | **No request id** | **Stores full user text** | Yes | Yes vs WS | Structured request event | **HIGH** privacy + unbounded |
| B Cognitive | `CognitiveTelemetry` + WS `cognitive_event` | `core/cognition/schemas.py`, `engine.py` | stage ms, cycles, replan_count, models/tools lists; broadcasts include **full request** | In-memory; WS ephemeral | N/A | Not attached | WS `COGNITION_STARTED` sends `request=` | Yes | vs `[PERF]` | Persist + IDs | **HIGH** WS leak |
| C Task | `TaskEngine` checkpoint | `task_engine.py`, `task_checkpoints` | goal, status, steps, artifacts, tool_results, models_used, retry_counts, termination | PG + JSON files | **None** | `task_id` only | May include goal/tool output | Yes | vs command_logs | Link to doom_request_id | MEDIUM content |
| D Step | step status on task + `CorrelationContext.new_step` | `task_engine`, `correlation.py` | step id/status | Checkpoint | None | Contextvar **rarely set** | — | Partial | — | Wire context at request start | MEDIUM |
| E Tool | prints in bridge; CanonicalToolResult | `cognition/bridge.py` | success, stdout/stderr, artifacts | In observations + checkpoint | None | `tool_execution_id` unused in logs | stdout may be large | Yes | — | Per-tool latency event | MEDIUM |
| F Provider | stdout failover; circuit breaker in-memory | `model_router.py`, `circuit_breaker.py` | name, exception str, OPEN/CLOSED | Memory + stdout | CB reset on process restart | **No** | Exception text may include URLs | Yes | vs IDE/dashboard status polls | Structured provider_call events | MEDIUM |
| G Model | `LLMResponse.usage` optional | providers | usage dict if API returns it | **Dropped** (not aggregated) | — | No | — | Yes | — | Tokens/latency per call | LOW |
| H Memory | retrieval latency on `MemoryContext`; lifecycle events | `retrieval.py`, `lifecycle.py` | latency, mode, counts; lifecycle has correlation_id **for memory events** | `memory_lifecycle_events` | None observed | Memory-local `correlation_id` ≠ request id | Summaries intended safe; broadcasts `query[:60]` | Yes | Two correlation namespaces | Unified ID; no content in telemetry | MEDIUM |
| I Vector | embed `latency_ms`; sync `_record_failure` | `fastembed_provider.py`, `sync_engine.py` | embed latency; sync errors | Mostly in-memory / outbox | Outbox own policy | No request id | Hashes not bodies | Yes | — | Vector search ms on telemetry bus | LOW |
| J Verify | `verification_ms` on cognitive telemetry | `bridge.py` | duration | In-memory | — | No | — | Yes | — | Pass/fail class on events | LOW |
| K Retry/fallback | prints + CB | router, bridge retry policy | consecutive failures, cooldown | Memory | Process | No | — | Yes | — | Explainable fallback chain | MEDIUM |
| L Error | unstructured `print` / exception str | many | message | stdout | — | No | May echo secrets in AWS/API errors | Yes | Many formats | Typed operational errors | **HIGH** if headers/URLs logged |
| M WebSocket | HUD + cognitive + task payloads | `dashboard/server.py` | mixed schemas | None | Session | No request id | **Full request on cognition start** | Yes | Independent reconstruct | Canonical events | **HIGH** |
| N Dashboard | live stats, `command_logs` UI, provider matrix | `dashboard/server.py` | CPU/RAM; chat logs; provider flags | PG + live | Unbounded logs | — | Activity is full commands | Yes | Polls `is_available` separately | Subscribe to bus | MEDIUM |
| O IDE | per-request `latency_ms` | `ide/server.py` | latency, model | Response JSON | — | No | — | Yes | Separate from dashboard | Same bus | LOW |
| P Security | approval tokens in task engine | `task_engine.py` | operation tokens | Task state | — | Partial | Tokens in memory | Yes | — | Security events without token values | MEDIUM |
| Q Performance | `[PERF]` stdout; CognitiveTelemetry | orchestrator | stage breakdown | stdout | — | No | Prompt already printed | Yes | Duplicate of telemetry object | Histograms | LOW overhead, LOW queryability |

`system_telemetry` is **host CPU/RAM/disk**, not request telemetry.

---

## 4. End-to-End Trace Analysis

**Can one user request be traced end-to-end today?** **No.**

Path: User → `DOOMCore.process_request` → `CognitiveEngine.process` → stages → `CognitiveBridge` → TaskEngine / tools / `model_router.generate` → verifier → TTS → `log_command` → optional WS.

Gaps:

- Orchestrator never calls `set_current_correlation`.
- Bridge **reads** `get_current_correlation()` (auto-creates a context if empty) — IDs can **leak across requests** on a reused thread/event loop.
- `[PERF]` and `command_logs` have **no id**.
- Router failover prints have **no id**.
- Dashboard `/api/agent/chat` uses the router but does not attach correlation.
- Cognitive WS events do not include `doom_request_id`.

---

## 5. Correlation ID Analysis

| ID | Exists? | Used in production path? | Notes |
|----|---------|--------------------------|--------|
| `doom_request_id` | Yes (`CorrelationContext`) | **Weak** — not set at request entry | Safe internal; do not show to user as a secret |
| `task_id` | Yes (`TaskEngine`) | Yes for tasks | User-visible in HUD; safe |
| `cognitive_cycle_id` | Generated on `new_cycle` | Rarely applied | Missing on engine.process |
| `step_id` | Task steps + context.new_step | Task yes; context unused | |
| `operation_id` | Context only | Unused | |
| `tool_execution_id` | Context factory | Unused in logs | |
| `execution_id` | No first-class | — | Propose alias of tool_execution or request |
| `provider_call_id` | **Missing** | — | Needed |
| `memory_operation_id` | Memory `source_event_id` / lifecycle | Memory subsystem only | Different namespace |
| `verification_id` | **Missing** | — | |
| `websocket/session` | `connected_clients` list | No session id | |
| `trace_id` | **Missing** | — | Can equal doom_request_id |

**Exposed to user:** `task_id` (appropriate). Full prompts (inappropriate).  
**Internal:** correlation ids should stay operational metadata.

---

## 6. Latency Analysis

| Question | Answerable now? | How |
|----------|-----------------|-----|
| Entire request | Partial | Orchestrator total ms (stdout + `command_logs`) |
| Cognitive processing | Yes in-memory | `total_cognitive_ms` (also printed) |
| Planning | Yes in-memory | `planning_ms` |
| Each tool | Partial | `execution_ms` **aggregated**, not per tool |
| Memory retrieval | Yes in-memory | `memory_retrieval_ms` / `MemoryContext.retrieval_latency_ms` |
| Embedding | Per call in `EmbeddingResult` | **Not rolled into request telemetry** |
| Vector search | Partial | Inside retrieval timer, not split |
| Database access | **No** per-query | |
| Provider inference | **No** | Router has no timer |
| Verification | Yes in-memory | `verification_ms` |
| Retries / fallback | **No** durations | Only print + CB counts |
| Bottleneck | Partial | `[PERF]` line if operator watches stdout |

Orchestrator docstring claims “Latency Profiling (planning, routing, llm, tool, …)” for the **legacy** path; the **production** path is cognition + one PERF line.

---

## 7. Provider Observability Analysis

Released router **can** know provider name, capabilities map, cost tier, enabled, `is_available()`, circuit state, and exception class (`ProviderRateLimitError`, timeout, etc.).

**Not reported as telemetry:** per-call latency, token usage aggregation, whether empty-response failover occurred, ordered fallback chain, “final provider vs first attempted.”

Stdout: `[MODEL ROUTER] Provider 'x' failed...` — unstructured.

**Must not expose:** API keys, `Authorization`, `.env`. Current generate paths do not `print` keys. **Gemini** builds URL with `?key=` — if `requests` debug or exception includes URL, that is a **CRITICAL** leak class (not observed as a dedicated log line; still a design hazard).

**Do not change routing.** Observability should wrap `model_router.generate` / record after return.

Bedrock: `_enabled = False`, skipped before health/generate. Telemetry must **not** health-probe Bedrock to “complete” a dashboard.

NIM: `cost_tier = FREE_TIER` (hosted free-tier class). **Do not** claim permanently/unconditionally free. Inference timeout is a **runtime** fact, not “NIM down.”

**Direct observability integration defect (do not fix here):** `/api/dev/generate_types` still constructs `GroqProvider()` — that path **cannot** emit canonical router telemetry. Chat path is canonical. Document only.

---

## 8. Cognitive / Task Observability Analysis

**Exists:** intent, decision type, plan step_count (WS), termination_reason, final_response_status, tool names, CognitiveTelemetry stage clocks, task checkpoint status/retries.

**Missing as queryable ops data:** provider chosen and why (capability + cost), verification verdict enum, replan *reason* (prints exist), partial-success flag on a bus.

**CoT:** `reasoning_summary` is stored on `CognitiveState` and broadcast as `REASONING_COMPLETE` `summary=`. That is **not** raw hidden CoT storage in a telemetry table today, but **WS already ships a reasoning summary**. V5.3.7.4 must treat CoT/reasoning prose as **PRIVATE_METADATA / FORBIDDEN** on the telemetry bus (allow enums: stage, decision, step_count).

---

## 9. Memory Observability Analysis

**Exists:** retrieval latency, mode, counts, `context_summary` (designed as safe), lifecycle events with optional `correlation_id`, embedding content **hashes**, fencing flags.

**Risks:**

- `MEMORY_RETRIEVAL_STARTED` WS: `query=user_request[:60]` — still **user content**.
- `command_logs` and orchestrator `print` of full prompt — **secondary store / log of user content**.
- Checkpoints `goal` / `tool_results` — operational resume, **not** telemetry; do not duplicate into a telemetry table.
- Memory telemetry must stay **ids, counts, latencies, privacy_class, status** — never `content`.

Current telemetry **risks violating** “no secondary memory store” via `command_logs` and stdout, not via a dedicated telemetry table (that table does not exist).

---

## 10. Failure Observability Analysis

Typed provider errors exist in `models/base_provider.py`. Elsewhere failures are **strings** (`print`, `Exception as e`).

**Cannot reliably distinguish** in one schema: validation vs auth vs timeout vs vector vs planner vs network.

Missing on almost all paths: `error_type`, `component`, `operation`, `retryable`, `reason` (enum), `latency`, `correlation_id`.

---

## 11. Retry / Fallback Analysis

Router: empty text → next capable provider (no `record_failure`); exceptions → `record_failure` + next; circuit OPEN skip.

**Operator cannot later ask** “which provider failed, who served the final text, how many hops, was budget exhausted?” without scraping stdout.

Bridge: retry/replan prints (`RETRY POLICY`, loop defense). Not structured.

---

## 12. Security & Privacy Audit

| ID | Severity | Finding | Location (no secrets) |
|----|----------|---------|------------------------|
| P-01 | **HIGH** | Full user prompt printed | `core/orchestrator.py` `process_request` |
| P-02 | **HIGH** | Full prompt + response persisted unbounded | `command_logs` via `log_command` |
| P-03 | **HIGH** | WS `COGNITION_STARTED` includes full `request` | `core/cognition/engine.py` |
| P-04 | **HIGH** | WS reasoning `summary` | `REASONING_COMPLETE` |
| P-05 | **MEDIUM** | WS memory query prefix 60 chars | `MEMORY_RETRIEVAL_STARTED` |
| P-06 | **MEDIUM** | `[DOOM CORE] [FINAL RESPONSE]` full spoken text | orchestrator |
| P-07 | **CRITICAL (conditional)** | Gemini API key in query string of HTTPS URL | `models/gemini_provider.py` — leak **if** URL logged |
| P-08 | **MEDIUM** | Provider/AWS error snippets to stdout | groq/bedrock prints |
| P-09 | **LOW** | Dashboard `/api/dev/generate_types` bypasses router | `dashboard/server.py` |
| P-10 | **INFORMATIONAL** | `CorrelationContext` docstring already forbids CoT in that module | `correlation.py` — not wired |

No live secret values are reproduced in this report.

---

## 13. Dashboard / WebSocket / IDE Audit

- Telemetry is **not** generated once and reused. Dashboard **polls** `model_router.get_provider_status()` / intelligence matrix; cognition **pushes** ad-hoc dicts; IDE returns its own `latency_ms`.
- Schemas differ: `cognitive_event`, `memory_event`, HUD clap events, task engine payloads.
- Task ids exist on task payloads; request ids do not.
- Timestamps: mix of `time.strftime("%H:%M:%S")` and ISO in PG.
- Broadcaster `try/except pass` — **fail-open** (good isolation).
- Raw errors: Agent Studio returns `Model invocation error: {model_err}` to the client — may include provider exception text (**MEDIUM**).

---

## 14. Persistence & Retention Audit

| Store | Schema (relevant) | Write path | Retention | Growth | Privacy | Fail behavior |
|-------|-------------------|------------|-----------|--------|---------|---------------|
| `command_logs` | text command/response, tools JSON, latency | sync INSERT | **None** | Unbounded | **Content store** | rollback + print; request continues |
| `system_telemetry` | cpu/ram/disk JSON | sync INSERT | **None** | Unbounded | Host metrics | same |
| `task_checkpoints` | full checkpoint | sync + local JSON file | Overwrite per task_id | Files in checkpoint dir | Goal/tools | print; execution continues |
| `memory_lifecycle_events` | memory ops | memory engines | **None** observed | Grows with memory | Should be metadata | memory-path specific |
| stdout | unstructured | print | OS log | Unbounded | Content | **can** slow process; rarely fails request |
| Circuit breaker | in-process dict | mutate | process lifetime | Bounded | Safe | N/A |

**Telemetry can grow without bounds** (`command_logs`, `system_telemetry`, stdout).

**No V5.3.7.4 migration proposed as applied** — future implementation must add **TTL / partition / max-rows**, not another unbounded dump of prompts.

---

## 15. Telemetry Failure-Isolation Audit

| Failure | Breaks execution? |
|---------|-------------------|
| `log_command` PG error | **No** (`except` / rollback) |
| Checkpoint PG/file | **No** (print, continue) — resume quality degrades (checkpoint is **authoritative for resume**, not telemetry) |
| WS send | **No** |
| Cognitive `_broadcast` | **No** |
| `print` | Unlikely |
| Metrics registry (none) | N/A |

**Violation vs “observational not authoritative”:** `task_checkpoints` are **operational state**, not telemetry. V5.3.7.4 must not make the **event bus** required for `process_request` to return.

**Minor:** unhandled `print` of full exceptions could theoretically block on a stuck pipe — low.

---

## 16. Duplication Analysis

- Timing: CognitiveTelemetry vs `[PERF]` vs `command_logs.latency_ms` vs IDE `latency_ms` vs embedding `latency_ms`.
- Provider health: router `is_available()` vs dashboard matrix vs circuit breaker vs NIM `/models` cache.
- Activity: `command_logs` vs episodic memory vs checkpoints vs WS.
- IDs: `CorrelationContext` vs memory `correlation_id` vs `task_id`.
- Logging: `print("[X]")` vs `logging.getLogger("DOOM.*")` (memory packages) vs none in orchestrator.

**Canonical recommendation:** one **TelemetryBus** (in-process, fail-open, bounded queue) + one **event schema** + optional async PG sink with redaction. stdout `[PERF]` becomes a **subscriber**, not a source of truth. `command_logs` either redacted or deprecated for ops (keep as optional **audit** with retention, not metrics).

---

## 17. Performance Analysis

Hot path today: extra **synchronous** `log_command` INSERT after every request; checkpoint INSERT+file on many step transitions; WS json.dumps per cognitive event; NIM availability GET cached 1h.

**Expected V5.3.7.4 overhead if naive sync INSERT per event:** milliseconds per event on local PG — acceptable only if **batched/async** and **sampled** for high-frequency embed calls.

Recommend: **nonblocking emit** → bounded buffer (drop oldest) → batch flush. Histograms in-process. **No** sync disk per tool on the voice path.

---

## 18. Missing Capabilities

1. Request-scoped correlation at `process_request` / HTTP / WS entry.  
2. Canonical event schema and bus.  
3. Provider call records (latency, error class, fallback chain, final winner).  
4. Per-tool latency events.  
5. Split memory vs embed vs vector vs DB timings.  
6. Structured failure taxonomy.  
7. Redaction pipeline.  
8. Metrics (counters/histograms) without duplicating CognitiveTelemetry.  
9. Retention/TTL.  
10. Dashboard/IDE **consumers** of the bus.  
11. Tests for isolation, redaction, correlation.  
12. Explicit “no CoT on the bus.”

---

## 19. Risk Register

| ID | Risk | Severity |
|----|------|----------|
| R-01 | Prompt/response as unbounded “telemetry” | HIGH |
| R-02 | WS leaks user text / reasoning | HIGH |
| R-03 | Gemini key-in-URL if logged | CRITICAL (conditional) |
| R-04 | Correlation IDs unused / cross-request bleed | HIGH (ops) |
| R-05 | Unbounded `command_logs` / `system_telemetry` | MEDIUM |
| R-06 | Telemetry implementation becomes required for cognition | HIGH if mis-built |
| R-07 | Duplicate health probes / Bedrock accidentally probed | MEDIUM |
| R-08 | CoT stored “for explainability” | HIGH (architecture) |
| R-09 | Provider-routing reopened under observability | Process risk |

---

## 20. Proposed V5.3.7.4 Architecture

**Name:** DOOM Operational Telemetry Plane (OTP)

1. **Emit API** — `telemetry.emit(event)` never throws to callers.  
2. **Context** — bind `CorrelationContext` at every public entry (`process_request`, `/api/agent/chat`, IDE chat).  
3. **Redactor** — strip secrets, prompts, memory `content`, headers, URLs with `key=`.  
4. **Bus** — in-memory bounded queue; subscribers: metrics, optional PG, optional WS (already-redacted).  
5. **Sink** — `operational_events` table: metadata only + TTL. **Not** a memory store.  
6. **Metrics** — process-local counters/histograms scraped by dashboard.  
7. **Providers** — observe `ModelRouter.generate` results only; **no** new routing; **no** Bedrock probe.  
8. **UI** — dashboard/IDE subscribe to redacted events; stop sending raw `request=` on WS.

Phased: (1) correlation + redacted events + isolation tests, (2) provider/tool/latency, (3) sink+retention+dashboard, (4) memory-safe ops events.

---

## 21. Proposed Event Schema

```text
OperationalEvent
  event_id            uuid
  ts_unix_ms          int
  doom_request_id     str
  task_id             str?
  cognitive_cycle_id  str?
  step_id             str?
  tool_execution_id   str?
  provider_call_id    str?
  category            enum  (see below)
  name                str   (e.g. provider.generate.completed)
  status              enum  (ok|error|timeout|skipped|fallback)
  latency_ms          float?
  component           str
  operation           str
  error_type          str?  (class name, not message body)
  retryable           bool?
  attributes          { SAFE_METADATA only }
```

**Categories:** `request`, `cognitive`, `task`, `step`, `tool`, `provider`, `memory`, `vector`, `verify`, `retry`, `security`, `ws`.

**Forbidden in `attributes`:** prompt, response body, memory content, api_key, Authorization, CoT, file contents.

---

## 22. Proposed Metrics

Inspect first — **do not duplicate** CognitiveTelemetry fields as a second source of truth; **derive** metrics from events.

Reuse existing clocks as **inputs** to events, then:

- `request_total` / `request_success_total` / `request_failure_total`
- `request_latency` (histogram)
- `cognitive_latency`, `planning_latency`, `tool_latency` (per tool label), `provider_latency` (per provider label)
- `memory_retrieval_latency`, `embedding_latency`, `verification_latency`
- `retry_total`, `fallback_total`, `provider_failure_total{type}`
- `tool_failure_total`, `verification_failure_total`
- `task_completion_total`, `task_partial_success_total`, `task_failed_total`
- `circuit_open` (gauge per provider)
- `telemetry_dropped_total` (bus overflow)

**Do not** add `bedrock_health_probe_total` while Bedrock is disabled.

---

## 23. Proposed Privacy Boundary

| Class | Examples | Telemetry |
|-------|----------|-----------|
| **SECRET** | API keys, passwords, AWS creds, Authorization | **FORBIDDEN** |
| **SENSITIVE_DATA** | credentials in user text, private memory `content`, PII bodies | **FORBIDDEN** |
| **PRIVATE_METADATA** | full prompt, full response, CoT, reasoning prose, file bodies | **FORBIDDEN** on bus; optional **short-retention audit** only with explicit operator flag (out of default V5.3.7.4) |
| **OPERATIONAL_METADATA** | ids, enums, counts, latencies, provider names, cost_tier, circuit state, termination_reason | **ALLOWED** |
| **SAFE_METADATA** | tool **names**, step **counts**, success booleans, model **ids** | **ALLOWED** |

Redact: URL query `key`, `gsk_`, `sk-`, `nvapi-`, `AKIA`, bearer tokens. Never log `.env`.

Telemetry **must not** become hidden persistence for user content. `command_logs` as currently designed **fails** this test; V5.3.7.4 should **not** extend it — either redact future writes or leave it as a separate legacy audit with TTL (implementation choice, later).

---

## 24. Proposed Test Plan

**Exact proposed count: 52 tests** (file: future `test_v5374_observability.py` or split modules — **not created now**).

| Group | Tests | Coverage |
|-------|------:|----------|
| Schema validation | 4 | required fields, forbidden keys rejected |
| Correlation propagation | 5 | request→cognition→tool→provider→verify |
| Lifecycle order | 4 | started before completed; fallback after fail |
| Latency | 4 | request, tool, provider, memory clocks present |
| Provider | 4 | name, cost_tier, error class, no Bedrock probe |
| Fallback / retry | 4 | hop list, final provider, CB skip, empty-response hop |
| Tool | 3 | name, latency, success |
| Cognitive | 3 | stage names, no CoT field, termination_reason |
| Memory privacy | 4 | no content, no prompt, hash ok, query not on bus |
| Secret leakage | 4 | key, bearer, gemini URL, .env |
| Isolation | 4 | sink down, serializer explode, WS down, emit never raises |
| UI consistency | 3 | dashboard, WS, IDE same event ids |
| Concurrency | 2 | two requests distinct doom_request_id |
| Performance | 2 | overhead bound; drop on overflow |
| Persistence | 2 | TTL/bound; insert fail isolated |

---

## 25. DOOM Capability Advantage Assessment

Chatbot logging answers “what was said.” DOOM observability should answer:

- Why this model (capability + cost + availability + circuit)?  
- Why 20s (which stage histogram)?  
- Which step failed (step_id + error_type)?  
- Why retry / provider switch (structured hop list)?  
- Why verification rejected (verdict enum, not essay)?  
- Which tool was slow (per-tool latency)?  
- Did retrieval affect the path (memory_hit, count, latency — **not** quotes)?  
- Partial success / prevent next time (termination_reason + failure class → later governance, **not** this phase changing governance).

Without correlation and redaction, DOOM cannot **explain itself** without becoming a leaky tape recorder.

---

## 26. Implementation Scope (future)

- Correlation bind at entries.  
- Telemetry emit + redactor + bounded bus.  
- Instrument: orchestrator, cognitive engine (redact WS), bridge tools, **existing** `model_router.generate`, retrieval latency (ids only).  
- Metrics from events.  
- Optional PG sink with retention.  
- Dashboard/IDE subscribe to redacted events.  
- 52 tests.  
- Isolation: emit never fails the agent.

---

## 27. Explicit Out-of-Scope Items

- Memory lifecycle / retrieval / ranking / evolution / evidence / experience / governance / knowledge transfer redesign.  
- Vector/outbox/worker redesign.  
- Provider **routing** redesign (released).  
- Enabling Bedrock.  
- Implementing Gemini streaming/tools.  
- Storing CoT.  
- Fixing historical FastEmbed suite failures.  
- V6/V7.  
- Cleaning untracked markdown.  
- `/api/dev/generate_types` Groq bypass **fix** (document only unless a later task).

---

## 28. Release Gates (for a future implementation)

- End-to-end correlation.  
- No secrets logged.  
- Private memory/content protected.  
- No raw CoT on the bus.  
- Telemetry failure cannot break execution.  
- Provider telemetry via canonical router abstraction.  
- Dashboard/IDE consume canonical events.  
- Major-stage latency measurable.  
- Retries/fallbacks explainable.  
- Structured failures.  
- Task + cognitive lifecycle observable.  
- Memory ops observable without content.  
- Bounded growth.  
- Concurrency-safe.  
- Overhead measured.  
- Production path tested.  
- Security scan of emit payloads.  
- Regression suite (existing 54 env failures **not** “fixed”).  
- Deterministic tests.

---

## 29. Final Recommendation

**Proceed to V5.3.7.4 implementation only after explicit approval.**

Direction: **fail-open operational event plane + redaction + correlation**, observing the **released** provider router. Treat current `[PERF]`, `command_logs`, and WS payloads as **legacy**, not the architecture.

**Blockers before a safe implementation:** P-01–P-04 (content on stdout/WS/PG) must be **addressed in the observability design** (stop putting PRIVATE_METADATA on the new bus; plan deprecation/TTL for `command_logs`). P-07 (Gemini URL key) should be a **documented provider hygiene item**, not a routing rewrite.

**Proposed test count: 52.**

---

**NO production implementation was performed.**  
**NO commit, tag, or push was performed.**  
**STOP. Wait for explicit approval to implement V5.3.7.4.**
