"""Deterministic local system observation. Read-only. No shell. No secrets."""

from __future__ import annotations

import platform
import re
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Bounded probe timeouts (seconds).
_PROBE_TIMEOUT = 0.4
_CPU_SAMPLE_SEC = 0.15
DASHBOARD_PORT = 8000
OLLAMA_PORT = 11434

_DRIVE_LABEL = re.compile(r"^[A-Za-z]:")
_SECRETISH = re.compile(
    r"(?i)(password|api[_-]?key|secret|token|csrf|session_id|owner_id|"
    r"authorization|bearer|cookie|plan_hash|\\\\Users\\\\)"
)


@dataclass(frozen=True)
class DiskObservation:
    label: str
    free_gb: float
    total_gb: float
    percent_used: float


@dataclass(frozen=True)
class SystemObservation:
    timestamp_unix: float
    cpu_percent: float
    memory_total_gb: float
    memory_used_gb: float
    memory_available_gb: float
    memory_percent: float
    disks: Tuple[DiskObservation, ...]
    os_name: str
    os_version: str
    architecture: str
    python_version: str
    ollama_status: str  # running | unavailable
    doom_dashboard_status: str  # running | unavailable
    health: str  # HEALTHY | UNDER_MEMORY_PRESSURE | DEGRADED

    def as_public_dict(self) -> Dict[str, Any]:
        """Bounded public fields only. No secrets, paths, usernames, or IDs."""
        return {
            "cpu_percent": self.cpu_percent,
            "memory_total_gb": self.memory_total_gb,
            "memory_used_gb": self.memory_used_gb,
            "memory_available_gb": self.memory_available_gb,
            "memory_percent": self.memory_percent,
            "disks": [
                {
                    "label": d.label,
                    "free_gb": d.free_gb,
                    "total_gb": d.total_gb,
                    "percent_used": d.percent_used,
                }
                for d in self.disks
            ],
            "os_name": self.os_name,
            "os_version": self.os_version,
            "architecture": self.architecture,
            "python_version": self.python_version,
            "ollama_status": self.ollama_status,
            "doom_dashboard_status": self.doom_dashboard_status,
            "health": self.health,
        }


def _localhost_port_open(port: int, timeout: float = _PROBE_TIMEOUT) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def probe_ollama_status() -> str:
    """Local loopback only. Never starts Ollama. Never downloads models."""
    try:
        if _localhost_port_open(OLLAMA_PORT):
            # Prefer existing provider health when available (Cost Guard gated).
            try:
                from models.ollama_provider import OllamaProvider
                if OllamaProvider().is_available():
                    return "running"
            except Exception:
                pass
            return "running"
        return "unavailable"
    except Exception:
        return "unavailable"


def probe_dashboard_status() -> str:
    """Local loopback port probe only. Never restarts or starts the dashboard."""
    try:
        return "running" if _localhost_port_open(DASHBOARD_PORT) else "unavailable"
    except Exception:
        return "unavailable"


def classify_health(
    *,
    cpu_percent: float,
    memory_percent: float,
    disks: Tuple[DiskObservation, ...],
) -> str:
    """Deterministic health label. Not decided by an LLM."""
    if memory_percent >= 95.0 or cpu_percent >= 95.0:
        return "DEGRADED"
    for d in disks:
        if d.free_gb < 1.0:
            return "DEGRADED"
    if memory_percent >= 85.0:
        return "UNDER_MEMORY_PRESSURE"
    return "HEALTHY"


def _safe_os_name() -> str:
    sysname = platform.system() or "Unknown"
    if sysname.lower() == "windows":
        # Bound to major family only (no username / hostname).
        ver = platform.version() or ""
        # Windows 10 and 11 share NT 10.0; report generically.
        if ver.startswith("10.") or "10." in (platform.release() or ""):
            return "Windows 10/11"
        return "Windows"
    return sysname[:32]


def _safe_os_version() -> str:
    rel = (platform.release() or "")[:32]
    return rel or "unknown"


def _collect_disks() -> Tuple[DiskObservation, ...]:
    import psutil
    out: List[DiskObservation] = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        parts = []
    seen = set()
    for part in parts:
        try:
            opts = (part.opts or "").lower()
            if "cdrom" in opts or "removable" in opts:
                continue
            mount = str(part.mountpoint or "")
            if not mount or _SECRETISH.search(mount):
                continue
            # Windows drive letter only; otherwise skip (avoid path leakage).
            label = ""
            if _DRIVE_LABEL.match(mount.replace("/", "\\")):
                label = mount[0].upper() + ":"
            elif mount in ("/",):
                label = "/"
            else:
                continue
            if label in seen:
                continue
            usage = psutil.disk_usage(mount)
            seen.add(label)
            out.append(DiskObservation(
                label=label,
                free_gb=round(usage.free / (1024**3), 1),
                total_gb=round(usage.total / (1024**3), 1),
                percent_used=round(float(usage.percent), 1),
            ))
        except Exception:
            continue
        if len(out) >= 8:
            break
    if not out:
        try:
            usage = psutil.disk_usage("/")
            out.append(DiskObservation(
                label="C:" if platform.system().lower() == "windows" else "/",
                free_gb=round(usage.free / (1024**3), 1),
                total_gb=round(usage.total / (1024**3), 1),
                percent_used=round(float(usage.percent), 1),
            ))
        except Exception:
            pass
    return tuple(out)


