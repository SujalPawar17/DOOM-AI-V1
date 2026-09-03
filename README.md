# 🤖 DOOM V2 — Personal AI Operating System (JARVIS-Level)

<p align="center">
  <b>An autonomous, voice-and-acoustic-activated AI desktop operating system inspired by Iron Man's JARVIS.</b><br>
  Engineered with multi-provider LLM routing, acoustic double-clap wake detection, studio-grade multilingual neural voices, real-time computer vision, OS automation, memory persistence, and holographic telemetry dashboard.
</p>

---

## 🌟 Key Capabilities

### 👏 1. Acoustic Double-Clap & Wake Detection
- **Acoustic Sensor:** Dynamic two-phase ambient calibration and double-clap acoustic peak recognition (RMS-based, hardware-free).
- **Multilingual Voice Wake Words:** Responds seamlessly to `"Hey DOOM"`, `"Jarvis"`, `"Hello DOOM"`, `"DOOM"` across English, Hindi, Marathi, Tamil, Telugu, and more.

### 🎙️ 2. Cinematic British Neural Voice & Multilingual Engine
- **Default Engine:** Microsoft Edge-TTS with authentic British JARVIS cadence (`en-GB-RyanNeural`).
- **Global Reach:** Native pronunciation and script support for 11 languages (English, Hindi, Marathi, Tamil, Telugu, Kannada, Malayalam, Gujarati, Bengali, Punjabi, Urdu).
- **Interactive Speech Control:** Hotkey listener (Ctrl+S / interrupt) to stop speaking instantly on user command.

### 🧠 3. Intelligent Multi-Model Router
Intelligent fallback and workload prioritization across leading AI providers:
1. **Groq Cloud** — Ultra-low latency LLaMA 3.3 70B & OSS models (@ ~500 tokens/sec)
2. **NVIDIA NIM** — Nemotron 3 Ultra & LLaMA 3.1
3. **Amazon Bedrock** — Claude 3.5 Sonnet / Claude Sonnet 4.6 / Nova Pro
4. **OpenAI** — GPT-4o
5. **Google Gemini** — 2.0 Flash
6. **Local Ollama** — Offline local LLM support (`llama3`)
7. **Autonomous Fallback Engine** — Zero-config rule-based executor that functions 100% offline without API keys.

### 🛠️ 4. Autonomous 32-Tool Arsenal
- **Desktop Automation:** Launch, switch, and close applications, take screenshots, optimize system resources, workstation locking.
- **Media Control:** YouTube streaming, Spotify, system media transport controls (play/pause/volume).
- **Dynamic Coding & Terminal:** Generates and executes Python code dynamically in memory, full PowerShell/CMD terminal command runner.
- **Computer Vision & Gestures:** Real-time webcam hand gesture detection (palm, peace, fist), screen brightness and pixel telemetry.
- **Developer Suite:** Automated project scaffolder (FastAPI, React/Vite, Next.js, Flask), REST API benchmarking & testing.

### 💾 5. Memory 2.0 & PostgreSQL Persistence
- **Four Memory Tiers:** User Profile, Multi-turn Short-Term Buffer, Episodic Action Memory (goals, plans, tools, outcomes), and Semantic Facts Knowledge Base.
- **Dual-Persistence:** Fast JSON disk storage synchronized with relational PostgreSQL tables for long-term telemetry and command audits.

### 📊 6. Holographic Control HUD & Built-in IDE
- **FastAPI Control Dashboard:** Live WebSocket telemetry (CPU, RAM, Disk, Network throughput) and command console.
- **Integrated Browser IDE:** Lightweight code editor directly accessible from the DOOM ecosystem.

---

## 🏗️ Architecture Overview

