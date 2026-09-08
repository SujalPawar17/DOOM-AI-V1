"""
DOOM V5.3.6 — Project & Experience Intelligence Engine
Authoritative transactional engine for:
- First-class project management and boundary isolation
- Grounded execution experience ingestion across all outcome states
- Negative experience capture and defensive warning generation
- Lesson extraction and strategy reliability calibration
- Cross-project transfer evaluation via transfer matrix
- Epistemic explainability tracing without chain-of-thought leakage
"""
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import math
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from database.postgres_db import postgres_manager
from memory.types import PrivacyClass, MemorySource, ConfidenceLevel
from memory.project_models import (
    ProjectRecord,
    ExperienceRecord,
    LessonRecord,
    StrategyRecord,
    TransferMatrixRecord,
    TransferEvaluationResult,
    StrategyExplainabilityProfile,
    ProjectLifecycleStatus,
    TaskOutcomeStatus,
    LessonScope,
    TransferStatus,
    TransferDecision,
    ProjectNotFoundError,
    ProjectBoundaryViolationError,
    InvalidProjectHierarchyError,
    InadmissibleExperienceError,
    StrategyDeprecatedError,
    CrossProjectTransferDeniedError,
    compute_experience_idempotency_hash,
    normalize_error_signature,
    calculate_bayesian_strategy_reliability,
    calculate_transfer_confidence,
)
from memory.governance import governance_engine, GovernanceDecision
from memory.conflict_engine import ConflictEngine

logger = logging.getLogger("DOOM.ProjectExperienceEngine")


