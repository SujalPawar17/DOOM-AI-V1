"""DOOM V6.1 Proactive Foundation. INFORM-only. Feature-flagged. Not a second OS."""

from proactive.config import is_proactive_enabled
from proactive.ingest import ingest_signal, normalize_signal
from proactive.worker import process_once, start_proactive_worker, stop_proactive_worker

__all__ = [
    "is_proactive_enabled",
    "ingest_signal",
    "normalize_signal",
    "process_once",
    "start_proactive_worker",
    "stop_proactive_worker",
]
