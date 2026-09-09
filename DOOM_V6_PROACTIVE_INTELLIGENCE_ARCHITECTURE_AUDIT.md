# DOOM V6 — Proactive Intelligence
# Architecture Audit (Phase 0 — Design Only)

**Mode:** AUDIT ONLY — no implementation, no schema changes, no production edits, no commit/tag/push  
**Date:** 2026-09-09  
**Baseline:** V5 CLOSED (`V5.3.7.5` accepted with non-blocking findings; **0 blockers**)  
**This document proposes architecture.** It does not authorize code.

**Recommendation:** **B. ARCHITECTURE APPROVED WITH CONDITIONS** (see §41)

---

## 1. Executive Summary

V5 made DOOM a **reactive personal OS**: remember, retrieve, govern, route, execute, verify, observe. V6 must add a **bounded notice-and-intervene layer** that watches *internal* world change first, then (later) authorized external connectors — without becoming a notification app, a second agent, or an unrestricted background LLM.

**Repository truth:** DOOM today is **request-driven**. The only production “background” behaviors are (1) wake/clap *listeners that still wait for a user utterance*, (2) vector-sync outbox workers, (3) OTP/metrics daemons, (4) HUD WebSocket fans. `core/automation.py` / `core/advanced_automation.py` contain `schedule` loops and even `system:` / `file:` / `shell=True` skills — they are **not** on the V4/V5 cognitive path (only `demo_doom.py` imports `DOOMAutomation`). Daily Briefing and Standup are **user-invoked tools**, not clocks. There is **no** calendar, email, GitHub, webhook, deadline column, attention budget, insight store, or proactive loop.

**World model:** V5 stores **fragmented authoritative records** (projects, memories, experiences, strategies, task checkpoints, profile JSON). It does **not** assemble a coherent temporal world model (deadlines, commitments, routines, expected future state). V6 must add a **derived, non-authoritative World Snapshot** plus an **Insight** object — not a second memory system.

**Hard principle:** Proactive failure **must not** break `DOOMCore.process_request`. V6 is **observational until policy says otherwise**. Default intervention is **IGNORE**. **ACT** is rare, gated by existing `RiskLevel`, `TaskEngine.require_user_approval`, Verifier, and Governance. Retrieval stays **`dI/dN_retrieval = 0`**.

**Do not reuse** `DOOMAutomation.run_scheduler` as V6 infrastructure: it can speak, issue OS shutdown/lock, and mutate files **without** TaskEngine, Verifier, or Governance. That path is **anti-scope**.

---

## 2. Baseline Verification

| Check | Expected | Observed |
|--------|----------|----------|
| Branch | `DOOM-V5.2` | **MATCH** |
| HEAD | `e933c212278f5bd7e05d9375c9f73a773960dcff` | **MATCH** |
| `origin/DOOM-V5.2` | `e933c21` | **MATCH** |
| `v5.3.7.4-observability` | `e933c21` | **MATCH** |
| `v5.3.7.3-provider-routing` | `086582e` | **MATCH** |
| `v5.3.7.3` | `73be7ff` | **MATCH** |
| Tracked dirty | none | **none** |
| Staged | none | **none** |
| Untracked | leftover markdown | provider-routing docs + `DOOM_V5.3.7.5_FINAL_ACCEPTANCE_AUDIT.md` (**not cleaned**) |

Remote: `https://github.com/SujalPawar17/DOOM-AI-V1.git`. Freeze **accepted**. Audit proceeded.

---

## 3. Existing Proactive Capability Inventory

| Component | Exists? | Production path? | Persistence? | Authority? | V6 relevance |
|-----------|---------|------------------|--------------|------------|--------------|
| Wake word + clap (`doom.py`, `sound_detector`) | Yes | Yes (listener) | No | User still must speak | **PARTIAL** trigger, not intelligence |
| Dashboard clap → `process_request` | Yes | Yes | No | Same as OS path | Same |
| `setup_autostart` / `doom_background.pyw` | Yes | **Disabled** by default | Windows Startup VBS | Background *listen* only | Hosting hook, not V6 brain |
| `DOOMAutomation` + `schedule` | Yes | **No** (demo only) | `automation_config.json` | **Unsafe** (speak/system/file) | **Do not adopt** |
| `DOOMAdvancedAutomation` | Yes | Unknown/not cognition | `doom_skills.json` | `subprocess shell=True` | **Do not adopt** |
| Vector sync worker / outbox | Yes | Internal | `vector_sync_queue` | Sync only | Pattern to **reuse** (leases, idempotency) |
| OTP `TelemetryBus` + PG sink thread | Yes | Observational | Bounded | Never authoritative | **Reuse** for V6 lifecycle metadata |
| State machine listeners | Yes | HUD | Ephemeral | Display | Signal *source* (state changes) |
| Task checkpoints | Yes | After plans | PG | TaskEngine | Signal *source* |
| Memory lifecycle events | Yes | Mutations | `memory_lifecycle_events` | Lifecycle engine | Signal *source* |
| `DailyBriefingTool` | Yes | User tool | None | Read host+DB counts | Template for **INFORM** content, not a clock |
| `StandupReportTool` | Yes | User tool | Reads `command_logs` | Privacy-risk content | **Do not** copy full logs into insights |
| `system_telemetry` | Yes | Snapshot inserts | Unbounded host metrics | Host, not request | INTERNAL signal (disk/CPU) |
| Circuit breaker cooldown | Yes | Router | Process memory | Provider health | INTERNAL signal only |
| WebSocket HUD | Yes | Dashboard | Session | Display | **Delivery channel** (redacted) |
| TTS `speak` | Yes | After OS request | No | User-facing | High-cost interrupt; default **off** for V6 |
| Governance engine | Yes | Persist/eval, not every chat | Matrix | DATA_ONLY | **Required** before cross-project / ACT |
| CognitiveEngine | Yes | OS path only | Task/memory writes post-exec | Request | Optional *bounded* invoke from V6 |
| ModelRouter | Yes | Chat + bridge gate | No | LLM | Cost-constrained V6 calls only |
| Calendar / email / GitHub / webhooks | **No** | — | — | — | **FUTURE INTEGRATION** |
| Deadlines / due dates in schema | **No** | — | — | — | **MISSING** (metadata JSON only if stuffed ad hoc) |
| Attention budget / insight store | **No** | — | — | — | **PROPOSED** |
| Preference interruption policy | **PARTIAL** | Profile JSON prefs | JSON+PG profile | Not safety | Needs first-class PREFERENCE memories |

