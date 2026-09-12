import os
import sys
import json
import asyncio
import time
import psutil
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

# Ensure DOOM root directory is on sys.path
DOOM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DOOM_ROOT not in sys.path:
    sys.path.insert(0, DOOM_ROOT)

load_dotenv()

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database.postgres_db import postgres_manager
from core.orchestrator import doom_core
from core.model_router import model_router
from core.state_machine import state_machine, DoomState
from core.task_engine import task_engine
from core.tool_registry import tool_registry
from memory import user_profile
from tools import ALL_TOOLS
from tools.workstation_modes import CodeModeTool, DailyBriefingTool, StandupReportTool, LockdownTool, ScreenVisionTool
from core.sound_detector import sound_detector
from core.listen import listen_for_command
from core.commands import submit_user_input
from core.cognition import cognitive_engine

app = FastAPI(title="DOOM V3 — Personal AI Operating System", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskOriginMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from dashboard.ask_session import origin_forbidden, path_is_ask_plane
        if scope.get("type") == "http" and path_is_ask_plane(scope.get("path") or ""):
            hdrs = {
                k.decode("latin1").lower(): v.decode("latin1")
                for k, v in (scope.get("headers") or [])
            }
            origin = (hdrs.get("origin") or "").strip().rstrip("/")
            method = (scope.get("method") or "GET").upper()
            from dashboard.ask_session import origin_allowed
            class _R:
                def __init__(self):
                    self.headers = hdrs
                    self.method = method
            if not origin_allowed(_R()):
                resp = origin_forbidden()
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(AskOriginMiddleware)

connected_clients: List[WebSocket] = []
dashboard_loop = None

# ─────────────────────────────────────────────────────────────────────────────
# Acoustic Clap Awakening System for Dashboard
# ─────────────────────────────────────────────────────────────────────────────
is_clap_processing = False

@app.on_event("startup")
async def on_server_startup():
    global dashboard_loop
    dashboard_loop = asyncio.get_running_loop()
    print("[DASHBOARD] Initializing DOOM Acoustic Clap Sensor...")
    try:
        import threading
        threading.Thread(target=lambda: sound_detector.start_background_detector(on_dashboard_clap), daemon=True).start()
        print("[DASHBOARD] [OK] Acoustic Clap Detector initializing in background thread!")
    except Exception as e:
        print(f"[DASHBOARD] Acoustic Clap Sensor warning: {e}")
    
    # Register task state broadcaster
    def broadcast_task_state(payload: dict):
        for client in list(connected_clients):
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text(json.dumps(payload)),
                    dashboard_loop
                )
            except Exception:
                pass
    task_engine.set_state_broadcaster(broadcast_task_state)
    cognitive_engine.set_broadcaster(broadcast_task_state)

    def _ws_ops_event(event):
        try:
            payload = {"type": "operational_event", **event.to_dict()}
            for client in list(connected_clients):
                try:
                    asyncio.run_coroutine_threadsafe(
                        client.send_text(json.dumps(payload)),
                        dashboard_loop
                    )
                except Exception:
                    pass
        except Exception:
            pass

    from observability.bus import telemetry_bus
    telemetry_bus.subscribe(_ws_ops_event)
    print("[DASHBOARD] [OK] Task and Cognitive state broadcasters registered")
    try:
        from proactive.config import is_proactive_enabled
        if is_proactive_enabled():
            from proactive.worker import start_proactive_worker
            start_proactive_worker()
            print("[DASHBOARD] [OK] V6.1 proactive worker started")
    except Exception as pe:
        print(f"[DASHBOARD] Proactive worker skipped: {type(pe).__name__}")

@app.on_event("shutdown")
async def on_server_shutdown():
    try:
        sound_detector.stop_detector()
        print("[DASHBOARD] Acoustic Clap Sensor stopped.")
    except Exception:
        pass


def broadcast_hud_event(event_dict: dict):
    """Safely broadcast event to all connected dashboard WebSockets."""
    for client in list(connected_clients):
        try:
            asyncio.run_coroutine_threadsafe(
                client.send_text(json.dumps(event_dict)),
                dashboard_loop
            )
        except Exception:
            pass

def on_dashboard_clap():
    """Triggered on physical double-clap while Dashboard is running."""
    global is_clap_processing
    if is_clap_processing:
        return
    is_clap_processing = True
    sound_detector.is_paused = True  # Pause detector while speaking & processing to prevent feedback loop
    print("\n[CLAP SENSOR] ACOUSTIC DOUBLE-CLAP DETECTED! Awakening DOOM...")

    try:
        from core.cinematic_voice import speak

        # 1. Alert Dashboard HUD via WebSocket
        broadcast_hud_event({
            "type": "clap_detected",
            "message": "DOOM is online. At your service, Boss.",
            "timestamp": time.strftime("%H:%M:%S")
        })

        # 2. Vocalize Iron Man JARVIS wake greeting
        try:
            speak("DOOM is online. At your service, Boss.")
        except Exception as se:
            print(f"[DASHBOARD] Voice error: {se}")

        # 3. Listen for voice command immediately
        cmd = listen_for_command()
        if cmd and cmd.strip():
            print(f"[DASHBOARD] Command received: '{cmd}'")
            broadcast_hud_event({
                "type": "clap_command",
                "command": cmd,
                "timestamp": time.strftime("%H:%M:%S")
            })
            # 4. Same text pipeline as typed input (policy/risk/approval/Cost Guard)
            response = submit_user_input(cmd, source="voice")
            broadcast_hud_event({
                "type": "command_executed",
                "goal": cmd,
                "response": response,
                "timestamp": time.strftime("%H:%M:%S")
            })
        else:
            print("[DASHBOARD] Standing by.")
            broadcast_hud_event({
                "type": "clap_standby",
                "message": "Standing by, Boss.",
                "timestamp": time.strftime("%H:%M:%S")
            })
            try:
                speak("Standing by, Boss.")
            except Exception:
                pass
    except Exception as e:
        print(f"[DASHBOARD] Clap processing error: {e}")
    finally:
        time.sleep(1.0)
        sound_detector.is_paused = False
        is_clap_processing = False






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
@app.get("/api/clap/status")
async def get_clap_status():
    """Returns real-time acoustic clap sensor diagnostics."""
    return sound_detector.get_status()

@app.post("/api/clap/calibrate")
async def trigger_clap_calibration():
    """Triggers ambient noise calibration for the microphone."""
    new_thresh = sound_detector.calibrate_threshold()
    return {"status": "CALIBRATED", "threshold": new_thresh}

@app.get("/api/status")
async def get_system_status():
    """Returns general DOOM V2 state, Memory health, and Model Router state."""
    memory_online = postgres_manager.is_connected()
    memory_counts = postgres_manager.get_table_counts() if memory_online else {}
    provider_status = model_router.get_provider_status()

    return {
        "status": "OPERATIONAL",
        "system": "DOOM V2 Personal AI OS",
        "creator": "Sujal",
        "clap_sensor": sound_detector.get_status(),
        "memory": {
            "connected": memory_online,
            "stores": memory_counts
        },
        "models": provider_status,
        "tools_count": len(ALL_TOOLS),
        "telemetry": get_live_telemetry()
    }

