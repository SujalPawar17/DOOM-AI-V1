# DOOM

### Personal AI Operating System

[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-authoritative_store-4169E1)](https://www.postgresql.org/)
[![Release](https://img.shields.io/badge/release-v6.2.2-2ea44f)](https://github.com/SujalPawar17/DOOM-AI-V1/releases/tag/v6.2.2)
[![Branch](https://img.shields.io/badge/branch-DOOM--V5.2-24292e)](https://github.com/SujalPawar17/DOOM-AI-V1)

> A persistent autonomous intelligence platform designed to understand, reason, plan, execute, verify, remember, and progressively operate across a user's digital environment.

DOOM is engineered as an **AI operating layer**, not a single conversational model. The system combines cognitive reasoning, persistent structured memory, a derived personal world model, durable task execution with verification and recovery, capability-based model routing, governance and privacy fencing, observational proactive intelligence, and bounded external READ connectors.

Voice and text remain interfaces. They are not the architecture.

Inspired in spirit by the idea of a personal operating intelligence—not positioned as a consumer “JARVIS clone,” an LLM wrapper, or a chatbot with a large tool list.

---

## Engineering status

| | |
|---|---|
| **Current release** | **v6.2.2** (`5b670def55d7f6680c09829f0455c8e75ca512a7`) |
| **Branch** | `DOOM-V5.2` |
| **Architecture** | Personal AI Operating System |
| **Core** | Cognition + memory + world model + autonomous task engine |
| **Released proactive line** | V6.1 INFORM foundation; V6.2.1 emitters; V6.2.2 vault + Calendar/GitHub READ |
| **Current development** | **V6.2.3** — Gmail READ + commitment intelligence (working tree; **not released**) |

Status labels used below: **RELEASED**, **IN DEVELOPMENT**, **PLANNED**.

---

## Architectural definition

DOOM is an autonomous intelligence operating layer that maintains persistent state about the user, executes bounded multi-step work, verifies outcomes against observed evidence, learns from verified experience, observes relevant external signals, and expands its ability to operate computer and digital environments only inside explicit authority boundaries.

---

## Design principles

| Principle | Meaning |
|---|---|
| Persistent state | Memory and task state survive individual conversations |
| Goal-oriented execution | Work is a durable workflow, not a one-shot reply |
| Verification | Completion requires observed evidence, not a model claiming “done” |
| Recovery | Interrupted or failed work can pause, resume, retry, or replan |
| Bounded autonomy | Actions sit inside explicit authority and risk classes |
| World modeling | External observations become structured, derived world state |
| Evidence | Confidence is tied to provenance, not rhetoric |
| Privacy | PRIVATE/SENSITIVE material is isolated from HUD, embeddings, and telemetry content |
| Observability | System activity is traceable as metadata, not as a second prompt store |
| Capability growth | New layers land on frozen architectural foundations and release gates |

---

## System architecture

```
                         USER / ENVIRONMENT
                                  |
               +------------------+------------------+
               |                  |                  |
             Voice              Text           External signals
               |                  |                  |
               +------------------+------------------+
                                  |
                                  v
                      +---------------------------+
                      |        DOOM CORE          |
                      |  Request understanding    |
                      |  Cognitive engine         |
                      |  Decision / planning      |
                      +-------------+-------------+
                                    |
               +--------------------+--------------------+
               |                    |                    |
               v                    v                    v
         Memory system        World model          Model router
               |                    |                    |
               |                    |              Providers
               +--------------------+--------------------+
                                    |
                                    v
                      +---------------------------+
                      |       TASK ENGINE         |
                      | Plan / execute / verify   |
                      | Recover / resume / retry  |
                      +-------------+-------------+
                                    |
                  +-----------------+-----------------+
                  |                 |                 |
                  v                 v                 v
                Tools         OS / computer     External systems
                  |           (invoked)         (READ connectors)
                  +-----------------+-----------------+
                                    |
                                    v
                      +---------------------------+
                      | Security / governance     |
                      | Risk / approval / fence   |
                      +-------------+-------------+
                                    |
                                    v
                      +---------------------------+
                      | Observability / audit     |
                      +---------------------------+
```

PostgreSQL is the authoritative store for memory records, task checkpoints, proactive outbox rows, connector metadata, and (in development) commitments. Vector indexes are derived and generation-synchronized. WorldSnapshot is a TTL cache, not a second database of record.

---

## Intelligence stack

| Line | Role | Status |
|---|---|---|
| V3 | Autonomous task orchestration, resume, partial success | RELEASED |
| V4 | Cognitive execution loop | RELEASED |
| V5 | Persistent memory, retrieval, lifecycle, projects, experience, governance, routing, observability | RELEASED |
| V6 | Proactive intelligence and external world observation | RELEASED through v6.2.2; V6.2.3 IN DEVELOPMENT |
| V7 | Computer / OS agent as a unified ACT surface | PLANNED |

V4 cognition (implemented) is a bounded loop:

`UNDERSTAND → REASON → DECIDE → PLAN → ACT → OBSERVE → EVALUATE → REFLECT → REPLAN`

The cognitive `ACT` stage is **in-process tool and task execution**. It is not V6.3 **ACT** (authorized external side effects). Raw chain-of-thought is not treated as a user- or log-facing artifact; summaries are fenced.

---

## Persistent memory

Memory is not chat history. The V5 stack stores **structured `memory_records`** with lifecycle, provenance, privacy class, generation, importance, and confidence.

**Lifecycle (implemented):** `PENDING_VERIFICATION` → `ACTIVE` → `SUPERSEDED` / `ARCHIVED` / `DELETED`. Transitions are transactional with append-only lifecycle audit.

Also implemented: semantic embeddings with hybrid ranking, freshness, evidence attachments, relationship DAG, supersession, project context, experiences, lessons, strategies, and cross-project transfer under governance.

**Invariants:** PostgreSQL is authoritative. Vector state follows generation-safe outbox synchronization. Retrieval is read-only with respect to importance/confidence evolution (`dI/dN_retrieval = 0`). SENSITIVE content is kept out of embeddings and model context where policy requires it.

---

## Personal world model

| | Memory | World model |
|---|---|---|
| Role | What DOOM retains as canonical records | Derived view of what is currently relevant |
| Authority | PostgreSQL records | Non-authoritative |
| Examples | Decisions, facts, experiences | Projects, tasks, connector health, commitments, host/circuit signals |

`WorldSnapshot` is **derived, cacheable, TTL-bound, and non-authoritative**. It must not become a second canonical memory store.

V6.2.3 (in development) adds PRIVATE commitment rows and unread counts into the snapshot **without** subjects or email bodies.

---

## Autonomous task execution

The task engine persists checkpoints and step state. A provider saying the work is finished is **not** sufficient; verification and recovery paths exist.

```
CREATED → PLANNING → RUNNING → VERIFYING → COMPLETED

         ↘ PAUSED
         ↘ WAITING_FOR_APPROVAL
         ↘ PARTIAL_SUCCESS
         ↘ FAILED
         ↘ CANCELLING → CANCELLED
         ↘ RECOVERY_REQUIRED
```

Implemented properties include durable tasks, retries, provider failover, cancellation drain, approval wait, and V6.2.1 observational emitters (`TASK_STATUS` / `MEMORY_LIFECYCLE`) into the proactive outbox when the feature flag is on.

---

## Security, governance, and bounded autonomy

DOOM is not designed for unrestricted autonomous action. Authority is intended to progress:

`OBSERVE → ANALYZE → INFORM → SUGGEST → PREPARE → ASK → ACT`

**Current V6.2 work remains below ACT.** V6.1 delivers INFORM-only HUD obligations. SUGGEST / PREPARE / ASK / ACT are **PLANNED**. Dashboard task-approval binding (session, CSRF, ownership, param hash) is a documented **release blocker** for ASK—not a workaround.

Also implemented: risk-aware governance gates, privacy classes (`NORMAL` / `PRIVATE` / `SENSITIVE`), DATA_ONLY payload fencing, DPAPI connector vault (`secret_ref` in PostgreSQL, not raw tokens), allowlisted HTTP (GET plus a single OAuth token POST URL), tool invocation as an explicit registry, ABSTAIN/fail-closed on injection and credential-shaped content, and telemetry redaction.

The system may refuse to act when evidence or authority is insufficient.

---

## Proactive intelligence

V6 is the shift from a purely reactive assistant to **bounded environmental intelligence**.

Intended pipeline (only some stages are live):

`SIGNAL → NORMALIZE → DEDUPE → CORRELATE → CONTEXTUALIZE → MEMORY? → SIGNIFICANCE → PREDICT → RISK → INTERVENTION → POLICY → DELIVER → OUTCOME → LEARN`

| Stage | Status |
|---|---|
| Typed ingest, fence, leased outbox, INFORM delivery | RELEASED (V6.1) |
| Internal emitters (task / lifecycle) | RELEASED (V6.2.1) |
| Calendar / GitHub READ, vault, `external_facts` | RELEASED (V6.2.2) |
| Gmail READ + commitment extraction | **IN DEVELOPMENT (V6.2.3)** |
| Prediction, SUGGEST, PREPARE, ASK, bounded LLM drafts | PLANNED |
| ACT / SEND / MERGE / Gmail write | PLANNED (not V6.2) |

Flags default **off** (`PROACTIVE_ENABLED`, connector flags). Global proactive off implies **zero** connector HTTP. Email-derived facts are PRIVATE and do not auto-INFORM. Worker code does not call `process_request` and does not write `memory_records`.

---

## External intelligence layer

READ-only connectors share one HTTP boundary (`SafeHttp`), one vault, and the existing poller/worker.

| Connector | Access | Purpose | Privacy | Status |
|---|---|---|---|---|
| Google Calendar | READ `events.list` | Schedule context | Controlled / fenced | RELEASED (v6.2.2) |
| GitHub | READ notifications / issues | Development signals | Controlled / fenced | RELEASED (v6.2.2) |
| Gmail | READ metadata + bounded snippet | Commitment extraction | **PRIVATE** | IN DEVELOPMENT (V6.2.3) |

PostgreSQL stores `secret_ref` and fenced facts—not access tokens or refresh tokens. No V6.2 connector WRITE, send, label, merge, or calendar mutation.

---

## Model and capability routing

Orchestration is separate from any one vendor. The router matches task **capability** (reasoning, coding, multi-step, vision, web, conversation, offline) to registered providers, then applies enablement, credentials, health/circuit state, and cost-tier tie-breaks.

Registered providers include local Ollama, NVIDIA NIM, Groq, OpenAI, Gemini, AWS Bedrock, and a zero-config fallback. Exact live model IDs are configuration, not README contracts. Throughput slogans are omitted.

---

## Observability

V5.3.7.4-style operational events: correlation identifiers, bounded attributes, provider/task/proactive metadata, redaction, and fail-closed drop of forbidden keys (prompts, bodies, tokens, chain-of-thought).

Telemetry is **not** a store for email snippets, memory content, or credentials.

---

## Repository architecture

Verified top-level layout (major subsystems):

```
DOOM/
├── doom.py                 # Process entry (voice/text loop)
├── install.py              # Dependency install from core/requirements.txt
├── config_example.txt      # Environment template (copy to .env)
├── core/                   # Orchestrator, cognition, task engine, router, reliability
├── memory/                 # Structured memory, retrieval, lifecycle, projects, governance
├── proactive/              # V6 outbox, worker, connectors, vault, snapshot
├── models/                 # LLM provider adapters
├── database/               # PostgreSQL manager and additive schema
├── observability/          # Event schema, redactor, bus, metrics
├── dashboard/              # FastAPI control surface / HUD
├── ide/                    # Companion browser editor (not the V7 OS agent)
├── tools/                  # Invoked tool implementations
└── test_doom.py            # Plus versioned test_v*.py suites at repository root
```

V6.2.3 working-tree additions (not in tag `v6.2.2`): `proactive/connectors/email_gmail.py`, `proactive/commitments.py`, `test_v623_email_commitments.py`.

---

## Engineering characteristics

- Transactional memory lifecycle and idempotent audit
- Generation-safe vector outbox (`FOR UPDATE SKIP LOCKED`)
- Leased proactive workers, retry, dead-letter
- Deterministic status machines for tasks and memory
- Evidence- and experience-derived strategy inputs
- DATA_ONLY fencing and privacy classes
- Capability-preserving provider failover
- Metadata-only observability
- Versioned tests and forensic/release gates

Desktop automation tools (apps, terminal, files, vision) exist as **invoked tools**. That is not the planned V7 Computer/OS Agent.

---

## Why this architecture

DOOM is aimed at properties that a conventional chat pane does not provide by itself:

1. Persistent user and world state  
2. Long-running workflows with checkpoints  
3. Verified execution  
4. Recovery after failure  
5. Evidence-based learning from experiences  
6. Proactive, flag-gated environmental observation  
7. Explicit authority boundaries  
8. Eventual computer/OS agency (planned, bounded)  
9. Privacy-aware world modeling  
10. Strategy adaptation from verified history  

The goal is a persistent intelligence system that can operate **inside** a user’s environment—not a larger model card.

---

## Capability matrix

| Capability | Status |
|---|---|
| Conversational / voice / text interface | RELEASED |
| Multi-provider capability routing | RELEASED |
| Cognitive loop | RELEASED |
| Persistent structured memory | RELEASED |
| Semantic retrieval + hybrid ranking | RELEASED |
| Memory lifecycle + vector sync | RELEASED |
| Evidence / confidence | RELEASED |
| Project + experience intelligence | RELEASED |
| Governance | RELEASED |
| Observability | RELEASED |
| Proactive INFORM (flag-gated) | RELEASED (V6.1) |
| Calendar READ | RELEASED (v6.2.2) |
| GitHub READ | RELEASED (v6.2.2) |
| Gmail READ | IN DEVELOPMENT (V6.2.3) |
| Commitment intelligence | IN DEVELOPMENT (V6.2.3) |
| Prediction | PLANNED (V6.2.4) |
| SUGGEST | PLANNED (V6.2.5) |
| PREPARE | PLANNED (V6.2.6) |
| ASK (authenticated approval) | PLANNED (V6.2.7) |
| Bounded LLM drafts | PLANNED (V6.2.8) |
| ACT (external side effects) | PLANNED (V6.3) |
| Computer / OS Agent | PLANNED (V7) |

---

## Development roadmap

### Completed (released)

| Version | Scope |
|---|---|
| V3.3 | Reliability, resume, partial success |
| V4.x | Cognitive core |
| V5.1 | Memory foundation |
| V5.2.x | Semantic retrieval, hybrid ranking, context fencing |
| V5.3.x | Lifecycle, vector sync, relationships, freshness, projects/experiences, governance, provider routing, observability |
| V6.1.0 | Proactive INFORM foundation |
| V6.2.1 | Task/lifecycle signal emitters |
| V6.2.2 | External READ connectors + DPAPI vault |

### Current (not tagged)

| Version | Scope | Status |
|---|---|---|
| V6.2.3 | Email READ + commitment intelligence | IN DEVELOPMENT |

### Planned

| Version | Scope |
|---|---|
| V6.2.4 | Evidence + prediction |
| V6.2.5 | SUGGEST |
| V6.2.6 | PREPARE |
| V6.2.7 | ASK |
| V6.2.8 | Bounded LLM assistance |
| V6.3 | ACT |
| V7 | Computer / OS Agent |

---

## Examples

**Task execution (released path)**  
“Analyze the repository test output and report only failures that were actually observed.”

**Memory (released path)**  
“Store the architecture decision for the authentication service as a structured memory, not as chat residue.”

**Verification (released path)**  
“Run the check, verify the resulting state, and do not treat the model’s summary as proof.”

**World model (V6.2.3 in development)**  
“What open commitments are approaching?” — depends on email/calendar facts and flags; not a released Gmail product.

**Proactive INFORM (released, flag-gated)**  
Observational HUD cards for high-significance INTERNAL signals. Not email send, not ACT.

**PLANNED — do not treat as available:** autonomous email send, merge, ASK execution, prediction engine, V7 OS agent.

---

## Getting started

### Prerequisites

- Python 3.8+ (installer enforces this)
- Windows is the primary development/runtime target (DPAPI vault is Windows-only)
- PostgreSQL for memory, tasks, and proactive tests (typical local `localhost:5432`)
- Optional: microphone/speakers for the voice loop; Ollama for offline models

### Install

```bash
git clone https://github.com/SujalPawar17/DOOM-AI-V1.git
cd DOOM-AI-V1
python install.py
```

Or:

```bash
pip install -r core/requirements.txt
```

### Environment

Copy `config_example.txt` to `.env` and set keys you actually use. Example shape (no real secrets):

```ini
USER_NAME=your_name
ASSISTANT_NAME=DOOM
GROQ_API_KEY=
PROACTIVE_ENABLED=false
PROACTIVE_CALENDAR_ENABLED=false
PROACTIVE_GITHUB_ENABLED=false
PROACTIVE_EMAIL_ENABLED=false
```

Never commit `.env`. Connector tokens belong in the DPAPI vault (`secret_ref` in PostgreSQL only).

### Tests

```bash
python test_doom.py
```

Versioned suites (PostgreSQL required for most): `test_v61_proactive_foundation.py`, `test_v62_emitters.py`, `test_v62_connectors.py`, `test_v532_transaction_engine.py`, and others at the repository root. V6.2.3 tests exist in the working tree as `test_v623_email_commitments.py` and are not part of tag `v6.2.2`.

### Launch

```bash
python doom.py
```

Dashboard: `python dashboard/run_dashboard.py` (typically `http://localhost:8000`). Companion IDE: `python ide/run_ide.py`.

---

## Security notes

- `.env` and local vault files must stay untracked
- Connector HTTP is allowlisted; Gmail paths are further restricted (V6.2.3)
- PRIVATE email facts are not ingested as INFORM signals
- OTP/telemetry forbids token, authorization, body, and prompt attributes
- Approval endpoints must not be treated as authenticated until V6.2.7 binds session, CSRF, ownership, and param hash
- `core/automation.py` / unrestricted OS automation is **not** the supported ACT path

---

## License

No `LICENSE` file is present in this tree at the time of writing; do not assume a SPDX identifier from this README.

Built as an independent AI systems engineering project.

---

*Tag `v6.2.2` is the last released proactive connector line. Gmail and commitments are development-line work until an explicit V6.2.3 release.*