**What can trigger DOOM without a typed request today?** Clap/wake (then listen), dashboard WS connect, vector worker ticks, OTP flush thread. **None** evaluate significance or notify without a user command except clap greeting.

---

## 4. V5 Foundation Available to V6

Reuse, do not fork:

- MemoryManager, retrieval, fencing, privacy classes, freshness, evidence, relationships  
- Projects, experiences, lessons, strategies, transfer matrix, ProjectContext  
- Governance 14 gates + DATA_ONLY  
- TaskEngine + approval tokens + checkpoints  
- Verifier + RiskLevel  
- ModelRouter (capability/cost/health/failover)  
- OTP OperationalEvent + redactor + bounded bus  
- Correlation ContextVar (`doom_request_id` — V6 should mint `proactive_cycle_id` similarly)  
- Outbox lease/idempotency patterns  
- Bounded cognitive loop (`MAX_COGNITIVE_ITERATIONS`, max steps)

V6 **consumes** these. It does **not** become a second copy.

---

## 5. Personal World Model Assessment

| Concept | Status | Evidence |
|---------|--------|----------|
| User identity / style prefs | **PARTIAL** | `user_profile.py` + `MemoryType.PREFERENCE` |
| Active projects | **IMPLEMENTED** | `projects` + lifecycle |
| Project health | **MISSING** | No health score; experiences exist as raw outcomes |
| Ongoing tasks | **PARTIAL** | `task_checkpoints` for *agent* tasks, not human TODOs |
| Deadlines / commitments | **MISSING** | No first-class fields |
| Dependencies | **PARTIAL** | Memory relationships DAG, not task deps |
| Recurring activities | **MISSING** | |
| Routines | **MISSING** | |
| People/entities | **MISSING** as first-class | May appear in memory text only |
| Unresolved problems | **PARTIAL** | Negative experiences / error_signature |
| Recent changes | **PARTIAL** | Lifecycle events, command_logs (privacy-hostile) |
| Expected future events | **MISSING** | |
| Risks / opportunities | **PARTIAL** | Strategies + failure warnings at *plan* time |
| Historical patterns | **PARTIAL** | Bayesian strategy reliability |
| Coherent assembled world model | **MISSING** | Fragments only |

**Conclusion:** V6 needs a **WorldSnapshot** (computed, cacheable, TTL’d, non-authoritative) over V5 records — not a replacement source of truth.

---

## 6. Signal / Event Architecture

**Signal (Q2):** A typed, normalized, untrusted-until-classified fact that *something changed or time elapsed*, with provenance, timestamp, privacy class, and idempotency key. Signals are **not** memories.

**Classes:**

| Class | Availability |
|-------|----------------|
| INTERNAL: task status, memory lifecycle, project row change, experience written, strategy failure increment, OTP request completed, host telemetry, provider circuit OPEN, inactivity (no OS request for T) | **AVAILABLE NOW** (emit or poll existing tables) |
| DERIVED: deadline proximity, stagnation (no experience for project in N days) | **PARTIAL** (needs rules; no deadline field yet) |
| EXTERNAL: email, calendar, GitHub, Slack, webhooks, files watchers | **REQUIRES FUTURE INTEGRATION** |

**Normalize** into `ProactiveSignal { signal_id, source, entity_type, entity_id, occurred_at, ingested_at, privacy_class, payload_ref, idempotency_key }` with **no raw bodies** in hot indexes (store pointers / hashes; payload in bounded blob with redaction).

Deduplicate on `(source, entity_id, signal_type, time_bucket)`.

---

## 7. Event → Insight Pipeline

