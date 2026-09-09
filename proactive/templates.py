"""INFORM templates. Factual, no future claims, no memory bodies."""

from __future__ import annotations

from typing import Any, Dict

TEMPLATES = {
    "host_disk_critical": "Host disk utilization is at {disk_percent} percent.",
    "host_disk_high": "Host disk utilization is at {disk_percent} percent.",
    "provider_circuit_open": "A model provider circuit is open. Count={open_count}.",
    "task_failed": "A task reported status FAILED. id={entity_id}.",
    "task_blocked": "A task is waiting or paused. id={entity_id}.",
    "strategy_failure": "A strategy failure signal was recorded. id={entity_id}.",
    "inactivity_7d": "No recent verified activity for at least 7 days was detected.",
    "experience_failure": "A verified experience recorded outcome FAILURE. id={entity_id}.",
    "generic_event": "An internal operational signal was classified as notable. type={insight_type}.",
}


def render_inform(template_id: str, params: Dict[str, Any]) -> str:
    tmpl = TEMPLATES.get(template_id, TEMPLATES["generic_event"])
    safe = {k: v for k, v in params.items() if isinstance(v, (str, int, float, bool))}
    try:
        return tmpl.format(**safe)
    except Exception:
        return TEMPLATES["generic_event"].format(insight_type=params.get("insight_type", "event"))
