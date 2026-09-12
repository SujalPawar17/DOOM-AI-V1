"""Explicit capability catalog. Reports availability; never executes."""

from __future__ import annotations

from typing import Dict

from orchestration.goal.types import CapabilityClass, CapabilityRecord
from proactive.config import (
    is_act_calendar_hold_enabled,
    is_act_enabled,
    is_act_internal_enabled,
    is_computer_browser_enabled,
    is_computer_click_enabled,
    is_computer_enabled,
    is_computer_filesystem_enabled,
    is_computer_observe_enabled,
    is_computer_sequences_enabled,
    is_computer_type_enabled,
    is_computer_verification_enabled,
    is_proactive_enabled,
    is_v8_enabled,
)


def _computer_enabled() -> bool:
    return is_computer_enabled() and (
        is_computer_observe_enabled()
        or is_computer_click_enabled()
        or is_computer_type_enabled()
    )


def _world_enabled() -> bool:
    return is_proactive_enabled() and is_act_enabled() and (
        is_act_internal_enabled() or is_act_calendar_hold_enabled()
    )


def catalog_snapshot() -> Dict[CapabilityClass, CapabilityRecord]:
    v8 = is_v8_enabled()
    rows = (
        CapabilityRecord(
            CapabilityClass.COMPUTER, "V7", True, _computer_enabled(),
            v8 and _computer_enabled(), False,
        ),
        CapabilityRecord(
            CapabilityClass.BROWSER, "V7", True,
            is_computer_enabled() and is_computer_browser_enabled(),
            v8 and is_computer_enabled() and is_computer_browser_enabled(), False,
        ),
        CapabilityRecord(
            CapabilityClass.FILESYSTEM, "V7", True,
            is_computer_enabled() and is_computer_filesystem_enabled(),
            v8 and is_computer_enabled() and is_computer_filesystem_enabled(), False,
        ),
        CapabilityRecord(
            CapabilityClass.SEQUENCE, "V7", True,
            is_computer_enabled() and is_computer_sequences_enabled(),
            v8 and is_computer_enabled() and is_computer_sequences_enabled(), False,
        ),
        CapabilityRecord(
            CapabilityClass.VERIFICATION, "V7", True,
            is_computer_enabled() and is_computer_verification_enabled(),
            v8 and is_computer_enabled() and is_computer_verification_enabled(), False,
        ),
        CapabilityRecord(
            CapabilityClass.WORLD_ACT, "V6", True, _world_enabled(),
            v8 and _world_enabled(), False,
        ),
        CapabilityRecord(
            CapabilityClass.MEMORY_READ, "V5", True, True,
            v8, False,
        ),
        CapabilityRecord(
            CapabilityClass.CONVERSATION, "V8", True, v8, v8, False,
        ),
        CapabilityRecord(
            CapabilityClass.NONE, "none", False, False, False, False,
        ),
    )
    return {row.capability_class: row for row in rows}


def lookup(capability: CapabilityClass) -> CapabilityRecord:
    snap = catalog_snapshot()
    return snap.get(capability, snap[CapabilityClass.NONE])