| Stage | Owner | In | Out | Mutate? | LLM? | Tools? | Fail | OTP |
|-------|-------|-----|-----|---------|------|--------|------|-----|
| SIGNAL | Ingest | raw | Signal | queue insert | No | No | drop+count | `proactive.signal.ingested` |
| NORMALIZE | Normalizer | Signal | Canonical | No | No | No | drop | |
| DEDUPE | Store | Canonical | keep/drop | Yes (index) | No | No | fail-open drop | `telemetry_dropped`-style |
| CORRELATE | Correlator | Signals | Cluster | No | Optional later | No | pass-through | |
| CONTEXTUALIZE | WorldResolver | Cluster | Snapshot slice | **Read V5 only** | No | No | empty context → ABSTAIN | |
| MEMORY LOOKUP | Retriever | query metadata | MemoryContext | **Read-only** | No | No | empty | no query text |
| SIGNIFICANCE | SignificanceEngine | slice | scores | No | No | No | 0 → IGNORE | |
| PREDICT | PredictionEngine | slice | optional forecast | No | Rare | No | omit prediction | confidence required |
| RISK | Policy | scores | risk class | No | No | No | fail closed | |
| INTERVENTION | AttentionEngine | all | IGNORE…ACT | **decision row** | No | No | IGNORE | `proactive.decision` |
| ACTION POLICY | Authority | decision | allowed ops | No | No | No | ASK/ABSTAIN | |
| DELIVER | Dispatcher | allowed | HUD/WS/quiet | delivery log | No | No | retry bounded | redacted |
| OUTCOME | Evaluator | user/system | outcome enum | Yes (insight) | No | No | unknown | |
| LEARN | Learning | outcome | pref/experience **via V5 writers** | Only authorized writers | No | No | skip | |

---

## 8. Significance Engine

**Insight (Q1):** A *provisional, scored, non-authoritative* statement that a signal cluster is **relevant to the user’s goals/projects** with explicit **confidence**, **urgency**, **privacy_class**, and **recommended intervention**. Insights die (TTL) unless promoted by policy + evidence.

**Do not** treat “new” as “important.”

| Dimension | In V5 today | V6 |
|-----------|-------------|-----|
| User/project relevance | Hybrid ranking, project_id | Reuse ranking **read** |
| Urgency | **Missing** (no deadlines) | **New** (rules: overdue, circuit OPEN, disk >90%) |
| Impact | Strategy reliability, risk_level | Map to intervention |
| Novelty | Relationships / supersession | Dedupe window |
| Confidence | Memory + transfer C | **Calibrated**; no MAX-only |
| Predicted consequence | **Missing** | Phase 6.4, optional |
| Historical importance | importance field (must not bump on read) | Read only |
| Deadline proximity | **Missing** | Needs world field or abstain |

Scoring: **deterministic weighted min-gates** (privacy hard-fail, confidence floor) — **not** an LLM score. Weights tuned in 6.3 with metrics, not guessed here.

---

## 9. Attention Engine

User attention is a **budgeted scarce resource**.

| Mode | When | Interrupt TTS? | HUD? |
|------|------|----------------|------|
| **IGNORE** | significance or confidence below floor; duplicate; quiet hours; budget exhausted | No | No |
| **INFORM** | Useful, no action; batched digest | Default no | Badge / digest |
| **SUGGEST** | High relevance, user should choose | No | Card |
| **PREPARE** | Safe side-effect-free prep (draft in memory of *insight*, not disk write unless SAFE) | No | Optional |
| **ASK** | Ambiguity, PRIVATE, HIGH/CRITICAL tools, governance CONDITIONAL | Maybe (policy) | Modal |
| **ACT** | Standing auth + SAFE/LOW + verifier path + not SENSITIVE | Rare | After fact |

**Remain silent (Q8)** when any of: low confidence, conflict, privacy quarantine, cooldown, budget, user dismissed same insight key, CognitiveEngine busy (`is_busy` / PROCESSING).

**Interrupt (Q7)** only INFORM+ with `urgency=critical` **and** not quiet period **and** budget remaining — still no ACT.

---

## 10. Prediction Engine

Separate **DETECTION** (state changed) from **PREDICTION** (likely to matter later).

| Category | Method | Phase |
|----------|--------|-------|
| Disk >90%, circuit OPEN, task FAILED | **RULE** | 6.1–6.2 |
| Project stagnation (no verified experience N days) | **RULE** | 6.2 |
| Deadline risk | **RULE** once deadline exists | 6.4 |
| “Likely next task” / opportunity language | **LLM-ASSISTED** only after rules, DATA_ONLY, no tools | 6.4+ |
| Statistical reliability | Reuse Bayesian strategy scores | 6.4 |

**No hallucinated deadlines:** if no dated fact in V5 records, prediction output **must omit** calendar claims (`unknown` temporal). LLM text is **never** stored as a deadline.

---

## 11. Intervention Policy

Policy is a **pure function** of (significance, confidence, risk, privacy, budget, authority, duplicates).

- Insufficient confidence → **DO NOT ACT** (IGNORE or ASK).  
- Ambiguous context → **ASK or ABSTAIN**.  
- Conflicting memories → **ABSTAIN** + optional ASK with uncertainty (no picking a winner via LLM).  
- Governance DENIED/ABSTAIN on transfer-related insights → cannot SUGGEST transfer as ALLOWED.

---

## 12. Authority Model

Reuse V5; **no V6 shadow authority**.

```
OBSERVE → ANALYZE → INFORM → SUGGEST → PREPARE → ASK → ACT
```

