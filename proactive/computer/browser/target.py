"""Structured page-target resolution. No coordinates, OCR, or screenshots."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from proactive.computer.browser.types import AncestryNode, BrowserElement, BrowserTarget, Status


def _eq(a: str, b: str) -> bool:
    return str(a or "") == str(b or "")


def _set(v: str) -> bool:
    return bool(str(v or "").strip())


def _ancestry_ok(el: BrowserElement, expected: Sequence[AncestryNode]) -> bool:
    if not expected:
        return True
    got = list(el.ancestry or ())
    if len(got) < len(expected):
        return False
    for i, spec in enumerate(expected):
        node = got[i]
        if _set(spec.role) and not _eq(spec.role, node.role):
            return False
        if _set(spec.name) and not _eq(spec.name, node.name):
            return False
        if _set(spec.element_id) and not _eq(spec.element_id, node.element_id):
            return False
    return True


def matches(el: BrowserElement, ident: BrowserTarget) -> bool:
    if _set(ident.element_id) and not _eq(ident.element_id, el.element_id):
        return False
    if _set(ident.test_id) and not _eq(ident.test_id, el.test_id):
        return False
    if _set(ident.role) and not _eq(ident.role, el.role):
        return False
    if _set(ident.name) and not _eq(ident.name, el.name):
        return False
    if _set(ident.input_type) and not _eq(ident.input_type, el.input_type):
        return False
    return _ancestry_ok(el, ident.ancestry or ())


def resolve_target(
    elements: Sequence[BrowserElement],
    ident: BrowserTarget,
    *,
    origin: str = "",
) -> Tuple[Optional[BrowserElement], Status]:
    if ident is None or not ident.is_deterministic():
        return None, Status.TARGET_AMBIGUOUS
    if _set(ident.origin) and origin and ident.origin != origin:
        return None, Status.PRECONDITION_FAILED
    found: List[BrowserElement] = [el for el in elements if matches(el, ident)]
    if not found:
        return None, Status.TARGET_NOT_FOUND
    if len(found) > 1:
        return None, Status.TARGET_AMBIGUOUS
    return found[0], Status.SUCCESS