@app.get("/api/memory/episodes")
async def get_recent_episodes(limit: int = 15):
    """Fetches recent action episodes from Memory 2.0."""
    if not postgres_manager.is_connected():
        return {"episodes": [], "source": "offline"}
    episodes = postgres_manager.get_recent_episodes(limit=limit)
    return {"episodes": episodes, "source": "memory", "count": len(episodes)}

@app.get("/api/memory/facts")
async def get_semantic_facts():
    """Fetches long-term semantic knowledge facts from Memory 2.0."""
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
    return {"facts": facts, "source": "memory", "count": len(facts)}

@app.get("/api/logs")
async def get_command_logs(limit: int = 20):
    """Fetches command audit logs with latency benchmarks from Memory 2.0."""
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


@app.get("/api/proactive/status")
async def proactive_status():
    """V6.1 worker health. No payloads."""
    from proactive.config import (
        DAILY_INFORM_BUDGET,
        OWNER_ID,
        is_prediction_enabled,
        is_proactive_enabled,
        is_suggest_enabled,
    )
    from proactive.worker import worker_alive
    proactive_on = is_proactive_enabled()
    suggest_on = bool(proactive_on and is_prediction_enabled() and is_suggest_enabled())
    if not proactive_on:
        ceiling = "NONE"
    elif suggest_on:
        ceiling = "SUGGEST"
    else:
        ceiling = "INFORM"
    return {
        "enabled": proactive_on,
        "worker_alive": worker_alive() if proactive_on else False,
        "owner_id": OWNER_ID,
        "daily_inform_budget": DAILY_INFORM_BUDGET,
        "tts_proactive": False,
        "suggest_enabled": suggest_on,
        "max_intervention": ceiling,
    }


@app.get("/api/proactive/insights")
async def proactive_insights(limit: int = 20):
    """HUD cards from PostgreSQL only. Empty when flag off. Never 500 on malformed rows."""
    from proactive.config import is_proactive_enabled
    from proactive.delivery import MAX_HUD
    from proactive.store import proactive_store
    from proactive.templates import render_inform
    if not is_proactive_enabled():
        return {"insights": [], "count": 0, "enabled": False}
    try:
        cap = min(max(int(limit or 20), 1), MAX_HUD)
    except (TypeError, ValueError):
        cap = 20
    try:
        rows = proactive_store.list_hud_insights(limit=cap)
    except Exception:
        return {"insights": [], "count": 0, "enabled": True, "error": "store_unavailable"}
    out = []
    for r in rows or []:
        try:
            if r.get("privacy_class") != "NORMAL":
                continue
            if (r.get("intervention") or r.get("recommended_intervention")) != "INFORM":
                continue
            params = r.get("safe_params") if isinstance(r.get("safe_params"), dict) else {}
            params = dict(params)
            params["entity_id"] = r.get("entity_id")
            params["insight_type"] = r.get("insight_type")
            out.append({
                "insight_id": r.get("insight_id"),
                "intervention": "INFORM",
                "template_id": r.get("template_id"),
                "message": render_inform(r.get("template_id") or "generic_event", params),
                "urgency": r.get("urgency"),
                "entity_id": r.get("entity_id"),
                "tts": False,
                "proactive_cycle_id": r.get("proactive_cycle_id"),
            })
        except Exception:
            continue
    bounded = out[:MAX_HUD]
    return {"insights": bounded, "count": len(bounded), "enabled": True}


@app.get("/api/proactive/suggestions")
async def proactive_suggestions(limit: int = 20):
    from proactive.config import OWNER_ID, is_prediction_enabled, is_proactive_enabled, is_suggest_enabled
    from proactive.delivery import MAX_HUD
    from proactive.store import proactive_store
    from proactive.suggest_templates import render_suggest
    enabled = bool(is_proactive_enabled() and is_prediction_enabled() and is_suggest_enabled())
    if not enabled:
        return {"suggestions": [], "count": 0, "enabled": False}
    try:
        cap = min(max(int(limit or 20), 1), MAX_HUD)
    except (TypeError, ValueError):
        cap = 20
    try:
        rows = proactive_store.list_hud_suggestions(owner_id=OWNER_ID, limit=cap)
    except Exception:
        return {"suggestions": [], "count": 0, "enabled": True, "error": "store_unavailable"}
    out = []
    for r in rows or []:
        try:
            if r.get("privacy_class") != "NORMAL":
                continue
            params = r.get("safe_params") if isinstance(r.get("safe_params"), dict) else {}
            out.append({
                "suggestion_id": r.get("suggestion_id"),
                "intervention": "SUGGEST",
                "suggestion_type": r.get("suggestion_type"),
                "template_id": r.get("template_id"),
                "message": render_suggest(r.get("template_id") or "suggest_review_work", params),
                "priority": r.get("priority"),
                "status": r.get("status"),
                "tts": False,
            })
        except Exception:
            continue
    bounded = out[:MAX_HUD]
    return {"suggestions": bounded, "count": len(bounded), "enabled": True}


@app.post("/api/proactive/suggestions/{suggestion_id}/dismiss")
async def dismiss_proactive_suggestion(suggestion_id: str):
    from proactive.config import OWNER_ID, is_prediction_enabled, is_proactive_enabled, is_suggest_enabled
    from proactive.otp import emit_proactive
    from proactive.store import proactive_store
    if not (is_proactive_enabled() and is_prediction_enabled() and is_suggest_enabled()):
        return {"ok": False, "error": "disabled"}
    sid = str(suggestion_id or "")[:64]
    ok = proactive_store.dismiss_suggestion(sid, OWNER_ID)
    if not ok:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    emit_proactive(
        "proactive.suggestion.dismissed",
        attributes={"suggestion_id": sid[:36]},
    )
    return {"ok": True}


@app.post("/api/proactive/session")
async def ask_session_unlock(request: Request):
    from dashboard.ask_session import (
        compare_unlock,
        create_session,
        origin_allowed,
        origin_forbidden,
        set_session_cookie,
        unlock_rate_ok,
    )
    if not origin_allowed(request):
        return origin_forbidden()
    ip = (request.client.host if request.client else "") or "unknown"
    if not unlock_rate_ok(ip):
        return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = {}
    owner, err = compare_unlock(str((body or {}).get("unlock_secret") or ""))
    if err:
        return err
    raw, csrf, exp = create_session(owner)
    if not raw:
        return JSONResponse({"ok": False, "error": "session_failed"}, status_code=503)
    resp = JSONResponse({
        "ok": True,
        "csrf": csrf,
        "owner_id": owner,
        "expires_at": int(exp),
    })
    set_session_cookie(resp, raw)
    return resp


def _remap_ask_error(err: JSONResponse) -> JSONResponse:
    try:
        payload = json.loads(bytes(err.body).decode("utf-8"))
    except Exception:
        return err
    code = str(payload.get("error") or "")
    mapped = {
        "csrf": "CSRF_FAILURE",
        "origin": "ORIGIN_FAILURE",
        "unauthenticated": "IDENTITY_REQUIRED",
    }.get(code, code)
    return JSONResponse({"ok": False, "error": mapped}, status_code=err.status_code)