| Transition | Approval | Governance | Tools |
|------------|----------|------------|-------|
| OBSERVE/ANALYZE | None | Privacy filter on read | None |
| INFORM/SUGGEST | None (content redacted) | If cross-project, only APPROVED matrix facts | None |
| PREPARE (non-mutating) | None | Same | None |
| PREPARE (filesystem draft) | Standing or ASK if ≥ MEDIUM | — | TaskEngine + RiskLevel |
| ASK | User | As needed | None until granted |
| ACT | Explicit or standing **and** RiskLevel SAFE/LOW unless approved | Required for transfer/SENSITIVE | TaskEngine + Verifier |

Standing authorization must be a **versioned preference/policy object**, not a buried JSON flag in `automation_config.json`.

---

## 13. Memory Integration

- Lookups via `MemoryRetriever` / fencer only.  
- **`dI/dN_retrieval = 0`** remains: no importance/confidence/reliability/evidence/strategy count updates on proactive retrieve.  
- `touch_accessed_at` is the only existing retrieve side-effect besides optional vector orphan deletes — V6 must **not** add ranking mutations.  
- SENSITIVE: never in default retrieve; never in notifications.  
- PRIVATE: INFORM/SUGGEST only with `include_private` policy **and** same-user local OS assumption; still no WS raw body.  
- Do **not** use `command_logs` as world state (V5 residual privacy).

---

## 14. World Model Mutation Rules

| Observation | Default |
|-------------|---------|
| Signal | Queue only |
| WorldSnapshot | Recompute cache (ephemeral / TTL) |
| Insight | Bounded insight table (not `memory_records`) |
| Memory | **Never** auto from event; only `MemorySource.SYSTEM_OBSERVATION` via canonical writer **after** evidence policy (PENDING_VERIFICATION) |
| Experience | Only after **verified** proactive *task* outcome through existing writers |
| Task | Only via TaskEngine when policy ≥ PREPARE/ASK/ACT |

**Forbidden:** `event → automatic ACTIVE memory`.

---

## 15. Experience / Learning Integration

If user **accepts** a suggestion and a **verified** TaskEngine run succeeds → existing `write_experience` / project experience path.

If user **dismisses** → update **attention preference** (cooldown, suppress key), **not** strategy success_count.

If user **corrects** → optional PREFERENCE memory via `write_preference` (user-explicit).

**Anti-loop:** dismissals **decrease** future significance for that insight identity; they must **not** increase importance of related memories.

---

## 16. Personalization

| Dimension | Store |
|-----------|--------|
| Quiet hours, interrupt tolerance, digest vs interrupt, verbosity | **PREFERENCE** memory (FOUNDATIONAL) |
| Project priority | Project metadata **or** preference — pick one owner: **preference overlay**, project remains lifecycle authority |
| Session mute | **SESSION** (ContextVar / HUD) |
| World facts | V5 records |

Personalization **cannot** raise ACT permission above RiskLevel/Governance.

---

## 17. Temporal Intelligence

- Memory **freshness** ≠ world **validity**. A FOUNDATIONAL preference can be fresh while a DYNAMIC_FACT (“branch X”) is stale.  
- Insights carry `valid_until`. Stale insights cannot ACT.  
- Inactivity detector: wall-clock since last `request.completed` OTP (metadata only).  
- Recurrence: **MISSING** — V6.2+ rule table, not LLM cron.  
- Overdue: requires deadline field (**MISSING**) — until then, **no overdue claims**.

---

## 18. Proactive Task Architecture

Tasks are created **only** by TaskEngine APIs used today (create_task, approval, checkpoint).

Low risk: analyze, summarize into insight payload, collect *already authorized* reads.  
Higher risk: email, deploy, delete, purchase — **ASK**, then existing HIGH/CRITICAL approval.

V6 planner is a **thin ProactivePlanner** that emits a *candidate plan* → CognitiveBridge / TaskEngine. It must **not** duplicate CognitiveEngine’s 9-stage loop unless invoking `CognitiveEngine.process` with a **synthetic but DATA_ONLY** goal string that contains **no untrusted raw external text**.

---

## 19. Proactive Execution Authority

| Action | Observe | Prepare | Ask | Act |
|--------|---------|---------|-----|-----|
| Read V5 metadata / host metrics | Y | — | — | — |
| Summarize (local, redacted) | Y | Y | — | — |
| Create insight / HUD card | Y | Y | — | — |
| Create local TaskEngine task | — | Y* | Y | — |
| Modify local file | — | draft in insight | Y | only SAFE/LOW + verify |
| Send communication | — | draft | **Y** | **N** default |
| External API mutation | — | — | **Y** | **N** default |
| Financial / destructive | — | — | **Y** | **N** |

\*Prepare task = checkpoint in PAUSED / WAITING_FOR_APPROVAL, not EXECUTE.

---

## 20. Background / Event Infrastructure

**Existing:** threading daemons (clap, OTP sink), vector outbox + leases, `schedule` in unused automation.

**Proposed V6:** PostgreSQL **signal + insight outbox** modeled on `vector_sync_queue` (FOR UPDATE SKIP LOCKED, lease, retry, DLQ). Poll interval bounded (e.g. 1–5s). **No** process-wide `while True` LLM.

