import os
import sys
import json
import time
import asyncio
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

# Add parent directory to path so DOOM core models and tools are accessible
parent_dir = str(Path(__file__).resolve().parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.model_router import model_router
from core.orchestrator import doom_core

app = FastAPI(title="DOOM Cyber IDE — Autonomous AI Development Environment", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active workspace directory (defaults to parent DOOM project root)
WORKSPACE_ROOT = os.path.abspath(parent_dir)

class WorkspaceChange(BaseModel):
    path: str

class FileReadRequest(BaseModel):
    path: str

class FileWriteRequest(BaseModel):
    path: str
    content: str

class FileCreateRequest(BaseModel):
    path: str
    is_directory: bool = False

class FileDeleteRequest(BaseModel):
    path: str

class FileRenameRequest(BaseModel):
    old_path: str
    new_path: str

class AIChatRequest(BaseModel):
    prompt: str
    model: str = "groq"
    current_file_path: Optional[str] = None
    current_file_content: Optional[str] = None
    selected_code: Optional[str] = None
    system_mode: str = "coder"  # coder, architect, debugger, reviewer

class TerminalExecRequest(BaseModel):
    command: str
    cwd: Optional[str] = None

# Extension to Monaco Language Mapping
LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".md": "markdown",
    ".txt": "plaintext",
    ".sql": "sql",
    ".sh": "shell",
    ".bat": "bat",
    ".ps1": "powershell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".php": "php",
    ".ini": "ini",
    ".env": "ini",
    ".dockerfile": "dockerfile"
}

def get_language_from_filename(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if filename.lower() == "dockerfile":
        return "dockerfile"
    return LANG_MAP.get(ext, "plaintext")

def resolve_path(rel_or_abs: str) -> str:
    if os.path.isabs(rel_or_abs):
        return os.path.abspath(rel_or_abs)
    return os.path.abspath(os.path.join(WORKSPACE_ROOT, rel_or_abs))

# ─────────────────────────────────────────────────────────────────────────────
# Workspace & File Explorer APIs
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/workspace/info")
async def get_workspace_info():
    global WORKSPACE_ROOT
    return {
        "workspace_root": WORKSPACE_ROOT,
        "workspace_name": os.path.basename(WORKSPACE_ROOT) or WORKSPACE_ROOT,
        "exists": os.path.exists(WORKSPACE_ROOT)
    }

@app.post("/api/workspace/open")
async def open_workspace(req: WorkspaceChange):
    global WORKSPACE_ROOT
    target = os.path.abspath(req.path.strip('"').strip("'"))
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail=f"Directory does not exist: {target}")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {target}")
    WORKSPACE_ROOT = target
    return {
        "status": "success",
        "workspace_root": WORKSPACE_ROOT,
        "workspace_name": os.path.basename(WORKSPACE_ROOT)
    }

@app.get("/api/fs/tree")
async def get_file_tree(dir_path: Optional[str] = None):
    """Returns the hierarchical file tree of the workspace."""
    global WORKSPACE_ROOT
    target_dir = resolve_path(dir_path) if dir_path else WORKSPACE_ROOT
    
    if not os.path.exists(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    IGNORED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".idea", ".vscode", "dist", "build"}
    
    def build_tree(current_dir: str, depth: int = 0) -> List[Dict[str, Any]]:
        if depth > 4:  # guard against deep nesting
            return []
        items = []
        try:
            entries = sorted(os.scandir(current_dir), key=lambda e: (not e.is_dir(), e.name.lower()))
            for entry in entries:
                if entry.name in IGNORED_DIRS or entry.name.startswith(".pytest_cache"):
                    continue
                rel_path = os.path.relpath(entry.path, WORKSPACE_ROOT).replace("\\", "/")
                if entry.is_dir():
                    items.append({
                        "name": entry.name,
                        "path": rel_path,
                        "abs_path": entry.path,
                        "type": "directory",
                        "children": build_tree(entry.path, depth + 1)
                    })
                else:
                    items.append({
                        "name": entry.name,
                        "path": rel_path,
                        "abs_path": entry.path,
                        "type": "file",
                        "size": entry.stat().st_size,
                        "language": get_language_from_filename(entry.name)
                    })
        except PermissionError:
            pass
        return items

    tree = build_tree(target_dir)
    return {
        "root": WORKSPACE_ROOT,
        "name": os.path.basename(WORKSPACE_ROOT),
        "tree": tree
    }

@app.get("/api/fs/read")
async def read_file(path: str = Query(...)):
    full_path = resolve_path(path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if os.path.isdir(full_path):
        raise HTTPException(status_code=400, detail="Path is a directory, not a file")
    
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {
            "path": path,
            "abs_path": full_path,
            "filename": os.path.basename(full_path),
            "content": content,
            "language": get_language_from_filename(full_path),
            "size": len(content)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fs/write")
async def write_file(req: FileWriteRequest):
    full_path = resolve_path(req.path)
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {
            "status": "success",
            "path": req.path,
            "size": len(req.content),
            "timestamp": time.strftime("%H:%M:%S")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fs/create")
async def create_node(req: FileCreateRequest):
    full_path = resolve_path(req.path)
    try:
        if req.is_directory:
            os.makedirs(full_path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if not os.path.exists(full_path):
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write("")
        return {"status": "success", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fs/delete")
async def delete_node(req: FileDeleteRequest):
    full_path = resolve_path(req.path)
    try:
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="Item not found")
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return {"status": "success", "path": req.path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fs/rename")
async def rename_node(req: FileRenameRequest):
    old_full = resolve_path(req.old_path)
    new_full = resolve_path(req.new_path)
    try:
        if not os.path.exists(old_full):
            raise HTTPException(status_code=404, detail="Source not found")
        os.rename(old_full, new_full)
        return {"status": "success", "old_path": req.old_path, "new_path": req.new_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fs/upload")
async def upload_files(target_dir: str = Form(""), files: List[UploadFile] = File(...)):
    dest_dir = resolve_path(target_dir)
    os.makedirs(dest_dir, exist_ok=True)
    saved = []
    for file in files:
        file_path = os.path.join(dest_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved.append(file.filename)
    return {"status": "success", "uploaded": saved}

# ─────────────────────────────────────────────────────────────────────────────
# AI Copilot & Pair Programmer Engine
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/ai/models")
async def get_ai_models():
    """Returns available LLMs configured in DOOM environment."""
    providers = model_router.get_provider_status()
    models_list = [
        {"id": "groq", "name": "Groq LPU (LLaMA 3.3 70B)", "speed": "500 T/S", "badge": "Ultra-Fast", "available": bool(os.getenv("GROQ_API_KEY"))},
        {"id": "bedrock_claude", "name": "Claude 3.5 Sonnet (AWS Bedrock)", "speed": "Priority 1", "badge": "Smartest", "available": bool(os.getenv("AWS_ACCESS_KEY_ID"))},
        {"id": "bedrock_nova", "name": "Amazon Nova Pro (AWS Bedrock)", "speed": "High", "badge": "Nova", "available": bool(os.getenv("AWS_ACCESS_KEY_ID"))},
        {"id": "gemini", "name": "Google Gemini 2.0 Flash", "speed": "Fast", "badge": "Multimodal", "available": bool(os.getenv("GEMINI_API_KEY"))},
        {"id": "openai", "name": "OpenAI GPT-4o / mini", "speed": "Standard", "badge": "Reasoning", "available": bool(os.getenv("OPENAI_API_KEY"))},
        {"id": "ollama", "name": "Local Ollama LLM", "speed": "Offline", "badge": "Private", "available": True},
        {"id": "fallback", "name": "DOOM Autonomous Engine", "speed": "Instant", "badge": "Zero-Key", "available": True}
    ]
    return {"models": models_list, "provider_status": providers}

@app.post("/api/ai/chat")
async def ai_ide_chat(req: AIChatRequest):
    """
    Direct AI Pair Programmer endpoint for code generation, debugging, refactoring,
    and architecting with full file context.
    """
    start_time = time.time()
    
    # Construct context-aware prompt
    system_instruction = (
        "You are DOOM Cyber AI — an elite pair programming assistant and autonomous software engineer.\n"
        "You are assisting Boss Sujal inside the DOOM Cyber IDE.\n"
        "Guidelines:\n"
        "1. Provide complete, production-ready, clean, well-commented code.\n"
        "2. When suggesting code changes, output fenced code blocks with clear language tags (e.g. `python, `	ypescript, `html).\n"
        "3. If refactoring or writing code, explain the changes concisely.\n"
        "4. Address the user with high respect as Boss Sujal or Sujal.\n"
    )

    context_parts = []
    if req.current_file_path:
        context_parts.append(f"--- ACTIVE FILE: {req.current_file_path} ---")
    if req.selected_code:
        context_parts.append(f"--- SELECTED CODE SNIPPET ---\n`\n{req.selected_code}\n`")
    elif req.current_file_content:
        # Include sample of file content (capped to 6000 chars)
        snippet = req.current_file_content[:6000]
        context_parts.append(f"--- FILE CONTENT ---\n`\n{snippet}\n`")

    full_prompt = system_instruction + "\n"
    if context_parts:
        full_prompt += "\n".join(context_parts) + "\n\n"
    full_prompt += f"User Request: {req.prompt}"

    # Use Model Router
    provider = model_router.providers.get(req.model)
    if not provider:
        # Auto pick best available
        provider = model_router.select_provider(req.prompt)
    
    try:
        response_text = provider.generate(full_prompt)
    except Exception as e:
        # Fallback to local
        fallback = model_router.providers.get("fallback")
        response_text = f"Primary model notice: {str(e)}\n\n" + (fallback.generate(full_prompt) if fallback else str(e))

    # Extract code blocks from markdown
    import re
    code_blocks = []
    pattern = r"`(\w*)\n([\s\S]*?)`"
    for m in re.finditer(pattern, response_text):
        lang = m.group(1) or "plaintext"
        code = m.group(2).strip()
        if code:
            code_blocks.append({
                "language": lang,
                "code": code
            })

    latency_ms = round((time.time() - start_time) * 1000, 2)
    return {
        "response": response_text,
        "code_blocks": code_blocks,
        "model_used": req.model,
        "latency_ms": latency_ms,
        "timestamp": time.strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────────────────────────────────────────
# Terminal Execution Endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/terminal/run")
async def run_terminal_command(req: TerminalExecRequest):
    """Executes a shell command in the current workspace directory and returns output."""
    global WORKSPACE_ROOT
    work_dir = resolve_path(req.cwd) if req.cwd else WORKSPACE_ROOT
    
    cmd = req.command.strip()
    if not cmd:
        return {"output": "", "exit_code": 0}

    try:
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=45
        )
        output = process.stdout
        if process.stderr:
            output += ("\n" if output else "") + process.stderr
        return {
            "output": output,
            "exit_code": process.returncode,
            "cwd": work_dir
        }
    except subprocess.TimeoutExpired:
        return {"output": "Command timed out after 45 seconds.", "exit_code": -1, "cwd": work_dir}
    except Exception as e:
        return {"output": f"Execution error: {str(e)}", "exit_code": 1, "cwd": work_dir}

# ─────────────────────────────────────────────────────────────────────────────
# Real-Time Interactive Terminal WebSocket
# ─────────────────────────────────────────────────────────────────────────────
@app.websocket("/ws/terminal")
async def websocket_terminal_endpoint(websocket: WebSocket):
    await websocket.accept()
    global WORKSPACE_ROOT
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                cmd = msg.get("command", "")
                cwd = resolve_path(msg.get("cwd", WORKSPACE_ROOT))
            except Exception:
                cmd = data
                cwd = WORKSPACE_ROOT
            
            if cmd.strip():
                try:
                    proc = await asyncio.create_subprocess_shell(
                        f"powershell.exe -NoProfile -Command {cmd}",
                        cwd=cwd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await proc.communicate()
                    out_text = (stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace"))
                    await websocket.send_text(json.dumps({
                        "type": "output",
                        "output": out_text,
                        "exit_code": proc.returncode,
                        "cwd": cwd
                    }))
                except Exception as ex:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "output": str(ex),
                        "cwd": cwd
                    }))
    except WebSocketDisconnect:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# Mount Static Frontend
# ─────────────────────────────────────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
