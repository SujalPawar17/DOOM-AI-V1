import os
import json
import asyncio
import time
import psutil
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database.postgres_db import postgres_manager
from core.orchestrator import doom_core
from core.model_router import model_router
from tools import ALL_TOOLS
from tools.workstation_modes import CodeModeTool, DailyBriefingTool, StandupReportTool, LockdownTool, ScreenVisionTool

app = FastAPI(title="DOOM V2 — Holographic Control HUD", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients: List[WebSocket] = []

class CommandRequest(BaseModel):
    goal: str
    mode: str = "text"

# ─────────────────────────────────────────────────────────────────────────────
# Helper: Gather live system telemetry
# ─────────────────────────────────────────────────────────────────────────────
def get_live_telemetry() -> Dict[str, Any]:
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        process_count = len(psutil.pids())
        boot_time = psutil.boot_time()
        uptime_seconds = int(time.time() - boot_time)

        telemetry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "cpu_percent": cpu,
            "memory_percent": mem.percent,
            "memory_used_gb": round(mem.used / (1024**3), 2),
            "memory_total_gb": round(mem.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "bytes_sent_mb": round(net.bytes_sent / (1024**2), 2),
            "bytes_recv_mb": round(net.bytes_recv / (1024**2), 2),
            "processes": process_count,
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s"
        }

        if postgres_manager.is_connected():
            postgres_manager.log_telemetry(
                cpu_percent=cpu,
                ram_percent=mem.percent,
                disk_percent=disk.percent,
                raw_metrics={"network_recv_mb": telemetry["bytes_recv_mb"], "network_sent_mb": telemetry["bytes_sent_mb"]}
            )

        return telemetry
    except Exception as e:
        return {"error": str(e), "cpu_percent": 0, "memory_percent": 0, "disk_percent": 0, "processes": 0}

# ─────────────────────────────────────────────────────────────────────────────
# REST Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/status")
async def get_system_status():
    """Returns general DOOM V2 state, DB health, and Model Router state."""
    db_online = postgres_manager.is_connected()
    db_counts = postgres_manager.get_table_counts() if db_online else {}
    provider_status = model_router.get_provider_status()

    return {
        "status": "OPERATIONAL",
        "system": "DOOM V2 Personal AI OS",
        "creator": "Sujal",
        "database": {
            "connected": db_online,
            "database_name": getattr(postgres_manager, "dbname", "Doom"),
            "tables": db_counts
        },
        "models": provider_status,
        "tools_count": len(ALL_TOOLS),
        "telemetry": get_live_telemetry()
    }

@app.get("/api/memory/episodes")
async def get_recent_episodes(limit: int = 15):
    """Fetches recent action episodes from PostgreSQL."""
    if not postgres_manager.is_connected():
        return {"episodes": [], "source": "offline"}
    episodes = postgres_manager.get_recent_episodes(limit=limit)
    return {"episodes": episodes, "source": "postgres", "count": len(episodes)}

@app.get("/api/memory/facts")
async def get_semantic_facts():
    """Fetches long-term semantic knowledge facts from PostgreSQL."""
    if not postgres_manager.is_connected():
        return {"facts": [], "source": "offline"}
    query = "SELECT key, value, category, updated_at FROM semantic_facts ORDER BY updated_at DESC LIMIT 50;"
    results = postgres_manager.execute_query(query)
    facts = []
    if results and isinstance(results, list):
        for r in results:
            if "error" not in r:
                facts.append({
                    "category": r.get("category", "general"),
                    "key": r.get("key", ""),
                    "value": str(r.get("value", "")),
                    "updated_at": str(r.get("updated_at", ""))
                })
    return {"facts": facts, "source": "postgres", "count": len(facts)}

@app.get("/api/logs")
async def get_command_logs(limit: int = 20):
    """Fetches command audit logs with latency benchmarks from PostgreSQL."""
    if not postgres_manager.is_connected():
        return {"logs": []}
    query = "SELECT id, user_command, response_text, tools_used, latency_ms, created_at FROM command_logs ORDER BY created_at DESC LIMIT %s;"
    results = postgres_manager.execute_query(query, (limit,))
    logs = []
    if results and isinstance(results, list):
        for r in results:
            if "error" not in r:
                tools = r.get("tools_used") or []
                tools_str = ", ".join(tools) if isinstance(tools, list) else str(tools)
                logs.append({
                    "id": r.get("id"),
                    "user_input": r.get("user_command", ""),
                    "response": r.get("response_text", ""),
                    "model_used": "GROQ" if os.getenv("GROQ_API_KEY") else "FALLBACK",
                    "tool_called": tools_str if tools_str and tools_str != "[]" else "Direct Intent",
                    "latency_ms": round(float(r.get("latency_ms", 0.0)), 2),
                    "status": "SUCCESS",
                    "created_at": str(r.get("created_at", ""))
                })
    return {"logs": logs, "count": len(logs)}

@app.get("/api/tools")
async def list_tools():
    """Returns the full catalogue of 30 autonomous tools."""
    tools_data = []
    for t in ALL_TOOLS:
        tools_data.append({
            "name": t.name,
            "description": t.description,
            "category": t.name.split("_")[0] if "_" in t.name else "general"
        })
    return {"tools": tools_data, "total": len(tools_data)}

@app.post("/api/command")
async def execute_command(req: CommandRequest):
    """Executes a natural language goal through the DOOM Core Orchestrator."""
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="Empty goal provided")

    start_time = time.time()
    response = doom_core.process_request(req.goal)
    duration_ms = round((time.time() - start_time) * 1000, 2)

    broadcast_data = {
        "type": "command_executed",
        "goal": req.goal,
        "response": response,
        "latency_ms": duration_ms,
        "timestamp": time.strftime("%H:%M:%S")
    }
    for client in connected_clients:
        try:
            asyncio.create_task(client.send_text(json.dumps(broadcast_data)))
        except Exception:
            pass

    return {
        "goal": req.goal,
        "response": response,
        "latency_ms": duration_ms,
        "timestamp": time.strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────────────────────────────────────────
# Workstation Modes Trigger Endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/modes/{mode_name}")
async def trigger_workstation_mode(mode_name: str):
    """Triggers high-level Iron Man workstation modes."""
    mode_map = {
        "code": CodeModeTool(),
        "briefing": DailyBriefingTool(),
        "standup": StandupReportTool(),
        "lockdown": LockdownTool(),
        "vision": ScreenVisionTool()
    }
    tool = mode_map.get(mode_name.lower())
    if not tool:
        raise HTTPException(status_code=404, detail=f"Unknown mode '{mode_name}'")

    res = tool.execute()
    output_text = res.output if hasattr(res, "output") else str(res)

    # Broadcast to all HUDs
    for client in connected_clients:
        try:
            asyncio.create_task(client.send_text(json.dumps({
                "type": "mode_triggered",
                "mode": mode_name,
                "response": output_text,
                "timestamp": time.strftime("%H:%M:%S")
            })))
        except Exception:
            pass

    return {
        "mode": mode_name,
        "response": output_text,
        "success": getattr(res, "success", True)
    }

# ─────────────────────────────────────────────────────────────────────────────
# Edge-TTS Neural Voice Stream (Cached Studio RyanNeural)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/tts")
async def generate_speech(text: str = Query(..., description="Text to synthesize with British Neural Voice")):
    """Streams synthesized British Neural Voice (Edge-TTS en-GB-RyanNeural) as MP3."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")

    import hashlib
    clean_text = text.replace('"', '').replace("'", "").strip()[:400]
    audio_dir = os.path.join(os.path.dirname(__file__), "static", "audio")
    os.makedirs(audio_dir, exist_ok=True)
    
    file_hash = hashlib.md5(clean_text.encode("utf-8")).hexdigest()
    cached_file = os.path.join(audio_dir, f"tts_{file_hash}.mp3")

    if os.path.exists(cached_file) and os.path.getsize(cached_file) > 0:
        with open(cached_file, "rb") as f:
            return Response(content=f.read(), media_type="audio/mpeg")

    try:
        import edge_tts
        communicate = edge_tts.Communicate(clean_text, voice="en-GB-RyanNeural", rate="+10%", pitch="+0Hz")
        await communicate.save(cached_file)

        if os.path.exists(cached_file):
            with open(cached_file, "rb") as f:
                data = f.read()
            return Response(content=data, media_type="audio/mpeg")
    except Exception as e:
        print(f"[TTS ERROR] {e}")

    return JSONResponse({"status": "tts_fallback", "message": "TTS stream error"}, status_code=500)

# ─────────────────────────────────────────────────────────────────────────────
# Music & Ambient DJ Track Presets
# ─────────────────────────────────────────────────────────────────────────────
MUSIC_STATIONS = [
    {
        "id": "synthwave",
        "title": "NEON OVERDRIVE // CYBERPUNK SYNTHWAVE",
        "genre": "Synthwave",
        "url": "https://ice1.somafm.com/vaporwaves-128-mp3",
        "badge": "FOCUS HIGH"
    },
    {
        "id": "lofi",
        "title": "CYBER CHILL // LO-FI CODING BEATS",
        "genre": "Lo-Fi",
        "url": "https://ice2.somafm.com/groovesalad-128-mp3",
        "badge": "FLOW STATE"
    },
    {
        "id": "darkwave",
        "title": "NIGHT CITY PROTOCOL // INDUSTRIAL DARKWAVE",
        "genre": "Dark Cyber",
        "url": "https://ice4.somafm.com/defcon-128-mp3",
        "badge": "INTENSE CODE"
    },
    {
        "id": "ambient",
        "title": "DEEP SPACE ORBIT // AMBIENT SOUNDSCAPES",
        "genre": "Ambient",
        "url": "https://ice2.somafm.com/dronezone-128-mp3",
        "badge": "CALM"
    }
]

@app.get("/api/music/tracks")
async def get_music_tracks():
    """Returns curated ambient coding and synthwave audio stations."""
    return {"tracks": MUSIC_STATIONS}

# ─────────────────────────────────────────────────────────────────────────────
# Morning Routine Scheduler Settings
# ─────────────────────────────────────────────────────────────────────────────
SCHEDULED_BRIEFING_TIME = os.getenv("BRIEFING_TIME", "09:00")
last_briefing_date = None

class BriefingTimeRequest(BaseModel):
    time_str: str  # Format: "HH:MM" e.g. "09:00"

@app.get("/api/settings/briefing_time")
async def get_briefing_time():
    global SCHEDULED_BRIEFING_TIME
    return {"briefing_time": SCHEDULED_BRIEFING_TIME}

@app.post("/api/settings/briefing_time")
async def set_briefing_time(req: BriefingTimeRequest):
    global SCHEDULED_BRIEFING_TIME
    SCHEDULED_BRIEFING_TIME = req.time_str.strip()
    if postgres_manager.is_connected():
        postgres_manager.save_semantic_fact(
            key="scheduled_briefing_time",
            value=SCHEDULED_BRIEFING_TIME,
            category="scheduler"
        )
    return {"briefing_time": SCHEDULED_BRIEFING_TIME, "status": "updated"}

# ─────────────────────────────────────────────────────────────────────────────
# Developer Arsenal Endpoints (Scaffolder, API Tester & Type Generator)
# ─────────────────────────────────────────────────────────────────────────────
class APITestRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: Optional[Dict[str, Any]] = None
    body: Optional[Any] = None

class ScaffoldRequest(BaseModel):
    project_name: str
    template: str = "fastapi_postgres"
    description: Optional[str] = None
    target_dir: Optional[str] = None

class TypeGenRequest(BaseModel):
    json_data: Any
    target_lang: str = "typescript" # "typescript" or "pydantic"

@app.post("/api/dev/test_endpoint")
async def dev_test_endpoint(req: APITestRequest):
    """Executes live HTTP API benchmark test with latency profiling."""
    from tools.developer_tools import APITesterTool
    tester = APITesterTool()
    res = tester.execute(url=req.url, method=req.method, headers=req.headers, body=req.body)
    return res.data or {"output": res.output, "success": res.success}

@app.post("/api/dev/scaffold")
async def dev_scaffold_project(req: ScaffoldRequest):
    """Autonomously scaffolds a production codebase structure with AI customization."""
    from tools.developer_tools import ProjectScaffolderTool
    scaffolder = ProjectScaffolderTool()
    res = scaffolder.execute(project_name=req.project_name, template=req.template, description=req.description, target_dir=req.target_dir)
    return {
        "success": res.success,
        "output": res.output,
        "data": res.data
    }

@app.post("/api/dev/generate_types")
async def dev_generate_types(req: TypeGenRequest):
    """Generates TypeScript interfaces or Python Pydantic models from JSON payload using Groq."""
    from models.groq_provider import GroqProvider
    groq = GroqProvider()

    json_sample = json.dumps(req.json_data, indent=2)[:2000] if not isinstance(req.json_data, str) else req.json_data[:2000]

    prompt = f"""Convert this JSON structure into production-grade {req.target_lang.upper()} code:
JSON:
{json_sample}

Rules:
- If typescript: output clean TypeScript interfaces with strict types.
- If pydantic: output clean Python Pydantic BaseModel classes with Field definitions and type annotations.
- Provide ONLY the code inside ```{req.target_lang.lower()}``` block. No chatty text."""

    result = groq.generate(prompt)
    out = result.text if hasattr(result, "text") else str(result)
    return {"code": out, "lang": req.target_lang}

# ─────────────────────────────────────────────────────────────────────────────
# AI Agent Studio Endpoints (Interactive Multi-Turn IDE Pair Programmer)
# ─────────────────────────────────────────────────────────────────────────────
class AgentChatRequest(BaseModel):
    prompt: str
    model: str = "groq"
    mode: str = "pair_programmer"
    file_path: Optional[str] = None
    file_content: Optional[str] = None

class AgentFileWriteRequest(BaseModel):
    file_path: str
    content: str

class AgentTerminalRunRequest(BaseModel):
    command: str

@app.get("/api/agent/models")
async def get_agent_models():
    """Returns available AI models and active provider states."""
    return {
        "models": [
            {"id": "groq", "name": "⚡ Groq LPU (LLaMA 3.3 70B / GPT-OSS 20B)", "speed": "500 T/S", "status": "ONLINE", "desc": "Sub-second instant reasoning"},
            {"id": "gemini", "name": "🌟 Google Gemini 2.0 Flash", "speed": "FAST", "status": "ONLINE", "desc": "Multimodal & deep code logic"},
            {"id": "ollama", "name": "🦙 Local Ollama (LLaMA 3)", "speed": "LOCAL", "status": "STANDBY", "desc": "100% offline & private"},
            {"id": "auto", "name": "🔄 DOOM Auto-Router", "speed": "DYNAMIC", "status": "ACTIVE", "desc": "Autonomously routes fastest & smartest brain"}
        ]
    }

@app.post("/api/agent/chat")
async def agent_chat_endpoint(req: AgentChatRequest):
    """Executes multi-step AI Agent reasoning, tool execution, and code generation."""
    start_time = time.time()

    try:
        def _extract_text(result):
            """Safely extract string from LLMResponse or plain string."""
            if result is None:
                return ""
            if hasattr(result, "text"):
                return result.text or ""
            if hasattr(result, "__await__"):  # coroutine guard
                return "[DOOM] Async response detected — use auto mode"
            return str(result)

        # 1. System persona based on mode
        mode_instructions = {
            "pair_programmer": "You are DOOM V2, Boss Sujal's Senior AI Pair Programmer. Write precise, production-grade code with step-by-step reasoning. Always wrap code in ```language``` fenced blocks.",
            "architect": "You are DOOM V2 Senior Systems Architect. Design scalable architectures, database schemas, and Docker setups. Wrap all config/code in ```language``` fenced blocks.",
            "debugger": "You are DOOM V2 Autonomous Bug Hunter. Analyze stack traces, identify root causes, and write instant verified patches. Wrap all fixes in ```language``` fenced blocks."
        }
        system_prompt = mode_instructions.get(req.mode, mode_instructions["pair_programmer"])

        # 2. Build prompt with optional file context
        full_prompt = req.prompt
        if req.file_path and req.file_content:
            full_prompt = (
                f"FILE CONTEXT: [{req.file_path}]\n"
                f"```\n{req.file_content[:4000]}\n```\n\n"
                f"USER REQUEST: {req.prompt}"
            )

        # 3. Route to selected model
        selected_model_name = req.model.lower()
        raw_response = ""
        steps = [
            {"type": "plan", "title": "Analyzing Request & Code Context", "desc": f"Mode: {req.mode.upper()} | Model: {req.model.upper()}"}
        ]

        try:
            if selected_model_name == "gemini":
                from models.gemini_provider import GeminiProvider
                provider = GeminiProvider()
                steps.append({"type": "tool", "title": "Invoked Model", "desc": "Google Gemini 2.0 Flash Engine"})
                raw_response = _extract_text(provider.generate(full_prompt, system_prompt=system_prompt))
            elif selected_model_name == "ollama":
                try:
                    from models.ollama_provider import OllamaProvider
                    provider = OllamaProvider()
                    steps.append({"type": "tool", "title": "Invoked Model", "desc": "Local Ollama LLaMA 3"})
                    raw_response = _extract_text(provider.generate(full_prompt, system_prompt=system_prompt))
                except Exception:
                    from models.groq_provider import GroqProvider
                    provider = GroqProvider()
                    steps.append({"type": "tool", "title": "Fallback Model", "desc": "Groq LPU (Ollama unavailable)"})
                    raw_response = _extract_text(provider.generate(full_prompt, system_prompt=system_prompt))
            elif selected_model_name == "auto":
                steps.append({"type": "tool", "title": "DOOM Auto-Router", "desc": "Autonomous router selected Groq 500 T/S"})
                from models.groq_provider import GroqProvider
                provider = GroqProvider()
                raw_response = _extract_text(provider.generate(full_prompt, system_prompt=system_prompt))
            else:  # Default: Groq
                from models.groq_provider import GroqProvider
                provider = GroqProvider()
                steps.append({"type": "tool", "title": "Invoked Model", "desc": "Groq LPU (GPT-OSS 20B @ 500 T/S)"})
                raw_response = _extract_text(provider.generate(full_prompt, system_prompt=system_prompt))

            if not raw_response.strip():
                raw_response = "[DOOM] Model returned an empty response. Please try again."

        except Exception as model_err:
            print(f"[AGENT STUDIO] Model error: {model_err}")
            raw_response = f"[DOOM] Model invocation error: {model_err}"

        duration_ms = round((time.time() - start_time) * 1000, 2)

        # 4. Extract code blocks
        import re
        code_blocks = []
        code_matches = re.findall(r'```([a-zA-Z0-9_\-\.]*)\n([\s\S]*?)```', raw_response)
        for lang, code in code_matches:
            code_blocks.append({
                "language": lang or "text",
                "code": code.strip(),
                "suggested_file": req.file_path or (f"solution.{lang}" if lang in ["py", "js", "ts", "html", "css", "json", "sh", "bash"] else "snippet.txt")
            })

        # 5. Log to PostgreSQL (safe — never crash the response)
        try:
            if postgres_manager.is_connected():
                postgres_manager.log_command(
                    user_input=req.prompt,
                    model_used=selected_model_name.upper(),
                    tool_called="agent_studio_chat",
                    latency_ms=duration_ms,
                    status="SUCCESS"
                )
        except Exception as db_err:
            print(f"[AGENT STUDIO] DB log error (non-fatal): {db_err}")

        return {
            "response": raw_response,
            "model": selected_model_name,
            "mode": req.mode,
            "latency_ms": duration_ms,
            "steps": steps,
            "code_blocks": code_blocks,
            "timestamp": time.strftime("%H:%M:%S")
        }

    except Exception as fatal_err:
        import traceback
        print(f"[AGENT STUDIO] FATAL: {traceback.format_exc()}")
        return JSONResponse(status_code=200, content={
            "response": f"[DOOM] Agent Studio encountered a critical error: {fatal_err}. Please try again.",
            "model": getattr(req, "model", "groq"),
            "mode": getattr(req, "mode", "pair_programmer"),
            "latency_ms": round((time.time() - start_time) * 1000, 2),
            "steps": [{"type": "plan", "title": "Error Recovery", "desc": str(fatal_err)}],
            "code_blocks": [],
            "timestamp": time.strftime("%H:%M:%S")
        })

@app.post("/api/agent/write_code_file")
async def agent_write_code_file(req: AgentFileWriteRequest):
    """Writes generated code directly to disk with 1 click."""
    target_path = req.file_path.strip()
    if not os.path.isabs(target_path):
        target_path = os.path.join(os.path.expanduser("~"), "Desktop", target_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(req.content)
    if postgres_manager.is_connected():
        postgres_manager.save_semantic_fact(
            key=f"agent_edited_{os.path.basename(target_path)}",
            value={"path": target_path, "bytes": len(req.content), "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")},
            category="developer"
        )
    return {"success": True, "path": target_path, "message": f"Saved {len(req.content)} bytes to {target_path}"}

@app.post("/api/agent/run_code_terminal")
async def agent_run_code_terminal(req: AgentTerminalRunRequest):
    """Executes code or terminal command autonomously on Windows."""
    from tools.terminal_tools import ExecuteTerminalCommandTool
    tool = ExecuteTerminalCommandTool()
    res = tool.execute(command=req.command)
    return {"success": res.success, "output": res.output}

@app.get("/api/agent/read_file")
async def agent_read_file(path: str = Query(...)):
    """Reads a file from the workspace to include as agent context."""
    try:
        full_path = path if os.path.isabs(path) else os.path.join(r"C:\Users\dell\Desktop\DOOM", path)
        if not os.path.exists(full_path):
            return JSONResponse(status_code=404, content={"error": f"File not found: {full_path}"})
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(8000)
        return {"path": full_path, "content": content, "lines": content.count("\n") + 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ─────────────────────────────────────────────────────────────────────────────
# DOOM IDE — Standalone Code Editor Routes
# ─────────────────────────────────────────────────────────────────────────────
class IDEWriteRequest(BaseModel):
    path: str
    content: str

class IDERunRequest(BaseModel):
    command: str
    cwd: str = ""

class IDEChatRequest(BaseModel):
    prompt: str
    model: str = "groq"
    mode: str = "pair_programmer"
    file_path: str = ""
    file_content: str = ""
    language: str = ""

@app.get("/ide")
async def serve_ide():
    ide_path = os.path.join(os.path.dirname(__file__), "static", "ide.html")
    with open(ide_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/ide/files")
async def ide_list_files(path: str = ""):
    """List directory contents for the IDE file explorer."""
    import pathlib
    try:
        base = path if path else os.path.expanduser("~")
        base = os.path.abspath(base)
        if not os.path.exists(base):
            return JSONResponse(status_code=404, content={"error": "Path not found"})
        
        items = []
        try:
            entries = sorted(os.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower()))
            for entry in entries:
                if entry.name.startswith('.') and entry.name not in ['.env', '.gitignore']:
                    continue
                try:
                    items.append({
                        "name": entry.name,
                        "path": entry.path.replace("\\", "/"),
                        "type": "directory" if entry.is_dir() else "file",
                        "size": entry.stat().st_size if entry.is_file() else 0,
                        "ext": os.path.splitext(entry.name)[1].lower() if entry.is_file() else ""
                    })
                except:
                    pass
        except PermissionError:
            pass
        
        return {"path": base.replace("\\", "/"), "items": items, "parent": str(pathlib.Path(base).parent).replace("\\", "/")}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/ide/read")
async def ide_read_file(path: str):
    """Read file content for the IDE editor."""
    try:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            return JSONResponse(status_code=404, content={"error": "File not found"})
        size = os.path.getsize(path)
        if size > 2 * 1024 * 1024:  # 2MB limit
            return JSONResponse(status_code=413, content={"error": "File too large (>2MB)"})
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        ext = os.path.splitext(path)[1].lower()
        lang_map = {
            ".py":"python",".js":"javascript",".ts":"typescript",".jsx":"javascript",
            ".tsx":"typescript",".html":"html",".css":"css",".json":"json",
            ".md":"markdown",".sh":"shell",".bash":"shell",".yml":"yaml",
            ".yaml":"yaml",".toml":"toml",".txt":"plaintext",".env":"plaintext",
            ".sql":"sql",".rs":"rust",".go":"go",".java":"java",".cpp":"cpp",
            ".c":"c",".cs":"csharp",".rb":"ruby",".php":"php",".kt":"kotlin",
            ".swift":"swift",".r":"r",".scala":"scala"
        }
        return {
            "path": path.replace("\\","/"),
            "content": content,
            "language": lang_map.get(ext, "plaintext"),
            "lines": content.count("\n") + 1,
            "size": size
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/ide/write")
async def ide_write_file(req: IDEWriteRequest):
    """Save file content from the IDE editor."""
    try:
        path = os.path.abspath(req.path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"success": True, "path": path.replace("\\","/"), "bytes": len(req.content.encode())}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/ide/run")
async def ide_run_command(req: IDERunRequest):
    """Run a terminal command from the IDE terminal panel."""
    import subprocess
    try:
        cwd = req.cwd if req.cwd and os.path.isdir(req.cwd) else os.getcwd()
        result = subprocess.run(
            req.command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=30
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Command timed out (30s)", "returncode": -1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/ide/chat")
async def ide_chat(req: IDEChatRequest):
    """AI chat endpoint for the IDE — uses DOOM's model router."""
    import time
    start = time.time()
    
    mode_prompts = {
        "pair_programmer": "You are an expert pair programmer. Write clean, production-ready code with comments. When generating code, wrap it in markdown code blocks with the language specified.",
        "architect": "You are a senior software architect. Provide detailed system design, patterns, and architectural guidance.",
        "debugger": "You are an expert debugger. Analyze code carefully, identify root causes, and provide step-by-step fixes.",
        "reviewer": "You are a code reviewer. Review code for bugs, performance, security, and best practices. Be specific.",
        "explainer": "You are a teacher. Explain code clearly and concisely, step by step, with examples.",
    }
    
    system_prompt = mode_prompts.get(req.mode, mode_prompts["pair_programmer"])
    
    context = ""
    if req.file_content and req.file_path:
        lang = req.language or "code"
        context = f"\n\nCurrent file: `{req.file_path}`\n```{lang}\n{req.file_content[:4000]}\n```\n"
    
    full_prompt = f"{context}\n{req.prompt}" if context else req.prompt
    
    try:
        # Map IDE model names to DOOM router models
        model_map = {
            "groq": "groq",
            "bedrock_claude": "bedrock",
            "bedrock_nova": "bedrock",
            "gemini": "gemini",
            "auto": None
        }
        provider = model_map.get(req.model, "groq")
        response_obj = model_router.generate(
            prompt=full_prompt,
            system_prompt=system_prompt,
            provider_override=provider
        )
        response_text = response_obj.content if hasattr(response_obj, 'content') else str(response_obj)
    except Exception as e:
        response_text = f"Model error ({req.model}): {str(e)}"
    
    # Extract code blocks
    import re
    code_blocks = []
    pattern = r"```(\w*)\n([\s\S]*?)```"
    for m in re.finditer(pattern, response_text):
        lang = m.group(1) or "plaintext"
        code = m.group(2).strip()
        if code:
            ext_map = {"python":"py","javascript":"js","typescript":"ts","html":"html","css":"css","json":"json","sql":"sql","bash":"sh","shell":"sh"}
            code_blocks.append({"language": lang, "code": code, "suggested_file": f"output.{ext_map.get(lang, 'txt')}"})
    
    latency = round((time.time() - start) * 1000)
    return {
        "response": response_text,
        "code_blocks": code_blocks,
        "model": req.model,
        "mode": req.mode,
        "latency_ms": latency,
        "timestamp": time.strftime("%H:%M:%S")
    }



@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            telemetry = get_live_telemetry()

            # Sentinel Watchdog: check for overload thresholds
            alert = None
            if telemetry.get("cpu_percent", 0) > 90:
                alert = {"level": "CRITICAL", "type": "CPU_OVERLOAD", "msg": f"CPU spike at {telemetry['cpu_percent']}%. Thermal load elevated."}
            elif telemetry.get("memory_percent", 0) > 90:
                alert = {"level": "WARNING", "type": "RAM_WARNING", "msg": f"RAM memory reached {telemetry['memory_percent']}%. Memory optimization recommended."}

            payload = {
                "type": "telemetry_update",
                "data": telemetry,
                "sentinel_alert": alert
            }
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
    except Exception:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


# ─────────────────────────────────────────────────────────────────────────────
# Mount Static Frontend (MUST be last — catch-all "/" mount intercepts everything)
# ─────────────────────────────────────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