def collect_system_observation(
    *,
    include_services: bool = True,
    cpu_interval: Optional[float] = None,
) -> SystemObservation:
    """One-shot read-only observation. No background loop. No mutation."""
    import psutil
    interval = _CPU_SAMPLE_SEC if cpu_interval is None else float(cpu_interval)
    interval = max(0.0, min(interval, 0.5))
    try:
        cpu = float(psutil.cpu_percent(interval=interval if interval > 0 else None))
    except Exception:
        cpu = 0.0
    try:
        mem = psutil.virtual_memory()
        total_gb = round(mem.total / (1024**3), 1)
        used_gb = round(mem.used / (1024**3), 1)
        avail_gb = round(mem.available / (1024**3), 1)
        mem_pct = round(float(mem.percent), 1)
    except Exception:
        total_gb = used_gb = avail_gb = mem_pct = 0.0

    disks = _collect_disks()
    ollama = probe_ollama_status() if include_services else "unavailable"
    dash = probe_dashboard_status() if include_services else "unavailable"
    health = classify_health(cpu_percent=cpu, memory_percent=mem_pct, disks=disks)
    arch = (platform.machine() or "unknown")[:16]
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    return SystemObservation(
        timestamp_unix=time.time(),
        cpu_percent=round(cpu, 1),
        memory_total_gb=total_gb,
        memory_used_gb=used_gb,
        memory_available_gb=avail_gb,
        memory_percent=mem_pct,
        disks=disks,
        os_name=_safe_os_name(),
        os_version=_safe_os_version(),
        architecture=arch,
        python_version=py_ver,
        ollama_status=ollama,
        doom_dashboard_status=dash,
        health=health,
    )


def _health_phrase(health: str) -> str:
    if health == "HEALTHY":
        return "Healthy"
    if health == "UNDER_MEMORY_PRESSURE":
        return "Under memory pressure"
    return "Degraded"


def format_system_report(obs: SystemObservation, query: str = "") -> str:
    """Deterministic answer text. Never invokes an LLM."""
    q = " ".join(str(query or "").lower().split())
    lines: List[str] = []

    want_cpu = bool(re.search(r"\bcpu\b", q))
    want_ram = bool(re.search(r"\b(ram|memory)\b", q))
    want_disk = bool(re.search(r"\bdisk\b", q))
    want_ollama = bool(re.search(r"\bollama\b", q))
    want_dash = bool(re.search(r"\b(dashboard|doom dashboard)\b", q))
    want_health = bool(re.search(r"\bhealthy\b|\bhealth\b", q))
    want_full = bool(re.search(
        r"\bsystem status\b|\bsystem (state|info|information)\b|"
        r"\bcurrent system\b|\bhow is (my )?system\b|"
        r"\bwhat('?s| is) my (current )?system\b",
        q,
    )) or (not any((want_cpu, want_ram, want_disk, want_ollama, want_dash, want_health)))

    if want_full or (want_health and not any((want_cpu, want_ram, want_disk, want_ollama, want_dash))):
        lines.append("DOOM system status:")
        lines.append(f"CPU: {obs.cpu_percent:.0f}%")
        lines.append(
            f"RAM: {obs.memory_used_gb:.1f} GB / {obs.memory_total_gb:.1f} GB "
            f"({obs.memory_percent:.0f}%)"
        )
        if obs.disks:
            d0 = obs.disks[0]
            lines.append(f"Disk {d0.label} {d0.free_gb:.0f} GB free / {d0.total_gb:.0f} GB")
            for d in obs.disks[1:3]:
                lines.append(f"Disk {d.label} {d.free_gb:.0f} GB free / {d.total_gb:.0f} GB")
        lines.append(f"Ollama: {'Running' if obs.ollama_status == 'running' else 'Unavailable'}")
        lines.append(
            f"Dashboard: {'Running' if obs.doom_dashboard_status == 'running' else 'Unavailable'}"
        )
        lines.append(f"Overall: {_health_phrase(obs.health)}")
        return "\n".join(lines)[:2048]

    if want_cpu:
        lines.append(f"CPU usage: {obs.cpu_percent:.0f}%")
    if want_ram:
        lines.append(
            f"RAM: {obs.memory_used_gb:.1f} GB / {obs.memory_total_gb:.1f} GB "
            f"({obs.memory_percent:.0f}%)"
        )
    if want_disk:
        if not obs.disks:
            lines.append("Disk usage is currently unavailable.")
        else:
            for d in obs.disks[:4]:
                lines.append(f"{d.label} {d.free_gb:.0f} GB free / {d.total_gb:.0f} GB")
    if want_ollama:
        if obs.ollama_status == "running":
            lines.append("Ollama is running.")
        else:
            lines.append("Ollama is unavailable.")
    if want_dash:
        if obs.doom_dashboard_status == "running":
            lines.append("DOOM dashboard is running on localhost.")
        else:
            lines.append("DOOM dashboard is unavailable.")
    if want_health and lines:
        lines.append(f"Overall: {_health_phrase(obs.health)}")
    elif want_health:
        lines.append(f"Overall: {_health_phrase(obs.health)}")

    if not lines:
        # Fallback full summary.
        return format_system_report(obs, "system status")
    return "\n".join(lines)[:2048]


def report_system_status(query: str = "") -> str:
    obs = collect_system_observation()
    return format_system_report(obs, query)


def assert_observation_is_inert(text: str) -> bool:
    """True when text looks like observation data, not executable instructions."""
    blob = str(text or "")
    if _SECRETISH.search(blob):
        return False
    return True