```
User Voice / Clap / Text Input
           │
           ▼
┌──────────────────────────────────────┐
│       DOOM Voice & Sound Engine      │
│  (RMS Double-Clap + Speech Rec STT)  │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│           DOOM Orchestrator          │
│   (9-Step Lifecycle & Goal Planner)  │
└──────┬────────────────────────┬──────┘
       │                        │
       ▼                        ▼
┌──────────────┐       ┌─────────────────┐
│ Model Router │       │ Tool Registry   │
│ (Groq/Bedrock│       │ (32 Automation  │
│ /NIM/Fallback│       │ & OS Tools)     │
└──────┬───────┘       └────────┬────────┘
       │                        │
       └───────────┬────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│        Memory 2.0 & PostgreSQL       │
│ (Profile / Episodic / Semantic / DB) │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│      Cinematic Voice Synthesizer     │
│   (Edge-TTS RyanNeural + HUD Sync)   │
└──────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
DOOM/
├── doom.py                  # Main voice assistant loop and acoustic listener
├── doom_background.pyw      # Headless background tray runner
├── test_doom.py             # 7-section automated test suite
├── install.py               # Dependency installer and configuration setup
├── core/                    # Central AI OS engine
│   ├── orchestrator.py      # 8/9-step AI OS execution pipeline
│   ├── model_router.py      # Multi-provider model selection
│   ├── sound_detector.py    # Acoustic double-clap sensor
│   ├── cinematic_voice.py   # Edge-TTS voice synthesis engine
│   ├── language_manager.py  # 11-language STT/TTS coordination
│   ├── planner.py           # Intent classification & task planning
│   └── verifier.py          # Voice output polishing and reasoning cleanup
├── models/                  # LLM provider drivers (Groq, Bedrock, NIM, OpenAI, Ollama, Fallback)
├── tools/                   # 32 modular tools (System, Filesystem, Terminal, Web, Vision, Coding)
├── memory/                  # Memory 2.0 (Profile, Short-Term, Episodic, Semantic)
├── database/                # PostgreSQL manager & relational telemetry schemas
├── dashboard/               # FastAPI Holographic HUD server & static frontend
└── ide/                     # Built-in browser IDE environment
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Python:** 3.8 to 3.12
- **Operating System:** Windows 10/11
- **Microphone:** For voice commands and double-clap acoustic wake
- *(Optional)* **PostgreSQL:** Running locally at `localhost:5432` for persistent telemetry

### 2. Installation
Clone the repository and install the dependencies:
```bash
git clone https://github.com/your-username/DOOM.git
cd DOOM
python install.py
```
Or manually install dependencies:
```bash
pip install -r core/requirements.txt
```

### 3. Configure Environment Variables
Copy the template `.env.example` to `.env`:
```bash
cp .env.example .env
```
Open `.env` and configure your preferences:
```ini
USER_NAME=Sujal
ASSISTANT_NAME=DOOM
PREFERRED_IDE=antigravity

# Optional Cloud LLM Keys (Works 100% offline even without keys)
GROQ_API_KEY=your_groq_key_here
```

### 4. Run Self-Diagnostics
Verify all tools, memory subsystems, and audio drivers:
```bash
python test_doom.py
```

### 5. Launch DOOM
```bash
python doom.py
```
- **Wake Up:** Double-clap 👏 or say **"Hey DOOM"** / **"Jarvis"**!
- **Launch HUD Dashboard:** Run `python dashboard/run_dashboard.py` (open `http://localhost:8000`)
- **Launch IDE:** Run `python ide/run_ide.py`

---

## 🎯 Example Voice Commands

| Domain | Voice Command Example | Action |
|---|---|---|
| **Acoustic Wake** | *(Double-clap hands)* | Awakens DOOM instantly with acoustic audio confirmation |
| **System Control** | `"Open VS Code"` / `"Open Chrome"` / `"Lock workstation"` | Launches target application or secures PC |
| **Workstation Modes** | `"Activate Code Mode"` / `"Give me a daily briefing"` | Launches IDE, checks git status, summarizes tasks |
| **Coding & Math** | `"Write a python script to test prime numbers and run it"` | Generates script, saves to disk, executes, and reports result |
| **Media & Music** | `"Play Hans Zimmer Interstellar on YouTube"` | Searches and streams audio directly |
| **Vision & Screen** | `"Scan hand gestures"` / `"Analyze screen"` | Activates camera gesture detection or screen analysis |
| **Information** | `"What's the weather in Mumbai?"` / `"Check AAPL stock price"` | Live web search and structured intelligence retrieval |
| **Memory Recall** | `"Remember that our deployment server is at 10.0.0.1"` | Persists fact into semantic and relational memory |

---

## 🛡️ Security & Privacy
- Sensitive credentials (`.env`), telemetry caches, and personal memory state files are strictly ignored via `.gitignore`.
- Always verify that `.env` is **never** checked into version control.

---

## 📄 License
Open source under the [MIT License](LICENSE). Crafted with ❤️ by Sujal.