Idempotency: reuse `core/reliability/idempotency.py` keys.  
Crash: unleased rows retry.  
Shutdown: daemon threads already; V6 worker must be stoppable without killing cognition.

**Do not** start `DOOMAutomation.run_scheduler` from `doom.py`.

---

## 21. CognitiveEngine Integration

**E — stage-dependent:**

- Ingest/significance/attention: **no** CognitiveEngine.  
- SUGGEST copy / ambiguous ASK: **optional** `CognitiveEngine.process` with fenced prompt, `request_scope`, **max 1 cycle**, tools **disabled** unless ASK→ACT.  
- ACT: full existing OS path (user-equivalent goal) so Verifier/TaskEngine apply.

V6 is **not** a second 9-stage core. Avoid `core/jarvis_agent.py` / `ai_brain` forks.

---

## 22. ModelRouter Integration

- Default V6: **zero LLM**.  
- Allowed: summarization / ASK phrasing via `model_router.generate(task_type="fast_conversation")` with **token/time budget**, circuit breaker, **no Bedrock**.  
- Cost: daily LLM call cap (metric `proactive_llm_calls_total`). Exceed → INFORM template only.  
- Never a second router. IDE bypass is a **V5 residual**; V6 must call `generate` only.

---

## 23. Governance Integration

- Cross-project SUGGEST/ACT: only facts from **APPROVED** matrix **or** live `evaluate_transfer` (prefer live eval at *action* time, matrix for *read* — same as V5 retrieve).  
- SENSITIVE quarantine absolute.  
- Governance unavailable → **no** transfer-related intervention (fail closed for that class); other INFORM may continue.  
- Governance remains **zero tool authority**.

---

## 24. Observability Integration

Extend OTP categories (`proactive` name prefix) — **do not** add a bus. Events: signal, insight, decision, delivery, outcome. Attributes: ids, enums, latencies, privacy_class. **No** insight prose in attributes (hash/len only). Fail-open. Correlate `proactive_cycle_id` + optional `doom_request_id` if cognition invoked.

---

## 25. Privacy Architecture

Background ≠ extra privilege. Same privacy_class filters. Notifications: **no** memory body, **no** command_logs dump (Standup tool is a V5 anti-pattern to copy). Retention: insight TTL + signal TTL (shorter than memory). User-visible audit: “why did you ping me?” from metadata enums.

---

## 26. Security Threat Model

| # | Threat | Impact | Mitigation | Control | Residual |
|---|--------|--------|------------|---------|----------|
| 1 | Malicious external event | False ACT | Untrusted DATA_ONLY; no tools from payload | Fencer + policy | Connector bugs |
| 2 | Prompt injection | Tool/gov spoof | Never concatenate raw into system prompt; fence | Fencing | LLM summarizer leak |
| 3 | Poisoned memory | Bad SUGGEST | Provenance/confidence floors; ACTIVE only | V5 retrieval | Weak USER_CONVERSATION |
| 4 | False event | Spam/ACT | Provenance + corroboration for ACT | Dual-source for ACT | — |
| 5 | Duplicate | Spam | Idempotency key | Store | Clock skew |
| 6 | Replay | Repeat ACT | idempotency + occurred_at window | Outbox | — |
| 7 | Stale event | Wrong ACT | `valid_until`, generation | WorldSnapshot TTL | — |
| 8 | Event poisoning | DoS | Rate limit ingest | Worker | — |
| 9 | Cross-project leak | Privacy | Governance + project_id | V5 | Matrix stale |
| 10 | Private notify leak | Privacy | Redactor + class filter | OTP+dispatcher | HUD XSS |
| 11 | Action escalation | Harm | RiskLevel + approval | TaskEngine | Standing-auth too broad |
| 12 | Feedback gaming | More spam | Dismiss lowers score | Learning rules | — |
| 13 | Notify DoS | Annoyance | Budget + queue cap | Attention | — |
| 14 | Runaway loop | Harm | max chain, cooldown, busy gate | Loop bounds | — |

---

## 27. Prompt Injection Boundary

```
EXTERNAL / UNTRUSTED
  → sanitize/normalize (no instruction merge)
  → DATA_ONLY context object
  → deterministic analysis
  → policy
  → (optional) LLM on fenced summary only
  → never system prompt, never tool names from payload, never “ignore governance”
```

Reuse `MemoryContextFencer` / `MemorySanitizer` patterns (`test_v525` spoof directive).

---

## 28. Anti-Spam / Attention Protection

Mandatory **before** any user-visible V6.1 INFORM:

- Per-insight-key cooldown  
- Daily interrupt cap  
- Batch digest  
- Quiet hours (preference; default conservative)  
- Suppress if CognitiveEngine PROCESSING  
- Aggregate repeats (“3× disk warning”)  
- Escalation only on worsening severity, not on clock  

Metrics (no numeric SLOs yet): notifications/day, useful rate, dismissal rate, FP rate, repeated-insight rate, correction rate, action success.

---

## 29. Failure & Recovery Model

**Core DOOM continues if V6 is down** (feature flag `PROACTIVE_ENABLED=false` default until 6.1 ships).