def _v8_ask_session(request: Request, *, need_csrf: bool):
    from dashboard.ask_session import require_ask_session
    sess, err = require_ask_session(request, need_csrf=need_csrf)
    if err:
        return None, _remap_ask_error(err)
    return sess, None


def _v8_error(code: str, http: int = 409) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(code or "BLOCKED")[:64]}, status_code=http)


def _v8_http_for(code: str) -> int:
    if code in ("IDENTITY_REQUIRED",):
        return 401
    if code in ("CSRF_FAILURE", "ORIGIN_FAILURE"):
        return 403
    if code in ("PLAN_NOT_FOUND",):
        return 404
    return 409


@app.post("/api/proactive/v8/command")
async def v8_authenticated_command(request: Request):
    """V8 request path. Identity comes only from the ASK cookie session."""
    from orchestration.identity import identity_from_ask_session_row
    from proactive.config import is_v8_enabled
    sess, err = _v8_ask_session(request, need_csrf=True)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    goal = str(body.get("goal") or "").strip()
    if not goal:
        return JSONResponse({"ok": False, "error": "empty_goal"}, status_code=400)
    ident, status = identity_from_ask_session_row(
        sess,
        computer_session_id=str(body.get("computer_session_id") or ""),
    )
    if ident is None:
        http = 401 if status == "IDENTITY_REQUIRED" else 409
        return _v8_error(status, http)
    if not is_v8_enabled():
        response = doom_core.process_request(goal, identity=ident)
        return {"ok": True, "response": response, "owner_id": ident.owner_id}

    from orchestration.authorization import medium_computer_approvable, safe_plan_display, stash_pending_plan
    from orchestration.executor import execute_plan
    from orchestration.executor_errors import ExecutionStatus
    from orchestration.production import prepare_v8_request

    prep = prepare_v8_request(goal, identity=ident)
    if prep.plan is None:
        return {
            "ok": False,
            "error": prep.status,
            "response": f"[V8] {prep.status}" + (f": {prep.reason}" if prep.reason else ""),
            "owner_id": ident.owner_id,
        }
    ok_medium, medium_code = medium_computer_approvable(prep.plan)
    if ok_medium:
        pending_id, stash_err = stash_pending_plan(prep.plan, ident)
        if pending_id is None:
            return _v8_error(stash_err, _v8_http_for(stash_err))
        return {
            "ok": True,
            "status": ExecutionStatus.APPROVAL_REQUIRED.value,
            "plan_id": pending_id,
            "display": safe_plan_display(prep.plan),
            "risk": prep.plan.plan_risk,
            "response": f"[V8] {ExecutionStatus.APPROVAL_REQUIRED.value}",
            "owner_id": ident.owner_id,
        }
    if prep.plan.plan_risk in ("HIGH", "CRITICAL") or prep.plan.approval_required:
        return _v8_error(medium_code, _v8_http_for(medium_code))

    result = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
    extra = prep.intent if result.status is ExecutionStatus.SUCCESS else ""
    response = f"[V8] {result.status.value}" + (f": {extra}" if extra else "")
    return {"ok": True, "status": result.status.value, "response": response, "owner_id": ident.owner_id}


@app.get("/api/proactive/v8/plans/{plan_id}")
async def v8_pending_plan_get(request: Request, plan_id: str):
    from orchestration.authorization import pending_display
    from orchestration.identity import identity_from_ask_session_row
    sess, err = _v8_ask_session(request, need_csrf=False)
    if err:
        return err
    ident, status = identity_from_ask_session_row(sess)
    if ident is None:
        return _v8_error(status, 401 if status == "IDENTITY_REQUIRED" else 409)
    display, code = pending_display(str(plan_id)[:64], ident)
    if display is None:
        return _v8_error(code, _v8_http_for(code))
    return {"ok": True, "plan_id": str(plan_id)[:64], "display": display}


@app.post("/api/proactive/v8/authorize")
async def v8_authorize_plan(request: Request):
    """Explicit ASK approval for a server-stashed MEDIUM computer plan."""
    from orchestration.authorization import claim_authorization, revoke_pending
    from orchestration.executor import execute_plan
    from orchestration.executor_errors import ExecutionStatus
    from orchestration.identity import identity_from_ask_session_row
    sess, err = _v8_ask_session(request, need_csrf=True)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    plan_id = str(body.get("plan_id") or "").strip()[:64]
    decision = str(body.get("decision") or "approve").strip().lower()
    ident, status = identity_from_ask_session_row(sess)
    if ident is None:
        return _v8_error(status, 401 if status == "IDENTITY_REQUIRED" else 409)
    if not plan_id:
        return _v8_error(ExecutionStatus.PLAN_NOT_FOUND.value, 404)
    if decision in ("cancel", "reject", "deny"):
        code = revoke_pending(plan_id, ident)
        if code != "OK":
            return _v8_error(code, _v8_http_for(code))
        return {"ok": True, "status": ExecutionStatus.AUTHORIZATION_REVOKED.value}
    requested_cs = str(body.get("computer_session_id") or "")
    claim, code = claim_authorization(
        plan_id,
        ident,
        requested_computer_session_id=requested_cs,
    )
    if claim is None:
        return _v8_error(code, _v8_http_for(code))
    result = execute_plan(
        claim.plan,
        identity=claim.identity,
        authorized_plan_hash=claim.authorized_plan_hash,
    )
    return {
        "ok": result.status is ExecutionStatus.SUCCESS,
        "status": result.status.value,
        "response": f"[V8] {result.status.value}",
        "owner_id": claim.identity.owner_id,
    }


@app.get("/api/proactive/session")
async def ask_session_get(request: Request):
    from dashboard.ask_session import require_ask_session
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    from proactive.config import is_v8_enabled
    return {
        "ok": True,
        "csrf": sess.get("csrf_token"),
        "owner_id": sess.get("owner_id"),
        "expires_at": int(sess.get("expires_at") or 0),
        "v8_enabled": bool(is_v8_enabled()),
    }


@app.post("/api/proactive/session/logout")
async def ask_session_logout(request: Request):
    from dashboard.ask_session import clear_session_cookie, require_ask_session
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    proactive_store.revoke_ask_session(str(sess.get("session_id_hash") or ""))
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp)
    return resp


def _prepare_flags():
    from proactive.config import (
        is_ask_enabled,
        is_prediction_enabled,
        is_prepare_enabled,
        is_proactive_enabled,
        is_suggest_enabled,
    )
    prep = bool(
        is_proactive_enabled()
        and is_prediction_enabled()
        and is_suggest_enabled()
        and is_prepare_enabled()
    )
    ask = bool(prep and is_ask_enabled())
    return prep, ask