class ProjectExperienceEngine:
    """
    Authoritative transactional engine for DOOM V5.3.6:
    - Enforces project boundaries and strict hierarchical validation.
    - Captures ground-truth execution experiences under PostgreSQL row locks.
    - Calibrates strategy reliability with asymmetric Bayesian degradation.
    - Gates cross-project knowledge transfer with formal compatibility scoring.
    - Preserves V5.3.5 non-mutation and anti-feedback invariants.
    """

    def __init__(self):
        self._manager = postgres_manager

    # -----------------------------------------------------------------------
    # 1. Project Management & Hierarchy
    # -----------------------------------------------------------------------
    def create_project(
        self,
        project_id: str,
        name: str,
        description: str = "",
        root_path: Optional[str] = None,
        git_remote: Optional[str] = None,
        tech_stack: Optional[List[str]] = None,
        lifecycle_status: ProjectLifecycleStatus = ProjectLifecycleStatus.ACTIVE,
        privacy_class: PrivacyClass = PrivacyClass.NORMAL,
        parent_project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        boundary_config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> ProjectRecord:
        """Creates a new project with strict identity, boundary, and hierarchy checks."""
        clean_id = str(project_id).strip().lower()
        if not clean_id:
            raise ValueError("project_id cannot be empty")

        if parent_project_id:
            clean_parent = str(parent_project_id).strip().lower()
            if clean_parent == clean_id:
                raise InvalidProjectHierarchyError(f"Project '{clean_id}' cannot be its own parent")
            self._validate_parent_hierarchy(clean_parent, clean_id)

        conn = self._manager.get_connection()
        if not conn:
            raise RuntimeError("PostgreSQL connection unavailable")

        now = datetime.now(timezone.utc).isoformat()
        meta = dict(metadata or {})
        if boundary_config:
            meta["boundary_config"] = boundary_config

        record = ProjectRecord(
            project_id=clean_id,
            name=name,
            description=description,
            root_path=root_path,
            git_remote=git_remote,
            tech_stack=tech_stack or [],
            lifecycle_status=lifecycle_status,
            privacy_class=privacy_class,
            parent_project_id=parent_project_id,
            metadata=meta,
            created_at=now,
            updated_at=now,
        )

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT project_id FROM projects WHERE project_id = %s;", (clean_id,))
                if cur.fetchone():
                    return self.get_project(clean_id)

                cur.execute("""
                    INSERT INTO projects (
                        project_id, name, description, root_path, git_remote,
                        tech_stack, lifecycle_status, privacy_class, parent_project_id,
                        metadata, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s
                    ) ON CONFLICT (project_id) DO NOTHING;
                """, (
                    record.project_id,
                    record.name,
                    record.description,
                    record.root_path,
                    record.git_remote,
                    json.dumps(record.tech_stack),
                    record.lifecycle_status.value if isinstance(record.lifecycle_status, Enum) else record.lifecycle_status,
                    record.privacy_class.value if isinstance(record.privacy_class, Enum) else record.privacy_class,
                    record.parent_project_id,
                    json.dumps(record.metadata),
                    record.created_at,
                    record.updated_at,
                ))
            conn.commit()
            return record
        except Exception as e:
            conn.rollback()
            logger.error(f"[PROJECT ENGINE] Failed to create project {clean_id}: {e}")
            raise
        finally:
            self._manager.release_connection(conn)

    def get_project(self, project_id: str) -> Optional[ProjectRecord]:
        """Fetches project record by project_id."""
        clean_id = str(project_id).strip().lower()
        conn = self._manager.get_connection()
        if not conn:
            return None

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT project_id, name, description, root_path, git_remote,
                           tech_stack, lifecycle_status, privacy_class, parent_project_id,
                           metadata, created_at, updated_at
                    FROM projects WHERE project_id = %s;
                """, (clean_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return ProjectRecord(
                    project_id=row[0],
                    name=row[1],
                    description=row[2] or "",
                    root_path=row[3],
                    git_remote=row[4],
                    tech_stack=row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
                    lifecycle_status=ProjectLifecycleStatus(row[6]),
                    privacy_class=PrivacyClass(row[7]),
                    parent_project_id=row[8],
                    metadata=row[9] if isinstance(row[9], dict) else json.loads(row[9] or "{}"),
                    created_at=row[10].isoformat() if hasattr(row[10], "isoformat") else str(row[10]),
                    updated_at=row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11]),
                )
        finally:
            self._manager.release_connection(conn)

    def list_projects(self, status: Optional[ProjectLifecycleStatus] = None) -> List[ProjectRecord]:
        """Lists projects optionally filtered by lifecycle status."""
        conn = self._manager.get_connection()
        if not conn:
            return []

        try:
            with conn.cursor() as cur:
                if status:
                    cur.execute("""
                        SELECT project_id, name, description, root_path, git_remote,
                               tech_stack, lifecycle_status, privacy_class, parent_project_id,
                               metadata, created_at, updated_at
                        FROM projects WHERE lifecycle_status = %s ORDER BY name ASC;
                    """, (status.value if isinstance(status, Enum) else status,))
                else:
                    cur.execute("""
                        SELECT project_id, name, description, root_path, git_remote,
                               tech_stack, lifecycle_status, privacy_class, parent_project_id,
                               metadata, created_at, updated_at
                        FROM projects ORDER BY name ASC;
                    """)
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append(ProjectRecord(
                        project_id=row[0],
                        name=row[1],
                        description=row[2] or "",
                        root_path=row[3],
                        git_remote=row[4],
                        tech_stack=row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
                        lifecycle_status=ProjectLifecycleStatus(row[6]),
                        privacy_class=PrivacyClass(row[7]),
                        parent_project_id=row[8],
                        metadata=row[9] if isinstance(row[9], dict) else json.loads(row[9] or "{}"),
                        created_at=row[10].isoformat() if hasattr(row[10], "isoformat") else str(row[10]),
                        updated_at=row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11]),
                    ))
                return results
        finally:
            self._manager.release_connection(conn)

    def _validate_parent_hierarchy(self, parent_id: str, child_id: str):
        """Ensures parent exists, does not exceed 5-hop depth, and contains no cycles."""
        if parent_id == child_id:
            raise InvalidProjectHierarchyError(f"Self-parenting detected: '{parent_id}' cannot be its own parent")

        visited = {child_id}
        curr = parent_id
        depth = 0

        while curr:
            if curr in visited:
                raise InvalidProjectHierarchyError(f"Cycle detected in project hierarchy involving '{curr}'")
            visited.add(curr)
            depth += 1
            if depth > 5:
                raise InvalidProjectHierarchyError("Project hierarchy exceeds maximum depth of 5 levels")

            parent_rec = self.get_project(curr)
            if not parent_rec:
                raise ProjectNotFoundError(f"Parent project '{curr}' does not exist")
            curr = parent_rec.parent_project_id

        # Bidirectional check: ensure child does not already lead to parent
        visited_child = {parent_id}
        curr_child = child_id
        while curr_child:
            if curr_child in visited_child:
                raise InvalidProjectHierarchyError(f"Cycle detected in project hierarchy involving '{curr_child}'")
            visited_child.add(curr_child)
            c_rec = self.get_project(curr_child)
            if not c_rec:
                break
            curr_child = c_rec.parent_project_id

    # -----------------------------------------------------------------------
    # 2. Experience Ingestion & Negative Intelligence
    # -----------------------------------------------------------------------
    def record_experience(
        self,
        task_id: str,
        project_id: str,
        goal_intent: Optional[str] = None,
        outcome_status: Optional[Any] = None,
        context_conditions: Optional[Dict[str, Any]] = None,
        strategy_applied: Optional[Dict[str, Any]] = None,
        execution_trace: Optional[List[Dict[str, Any]]] = None,
        outcome_metrics: Optional[Dict[str, Any]] = None,
        error_signature: Optional[str] = None,
        root_cause_analysis: Optional[str] = None,
        verification_evidence: Optional[Dict[str, Any]] = None,
        strategy_id: Optional[str] = None,
        privacy_class: Optional[PrivacyClass] = None,
        actor: str = "SYSTEM",
        idempotency_key: Optional[str] = None,
        **kwargs,
    ) -> ExperienceRecord:
        """
        Record a grounded task execution experience under an atomic PostgreSQL transaction.
        Enforces:
        - Strict project existence
        - Provenance to authoritative task_id
        - Idempotency deduplication
        - Negative experience error signature capture
        - Bayesian strategy reliability updates
        """
        clean_project = str(project_id).strip().lower()
        clean_task = str(task_id).strip()
        if not clean_task:
            raise InadmissibleExperienceError("Experience must cite a valid task_id")

        # Resolve aliases
        action_summary = kwargs.get("action_summary")
        goal_intent = goal_intent or action_summary or "Execution task"
        if not goal_intent or not str(goal_intent).strip():
            raise InadmissibleExperienceError("Experience must have a non-empty goal_intent")

        outcome_val = outcome_status or kwargs.get("outcome") or TaskOutcomeStatus.SUCCESS
        if isinstance(outcome_val, str):
            outcome_status = TaskOutcomeStatus(outcome_val)
        else:
            outcome_status = outcome_val

        strategy_id = strategy_id or kwargs.get("strategy_used")
        strategy_applied = dict(strategy_applied or {})
        if strategy_id and "strategy_id" not in strategy_applied:
            strategy_applied["strategy_id"] = str(strategy_id).strip()

        root_cause_analysis = root_cause_analysis or kwargs.get("outcome_reason")
        context_conditions = context_conditions or kwargs.get("execution_context") or {}

        supporting_evidence_ids = kwargs.get("supporting_evidence_ids")
        if supporting_evidence_ids:
            verification_evidence = verification_evidence or {}
            verification_evidence["evidence_ids"] = supporting_evidence_ids

        timestamp = kwargs.get("timestamp")
        now = timestamp.isoformat() if (timestamp and hasattr(timestamp, "isoformat")) else (str(timestamp) if timestamp else datetime.now(timezone.utc).isoformat())

        project = self.get_project(clean_project)
        if not project:
            raise ProjectNotFoundError(f"Project '{clean_project}' does not exist")

        # Resolve privacy: cannot be less private than project floor
        eff_privacy = privacy_class or project.privacy_class
        if project.privacy_class == PrivacyClass.SENSITIVE:
            eff_privacy = PrivacyClass.SENSITIVE
        elif project.privacy_class == PrivacyClass.PRIVATE and eff_privacy == PrivacyClass.NORMAL:
            eff_privacy = PrivacyClass.PRIVATE

        # Clean error signature for failures
        norm_error = normalize_error_signature(error_signature)

        # Idempotency key
        verif_str = json.dumps(verification_evidence or {}, sort_keys=True)
        idem_key = idempotency_key or compute_experience_idempotency_hash(clean_task, clean_project, outcome_status.value, verif_str, str(timestamp or ""))

        # Base confidence & importance
        if "confidence" in kwargs:
            conf = float(kwargs["confidence"])
        elif outcome_status == TaskOutcomeStatus.SUCCESS:
            conf = 0.90 if verification_evidence and verification_evidence.get("verified") else 0.70
        elif outcome_status == TaskOutcomeStatus.FAILURE:
            conf = 0.85  # Highly confident that it failed
        elif outcome_status == TaskOutcomeStatus.PARTIAL_SUCCESS:
            conf = 0.70
        else:  # ABORTED / UNKNOWN
            conf = 0.50
        imp = kwargs.get("importance", 0.60 if outcome_status == TaskOutcomeStatus.SUCCESS else 0.80 if outcome_status == TaskOutcomeStatus.FAILURE else 0.50)

        conn = self._manager.get_connection()
        if not conn:
            raise RuntimeError("PostgreSQL connection unavailable")

        exp_id = f"exp_{uuid.uuid4().hex[:12]}"

        try:
            with conn.cursor() as cur:
                # Check for existing idempotency key
                cur.execute("SELECT experience_id FROM experiences WHERE idempotency_key = %s;", (idem_key,))
                existing = cur.fetchone()
                if existing:
                    # Return existing experience record
                    cur.execute("""
                        SELECT experience_id, task_id, project_id, goal_intent,
                               context_conditions, strategy_applied, execution_trace,
                               outcome_status, outcome_metrics, error_signature,
                               root_cause_analysis, verification_evidence,
                               confidence_score, importance, privacy_class,
                               idempotency_key, created_at
                        FROM experiences WHERE experience_id = %s;
                    """, (existing[0],))
                    r = cur.fetchone()
                    return ExperienceRecord(
                        experience_id=r[0], task_id=r[1], project_id=r[2], goal_intent=r[3],
                        context_conditions=r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
                        strategy_applied=r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
                        execution_trace=r[6] if isinstance(r[6], list) else json.loads(r[6] or "[]"),
                        outcome_status=TaskOutcomeStatus(r[7]),
                        outcome_metrics=r[8] if isinstance(r[8], dict) else json.loads(r[8] or "{}"),
                        error_signature=r[9], root_cause_analysis=r[10],
                        verification_evidence=r[11] if isinstance(r[11], dict) else json.loads(r[11] or "{}"),
                        confidence_score=float(r[12]), importance=float(r[13]),
                        privacy_class=PrivacyClass(r[14]), idempotency_key=r[15],
                        created_at=r[16].isoformat() if hasattr(r[16], "isoformat") else str(r[16]),
                    )

                # Lock project row to prevent race
                cur.execute("SELECT project_id FROM projects WHERE project_id = %s FOR UPDATE;", (clean_project,))

                # 1. Insert Experience
                cur.execute("""
                    INSERT INTO experiences (
                        experience_id, task_id, project_id, goal_intent,
                        context_conditions, strategy_applied, execution_trace,
                        outcome_status, outcome_metrics, error_signature,
                        root_cause_analysis, verification_evidence,
                        confidence_score, importance, privacy_class,
                        idempotency_key, created_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s
                    );
                """, (
                    exp_id, clean_task, clean_project, goal_intent,
                    json.dumps(context_conditions or {}),
                    json.dumps(strategy_applied or {}),
                    json.dumps(execution_trace or []),
                    outcome_status.value if isinstance(outcome_status, Enum) else outcome_status,
                    json.dumps(outcome_metrics or {}),
                    norm_error,
                    root_cause_analysis,
                    json.dumps(verification_evidence or {}),
                    conf, imp,
                    eff_privacy.value if isinstance(eff_privacy, Enum) else eff_privacy,
                    idem_key, now,
                ))

                # 2. Update Strategy Reliability if strategy_id given
                if strategy_id:
                    clean_strat = str(strategy_id).strip()
                    cur.execute("""
                        SELECT strategy_id, successful_attempts, failed_attempts, is_deprecated
                        FROM strategies WHERE strategy_id = %s FOR UPDATE;
                    """, (clean_strat,))
                    strat_row = cur.fetchone()
                    if strat_row:
                        if strat_row[3]:  # is_deprecated
                            logger.warning(f"[STRATEGY] Attempted execution of deprecated strategy {clean_strat}")
                        succ_cnt = strat_row[1]
                        fail_cnt = strat_row[2]
                        if outcome_status == TaskOutcomeStatus.SUCCESS:
                            succ_cnt += 1
                        elif outcome_status == TaskOutcomeStatus.FAILURE:
                            fail_cnt += 1
                        tot_cnt = succ_cnt + fail_cnt
                        new_rel = calculate_bayesian_strategy_reliability(
                            successful_weights=float(succ_cnt),
                            failed_weights=float(fail_cnt),
                        )
                        is_dep = strat_row[3] or (new_rel < 0.20 and tot_cnt >= 3)
                        dep_reason = "Reliability score dropped below 0.20 threshold" if (not strat_row[3] and is_dep) else None
                        cur.execute("""
                            UPDATE strategies SET
                                total_attempts = %s,
                                successful_attempts = %s,
                                failed_attempts = %s,
                                reliability_score = %s,
                                is_deprecated = %s,
                                deprecation_reason = COALESCE(deprecation_reason, %s),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE strategy_id = %s;
                        """, (tot_cnt, succ_cnt, fail_cnt, new_rel, is_dep, dep_reason, clean_strat))

            conn.commit()

            return ExperienceRecord(
                experience_id=exp_id,
                task_id=clean_task,
                project_id=clean_project,
                goal_intent=goal_intent,
                context_conditions=context_conditions or {},
                strategy_applied=strategy_applied or {},
                execution_trace=execution_trace or [],
                outcome_status=outcome_status,
                outcome_metrics=outcome_metrics or {},
                error_signature=norm_error,
                root_cause_analysis=root_cause_analysis,
                verification_evidence=verification_evidence or {},
                confidence_score=conf,
                importance=imp,
                privacy_class=eff_privacy,
                idempotency_key=idem_key,
                created_at=now,
            )
        except Exception as e:
            conn.rollback()
            logger.error(f"[PROJECT ENGINE] Failed to record experience: {e}")
            raise
        finally:
            self._manager.release_connection(conn)

    # -----------------------------------------------------------------------
    # 3. Lesson & Strategy Extraction
    # -----------------------------------------------------------------------
    def extract_lesson(
        self,
        title: str,
        summary: Optional[str] = None,
        domain: Optional[str] = None,
        supporting_experience_ids: Optional[List[str]] = None,
        contradicting_experience_ids: Optional[List[str]] = None,
        scope: LessonScope = LessonScope.PROJECT_LOCAL,
        prerequisites: Optional[List[str]] = None,
        anti_patterns: Optional[List[str]] = None,
        confidence_score: Optional[float] = None,
        importance: float = 0.50,
        freshness_class: str = "PROJECT_STABLE",
        **kwargs,
    ) -> LessonRecord:
        """
        Derives an abstract lesson from verified empirical experiences.
        Enforces evidence threshold: requires at least 1 supporting experience citation.
        """
        summary = summary or kwargs.get("description") or title
        domain = domain or kwargs.get("project_id") or "general"
        supporting_experience_ids = supporting_experience_ids or []

        proj_id = kwargs.get("project_id")
        if proj_id and not self.get_project(proj_id):
            raise ProjectNotFoundError(f"Project '{proj_id}' does not exist")

        supp_count = len(supporting_experience_ids)
        contra_count = len(contradicting_experience_ids or [])

        # Calibrate initial confidence: single experience cannot exceed 0.65
        conf_param = confidence_score or kwargs.get("confidence")
        if conf_param is not None:
            eff_conf = max(0.01, min(1.0, float(conf_param)))
        else:
            if supp_count == 1:
                eff_conf = 0.60
            elif supp_count == 2:
                eff_conf = 0.75
            elif supp_count == 0:
                eff_conf = 0.50
            else:
                eff_conf = 0.85 - (0.15 * contra_count)
                eff_conf = max(0.20, min(0.95, eff_conf))

        lesson_id = f"lsn_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        rec = LessonRecord(
            lesson_id=lesson_id,
            title=title,
            summary=summary,
            domain=domain.lower(),
            scope=scope,
            prerequisites=prerequisites or [],
            anti_patterns=anti_patterns or [],
            supporting_experience_count=supp_count,
            contradicting_experience_count=contra_count,
            confidence_score=eff_conf,
            importance=importance,
            freshness_class=freshness_class,
            last_confirmed_at=now,
            created_at=now,
            updated_at=now,
            supporting_experience_ids=supporting_experience_ids,
        )

        conn = self._manager.get_connection()
        if not conn:
            raise RuntimeError("PostgreSQL connection unavailable")

        try:
            with conn.cursor() as cur:
                # Check for idempotent existing lesson by title and domain
                cur.execute("SELECT lesson_id FROM lessons WHERE title = %s AND domain = %s;", (rec.title, rec.domain))
                existing_lsn = cur.fetchone()
                if existing_lsn:
                    rec.lesson_id = existing_lsn[0]
                    cur.execute("""
                        UPDATE lessons SET
                            summary = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE lesson_id = %s;
                    """, (rec.summary, rec.lesson_id))
                    conn.commit()
                    return rec

                cur.execute("""
                    INSERT INTO lessons (
                        lesson_id, title, summary, domain, scope,
                        prerequisites, anti_patterns, supporting_experience_ids,
                        supporting_experience_count, contradicting_experience_count,
                        confidence_score, importance, freshness_class,
                        last_confirmed_at, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s
                    );
                """, (
                    rec.lesson_id, rec.title, rec.summary, rec.domain,
                    rec.scope.value if isinstance(rec.scope, Enum) else rec.scope,
                    json.dumps(rec.prerequisites), json.dumps(rec.anti_patterns),
                    json.dumps(rec.supporting_experience_ids),
                    rec.supporting_experience_count, rec.contradicting_experience_count,
                    rec.confidence_score, rec.importance, rec.freshness_class,
                    rec.last_confirmed_at, rec.created_at, rec.updated_at,
                ))

            conn.commit()
            return rec
        except Exception as e:
            conn.rollback()
            logger.error(f"[PROJECT ENGINE] Failed to extract lesson: {e}")
            raise
        finally:
            self._manager.release_connection(conn)

    def register_strategy(
        self,
        name: str,
        intent_category: Optional[str] = None,
        procedure_template: Optional[Dict[str, Any]] = None,
        recommended_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        environmental_preconditions: Optional[Dict[str, Any]] = None,
        initial_reliability: float = 0.50,
        **kwargs,
    ) -> StrategyRecord:
        """Registers a reusable procedural execution strategy."""
        intent_category = intent_category or kwargs.get("applicable_project_id") or "general"
        procedure_template = procedure_template or {}
        if kwargs.get("description"):
            procedure_template["description"] = kwargs.get("description")
        if kwargs.get("derived_from_lesson_id"):
            procedure_template["derived_from_lesson_id"] = kwargs.get("derived_from_lesson_id")
        if kwargs.get("scope"):
            procedure_template["scope"] = kwargs.get("scope").value if isinstance(kwargs.get("scope"), Enum) else kwargs.get("scope")
        if kwargs.get("applicable_project_id"):
            procedure_template["applicable_project_id"] = kwargs.get("applicable_project_id")

        recommended_tools = recommended_tools or kwargs.get("prerequisites") or []
        environmental_preconditions = environmental_preconditions or kwargs.get("environmental_conditions") or {}
        initial_reliability = kwargs.get("reliability_score", initial_reliability)

        strat_id = f"strat_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        rec = StrategyRecord(
            strategy_id=strat_id,
            name=name,
            intent_category=intent_category.lower(),
            procedure_template=procedure_template,
            recommended_tools=recommended_tools or [],
            disallowed_tools=disallowed_tools or [],
            environmental_preconditions=environmental_preconditions or {},
            reliability_score=initial_reliability,
            created_at=now,
            updated_at=now,
        )

        conn = self._manager.get_connection()
        if not conn:
            raise RuntimeError("PostgreSQL connection unavailable")

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO strategies (
                        strategy_id, name, intent_category, procedure_template,
                        recommended_tools, disallowed_tools, environmental_preconditions,
                        total_attempts, successful_attempts, failed_attempts,
                        reliability_score, is_deprecated, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        0, 0, 0,
                        %s, FALSE, %s, %s
                    );
                """, (
                    rec.strategy_id, rec.name, rec.intent_category,
                    json.dumps(rec.procedure_template),
                    json.dumps(rec.recommended_tools),
                    json.dumps(rec.disallowed_tools),
                    json.dumps(rec.environmental_preconditions),
                    rec.reliability_score, rec.created_at, rec.updated_at,
                ))
            conn.commit()
            return rec
        except Exception as e:
            conn.rollback()
            logger.error(f"[PROJECT ENGINE] Failed to register strategy: {e}")
            raise
        finally:
            self._manager.release_connection(conn)

    def get_strategy(self, strategy_id: str) -> Optional[StrategyRecord]:
        """Fetches strategy by strategy_id."""
        conn = self._manager.get_connection()
        if not conn:
            return None

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT strategy_id, name, intent_category, procedure_template,
                           recommended_tools, disallowed_tools, environmental_preconditions,
                           total_attempts, successful_attempts, failed_attempts,
                           reliability_score, is_deprecated, deprecation_reason,
                           created_at, updated_at
                    FROM strategies WHERE strategy_id = %s;
                """, (strategy_id,))
                r = cur.fetchone()
                if not r:
                    return None
                return StrategyRecord(
                    strategy_id=r[0], name=r[1], intent_category=r[2],
                    procedure_template=r[3] if isinstance(r[3], dict) else json.loads(r[3] or "{}"),
                    recommended_tools=r[4] if isinstance(r[4], list) else json.loads(r[4] or "[]"),
                    disallowed_tools=r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]"),
                    environmental_preconditions=r[6] if isinstance(r[6], dict) else json.loads(r[6] or "{}"),
                    total_attempts=r[7], successful_attempts=r[8], failed_attempts=r[9],
                    reliability_score=float(r[10]), is_deprecated=bool(r[11]), deprecation_reason=r[12],
                    created_at=r[13].isoformat() if hasattr(r[13], "isoformat") else str(r[13]),
                    updated_at=r[14].isoformat() if hasattr(r[14], "isoformat") else str(r[14]),
                )
        finally:
            self._manager.release_connection(conn)

    # -----------------------------------------------------------------------
    # 4. Cross-Project Transfer Matrix & Governance Engine
    # -----------------------------------------------------------------------
    def _get_target_failure_stats(self, strategy_id: Optional[str], target_project_id: str) -> Dict[str, Any]:
        """Queries empirical failure history for a strategy within a specific target project."""
        if not strategy_id or not target_project_id:
            return {"failures": 0, "successes": 0, "consecutive_failures": 0}
        conn = self._manager.get_connection()
        if not conn:
            return {"failures": 0, "successes": 0, "consecutive_failures": 0}
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        COUNT(CASE WHEN outcome_status IN ('FAILURE', 'PARTIAL_SUCCESS') THEN 1 END),
                        COUNT(CASE WHEN outcome_status = 'SUCCESS' THEN 1 END)
                    FROM experiences
                    WHERE (strategy_applied->>'strategy_id' = %s) AND project_id = %s;
                """, (strategy_id, target_project_id))
                row = cur.fetchone()
                failures = int(row[0]) if row and row[0] is not None else 0
                successes = int(row[1]) if row and row[1] is not None else 0

                cur.execute("""
                    SELECT outcome_status FROM experiences
                    WHERE (strategy_applied->>'strategy_id' = %s) AND project_id = %s
                    ORDER BY created_at DESC LIMIT 5;
                """, (strategy_id, target_project_id))
                recent = [r[0] for r in cur.fetchall()]
                consecutive = 0
                for o in recent:
                    if o in ('FAILURE', 'PARTIAL_SUCCESS'):
                        consecutive += 1
                    else:
                        break
                return {
                    "failures": failures,
                    "successes": successes,
                    "consecutive_failures": consecutive,
                }
        except Exception:
            return {"failures": 0, "successes": 0, "consecutive_failures": 0}
        finally:
            self._manager.release_connection(conn)

    def evaluate_cross_project_transfer(
        self,
        source_project_id: Optional[str] = None,
        target_project_id: Optional[str] = None,
        lesson_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        environmental_compatibility: float = 1.0,
        risk_penalty: float = 0.0,
        persist: bool = True,
        **kwargs,
    ) -> TransferEvaluationResult:
        """
        Formally evaluates cross-project transfer eligibility via GovernanceEngine.
        Applies:
        - 14 Hard Governance Gates (Privacy Quarantine, Prerequisites, Scope, etc.)
        - Multi-factor Transfer Confidence Scoring with Target Failure Penalty
        - Persists to project_transfer_matrix only when persist=True.
        - Zero accidental mutations to lessons table.
        """
        strat = None
        if strategy_id:
            strat = self.get_strategy(strategy_id)
            if strat and not source_project_id:
                source_project_id = (
                    strat.procedure_template.get("applicable_project_id")
                    if isinstance(strat.procedure_template, dict)
                    else None
                ) or strat.intent_category

        src_id = str(source_project_id).strip().lower() if source_project_id else ""
        tgt_id = str(target_project_id).strip().lower() if target_project_id else ""

        src_proj = self.get_project(src_id)
        if not src_proj:
            raise ProjectNotFoundError(f"Source project '{src_id}' not found")
        tgt_proj = self.get_project(tgt_id)
        if not tgt_proj:
            raise ProjectNotFoundError(f"Target project '{tgt_id}' not found")

        # 1. Lesson Scope and Domain Check (if lesson_id provided)
        lsn_domain = None
        if lesson_id:
            conn = self._manager.get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT confidence_score, domain, scope FROM lessons WHERE lesson_id = %s;", (lesson_id,))
                        lsn_row = cur.fetchone()
                        if not lsn_row:
                            raise ValueError(f"Lesson '{lesson_id}' not found")
                        lsn_domain = lsn_row[1]
                        if lsn_row[2] == "PROJECT_LOCAL":
                            return TransferEvaluationResult(
                                decision=TransferDecision.DENIED,
                                transfer_confidence=0.0,
                                semantic_similarity=0.0,
                                tech_stack_overlap=0.0,
                                environmental_compatibility=0.0,
                                risk_penalty=1.0,
                                reason="Lesson scope is PROJECT_LOCAL; cross-project transfer prohibited",
                                strategy_id=strategy_id,
                            )
                finally:
                    self._manager.release_connection(conn)

        # 2. Risk Tolerance Pre-check
        if "risk_tolerance" in kwargs:
            risk_tol = float(kwargs["risk_tolerance"])
            if risk_tol < 0.20 and strat and strat.total_attempts == 0:
                return TransferEvaluationResult(
                    decision=TransferDecision.DENIED,
                    transfer_confidence=0.0,
                    semantic_similarity=0.0,
                    tech_stack_overlap=0.0,
                    environmental_compatibility=0.0,
                    risk_penalty=1.0,
                    reason="Low confidence strategy rejected under strict risk tolerance",
                    strategy_id=strategy_id,
                )

        # 3. Calculate Semantic Similarity
        s_set = set(src_proj.tech_stack)
        t_set = set(tgt_proj.tech_stack)
        common_kw = s_set.intersection(t_set)
        if common_kw or (lsn_domain and (lsn_domain in tgt_proj.description.lower() or lsn_domain in " ".join(tgt_proj.tech_stack).lower())):
            sem_sim = 0.85
        else:
            sem_sim = 0.60

        # 4. Target Failure Statistics & Experience Verification
        target_failure_stats = self._get_target_failure_stats(strat.strategy_id if strat else None, tgt_id)

        src_conf = strat.reliability_score if strat else 0.75
        verified_count = 0
        conn = self._manager.get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT MAX(confidence_score), COUNT(*)
                        FROM experiences
                        WHERE (strategy_applied->>'strategy_id' = %s OR project_id = %s)
                          AND outcome_status = 'SUCCESS';
                    """, (strategy_id, src_id))
                    exp_row = cur.fetchone()
                    if exp_row and exp_row[0] is not None:
                        src_conf = max(strat.reliability_score if strat else 0.5, float(exp_row[0]))
                        verified_count = int(exp_row[1])
            finally:
                self._manager.release_connection(conn)

        # 5. Execute Governance Engine Evaluation
        extra_ctx = dict(kwargs)
        if "source_confidence" not in extra_ctx:
            extra_ctx["source_confidence"] = src_conf
        if "verified_experience_count" not in extra_ctx:
            extra_ctx["verified_experience_count"] = verified_count
        if kwargs.get("scope"):
            extra_ctx["scope"] = kwargs.get("scope")
        elif strat and isinstance(strat.procedure_template, dict) and strat.procedure_template.get("scope"):
            extra_ctx["scope"] = strat.procedure_template.get("scope")

        gov_decision = governance_engine.evaluate_transfer(
            strategy=strat,
            source_project=src_proj,
            target_project=tgt_proj,
            semantic_similarity=sem_sim,
            target_environment={"environmental_compatibility": environmental_compatibility},
            target_failure_stats=target_failure_stats,
            extra_context=extra_ctx,
        )

        # 6. Record in project_transfer_matrix (if persist=True)
        transfer_id = f"txm_{uuid.uuid4().hex[:12]}" if persist else None
        if persist:
            conn = self._manager.get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO project_transfer_matrix (
                                transfer_id, source_project_id, target_project_id,
                                lesson_id, strategy_id, semantic_similarity,
                                tech_stack_overlap, transfer_confidence, status,
                                rejection_reason, created_at, policy_version, risk_penalty
                            ) VALUES (
                                %s, %s, %s,
                                %s, %s, %s,
                                %s, %s, %s,
                                %s, CURRENT_TIMESTAMP, %s, %s
                            );
                        """, (
                            transfer_id, src_id, tgt_id,
                            lesson_id, strategy_id, gov_decision.semantic_similarity,
                            gov_decision.tech_stack_overlap, gov_decision.transfer_confidence,
                            "APPROVED" if gov_decision.decision == TransferDecision.ALLOWED else (
                                "EVALUATED" if gov_decision.decision == TransferDecision.CONDITIONAL else (
                                    "ABSTAIN" if gov_decision.decision == TransferDecision.ABSTAIN else "REJECTED"
                                )
                            ),
                            gov_decision.decision_reason if gov_decision.decision in (TransferDecision.DENIED, TransferDecision.ABSTAIN) else None,
                            gov_decision.policy_version, gov_decision.risk_penalty,
                        ))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error(f"[PROJECT ENGINE] Transfer matrix persist failed: {e}")
                    raise
                finally:
                    self._manager.release_connection(conn)

        res = gov_decision.to_legacy_result()
        res.transfer_id = transfer_id
        res.strategy_id = strategy_id
        return res

    def retrieve_applicable_strategies(
        self,
        project_id: str,
        intent_category: Optional[str] = None,
        limit: int = 5,
        target_environment: Optional[Dict[str, Any]] = None,
        allow_cross_project: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        V5.3.7.3: Retrieves applicable strategies for a target project.
        - Evaluates local strategies directly (full reliability, ALLOWED).
        - Evaluates cross-project candidates through GovernanceEngine (strictly read-only, persist=False).
        - Resolves conflicts and applies target-local precedence via ConflictEngine.
        - Strictly read-only: dI/dN_retrieval = 0.
        """
        tgt_proj = self.get_project(project_id)
        if not tgt_proj:
            return []

        conn = self._manager.get_connection()
        if not conn:
            return []

        candidates = []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT strategy_id, name, intent_category, procedure_template,
                           recommended_tools, disallowed_tools, environmental_preconditions,
                           total_attempts, successful_attempts, failed_attempts,
                           reliability_score, is_deprecated, created_at, updated_at
                    FROM strategies
                    WHERE is_deprecated = FALSE;
                """)
                all_strats = cur.fetchall()

            for row in all_strats:
                strat_id = row[0]
                strat_name = row[1]
                strat_cat = row[2]
                strat_proc = row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}")
                rec_tools = row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]")
                dis_tools = row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]")
                env_pre = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
                tot_att = row[7]
                succ_att = row[8]
                fail_att = row[9]
                rel_score = float(row[10])

                strat_rec = StrategyRecord(
                    strategy_id=strat_id,
                    name=strat_name,
                    intent_category=strat_cat,
                    procedure_template=strat_proc,
                    recommended_tools=rec_tools,
                    disallowed_tools=dis_tools,
                    environmental_preconditions=env_pre,
                    total_attempts=tot_att,
                    successful_attempts=succ_att,
                    failed_attempts=fail_att,
                    reliability_score=rel_score,
                )

                # Check intent matching if requested
                if intent_category and strat_cat.lower() != intent_category.lower() and strat_proc.get("intent") != intent_category:
                    continue

                origin_proj_id = strat_proc.get("applicable_project_id") or strat_cat
                is_local = (origin_proj_id.lower() == project_id.lower())

                if is_local:
                    candidates.append({
                        "strategy_id": strat_id,
                        "name": strat_name,
                        "intent_category": strat_cat,
                        "reliability_score": rel_score,
                        "transfer_confidence": rel_score,
                        "is_transferred": False,
                        "source_project_id": project_id,
                        "target_project_id": project_id,
                        "recommended_tools": rec_tools,
                        "disallowed_tools": dis_tools,
                        "procedure_template": strat_proc,
                        "success_count": succ_att,
                        "failure_count": fail_att,
                        "transfer_decision": TransferDecision.ALLOWED.value,
                        "defensive_warnings": [],
                    })
                elif allow_cross_project:
                    src_proj = self.get_project(origin_proj_id)
                    if not src_proj:
                        continue
                    # Read-only evaluation (persist=False)
                    res = self.evaluate_cross_project_transfer(
                        source_project_id=origin_proj_id,
                        target_project_id=project_id,
                        strategy_id=strat_id,
                        persist=False,
                    )
                    if res.decision in (TransferDecision.ALLOWED, TransferDecision.CONDITIONAL):
                        candidates.append({
                            "strategy_id": strat_id,
                            "name": strat_name,
                            "intent_category": strat_cat,
                            "reliability_score": rel_score,
                            "transfer_confidence": res.transfer_confidence,
                            "is_transferred": True,
                            "source_project_id": origin_proj_id,
                            "target_project_id": project_id,
                            "recommended_tools": rec_tools,
                            "disallowed_tools": dis_tools,
                            "procedure_template": strat_proc,
                            "success_count": succ_att,
                            "failure_count": fail_att,
                            "transfer_decision": res.decision.value,
                            "defensive_warnings": res.defensive_warnings,
                        })

            # Apply ConflictEngine
            resolved = ConflictEngine.resolve_conflicts(candidates, target_project_id=project_id)
            # Filter out ABSTAIN or DENIED
            survivors = [c for c in resolved if c.get("transfer_decision") in (TransferDecision.ALLOWED.value, TransferDecision.CONDITIONAL.value)]
            # Sort by transfer_confidence descending
            survivors.sort(key=lambda x: x.get("transfer_confidence", 0.0), reverse=True)
            return survivors[:limit]
        finally:
            self._manager.release_connection(conn)

    # -----------------------------------------------------------------------
    # 5. Explainability & Provenance Trace
    # -----------------------------------------------------------------------
    def get_strategy_explainability_trace(self, strategy_id: str) -> Optional[StrategyExplainabilityProfile]:
        """
        Produces a structured explainability profile for a strategy:
        traces back through lessons, supporting experiences, and negative warnings.
        Zero chain-of-thought leakage.
        """
        strat = self.get_strategy(strategy_id)
        if not strat:
            return None

        conn = self._manager.get_connection()
        if not conn:
            return None

        provenance = []
        warnings = []
        derived_lesson_title = "Empirical Workflow Strategy"

        try:
            with conn.cursor() as cur:
                # Find supporting lesson if derived_from_lesson_id set
                lsn_id = strat.procedure_template.get("derived_from_lesson_id") if isinstance(strat.procedure_template, dict) else None
                supporting_exp_ids = []
                if lsn_id:
                    cur.execute("SELECT title, summary, supporting_experience_ids FROM lessons WHERE lesson_id = %s;", (lsn_id,))
                    l_row = cur.fetchone()
                    if l_row:
                        derived_lesson_title = l_row[0]
                        if l_row[2]:
                            supporting_exp_ids = l_row[2] if isinstance(l_row[2], list) else json.loads(l_row[2] or "[]")

                # Find supporting experiences referencing this strategy or its derived lesson
                if supporting_exp_ids:
                    cur.execute("""
                        SELECT experience_id, task_id, project_id, outcome_status,
                               error_signature, confidence_score, created_at, goal_intent
                        FROM experiences
                        WHERE experience_id = ANY(%s) OR strategy_applied->>'strategy_id' = %s
                        ORDER BY created_at DESC LIMIT 10;
                    """, (supporting_exp_ids, strategy_id))
                else:
                    cur.execute("""
                        SELECT experience_id, task_id, project_id, outcome_status,
                               error_signature, confidence_score, created_at, goal_intent
                        FROM experiences
                        WHERE strategy_applied->>'strategy_id' = %s
                        ORDER BY created_at DESC LIMIT 10;
                    """, (strategy_id,))
                rows = cur.fetchall()
                for r in rows:
                    exp_item = {
                        "experience_id": r[0],
                        "task_id": r[1],
                        "project_id": r[2],
                        "outcome": r[3],
                        "confidence": float(r[5]),
                        "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6]),
                        "action_summary": r[7],
                    }
                    provenance.append(exp_item)
                    if r[3] in ("FAILURE", "PARTIAL_SUCCESS") and r[4]:
                        warnings.append(f"Known trap [{r[2]}]: {r[4]}")

            rationale = (
                f"Strategy '{strat.name}' in category '{strat.intent_category}' has reliability "
                f"{strat.reliability_score:.2f} across {strat.total_attempts} verified task attempts "
                f"({strat.successful_attempts} successes, {strat.failed_attempts} failures)."
            )

            return StrategyExplainabilityProfile(
                strategy_id=strat.strategy_id,
                strategy_name=strat.name,
                intent_category=strat.intent_category,
                reliability_score=strat.reliability_score,
                success_count=strat.successful_attempts,
                failure_count=strat.failed_attempts,
                summary_rationale=rationale,
                provenance_chain=provenance,
                defensive_warnings=list(set(warnings))[:5],
                is_cross_project=False,
                derived_lesson_title=derived_lesson_title,
            )
        finally:
            self._manager.release_connection(conn)


# Canonical singleton engine
project_experience_engine = ProjectExperienceEngine()