Ingest fail → drop + counter.  
Dedupe fail → fail-open drop (not double ACT).  
Memory down → IGNORE.  
LLM down → templates.  
Governance down → no transfer/ACT.  
Notify fail → retry ≤N then drop.  
User silent → expire insight, no nag.  
Worker crash → lease expiry (V5.3.7.2 pattern).

---

## 30. Architectural Components (justified)

| Component | Purpose | Authority | Persist | Deps | Fail |
|-----------|---------|-----------|---------|------|------|
| **ProactiveIngest** | Enqueue signals | None | outbox | PG | drop |
| **SignalNormalizer** | Canonicalize | None | no | — | drop |
| **InsightStore** | Dedupe/TTL insights | Not memory | PG bounded | — | drop |
| **WorldSnapshotBuilder** | Read-assemble V5 | Read | cache TTL | memory/projects/tasks | empty |
| **SignificanceEngine** | Score | None | no | snapshot | 0 |
| **AttentionEngine** | IGNORE…ASK | Policy | budget counters | prefs | IGNORE |
| **InterventionPolicy** | Map to ops | None | no | RiskLevel | ASK/ABSTAIN |
| **ProactiveDispatcher** | HUD/WS | Display | delivery log | OTP | retry |
| **ProactiveBridge** | Optional cognition/task | Delegates | — | CognitiveEngine, TaskEngine | skip |
| **OutcomeLearner** | Feedback | V5 writers only | V5 | experiences/prefs | skip |
| **ProactiveWorker** | Drain outbox | Lease | queue | reliability | recover |

**Not justified in 6.1:** PredictionEngine (stub), external connectors, TTS interrupts, `DOOMAutomation`.

---

## 31. Architecture Diagram

```
 INTERNAL V5 SOURCES                 FUTURE CONNECTORS
 task_checkpoints                    (email/calendar/git) [UNTRUSTED]
 memory_lifecycle_events                    │
 projects / experiences                     ▼
 OTP request.completed              DATA_ONLY fence
 host system_telemetry                      │
 circuit breaker                            │
        │                                   │
        ▼                                   ▼
 ┌──────────── SIGNAL INGEST / NORMALIZE / DEDUPE (outbox) ────────────┐
 └────────────────────────────┬─────────────────────────────────────────┘
                              ▼
                    WorldSnapshotBuilder  ←── MemoryRetriever (READ-ONLY)
                              │               Governance (read APPROVED / eval at ACT)
                              ▼
                    SignificanceEngine ──► PredictionEngine (later, optional)
                              │
                              ▼
                    AttentionEngine + Budget + Cooldown
                              │
              IGNORE / INFORM / SUGGEST / PREPARE / ASK / ACT
                              │
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
     Dispatcher          ProactiveBridge     (blocked)
     HUD/WS redacted     CognitiveEngine?    if policy deny
     (no TTS default)    TaskEngine
                         Verifier
                         ModelRouter.generate
                              │
                              ▼
                         User response
                              │
                         OutcomeLearner → V5 experience/preference
                              │
                         OTP (fail-open, metadata)

 Security/Privacy wrap all reads. Approval sits on ASK/ACT. Observability on every hop.
```

---

## 32. Data Ownership Matrix

| Data | Source | Authority | Persist | Mutability | Privacy | V6 owner |
|------|--------|-----------|---------|------------|---------|----------|
| Signals | ingest | V6 queue | bounded | append | classed | Ingest |
| WorldSnapshot | derived V5 | **V5 records** | cache | recompute | inherited | Builder |
| Insights | V6 | V6 store | TTL | immutable+status | classed | InsightStore |
| Predictions | V6 | V6 | TTL | replace | classed | Prediction |
| Attention decisions | policy | AttentionEngine | counters | update | meta | Attention |
| Interventions | dispatcher | delivery log | bounded | append | redacted | Dispatcher |
| Proactive tasks | TaskEngine | **TaskEngine** | checkpoints | V5 rules | task | Bridge |
| Outcomes | user/system | InsightStore | TTL | update | meta | Learner |
| Feedback | user | PREFERENCE/insight | V5+V6 | authorized | pref | Learner |
| Preferences | user | MemoryManager | V5 | writers | PRIVATE/NORMAL | V5 |
| Memories | V5 | MemoryManager | V5 | lifecycle | classed | V5 |
| Experiences/lessons/strategies | V5 | project engine | V5 | writers | classed | V5 |

---

## 33. Authority Matrix

| Capability | V6 | CognitiveEngine | Governance | TaskEngine | Verifier | User |
|------------|----|-----------------|------------|------------|----------|------|
| Detect/score | **Owner** | — | privacy read | — | — | — |
| Show INFORM | **Owner** | — | — | — | — | mute |
| Phrase ASK | delegate | optional | — | — | — | **decides** |
| Create/run task | propose | execute if invoked | transfer | **Owner** | post-exec | approval if HIGH |
| Tool execute | never direct | via bridge | never | via tools | — | — |
| Memory write | never direct | existing post-exec | — | — | evidence | explicit remember |
| LLM | budgeted generate | process | — | — | — | — |
| Routing | — | — | — | — | — | — | **ModelRouter owner** |

No dual authority: V6 **cannot** mark a task SUCCESS; Verifier/TaskEngine remain sole.

