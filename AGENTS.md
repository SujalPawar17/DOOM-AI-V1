# DOOM V2 — Agent Instructions

## Quick Commands
| Action | Command |
|--------|---------|
| Install deps | `python install.py` |
| Run tests | `python test_doom.py` |
| Run assistant | `python doom.py` |
| Install deps manually | `pip install -r core/requirements.txt` |

## Architecture Overview
- **Entry points**: `doom.py` (voice loop), `test_doom.py` (full test suite)
- **Core orchestrator**: `core/orchestrator.py` — 8-step AI OS lifecycle
- **Model router**: `core/model_router.py` — priority: Groq → NIM → Bedrock → OpenAI → Gemini → Ollama → Fallback
- **Tool registry**: `core/tool_registry.py` — 45 tools via `tools/__init__.py::ALL_TOOLS`
- **Memory 2.0**: `memory/__init__.py` — user_profile, short_term, episodic, semantic
- **Database**: `database/postgres_db.py` — PostgreSQL (auto-creates `Doom` DB, 5 tables)

## Key Conventions
- **Python 3.8+** required
- **Environment**: `.env` loaded via `python-dotenv` (copy from `config_example.txt`)
- **No pyproject.toml / setup.py** — dependencies only in `core/requirements.txt`
- **Async voice loop** in `doom.py` uses global `is_busy` flag for reentrancy protection
- **Wake phrases**: "hey doom", "jarvis", "hello doom", "doom" (see `core/commands.py`)

## Testing
- Single test file: `test_doom.py` — runs 7 sections (imports, memory, tools, router, orchestrator, voice, postgres)
- Run with: `python test_doom.py` (exits 0 on pass, 1 on fail)
- PostgreSQL must be running for DB tests (uses `.env` credentials)

## Model Providers (priority order)
1. **Groq** — ultra-fast LLaMA 3.3 70B (requires `GROQ_API_KEY`)
2. **NVIDIA NIM** — Nemotron 3 Ultra / Llama 3.1 (requires `NVIDIA_API_KEY`, `NVIDIA_NIM_MODEL`, `NVIDIA_NIM_BASE_URL`)
3. **Bedrock** — Claude 4.6 / Haiku (requires AWS creds in `.env`)
4. **OpenAI** — GPT-4o (requires `OPENAI_API_KEY`)
5. **Gemini** — 2.0 Flash
6. **Ollama** — local `llama3` at `http://localhost:11434`
7. **Fallback** — zero-config rule engine (always available)

## Database Schema (auto-created)
- `user_profiles` — Sujal's persistent profile
- `episodic_memory` — action episodes with goals, plans, tools, outcomes
- `semantic_facts` — key-value facts
- `system_telemetry` — CPU/RAM/disk snapshots
- `command_logs` — user commands + responses + latency

## Common Pitfalls
- **Audio deps** (`pyaudio`, `pygame`) may fail on Windows — install via `pipwin` if needed
- **PostgreSQL** must be running locally (default: `localhost:5432`, user `postgres`, pass `Admin@123`)
- **Ollama** optional but recommended for offline mode (`ollama pull llama3`)
- **Voice** defaults to Edge-TTS `en-GB-RyanNeural` (free, no key needed)

## File Structure
```
DOOM/
├── doom.py                 # Main voice assistant entry
├── test_doom.py            # Full test suite
├── install.py              # Installer (deps + .env)
├── core/                   # Orchestrator, router, tools, memory, voice
├── models/                 # LLM providers (Groq, NIM, OpenAI, Bedrock, etc.)
├── tools/                  # 45 tool implementations
├── memory/                 # Memory 2.0 (profile, short/episodic/semantic)
├── database/               # PostgreSQL manager
├── .env                    # Runtime config (API keys, DB creds)
└── core/requirements.txt   # All Python dependencies
```