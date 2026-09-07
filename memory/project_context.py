"""
DOOM V5.3.7.1 — Canonical Project Context Resolution Subsystem
Provides authoritative, deterministic project-context resolution and propagation.

Invariants:
- Explicit supplied project_id is NEVER silently replaced with 'doom'.
- Project existence is validated against the authoritative ProjectExperienceEngine.
- Unknown explicit projects are rejected (strict) or flagged INVALID.
- Projects are never automatically inferred using an LLM.
- Safe default ('doom') is preserved when no project context is provided.
- Privacy boundaries (NORMAL, PRIVATE, SENSITIVE) are strictly preserved.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("DOOM.ProjectContext")


class ProjectResolutionStatus(str, Enum):
    """Resolution status of project context."""
    RESOLVED = "RESOLVED"      # Explicitly resolved and validated against project authority
    DEFAULTED = "DEFAULTED"    # Safely defaulted to 'doom' in absence of project context
    FALLBACK = "FALLBACK"      # Fallback mode (e.g. offline/unconnected database)
    INVALID = "INVALID"        # Unknown or invalid project identifier


@dataclass
class ProjectContext:
    """
    Canonical runtime project context entity.
    Carries verified project identity and provenance across the cognitive pipeline.
    """
    project_id: str
    source: str = "default"  # "explicit", "workspace", "session", "default"
    resolution_status: ProjectResolutionStatus = ProjectResolutionStatus.DEFAULTED
    confidence: float = 1.0
    session_id: Optional[str] = None
    workspace_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.resolution_status in (
            ProjectResolutionStatus.RESOLVED,
            ProjectResolutionStatus.DEFAULTED,
            ProjectResolutionStatus.FALLBACK,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "source": self.source,
            "resolution_status": self.resolution_status.value if isinstance(self.resolution_status, Enum) else self.resolution_status,
            "confidence": self.confidence,
            "session_id": self.session_id,
            "workspace_path": self.workspace_path,
            "metadata": self.metadata,
        }


def resolve_project_context(
    explicit_project_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    strict: bool = True,
) -> ProjectContext:
    """
    Canonical Project Context Resolution function.
    Resolves project_id from:
    1. Explicitly supplied project_id parameter
    2. Context dictionary ('project_id', 'workspace_project', 'workspace_id', 'project', 'workspace')
    3. Session provenance ('session_project', 'session')
    4. Default fallback: 'doom' (status = DEFAULTED)

    Rules:
    - If an explicit project_id is given, it is validated against ProjectExperienceEngine.
    - If database is connected and project does not exist:
        - If strict=True: raises ProjectNotFoundError.
        - If strict=False: returns ProjectContext with resolution_status=INVALID.
    - An explicit project_id is NEVER silently converted to 'doom'.
    - If no project is provided, safely returns 'doom' with DEFAULTED status.
    """
    from memory.project_models import ProjectNotFoundError

    candidate_id: Optional[str] = None
    source = "default"
    meta: Dict[str, Any] = {}

    ctx = dict(context or {})

    # 1. Check explicit parameter
    if explicit_project_id and str(explicit_project_id).strip():
        candidate_id = str(explicit_project_id).strip().lower()
        source = "explicit"
    # 2. Check context dict for explicit project_id
    elif ctx.get("project_id") and str(ctx["project_id"]).strip():
        candidate_id = str(ctx["project_id"]).strip().lower()
        source = "explicit"
    # 3. Check workspace context
    elif ctx.get("workspace_project") and str(ctx["workspace_project"]).strip():
        candidate_id = str(ctx["workspace_project"]).strip().lower()
        source = "workspace"
    elif ctx.get("workspace_id") and str(ctx["workspace_id"]).strip():
        candidate_id = str(ctx["workspace_id"]).strip().lower()
        source = "workspace"
    elif ctx.get("workspace") and isinstance(ctx["workspace"], str) and str(ctx["workspace"]).strip():
        candidate_id = str(ctx["workspace"]).strip().lower()
        source = "workspace"
    # 4. Check session context
    elif ctx.get("session_project") and str(ctx["session_project"]).strip():
        candidate_id = str(ctx["session_project"]).strip().lower()
        source = "session"
    elif session_id and str(session_id).strip():
        meta["session_id"] = str(session_id).strip()

    if workspace_path:
        meta["workspace_path"] = str(workspace_path).strip()
    if ctx.get("workspace_path"):
        meta["workspace_path"] = str(ctx["workspace_path"]).strip()

    # 5. Fallback to default if no candidate
    if not candidate_id:
        return ProjectContext(
            project_id="doom",
            source="default",
            resolution_status=ProjectResolutionStatus.DEFAULTED,
            confidence=1.0,
            session_id=session_id or ctx.get("session_id"),
            workspace_path=workspace_path or ctx.get("workspace_path"),
            metadata=meta,
        )

    # 6. Validate candidate against authoritative ProjectExperienceEngine
    from database.postgres_db import postgres_manager
    if postgres_manager.is_connected():
        try:
            from memory.project_engine import project_experience_engine
            proj = project_experience_engine.get_project(candidate_id)
            if proj is not None:
                meta["project_name"] = proj.name
                meta["privacy_class"] = proj.privacy_class.value if hasattr(proj.privacy_class, "value") else str(proj.privacy_class)
                return ProjectContext(
                    project_id=candidate_id,
                    source=source,
                    resolution_status=ProjectResolutionStatus.RESOLVED,
                    confidence=1.0,
                    session_id=session_id or ctx.get("session_id"),
                    workspace_path=workspace_path or ctx.get("workspace_path"),
                    metadata=meta,
                )
            else:
                if strict:
                    raise ProjectNotFoundError(
                        f"Project '{candidate_id}' not found in authoritative project registry."
                    )
                return ProjectContext(
                    project_id=candidate_id,
                    source=source,
                    resolution_status=ProjectResolutionStatus.INVALID,
                    confidence=0.0,
                    session_id=session_id or ctx.get("session_id"),
                    workspace_path=workspace_path or ctx.get("workspace_path"),
                    metadata=meta,
                )
        except ProjectNotFoundError:
            raise
        except Exception as e:
            logger.warning(f"[PROJECT CONTEXT] Project check failed ({e}), using FALLBACK: {candidate_id}")
            return ProjectContext(
                project_id=candidate_id,
                source=source,
                resolution_status=ProjectResolutionStatus.FALLBACK,
                confidence=0.8,
                session_id=session_id or ctx.get("session_id"),
                workspace_path=workspace_path or ctx.get("workspace_path"),
                metadata=meta,
            )

    # Offline / Unconnected DB mode
    return ProjectContext(
        project_id=candidate_id,
        source=source,
        resolution_status=ProjectResolutionStatus.FALLBACK,
        confidence=0.9,
        session_id=session_id or ctx.get("session_id"),
        workspace_path=workspace_path or ctx.get("workspace_path"),
        metadata=meta,
    )
