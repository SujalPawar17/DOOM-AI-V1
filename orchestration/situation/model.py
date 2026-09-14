"""V8.20 typed Situation Model. Informational only — never authorizes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class DerivedFactors:
    cpu_load: str = "NORMAL"  # NORMAL | ELEVATED | HIGH
    ram_pressure: str = "NORMAL"
    disk_pressure: str = "NORMAL"
    ollama: str = "UNAVAILABLE"  # AVAILABLE | UNAVAILABLE
    system_health: str = "HEALTHY"  # HEALTHY | UNDER_MEMORY_PRESSURE | DEGRADED


@dataclass(frozen=True)
class SituationModel:
    """Bounded advisory snapshot. No owner/session/plan/CSRF/DB IDs or secrets."""

    user_request: str = ""
    relevant_conversation: Tuple[str, ...] = ()
    relevant_memory: Tuple[str, ...] = ()
    system_observation: str = ""
    doom_state: str = ""
    derived_factors: DerivedFactors = field(default_factory=DerivedFactors)
    includes_memory: bool = False
    includes_system: bool = False
    includes_conversation: bool = False

    def factor_map(self) -> Dict[str, str]:
        f = self.derived_factors
        return {
            "CPU_LOAD": f.cpu_load,
            "RAM_PRESSURE": f.ram_pressure,
            "DISK_PRESSURE": f.disk_pressure,
            "OLLAMA": f.ollama,
            "SYSTEM_HEALTH": f.system_health,
        }