@app.get("/api/proactive/preparations")
async def list_preparations(request: Request, limit: int = 20):
    from dashboard.ask_session import require_ask_session
    from proactive.delivery import MAX_HUD
    from proactive.prepare_templates import render_prepare
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    prep_on, _ask_on = _prepare_flags()
    if not prep_on:
        return {"preparations": [], "count": 0, "enabled": False}
    owner = str(sess.get("owner_id") or "")
    try:
        cap = min(max(int(limit or 20), 1), MAX_HUD)
    except (TypeError, ValueError):
        cap = 20
    rows = proactive_store.list_hud_preparations(owner, cap)
    out = []
    for r in rows or []:
        out.append({
            "preparation_id": r.get("preparation_id"),
            "type": "proactive_preparation",
            "preparation_type": r.get("preparation_type"),
            "action_type": r.get("action_type"),
            "suggestion_id": r.get("suggestion_id"),
            "status": r.get("status"),
            "risk_class": r.get("risk_class"),
            "privacy_class": r.get("privacy_class"),
            "valid_until": r.get("valid_until"),
            "message": render_prepare(r.get("template_id") or ""),
            "disclaimer": "PREPARED — NOT EXECUTED.",
            "tts": False,
        })
    return {"preparations": out, "count": len(out), "enabled": True}