---

## 34. Failure Matrix

| Failure | Behavior | Safety | User | Recovery |
|---------|----------|--------|------|----------|
| Ingest | drop | no ACT | none | metric |
| Duplicate | suppress | no double ACT | none | key |
| Late event | stale reject | no ACT | none | TTL |
| Memory down | IGNORE | fail closed action | silent | retry later |
| Prediction fail | detect-only | no claim | none | — |
| LLM down | template | no hallucinated deadline | weaker copy | cap |
| Governance down | no transfer/ACT | fail closed | none | — |
| Notify fail | retry N | no loop | missed INFORM | DLQ |
| Task create fail | ASK later | no fake success | card error | — |
| No user reply | expire | no nag | none | TTL |
| External down | pause connector | no ACT | none | backoff |
| V6 worker dead | cognition lives | isolated | no proactive | lease recover |

---

## 35. Metrics & Acceptance Philosophy

V6 is complete only if: detection works; significance ≠ novelty; attention caps work; no spam; predictions carry confidence or are absent; privacy/governance/task verification intact; loops terminate; feedback captured without retrieval mutation; OTP traces the lifecycle; **zero unauthorized ACT**; measurable useful-intervention vs passive DOOM.

Proposed metric families: detection latency, dedupe rate, useful insight, FP, calibration, dismissal, unauthorized action rate (**must stay 0**), verify success, event loss, forbidden leakage (tests).

---

## 36. V6 Anti-Scope

| Exclusion | Why |
|-----------|-----|
| Generic notification engine | No world+memory+authority |
| Unrestricted autonomous agent | Violates ACT matrix |
| Unlimited background LLM | Cost/latency/injection |
| Uncontrolled web monitoring | Privacy/DoS |
| Autonomous finance / silent email | CRITICAL harm |
| Hidden profiling | Privacy |
| Unrestricted memory mutation | Pollutes V5 truth |
| Duplicate cognition/router/telemetry/governance | Split-brain |
| Bypass verifier/approval | False success |
| Uncontrolled self-mod (write own code) | Out of scope |
| **Adopt `core/automation.py` scheduler** | Ungoverned OS/file/speak |
| Copy Standup `command_logs` into HUD insights | V5 privacy residual |
| Enable Bedrock for V6 | Routing freeze |

---

## 37. Proposed V6 Phase Breakdown

Revised from the prompt to match **internal-first** and **no unsafe scheduler**.

### V6.1 — Proactive Foundation (Observe + INFORM)
- **Objective:** Isolated worker, signal outbox, insight TTL, attention budget, IGNORE/INFORM only, OTP, feature flag off-by-default.  
- **Deps:** V5 freeze.  
- **New:** ingest, store, snapshot builder (read), significance stub, dispatcher HUD.  
- **Reuse:** PG, OTP, retriever, fencing.  
- **Security:** no tools, no LLM required.  
- **Accept:** flag off ⇒ identical OS behavior; flag on ⇒ ≤N INFORM/day; zero ACT; 0 retrieval mutations; fail-open vs cognition.

### V6.2 — Internal Signal Coverage
- Task/memory/project/experience/circuit/host/inactivity signals.  
- **Accept:** each source tested; dedupe; privacy filters.

### V6.3 — Attention + Anti-spam
- Quiet hours, cooldown, aggregation, busy-gate.  
- **Accept:** spam tests; dismissal suppresses.

### V6.4 — Rule prediction + optional LLM summary
- Stagnation/overdue-if-field-exists; LLM cap.  
- **Accept:** no deadline hallucination tests.

### V6.5 — PREPARE + ASK via TaskEngine
- Drafts; WAITING_FOR_APPROVAL.  
- **Accept:** no HIGH tool without token.

### V6.6 — Constrained ACT
- SAFE/LOW only; full cognition+verify.  
- **Accept:** unauthorized action rate 0.

### V6.7 — Outcome learning
- Experience/preference via V5 writers; anti-amplification.

### V6.8 — Hardening + optional connectors
- Webhooks as UNTRUSTED; no silent send.

---

## 38. Architectural Risks

| ID | Finding | Class |
|----|---------|--------|
| R1 | Legacy automation can execute OS/file/shell without V5 gates | **CRITICAL** if reused; **INFORMATIONAL** if excluded (excluded) |
| R2 | No coherent world model / deadlines | **HIGH** (limits 6.4; 6.1 still viable on host+task+lifecycle) |
| R3 | Split UI (Agent Studio/IDE bypass cognition) | **HIGH** — V6 must not notify those paths with unredacted LLM; delivery = HUD/OS only first |
| R4 | Live governance not on every chat | **HIGH** — ACT/transfer must eval or use APPROVED only |
| R5 | `command_logs` / `/api/logs` full text | **HIGH** privacy — V6 must not ingest as signal payload |
| R6 | FastEmbed/pgvector env gaps | **MEDIUM** — V6.1 can use SQL/lifecycle signals without embeddings |
| R7 | WS `event` overwrite (V5 finding) | **MEDIUM** — V6 dispatcher should use `type: operational_event` / distinct `proactive_event` **without** clobbering `event` |
| R8 | Unbounded `system_telemetry` | **MEDIUM** — don’t clone unbounded tables for insights |
| R9 | Clap/TTS as interrupt | **MEDIUM** — default off |
| R10 | Cost of periodic LLM | **HIGH** if 6.1 uses LLM — **forbidden in 6.1** |

No **CRITICAL** once R1 is **anti-scoped**.

---

## 39. Acceptance Gates (Architecture)

| Gate | Meaning |
|------|---------|
| G1 | No duplicate authority (task/memory/gov/router/telemetry) |
| G2 | No governance bypass |
| G3 | No tool authority from external/untrusted data |
| G4 | Bounded worker; max chain; feature flag |
| G5 | Attention budget before INFORM |
| G6 | No SENSITIVE/PRIVATE bodies in notify/OTP |
| G7 | No unbounded LLM (cap + 6.1 zero-LLM) |
| G8 | No auto ACTIVE memory from signals |
| G9 | No unauthorized ACT |
| G10 | Idempotent ingest; no duplicate ACT |
| G11 | Stale insights cannot ACT |
| G12 | Cross-project via V5 only |
| G13 | V6 crash ≠ cognition crash |
| G14 | OTP lifecycle metadata |
| G15 | Measurable usefulness + dismissals |
| G16 | `dI/dN_retrieval = 0` |
| G17 | Do not start `DOOMAutomation` scheduler |
| G18 | Default `PROACTIVE_ENABLED=false` until 6.1 tests pass |

---

## 40. Critical Architectural Questions

**Q1 Insight:** Scored, TTL’d, non-authoritative hypothesis of relevance + recommended intervention.  
**Q2 Signal:** Typed change/time fact with provenance; not memory.  
**Q3 Truth:** V5 records + TaskEngine + Verifier; V6 snapshot/insights are derived.  
**Q4 Event→memory:** Only canonical writer + evidence/lifecycle; default never.  
**Q5 Event→task:** Only policy PREPARE/ASK/ACT via TaskEngine.  
**Q6 Show user:** AttentionEngine INFORM+ and budget.  
**Q7 Interrupt:** Critical urgency + not quiet + budget; TTS off by default.  
**Q8 Silent:** Low score, dup, busy, privacy, conflict, cooldown.  
**Q9 Prepare:** Non-mutating always; mutating only SAFE + policy.  
**Q10 Act:** SAFE/LOW + standing/explicit approval + verify; never from untrusted text.  
**Q11 Attention:** Budget, quiet hours, busy-gate, caps.  
**Q12 Prediction confidence:** Separate numeric; omit if unknown; never MAX.  
**Q13 No hallucinated futures:** No date ⇒ no deadline speech; LLM not a clock.  
**Q14 Stale world:** Snapshot TTL + `valid_until` + generation.  
**Q15 Repeats:** Identity key + cooldown + aggregation.  
**Q16 Ignored suggestions:** Suppress key; don’t boost memory importance.  
**Q17 Experience:** Verified proactive tasks only → V5 writers.  
**Q18 Governance:** Read APPROVED; live eval before transfer ACT.  
**Q19 CognitiveEngine:** Optional fenced invoke; not ingest path.  
**Q20 TaskEngine:** Sole task authority.  
**Q21 ModelRouter:** Budgeted `generate` only.  
**Q22 Observability:** OTP categories, fail-open, no prose.  
**Q23 Untrusted data:** DATA_ONLY fence; never instructions.  
**Q24 Minimum real V6:** 6.1 internal signals + IGNORE/INFORM + budget + isolation + OTP.  
**Q25 Not built:** §36 list, especially automation scheduler and silent ACT.

---

## 41. Final Recommendation

**B. ARCHITECTURE APPROVED WITH CONDITIONS**

V6.1 implementation may begin **only when** all of the following are accepted as contract:

1. **Scope lock:** Observe + **INFORM** only; `PROACTIVE_ENABLED` defaults **false**.  
2. **Isolation:** V6 worker cannot throw into `process_request`; cognition works if V6 is crashed.  
3. **Anti-scope R1:** Never wire `core/automation.py` / `advanced_automation.py` schedulers.  
4. **No LLM required** in 6.1; if added later, daily cap + `generate` only.  
5. **No ACT/PREPARE mutating** until V6.5+ gates.  
6. **New bounded tables** for signals/insights/deliveries — not `command_logs`, not unbounded clones. Schema work is a **future implementation phase**, not this audit.  
7. **Privacy:** no SENSITIVE/PRIVATE bodies; no command_log ingestion.  
8. **Retrieval:** `dI/dN_retrieval = 0` tests extended to proactive retrieve.  
9. **OTP:** metadata-only proactive events; distinct payload `type` so HUD `event` is not clobbered.  
10. **Do not “fix V5”** inside V6.1 except where a collision would ship (dispatcher event shape). Known residuals stay residuals.  
11. **Delivery:** Dashboard HUD / OS state — not Agent Studio prompt injection, not IDE `provider.generate`.  
12. **Acceptance tests planned before code:** isolation, dedupe, budget, privacy, no memory pollution, flag-off regression.

**Not READY (C)** would apply if V6 were specified to ACT in 6.1 or to reuse the legacy scheduler. That is **rejected** here.

---

*End of V6 architecture audit. Production code untouched. No commit.*