@app.post("/api/proactive/preparations/{preparation_id}/cancel")
async def cancel_preparation_api(request: Request, preparation_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.ask_decisions import cancel_preparation
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    prep_on, _ask_on = _prepare_flags()
    if not prep_on:
        return {"ok": False, "error": "disabled"}
    owner = str(sess.get("owner_id") or "")
    result = cancel_preparation(str(preparation_id)[:64], owner)
    if not result.get("ok"):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return {"ok": True}


@app.get("/api/proactive/preparations/{preparation_id}/draft")
async def get_preparation_draft(request: Request, preparation_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.config import is_llm_draft_enabled
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    prep_on, _ask_on = _prepare_flags()
    if not prep_on or not is_llm_draft_enabled():
        return {"ok": False, "current": False, "enabled": False}
    owner = str(sess.get("owner_id") or "")
    row = proactive_store.get_current_draft(str(preparation_id)[:64], owner)
    if not row:
        return {"ok": False, "current": False, "enabled": True}
    out = row.get("structured_output") if isinstance(row.get("structured_output"), dict) else {}
    return {
        "ok": True,
        "current": True,
        "enabled": True,
        "type": "proactive_draft",
        "draft_id": row.get("draft_id"),
        "preparation_id": row.get("preparation_id"),
        "draft_type": row.get("draft_type"),
        "title": out.get("title") or "",
        "summary": out.get("summary") or "",
        "body": out.get("body") or "",
        "warnings": out.get("warnings") or [],
        "uncertainties": out.get("uncertainties") or [],
        "disclaimer": (
            "This text is a draft. Approval authorizes the bounded preparation "
            "parameters, not execution."
        ),
        "tts": False,
    }


@app.get("/api/proactive/actions/{action_id}")
async def get_proactive_action(request: Request, action_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.config import is_act_enabled
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    if not is_act_enabled():
        return {"ok": False, "enabled": False}
    owner = str(sess.get("owner_id") or "")
    row = proactive_store.get_action(str(action_id)[:64], owner)
    if not row:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return {
        "ok": True,
        "action_id": row.get("action_id"),
        "status": row.get("status"),
        "capability_id": row.get("capability_id"),
        "action_hash": row.get("action_hash"),
        "risk_class": row.get("risk_class"),
        "privacy_class": row.get("privacy_class"),
        "exec_params": row.get("exec_params") or {},
        "disclaimer": "GET is read-only. APPROVED does not execute. RUN is a separate POST.",
    }


@app.post("/api/proactive/actions/{action_id}/run")
async def run_proactive_action(request: Request, action_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.act_engine import request_run
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    owner = str(sess.get("owner_id") or "")
    result = request_run(
        str(action_id)[:64],
        owner,
        str((body or {}).get("action_hash") or ""),
    )
    http = int(result.pop("http", 200) or 200)
    if not result.get("ok"):
        return JSONResponse(result, status_code=http if http in (401, 403, 404, 409) else 409)
    return result


@app.post("/api/proactive/actions/{action_id}/cancel")
async def cancel_proactive_action(request: Request, action_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.act_engine import cancel_action
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    owner = str(sess.get("owner_id") or "")
    result = cancel_action(str(action_id)[:64], owner)
    http = int(result.get("http", 200) or 200)
    if not result.get("ok"):
        return JSONResponse(result, status_code=http if http in (401, 403, 404, 409) else 409)
    return result


def _computer_flags():
    from proactive.config import is_computer_enabled, is_computer_observe_enabled
    return is_computer_enabled() and is_computer_observe_enabled()


@app.post("/api/proactive/computer/sessions")
async def computer_session_create(request: Request):
    from dashboard.ask_session import require_ask_session
    from proactive.computer.session import start_session
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    if not _computer_flags():
        return JSONResponse({"ok": False, "enabled": False}, status_code=403)
    owner = str(sess.get("owner_id") or "")
    result = start_session(owner)
    http = int(result.pop("http", 200) or 200)
    if not result.get("ok"):
        return JSONResponse(result, status_code=http if http in (401, 403, 404, 409, 503) else 409)
    from proactive.computer.session import public_computer_session
    result["session"] = public_computer_session(result.get("session"))
    return result


@app.get("/api/proactive/computer/sessions")
async def computer_session_list(request: Request):
    from dashboard.ask_session import require_ask_session
    from proactive.computer.session import list_sessions, public_computer_session
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    owner = str(sess.get("owner_id") or "")
    if not _computer_flags():
        return {"ok": True, "sessions": [], "enabled": False}
    rows = [public_computer_session(r) for r in list_sessions(owner)]
    return {"ok": True, "sessions": rows, "enabled": True}


@app.get("/api/proactive/computer/sessions/{session_id}")
async def computer_session_get(request: Request, session_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.computer.session import get_session, public_computer_session
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    owner = str(sess.get("owner_id") or "")
    row = get_session(str(session_id)[:64], owner)
    if not row:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return {"ok": True, "session": public_computer_session(row)}


@app.get("/api/proactive/computer/sessions/{session_id}/observation")
async def computer_session_observation(request: Request, session_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    owner = str(sess.get("owner_id") or "")
    row = proactive_store.get_latest_computer_observation(str(session_id)[:64], owner)
    if not row:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return {
        "ok": True,
        "observation": row,
        "data_only": True,
        "screenshot_present": False,
    }


@app.post("/api/proactive/computer/sessions/{session_id}/stop")
async def computer_session_stop(request: Request, session_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.computer.stop import stop_session
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    owner = str(sess.get("owner_id") or "")
    result = stop_session(str(session_id)[:64], owner)
    http = int(result.pop("http", 200) or 200)
    if not result.get("ok"):
        return JSONResponse(result, status_code=http if http in (401, 403, 404, 409) else 404)
    if result.get("session"):
        from proactive.computer.session import public_computer_session
        result["session"] = public_computer_session(result.get("session"))
    return result


_BIND_HTTP = {
    "OK": 200,
    "ALREADY_BOUND": 409,
    "CANDIDATE_EXPIRED": 409,
    "WINDOW_GONE": 409,
    "INVALID_CANDIDATE": 400,
    "SESSION_UNAVAILABLE": 404,
    "PID_MISMATCH": 409,
    "EXE_MISMATCH": 409,
    "CLASS_MISMATCH": 409,
    "HWND_REUSE": 409,
    "INVALID_IDENTITY": 409,
}


@app.get("/api/proactive/computer/sessions/{session_id}/window-candidates")
async def computer_window_candidates(request: Request, session_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.computer.session import list_bind_candidates
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    if not _computer_flags():
        return JSONResponse({"ok": False, "enabled": False}, status_code=403)
    owner = str(sess.get("owner_id") or "")
    rows, code = list_bind_candidates(owner, str(session_id)[:64])
    if rows is None:
        http = _BIND_HTTP.get(code, 409)
        return JSONResponse({"ok": False, "error": code}, status_code=http)
    return {"ok": True, "candidates": rows}


@app.post("/api/proactive/computer/sessions/{session_id}/bind-window")
async def computer_bind_window(request: Request, session_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.computer.session import bind_session_window, public_computer_session
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    if not _computer_flags():
        return JSONResponse({"ok": False, "enabled": False}, status_code=403)
    owner = str(sess.get("owner_id") or "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if "hwnd" in body or "pid" in body or "window_handle" in body:
        return JSONResponse({"ok": False, "error": "INVALID_CANDIDATE"}, status_code=400)
    cid = str(body.get("candidate_id") or "").strip()[:64]
    bound, code = bind_session_window(owner, str(session_id)[:64], cid)
    if bound is None:
        http = _BIND_HTTP.get(code, 409)
        return JSONResponse({"ok": False, "error": code}, status_code=http)
    return {"ok": True, "session": public_computer_session(bound)}


@app.get("/api/proactive/approvals")
async def list_approvals(request: Request, limit: int = 20):
    from dashboard.ask_session import require_ask_session
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    _prep_on, ask_on = _prepare_flags()
    if not ask_on:
        return {"approvals": [], "count": 0, "enabled": False}
    owner = str(sess.get("owner_id") or "")
    try:
        cap = min(max(int(limit or 20), 1), 50)
    except (TypeError, ValueError):
        cap = 20
    rows = proactive_store.list_hud_approvals(owner, cap)
    out = []
    for r in rows or []:
        out.append({
            "approval_id": r.get("approval_id"),
            "type": "proactive_ask",
            "preparation_id": r.get("preparation_id"),
            "action_type": r.get("action_type"),
            "status": r.get("status"),
            "risk_class": r.get("risk_class"),
            "privacy_class": r.get("privacy_class"),
            "valid_until": r.get("valid_until"),
            "binding_hash": r.get("binding_hash"),
            "disclaimer": "APPROVAL DOES NOT EXECUTE.",
        })
    return {"approvals": out, "count": len(out), "enabled": True}


@app.get("/api/proactive/approvals/{approval_id}")
async def get_approval_api(request: Request, approval_id: str):
    from dashboard.ask_session import require_ask_session
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=False)
    if err:
        return err
    _prep_on, ask_on = _prepare_flags()
    if not ask_on:
        return {"ok": False, "error": "disabled", "enabled": False}
    owner = str(sess.get("owner_id") or "")
    row = proactive_store.get_approval(str(approval_id)[:64], owner)
    if not row:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return {
        "ok": True,
        "approval_id": row.get("approval_id"),
        "preparation_id": row.get("preparation_id"),
        "status": row.get("status"),
        "action_type": row.get("action_type"),
        "binding_hash": row.get("binding_hash"),
        "valid_until": row.get("valid_until"),
        "disclaimer": "APPROVAL DOES NOT EXECUTE.",
    }


async def _ask_decide(request: Request, approval_id: str, kind: str):
    from dashboard.ask_session import require_ask_session
    from proactive.ask_decisions import decide
    from proactive.delivery import deliver_authorization
    from proactive.store import proactive_store
    sess, err = require_ask_session(request, need_csrf=True)
    if err:
        return err
    _prep_on, ask_on = _prepare_flags()
    if not ask_on:
        return {"ok": False, "error": "disabled"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    owner = str(sess.get("owner_id") or "")
    result = decide(
        kind,
        str(approval_id)[:64],
        owner,
        str(sess.get("session_id_hash") or ""),
        str((body or {}).get("binding_hash") or ""),
    )
    http = int(result.pop("http", 200) or 200)
    if not result.get("ok"):
        code = http if http in (401, 403, 404, 409) else 409
        return JSONResponse(result, status_code=code)
    if kind == "approve" and not result.get("replay"):
        row = proactive_store.get_approval(str(approval_id)[:64], owner) or {}
        try:
            deliver_authorization(row)
        except Exception:
            pass
    return result


@app.post("/api/proactive/approvals/{approval_id}/approve")
async def ask_approve(request: Request, approval_id: str):
    return await _ask_decide(request, approval_id, "approve")


@app.post("/api/proactive/approvals/{approval_id}/reject")
async def ask_reject(request: Request, approval_id: str):
    return await _ask_decide(request, approval_id, "reject")


@app.post("/api/proactive/approvals/{approval_id}/revoke")
async def ask_revoke(request: Request, approval_id: str):
    return await _ask_decide(request, approval_id, "revoke")


@app.get("/api/tools")
async def list_tools():
    """Returns the full catalogue of autonomous tools."""
    return {"tools": tool_registry.get_tools_catalog(), "total": len(tool_registry.get_all_tools())}

@app.get("/api/tasks")
async def get_tasks():
    """Fetches active task status and execution history for the Tasks UI."""
    return {
        "active_task": task_engine.get_active_task_dict(),
        "history": task_engine.get_history_dicts(limit=15)
    }

@app.get("/api/tasks/resumable")
async def get_resumable_tasks():
    """Returns list of tasks that are paused or failed and can be resumed from checkpoint."""
    return {"resumable_tasks": task_engine.get_resumable_tasks()}

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Fetches detailed task state including steps, verification, and resume availability."""
    task = task_engine.get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()

@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    """Resumes a paused/failed task from checkpoint."""
    task = task_engine.resume_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or cannot be resumed")
    
    # Broadcast resume event
    for client in connected_clients:
        try:
            asyncio.create_task(client.send_text(json.dumps({
                "type": "task_state",
                "task_id": task_id,
                "status": "RUNNING",
                "current_step": task.current_step,
                "resumed": True,
                "timestamp": time.strftime("%H:%M:%S")
            })))
        except Exception:
            pass
    
    return {
        "task_id": task_id,
        "status": "RUNNING",
        "resumed_from_step": task.current_step,
        "message": f"Task resumed from step: {task.current_step}"
    }

@app.post("/api/tasks/{task_id}/approve")
async def approve_task_action(task_id: str, payload: Optional[Dict[str, Any]] = None):
    """User authorization for HIGH / CRITICAL risk tool execution with token binding."""
    token = payload.get("operation_token") if payload else None
    success, msg = task_engine.approve_task_action(task_id, operation_token=token)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "APPROVED", "task_id": task_id, "message": msg}

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, payload: Optional[Dict[str, Any]] = None):
    """Cancels an active or paused task safely."""
    reason = payload.get("reason", "User requested cancellation") if payload else "User requested cancellation"
    task = task_engine.cancel_task(task_id, reason=reason)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "CANCELLED", "task_id": task_id, "reason": reason}

@app.get("/api/system/intelligence")
async def get_system_intelligence():
    """Returns full intelligence providers and routing capability matrix."""
    return {
        "active_model": model_router.route("general").name,
        "providers": model_router.get_intelligence_matrix()
    }

@app.get("/api/system/tools")
async def get_system_tools():
    """Returns tools catalogue with permissions and risk levels for Control Center."""
    return {
        "tools": tool_registry.get_tools_catalog(),
        "count": len(tool_registry.get_all_tools())
    }

@app.get("/api/memory/profile")
async def get_memory_profile():
    """Returns human-readable personal profile and preferences for Personal DOOM Memory UI."""
    return {
        "name": user_profile.get_name(),
        "role": user_profile.get_role(),
        "title": user_profile.get_title(),
        "preferences": user_profile.get_preferences(),
        "projects": user_profile.get_projects(),
        "custom_notes": user_profile.get_custom_notes()
    }

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

    from core.cost_guard import CostDecisionAction, ResourceRequest, ResourceType, cost_guard
    tts_decision = cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.TTS,
        provider="edge_tts",
        capability="tts",
        endpoint="speech.platform.bing.com",
    ))
    if tts_decision.action != CostDecisionAction.ALLOW:
        return JSONResponse(
            {"status": "tts_blocked", "message": tts_decision.reason.value},
            status_code=503,
        )

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
    """Generates TypeScript interfaces or Python Pydantic models via Cost-Guard-wrapped ModelRouter."""
    from core.model_router import NoCapableProviderError, model_router

    json_sample = json.dumps(req.json_data, indent=2)[:2000] if not isinstance(req.json_data, str) else req.json_data[:2000]

    prompt = f"""Convert this JSON structure into production-grade {req.target_lang.upper()} code:
JSON:
{json_sample}

Rules:
- If typescript: output clean TypeScript interfaces with strict types.
- If pydantic: output clean Python Pydantic BaseModel classes with Field definitions and type annotations.
- Provide ONLY the code inside ```{req.target_lang.lower()}``` block. No chatty text."""

    try:
        result = model_router.generate(prompt=prompt, task_type="coding")
        out = result.text if hasattr(result, "text") else str(result)
    except NoCapableProviderError:
        out = ""
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
    providers = model_router.get_provider_status()
    metadata = model_router.get_intelligence_matrix()
    # Build model list from router metadata
    models_list = []
    for m in metadata:
        models_list.append({
            "id": m["key"],
            "name": m["name"],
            "model": m["model"],
            "role": m["role"],
            "cost_tier": m["cost_tier"],
            "speed": m.get("role", "").split("(")[-1].replace(")", "") if "(" in m.get("role", "") else "STANDARD",
            "status": "ONLINE" if m["is_available"] and m["is_enabled"] else ("DISABLED" if not m["is_enabled"] else "STANDBY"),
            "desc": m["role"],
            "enabled": m["is_enabled"],
            "available": m["is_available"],
        })
    # Add auto router option
    models_list.append({"id": "auto", "name": "🔄 DOOM Auto-Router", "speed": "DYNAMIC", "status": "ACTIVE", "desc": "Autonomously routes fastest & smartest brain"})
    return {"models": models_list, "provider_status": providers}

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
            from observability.telemetry import request_scope
            with request_scope():
                if selected_model_name == "auto":
                    provider_override = None
                    steps.append({"type": "tool", "title": "DOOM Auto-Router", "desc": "Autonomous router selected best available"})
                elif selected_model_name in model_router.providers:
                    provider_override = selected_model_name
                    steps.append({"type": "tool", "title": "Invoked Model", "desc": f"ModelRouter override={provider_override}"})
                else:
                    provider_override = None
                    steps.append({"type": "tool", "title": "Invoked Model", "desc": "ModelRouter auto (unknown id)"})

                router_result = model_router.generate(
                    prompt=full_prompt,
                    system_prompt=system_prompt,
                    task_type="general",
                    provider_override=provider_override,
                )
                raw_response = _extract_text(router_result)

                if not raw_response.strip():
                    raw_response = "[DOOM] Model returned an empty response. Please try again."

        except Exception as model_err:
            print(f"[AGENT STUDIO] Model error: {type(model_err).__name__}")
            raw_response = f"[DOOM] Model invocation error: {type(model_err).__name__}"

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
                    user_command=req.prompt,
                    response_text=raw_response,
                    tools_used=["agent_studio_chat"],
                    latency_ms=duration_ms
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
            "nim": "nim",
            "bedrock_claude": "bedrock",
            "bedrock_nova": "bedrock",
            "gemini": "gemini",
            "auto": None
        }
        provider = model_map.get(req.model, None)
        response_obj = model_router.generate(
            prompt=full_prompt,
            system_prompt=system_prompt,
            provider_override=provider
        )
        response_text = response_obj.text if hasattr(response_obj, "text") else str(response_obj)
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
                "sentinel_alert": alert,
                "doom_state": state_machine.get_status_payload(),
                "active_task": task_engine.get_active_task_dict()
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

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard_root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse(content="<h1>DOOM HUD static file missing</h1>", status_code=404)

@app.get("/css/style.css")
async def serve_style_css():
    css_path = os.path.join(static_dir, "css", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/css", headers={"Cache-Control": "no-cache"})
    return Response(content="/* CSS missing */", status_code=404, media_type="text/css")

@app.get("/js/app.js")
async def serve_app_js():
    js_path = os.path.join(static_dir, "js", "app.js")
    if os.path.exists(js_path):
        with open(js_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="application/javascript", headers={"Cache-Control": "no-cache"})
    return Response(content="// JS missing", status_code=404, media_type="application/javascript")

@app.get("/ide", response_class=HTMLResponse)
async def serve_ide():
    """Serves the DOOM Integrated Code Editor (CodeMirror 6)."""
    ide_html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DOOM Cyber IDE</title>
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;background:#050b14;color:#e0f2fe;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;overflow:hidden}
.ide-layout{display:flex;flex-direction:column;height:100vh}
.ide-toolbar{display:flex;align-items:center;gap:0.75rem;padding:0.5rem 1rem;background:#0a1628;border-bottom:1px solid rgba(0,240,255,0.12);flex-shrink:0}
.ide-brand{font-family:ui-monospace,Consolas,'Courier New',monospace;font-size:0.72rem;color:#00f0ff;letter-spacing:2px;opacity:0.85}
.ide-file-name{font-family:ui-monospace,Consolas,'Courier New',monospace;font-size:0.78rem;color:#94a3b8;background:rgba(0,240,255,0.06);border:1px solid rgba(0,240,255,0.15);border-radius:4px;padding:0.25rem 0.6rem;min-width:200px}
.ide-toolbar-actions{display:flex;gap:0.5rem;margin-left:auto}
.ide-btn{font-family:ui-monospace,Consolas,'Courier New',monospace;font-size:0.72rem;letter-spacing:0.5px;padding:0.3rem 0.75rem;border-radius:4px;border:1px solid rgba(0,240,255,0.2);background:rgba(0,240,255,0.05);color:#00f0ff;cursor:pointer;transition:all 0.2s}
.ide-btn:hover{background:rgba(0,240,255,0.12);border-color:rgba(0,240,255,0.4)}
.ide-btn.run-btn{border-color:rgba(0,255,157,0.3);background:rgba(0,255,157,0.07);color:#00ff9d}
.ide-btn.run-btn:hover{background:rgba(0,255,157,0.15);border-color:rgba(0,255,157,0.5)}
.ide-btn.clear-btn{border-color:rgba(255,51,102,0.3);color:rgba(255,100,130,0.9)}
.ide-main{display:flex;flex:1;overflow:hidden}
.ide-sidebar{width:180px;background:#080f20;border-right:1px solid rgba(0,240,255,0.08);padding:0.75rem;flex-shrink:0;overflow-y:auto}
.sidebar-section-title{font-size:0.62rem;letter-spacing:1.5px;color:#64748b;text-transform:uppercase;margin-bottom:0.5rem;padding-bottom:0.25rem;border-bottom:1px solid rgba(255,255,255,0.04)}
.sidebar-file{font-family:ui-monospace,Consolas,'Courier New',monospace;font-size:0.72rem;color:#94a3b8;padding:0.3rem 0.5rem;border-radius:4px;cursor:pointer;transition:all 0.15s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sidebar-file:hover{background:rgba(0,240,255,0.07);color:#e0f2fe}
.sidebar-file.active{background:rgba(0,240,255,0.1);color:#00f0ff}
.ide-editor-panel{flex:1;display:flex;flex-direction:column;overflow:hidden}
.ide-editor-wrap{flex:1;position:relative;overflow:auto}
#editor{width:100%;height:100%;font-family:ui-monospace,Consolas,'Courier New',monospace;font-size:13.5px;line-height:1.65;background:#060d1c;color:#e0f2fe;border:none;outline:none;resize:none;padding:1rem 1rem 1rem 3.5rem;tab-size:4;caret-color:#00f0ff}
.line-numbers{position:absolute;left:0;top:0;width:3rem;height:100%;background:#060d1c;border-right:1px solid rgba(255,255,255,0.05);text-align:right;font-family:ui-monospace,Consolas,'Courier New',monospace;font-size:13.5px;line-height:1.65;color:#3a4a5c;padding:1rem 0.5rem 1rem 0;pointer-events:none;user-select:none;overflow:hidden}
.ide-output-panel{height:160px;border-top:1px solid rgba(0,240,255,0.1);background:#04090f;display:flex;flex-direction:column;flex-shrink:0}
.output-header{display:flex;align-items:center;gap:0.5rem;padding:0.4rem 1rem;border-bottom:1px solid rgba(0,240,255,0.08)}
.output-header-label{font-size:0.68rem;letter-spacing:1.5px;color:#64748b;text-transform:uppercase}
.output-status{font-size:0.68rem;font-family:ui-monospace,Consolas,'Courier New',monospace;color:#00ff9d;margin-left:auto}
#output-console{flex:1;font-family:ui-monospace,Consolas,'Courier New',monospace;font-size:0.8rem;color:#94a3b8;padding:0.75rem 1rem;overflow-y:auto;line-height:1.6}
.output-line-ok{color:#00ff9d}.output-line-err{color:#ff3366}.output-line-info{color:#00f0ff}
.status-bar{display:flex;align-items:center;gap:1rem;padding:0.2rem 1rem;background:#030810;border-top:1px solid rgba(0,240,255,0.06);font-size:0.65rem;font-family:ui-monospace,Consolas,'Courier New',monospace;color:#3a4a5c;flex-shrink:0}
.status-item{display:flex;align-items:center;gap:0.3rem}.status-dot{width:6px;height:6px;border-radius:50%;background:#00ff9d}
</style>
</head>
<body>
<div class="ide-layout">
  <div class="ide-toolbar">
    <span class="ide-brand">DOOM IDE</span>
    <input class="ide-file-name" id="file-name-input" value="untitled.py" placeholder="filename.py">
    <div class="ide-toolbar-actions">
      <button class="ide-btn run-btn" id="btn-run">&#9654; Run</button>
      <button class="ide-btn" id="btn-save">&#8659; Save</button>
      <button class="ide-btn clear-btn" id="btn-clear">&#215; Clear</button>
    </div>
  </div>
  <div class="ide-main">
    <div class="ide-sidebar">
      <div class="sidebar-section-title">Files</div>
      <div class="sidebar-file active" data-file="untitled.py">untitled.py</div>
      <div class="sidebar-file" data-file="doom.py">doom.py</div>
      <div class="sidebar-file" data-file="test.py">test.py</div>
      <div class="sidebar-section-title" style="margin-top:1rem">Snippets</div>
      <div class="sidebar-file" data-snippet="hello">Hello World</div>
      <div class="sidebar-file" data-snippet="status">System Status</div>
      <div class="sidebar-file" data-snippet="profile">User Profile</div>
    </div>
    <div class="ide-editor-panel">
      <div class="ide-editor-wrap">
        <div class="line-numbers" id="line-numbers"></div>
        <textarea id="editor" spellcheck="false"># DOOM V3 — Cyber IDE
# Type your Python code here and click Run

def greet(name):
    return f"Online, {name}. DOOM IDE ready."

print(greet("Boss"))
</textarea>
      </div>
      <div class="ide-output-panel">
        <div class="output-header">
          <span class="output-header-label">Output</span>
          <span class="output-status" id="run-status">Ready</span>
        </div>
        <div id="output-console"><span class="output-line-info">&gt; DOOM Cyber IDE initialized. Ready to execute.</span></div>
      </div>
    </div>
  </div>
  <div class="status-bar">
    <span class="status-item"><span class="status-dot"></span> Python 3.11</span>
    <span class="status-item" id="cursor-pos">Ln 1, Col 1</span>
    <span class="status-item" id="char-count">0 chars</span>
    <span style="margin-left:auto">DOOM V3 // Cyber Workspace</span>
  </div>
</div>
<script>
const editor = document.getElementById('editor');
const lineNumbers = document.getElementById('line-numbers');
const output = document.getElementById('output-console');
const runStatus = document.getElementById('run-status');
const cursorPos = document.getElementById('cursor-pos');
const charCount = document.getElementById('char-count');

const snippets = {
  hello: '# Hello World\\nprint("Hello, Boss!")',
  status: 'import psutil\\nc=psutil.cpu_percent()\\nm=psutil.virtual_memory().percent\\nprint(f"CPU: {c}%  RAM: {m}%")',
  profile: 'import json\\nwith open("memory_profile.json") as f:\\n    p=json.load(f)\\n    print("Name:", p["name"])\\n    print("Role:", p["role"])'
};

function updateLineNumbers() {
  const lines = editor.value.split('\\n').length;
  lineNumbers.innerHTML = Array.from({length:lines},(_,i)=>i+1).join('<br>');
}
function updateStats() {
  const lines = editor.value.split('\\n');
  const lineIdx = editor.value.substr(0, editor.selectionStart).split('\\n');
  cursorPos.textContent = `Ln ${lineIdx.length}, Col ${lineIdx[lineIdx.length-1].length+1}`;
  charCount.textContent = `${editor.value.length} chars`;
}
editor.addEventListener('input', () => { updateLineNumbers(); updateStats(); });
editor.addEventListener('keydown', e => {
  if (e.key === 'Tab') { e.preventDefault(); const s=editor.selectionStart; editor.value=editor.value.substring(0,s)+'    '+editor.value.substring(editor.selectionEnd); editor.selectionStart=editor.selectionEnd=s+4; updateLineNumbers(); }
  if (e.key === 'Enter') { setTimeout(updateLineNumbers, 0); }
});
editor.addEventListener('click', updateStats);
editor.addEventListener('keyup', updateStats);

document.getElementById('btn-clear').addEventListener('click', () => { editor.value = ''; updateLineNumbers(); output.innerHTML = '<span class="output-line-info">> Editor cleared.</span>'; });

document.getElementById('btn-save').addEventListener('click', () => {
  const fname = document.getElementById('file-name-input').value || 'untitled.py';
  const blob = new Blob([editor.value], {type:'text/plain'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = fname; a.click();
  output.innerHTML += '<br><span class="output-line-ok">> Saved as ' + fname + '</span>';
});

document.getElementById('btn-run').addEventListener('click', async () => {
  const code = editor.value.trim();
  if (!code) return;
  runStatus.textContent = 'Running...'; runStatus.style.color = '#ffaa00';
  output.innerHTML = '<span class="output-line-info">> Executing via DOOM backend...</span>';
  try {
    const res = await fetch('/api/command', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal:'Run this Python code and show output: ' + code.substring(0,500)})});
    const data = await res.json();
    runStatus.textContent = 'Done'; runStatus.style.color = '#00ff9d';
    output.innerHTML = '<span class="output-line-ok">> ' + (data.response || 'Executed.').replace(/\\n/g,'<br>> ') + '</span>';
  } catch(e) {
    runStatus.textContent = 'Error'; runStatus.style.color = '#ff3366';
    output.innerHTML = '<span class="output-line-err">> Error: ' + e.message + '</span>';
  }
});

document.querySelectorAll('.sidebar-file').forEach(el => {
  el.addEventListener('click', () => {
    const snippet = el.dataset.snippet;
    if (snippet && snippets[snippet]) { editor.value = snippets[snippet]; updateLineNumbers(); return; }
    document.querySelectorAll('.sidebar-file').forEach(f=>f.classList.remove('active'));
    el.classList.add('active');
  });
});

updateLineNumbers(); updateStats();
</script>
</body>
</html>"""
    return HTMLResponse(content=ide_html)



# ─────────────────────────────────────────────────────────────────────────────
# V5.1 Memory Foundation API — /api/v2/memory
# Canonical memory CRUD through MemoryManager. Legacy /api/memory preserved.
# ─────────────────────────────────────────────────────────────────────────────

class MemoryCreateRequest(BaseModel):
    content: str
    memory_type: str = "SEMANTIC"
    source: str = "USER_EXPLICIT"
    importance: float = 0.5
    project_id: Optional[str] = None
    tags: Optional[List[str]] = None
    privacy_class: str = "NORMAL"

class MemoryUpdateRequest(BaseModel):
    content: str

class MemorySearchRequest(BaseModel):
    query: Optional[str] = None
    memory_type: Optional[str] = None
    project_id: Optional[str] = None
    limit: int = 20


@app.get("/api/v2/memory")
async def get_all_memories(
    memory_type: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """List ACTIVE memory records. Optionally filter by type or project."""
    try:
        from memory.manager import memory_manager
        from memory.types import MemoryType
        mt = MemoryType(memory_type) if memory_type else None
        records = memory_manager.search(memory_type=mt, project_id=project_id, limit=limit)
        return {
            "status": "ok",
            "count": len(records),
            "memories": [r.to_dict() for r in records],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v2/memory/{memory_id}")
async def get_memory(memory_id: str):
    """Fetch a single memory record by ID."""
    try:
        from memory.manager import memory_manager
        record = memory_manager.get(memory_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found")
        return {"status": "ok", "memory": record.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/memory")
async def create_memory(req: MemoryCreateRequest):
    """Store a new memory record through MemoryManager (policy enforced)."""
    try:
        from memory.manager import memory_manager
        from memory.schemas import MemoryRecord
        from memory.types import MemoryType, MemorySource, PrivacyClass

        record = MemoryRecord(
            memory_type=MemoryType(req.memory_type),
            content=req.content,
            source=MemorySource(req.source),
            importance=req.importance,
            project_id=req.project_id,
            tags=req.tags or [],
            privacy_class=PrivacyClass(req.privacy_class),
        )
        stored = memory_manager.store(record)
        if not stored:
            raise HTTPException(status_code=422, detail="Memory rejected by policy gate")
        broadcast_hud_event({"type": "memory_event", "event": "MEMORY_STORED",
                              "memory_id": stored.memory_id,
                              "memory_type": stored.memory_type.value})
        return {"status": "ok", "memory": stored.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/v2/memory/{memory_id}")
async def update_memory(memory_id: str, req: MemoryUpdateRequest):
    """Update the content of an existing ACTIVE memory."""
    try:
        from memory.manager import memory_manager
        success = memory_manager.update(memory_id, req.content)
        if not success:
            raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found or not ACTIVE")
        broadcast_hud_event({"type": "memory_event", "event": "MEMORY_UPDATED", "memory_id": memory_id})
        return {"status": "ok", "memory_id": memory_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v2/memory/{memory_id}")
async def delete_memory(memory_id: str):
    """Logically delete a memory record (status → DELETED, record preserved in DB)."""
    try:
        from memory.manager import memory_manager
        success = memory_manager.delete(memory_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found")
        broadcast_hud_event({"type": "memory_event", "event": "MEMORY_DELETED", "memory_id": memory_id})
        return {"status": "ok", "memory_id": memory_id, "action": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/memory/search")
async def search_memories(req: MemorySearchRequest):
    """Search memory records by query text, type, and project."""
    try:
        from memory.manager import memory_manager
        from memory.types import MemoryType
        mt = MemoryType(req.memory_type) if req.memory_type else None
        records = memory_manager.search(
            query=req.query,
            memory_type=mt,
            project_id=req.project_id,
            limit=req.limit,
        )
        return {
            "status": "ok",
            "count": len(records),
            "memories": [r.to_dict() for r in records],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v2/memory/stats")
async def get_memory_stats():
    """Return V5.1 memory system statistics."""
    try:
        from memory.manager import memory_manager
        count = memory_manager.count()
        telemetry = memory_manager.telemetry.to_dict()
        db_stats = {}
        if postgres_manager.is_connected():
            db_stats = postgres_manager.get_memory_table_stats()
        return {
            "status": "ok",
            "active_count": count,
            "db_breakdown": db_stats,
            "telemetry": telemetry,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    print("[*] Starting DOOM V3 OS Dashboard on http://localhost:8000 ...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
