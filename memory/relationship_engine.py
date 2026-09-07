"""
DOOM V5.3.4 — Memory Relationship Engine & Supersession DAG
Authoritative processor for memory graph relationships, atomic N:1 and 1:N
supersession, cycle-free DAG enforcement, candidate detection, and lineage resolution.
"""
from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from database.postgres_db import postgres_manager
from memory.lifecycle import (
    MemoryLifecycleError,
    MemoryStatus,
    LifecycleActor,
    LifecycleTransitionResult,
    MemoryLifecycleEvent,
    validate_transition,
    _emit_lifecycle_telemetry,
)
from memory.relationships import (
    RelationshipType,
    RelationshipCandidateType,
    CandidateStatus,
    MemoryRelationship,
    RelationshipCandidate,
    RelationshipMutationResult,
    MemoryRelationshipError,
    SelfReferenceError,
    CyclicSupersessionError,
    InvalidRelationshipTypeError,
    RelationshipValidationError,
    IdempotencyConflictError,
    SensitiveRelationshipError,
    compute_relationship_idempotency_key,
    new_relationship_id,
    new_candidate_id,
)
from memory.schemas import MemoryRecord
from memory.types import PrivacyClass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> Set[str]:
    """Tokenize text into lowercase alphabetic words for overlap detection."""
    return set(re.findall(r"\b[a-zA-Z]{3,}\b", text.lower()))


class MemoryRelationshipEngine:
    """
    Authoritative governance engine for the DOOM Memory Knowledge Graph.
    Enforces DAG constraints on supersession, atomic N:1 and 1:N operations,
    and PostgreSQL transactional consistency.
    """

    # ------------------------------------------------------------------
    # 1. Cycle Detection (DAG Invariant on SUPERSEDES)
    # ------------------------------------------------------------------
    def check_cycle(
        self,
        cur: Any,
        source_memory_id: str,
        target_memory_id: str,
        relationship_type: str = "SUPERSEDES",
        max_depth: int = 15,
    ) -> bool:
        """
        Check if inserting (source_memory_id -> target_memory_id) would introduce a cycle.
        Returns True if a cycle would be created, False if DAG invariant is preserved.
        Only applies to SUPERSEDES and DERIVED_FROM edges.
        """
        s_id = str(source_memory_id).strip()
        t_id = str(target_memory_id).strip()

        # Immediate self-reference check
        if s_id == t_id:
            return True

        if relationship_type not in (RelationshipType.SUPERSEDES.value, RelationshipType.DERIVED_FROM.value):
            return False

        # Query: Is source_memory_id reachable by starting at target_memory_id and following outgoing edges?
        sql = """
            WITH RECURSIVE supersession_reach AS (
                SELECT target_memory_id, 1 AS depth
                FROM memory_relationships
                WHERE source_memory_id = %s AND relationship_type = %s
                UNION ALL
                SELECT r.target_memory_id, p.depth + 1
                FROM memory_relationships r
                JOIN supersession_reach p ON r.source_memory_id = p.target_memory_id
                WHERE r.relationship_type = %s AND p.depth < %s
            )
            SELECT 1 FROM supersession_reach WHERE target_memory_id = %s LIMIT 1;
        """
        cur.execute(sql, (t_id, relationship_type, relationship_type, max_depth, s_id))
        row = cur.fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # 2. Authoritative Relationship Creation
    # ------------------------------------------------------------------
    def create_relationship(
        self,
        source_memory_id: str,
        target_memory_id: str,
        relationship_type: Any,
        reason: Optional[str] = None,
        actor: str = "SYSTEM",
        confidence: float = 1.0,
        idempotency_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        cur: Optional[Any] = None,
    ) -> RelationshipMutationResult:
        """
        Create a single authoritative relationship edge.
        If cur is provided, executes inside caller's transaction.
        Otherwise manages an independent transaction.
        """
        s_id = str(source_memory_id).strip()
        t_id = str(target_memory_id).strip()
        r_type = relationship_type.value if hasattr(relationship_type, "value") else str(relationship_type).strip().upper()

        if s_id == t_id:
            raise SelfReferenceError(s_id)

        try:
            enum_type = RelationshipType(r_type)
        except Exception:
            raise InvalidRelationshipTypeError(f"Unsupported relationship type: '{r_type}'")

        if confidence < 0.0 or confidence > 1.0:
            raise RelationshipValidationError(f"Confidence must be between 0.0 and 1.0, got {confidence}")

        clean_reason = (reason or "").replace("\r", " ").replace("\n", " ")[:500]
        meta = metadata or {}
        idem_key = idempotency_key or compute_relationship_idempotency_key(s_id, t_id, r_type)

        def _execute_inside(c: Any) -> RelationshipMutationResult:
            # 1. Idempotency Check
            c.execute("""
                SELECT relationship_id, source_memory_id, target_memory_id, relationship_type
                FROM memory_relationships
                WHERE idempotency_key = %s;
            """, (idem_key,))
            existing = c.fetchone()
            if existing:
                ex_src, ex_tgt, ex_type = existing[1], existing[2], existing[3]
                if ex_src == s_id and ex_tgt == t_id and ex_type == r_type:
                    return RelationshipMutationResult(
                        success=True,
                        relationship_id=existing[0],
                        relationship_type=r_type,
                        source_memory_id=s_id,
                        target_memory_id=t_id,
                        is_idempotent_replay=True,
                    )
                else:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idem_key}' already exists with different relationship parameters."
                    )

            # 2. Check source & target records exist and security rules
            c.execute("""
                SELECT memory_id, privacy_class, status
                FROM memory_records
                WHERE memory_id IN (%s, %s);
            """, (s_id, t_id))
            rec_rows = {r[0]: (r[1], r[2]) for r in c.fetchall()}

            if s_id not in rec_rows:
                raise RelationshipValidationError(f"Source memory '{s_id}' does not exist in memory_records.")
            if t_id not in rec_rows:
                raise RelationshipValidationError(f"Target memory '{t_id}' does not exist in memory_records.")

            src_pclass, src_status = rec_rows[s_id]
            tgt_pclass, tgt_status = rec_rows[t_id]

            if src_pclass == PrivacyClass.SENSITIVE.value or tgt_pclass == PrivacyClass.SENSITIVE.value:
                raise SensitiveRelationshipError(
                    s_id if src_pclass == PrivacyClass.SENSITIVE.value else t_id,
                    "SENSITIVE memories cannot participate in the memory relationship graph."
                )

            # Rule: DELETED memories cannot form new relationships
            if src_status == MemoryStatus.DELETED.value or tgt_status == MemoryStatus.DELETED.value:
                raise RelationshipValidationError("Cannot create relationship involving a DELETED memory.")

            # Rule: SUPERSEDED source cannot initiate new relationships
            if src_status == MemoryStatus.SUPERSEDED.value and r_type == RelationshipType.SUPERSEDES.value:
                raise RelationshipValidationError("A SUPERSEDED memory cannot supersede another memory.")

            # 3. Cycle Prevention for DAG edges
            if enum_type in (RelationshipType.SUPERSEDES, RelationshipType.DERIVED_FROM):
                if self.check_cycle(c, s_id, t_id, relationship_type=r_type, max_depth=15):
                    raise CyclicSupersessionError(s_id, t_id)

            # 4. Insert relationship edge
            rel_id = new_relationship_id()
            c.execute("""
                INSERT INTO memory_relationships (
                    relationship_id, source_memory_id, target_memory_id, relationship_type,
                    confidence, reason, actor, idempotency_key, created_at, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s);
            """, (rel_id, s_id, t_id, r_type, confidence, clean_reason, actor, idem_key, json.dumps(meta, default=str)))

            # 5. Legacy scalar mirror: if 1:1 SUPERSEDES, mirror to supersedes_memory_id on source
            if enum_type == RelationshipType.SUPERSEDES:
                c.execute("""
                    UPDATE memory_records
                    SET supersedes_memory_id = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE memory_id = %s AND (supersedes_memory_id IS NULL OR supersedes_memory_id = %s);
                """, (t_id, s_id, t_id))

            return RelationshipMutationResult(
                success=True,
                relationship_id=rel_id,
                relationship_type=r_type,
                source_memory_id=s_id,
                target_memory_id=t_id,
                is_idempotent_replay=False,
            )

        if cur is not None:
            return _execute_inside(cur)

        # Standalone transaction
        with postgres_manager.transaction(lock_timeout_ms=3000) as conn:
            with conn.cursor() as c:
                return _execute_inside(c)

    # ------------------------------------------------------------------
    # 3. Atomic N:1 Supersession (Consolidation)
    # ------------------------------------------------------------------
    def consolidate_n_to_1(
        self,
        old_memory_ids: List[str],
        new_record: MemoryRecord,
        reason: str = "Consolidated multiple memories",
        actor: str = LifecycleActor.SYSTEM.value,
        idempotency_key: Optional[str] = None,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> LifecycleTransitionResult:
        """
        Consolidate multiple existing active memories {A_1, A_2, ... A_n} into a single
        new active memory C in a single atomic transaction.
        All old memories are transitioned to SUPERSEDED, generations incremented,
        authoritative SUPERSEDES edges created, and vector sync queues updated.
        """
        clean_old_ids = [str(mid).strip() for mid in old_memory_ids if mid and str(mid).strip()]
        if not clean_old_ids:
            raise RelationshipValidationError("Cannot consolidate empty list of old memories.")

        t0 = time.perf_counter()
        act_str = actor.value if hasattr(actor, "value") else str(actor).strip().upper()
        event_id = None
        sync_ids_to_dispatch: List[str] = []

        with postgres_manager.transaction(lock_timeout_ms=3000) as conn:
            with conn.cursor() as cur:
                # 1. Deterministic Lexicographical Lock Ordering
                ordered_lock_ids = sorted(list(set(clean_old_ids + [new_record.memory_id])))
                for mid in ordered_lock_ids:
                    cur.execute("""
                        SELECT memory_id, status, generation, privacy_class, importance, confidence
                        FROM memory_records
                        WHERE memory_id = %s
                        FOR UPDATE;
                    """, (mid,))

                # 2. Fetch authoritative state of old memories
                cur.execute("""
                    SELECT memory_id, status, generation, privacy_class, importance, confidence
                    FROM memory_records
                    WHERE memory_id = ANY(%s);
                """, (clean_old_ids,))
                rows = cur.fetchall()
                if len(rows) != len(clean_old_ids):
                    found_ids = {r[0] for r in rows}
                    missing = set(clean_old_ids) - found_ids
                    raise RelationshipValidationError(f"Missing memory records for consolidation: {missing}")

                # 3. Validate eligibility
                old_updates = []
                for r in rows:
                    m_id, st, gen, pclass, imp, conf = r[0], r[1], int(r[2] or 1), r[3], float(r[4] or 0.5), r[5]
                    if pclass == PrivacyClass.SENSITIVE.value:
                        raise SensitiveRelationshipError(m_id, f"Cannot consolidate SENSITIVE memory '{m_id}'.")
                    if st != MemoryStatus.ACTIVE.value:
                        raise RelationshipValidationError(
                            f"Cannot consolidate non-ACTIVE memory '{m_id}' (current status '{st}')."
                        )
                    # Cycle check for each (new_record.memory_id -> m_id)
                    if self.check_cycle(cur, new_record.memory_id, m_id, relationship_type="SUPERSEDES", max_depth=15):
                        raise CyclicSupersessionError(new_record.memory_id, m_id)

                    old_updates.append((m_id, st, gen + 1, imp, conf))

                # 4. Insert or activate new record
                new_record.generation = 1
                new_record.supersedes_memory_id = clean_old_ids[0]  # Primary legacy reference
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, content, source, confidence,
                        importance, status, generation, project_id, task_id, entity_ids, tags,
                        supersedes_memory_id, source_event_id, verification_status,
                        privacy_class, metadata, created_at, updated_at, last_accessed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (memory_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        confidence = EXCLUDED.confidence,
                        importance = EXCLUDED.importance,
                        status = EXCLUDED.status,
                        generation = EXCLUDED.generation,
                        supersedes_memory_id = EXCLUDED.supersedes_memory_id,
                        updated_at = CURRENT_TIMESTAMP;
                """, (
                    new_record.memory_id,
                    new_record.memory_type.value if hasattr(new_record.memory_type, "value") else str(new_record.memory_type),
                    new_record.content,
                    new_record.source.value if hasattr(new_record.source, "value") else str(new_record.source),
                    new_record.confidence.value if hasattr(new_record.confidence, "value") else str(new_record.confidence),
                    new_record.importance,
                    new_record.status.value if hasattr(new_record.status, "value") else str(new_record.status),
                    1,
                    new_record.project_id,
                    new_record.task_id or task_id,
                    json.dumps(new_record.entity_ids or [], default=str),
                    json.dumps(new_record.tags or [], default=str),
                    new_record.supersedes_memory_id,
                    new_record.source_event_id,
                    new_record.verification_status.value if hasattr(new_record.verification_status, "value") else str(new_record.verification_status),
                    new_record.privacy_class.value if hasattr(new_record.privacy_class, "value") else str(new_record.privacy_class),
                    json.dumps(new_record.metadata or {}, default=str),
                    new_record.created_at,
                    new_record.updated_at,
                    new_record.last_accessed_at,
                ))

                # 5. Transition old records to SUPERSEDED and create relationships + audit events
                from memory.sync_engine import vector_sync_engine

                for m_id, prev_st, new_gen, imp_b, conf_b in old_updates:
                    cur.execute("""
                        UPDATE memory_records
                        SET status = %s, generation = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE memory_id = %s;
                    """, (MemoryStatus.SUPERSEDED.value, new_gen, m_id))

                    # Authoritative SUPERSEDES edge
                    edge_idem = compute_relationship_idempotency_key(new_record.memory_id, m_id, "SUPERSEDES")
                    rel_id = new_relationship_id()
                    cur.execute("""
                        INSERT INTO memory_relationships (
                            relationship_id, source_memory_id, target_memory_id, relationship_type,
                            confidence, reason, actor, idempotency_key, created_at, metadata
                        ) VALUES (%s, %s, %s, %s, 1.0, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                        ON CONFLICT (idempotency_key) DO NOTHING;
                    """, (rel_id, new_record.memory_id, m_id, RelationshipType.SUPERSEDES.value, reason, act_str, edge_idem, json.dumps({"consolidated_count": len(clean_old_ids)})))

                    # Audit Event
                    evt = MemoryLifecycleEvent(
                        memory_id=m_id,
                        previous_status=MemoryStatus(prev_st),
                        new_status=MemoryStatus.SUPERSEDED,
                        transition_reason=reason,
                        actor=act_str,
                        related_memory_id=new_record.memory_id,
                        source_event_id=new_record.source_event_id,
                        task_id=task_id or new_record.task_id,
                        correlation_id=correlation_id,
                        confidence_before=conf_b,
                        confidence_after=conf_b,
                        importance_before=imp_b,
                        importance_after=imp_b,
                        idempotency_key=f"idem_evt_cons_{m_id}_{new_record.memory_id}",
                    )
                    event_id = evt.event_id
                    cur.execute("""
                        INSERT INTO memory_lifecycle_events (
                            event_id, memory_id, previous_status, new_status,
                            transition_reason, actor, related_memory_id,
                            source_event_id, task_id, correlation_id,
                            confidence_before, confidence_after,
                            importance_before, importance_after,
                            metadata, idempotency_key, created_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s,
                            %s, %s, %s
                        );
                    """, (
                        evt.event_id, evt.memory_id, evt.previous_status.value, evt.new_status.value,
                        evt.transition_reason, evt.actor, evt.related_memory_id,
                        evt.source_event_id, evt.task_id, evt.correlation_id,
                        evt.confidence_before, evt.confidence_after,
                        evt.importance_before, evt.importance_after,
                        json.dumps(evt.metadata or {}, default=str), evt.idempotency_key, evt.created_at
                    ))

                    # Outbox vector sync deletion for old memory
                    sync_old = vector_sync_engine.enqueue_sync_work(
                        cur=cur,
                        memory_id=m_id,
                        operation="DELETE",
                        target_generation=new_gen,
                        target_status=MemoryStatus.SUPERSEDED.value,
                    )
                    if sync_old:
                        sync_ids_to_dispatch.append(sync_old)

                # Outbox vector sync upsert for new memory (if ACTIVE)
                if new_record.status == MemoryStatus.ACTIVE and new_record.privacy_class != PrivacyClass.SENSITIVE:
                    sync_new = vector_sync_engine.enqueue_sync_work(
                        cur=cur,
                        memory_id=new_record.memory_id,
                        operation="UPSERT",
                        target_generation=1,
                        target_status=new_record.status.value,
                    )
                    if sync_new:
                        sync_ids_to_dispatch.append(sync_new)

        # Post-Commit Dispatch
        from memory.sync_engine import vector_sync_engine
        for sid in sync_ids_to_dispatch:
            try:
                vector_sync_engine.trigger_post_commit(sid)
            except Exception as pc_err:
                print(f"[RELATIONSHIP ENGINE] Post-commit sync deferred: {pc_err}")

        _emit_lifecycle_telemetry(
            event="MEMORY_N_TO_1_CONSOLIDATED",
            memory_id=new_record.memory_id,
            previous_status=None,
            new_status=MemoryStatus.ACTIVE.value,
            actor=act_str,
            event_id=event_id,
            task_id=task_id or new_record.task_id,
            correlation_id=correlation_id,
            duration_ms=(time.perf_counter() - t0) * 1000,
            success=True,
            idempotent_replay=False,
        )

        return LifecycleTransitionResult(
            success=True,
            memory_id=new_record.memory_id,
            previous_status=None,
            new_status=MemoryStatus.ACTIVE,
            event_id=event_id,
            idempotent_replay=False,
        )

    # ------------------------------------------------------------------
    # 4. Atomic 1:N Supersession (Decomposition)
    # ------------------------------------------------------------------
    def decompose_1_to_n(
        self,
        old_memory_id: str,
        new_records: List[MemoryRecord],
        reason: str = "Decomposed memory into multiple components",
        actor: str = LifecycleActor.SYSTEM.value,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> LifecycleTransitionResult:
        """
        Decompose an existing active memory into multiple distinct new active memories.
        Old memory is transitioned to SUPERSEDED, and each new memory receives a
        directed SUPERSEDES edge pointing to old_memory_id.
        """
        clean_old_id = str(old_memory_id).strip()
        if not new_records:
            raise RelationshipValidationError("Cannot decompose into empty list of new memories.")

        t0 = time.perf_counter()
        act_str = actor.value if hasattr(actor, "value") else str(actor).strip().upper()
        event_id = None
        sync_ids_to_dispatch: List[str] = []

        with postgres_manager.transaction(lock_timeout_ms=3000) as conn:
            with conn.cursor() as cur:
                # 1. Lock old memory
                cur.execute("""
                    SELECT memory_id, status, generation, privacy_class, importance, confidence
                    FROM memory_records
                    WHERE memory_id = %s
                    FOR UPDATE;
                """, (clean_old_id,))
                row = cur.fetchone()
                if not row:
                    raise RelationshipValidationError(f"Old memory '{clean_old_id}' not found.")

                m_id, st, gen, pclass, imp, conf = row[0], row[1], int(row[2] or 1), row[3], float(row[4] or 0.5), row[5]
                if pclass == PrivacyClass.SENSITIVE.value:
                    raise SensitiveRelationshipError(clean_old_id, "Cannot decompose SENSITIVE memory.")
                if st != MemoryStatus.ACTIVE.value:
                    raise RelationshipValidationError(f"Cannot decompose non-ACTIVE memory '{clean_old_id}'.")

                # 2. Transition old memory to SUPERSEDED
                new_old_gen = gen + 1
                cur.execute("""
                    UPDATE memory_records
                    SET status = %s, generation = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE memory_id = %s;
                """, (MemoryStatus.SUPERSEDED.value, new_old_gen, clean_old_id))

                # Enqueue old memory vector deletion
                from memory.sync_engine import vector_sync_engine
                s_del = vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=clean_old_id,
                    operation="DELETE",
                    target_generation=new_old_gen,
                    target_status=MemoryStatus.SUPERSEDED.value,
                )
                if s_del:
                    sync_ids_to_dispatch.append(s_del)

                # 3. Insert new records and create relationships
                for new_rec in new_records:
                    if self.check_cycle(cur, new_rec.memory_id, clean_old_id, relationship_type="SUPERSEDES", max_depth=15):
                        raise CyclicSupersessionError(new_rec.memory_id, clean_old_id)

                    new_rec.generation = 1
                    new_rec.supersedes_memory_id = clean_old_id

                    cur.execute("""
                        INSERT INTO memory_records (
                            memory_id, memory_type, content, source, confidence,
                            importance, status, generation, project_id, task_id, entity_ids, tags,
                            supersedes_memory_id, source_event_id, verification_status,
                            privacy_class, metadata, created_at, updated_at, last_accessed_at
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (memory_id) DO UPDATE SET
                            content = EXCLUDED.content,
                            confidence = EXCLUDED.confidence,
                            importance = EXCLUDED.importance,
                            status = EXCLUDED.status,
                            generation = EXCLUDED.generation,
                            supersedes_memory_id = EXCLUDED.supersedes_memory_id,
                            updated_at = CURRENT_TIMESTAMP;
                    """, (
                        new_rec.memory_id,
                        new_rec.memory_type.value if hasattr(new_rec.memory_type, "value") else str(new_rec.memory_type),
                        new_rec.content,
                        new_rec.source.value if hasattr(new_rec.source, "value") else str(new_rec.source),
                        new_rec.confidence.value if hasattr(new_rec.confidence, "value") else str(new_rec.confidence),
                        new_rec.importance,
                        new_rec.status.value if hasattr(new_rec.status, "value") else str(new_rec.status),
                        1,
                        new_rec.project_id,
                        new_rec.task_id or task_id,
                        json.dumps(new_rec.entity_ids or [], default=str),
                        json.dumps(new_rec.tags or [], default=str),
                        new_rec.supersedes_memory_id,
                        new_rec.source_event_id,
                        new_rec.verification_status.value if hasattr(new_rec.verification_status, "value") else str(new_rec.verification_status),
                        new_rec.privacy_class.value if hasattr(new_rec.privacy_class, "value") else str(new_rec.privacy_class),
                        json.dumps(new_rec.metadata or {}, default=str),
                        new_rec.created_at,
                        new_rec.updated_at,
                        new_rec.last_accessed_at,
                    ))

                    rel_id = new_relationship_id()
                    edge_idem = compute_relationship_idempotency_key(new_rec.memory_id, clean_old_id, "SUPERSEDES")
                    cur.execute("""
                        INSERT INTO memory_relationships (
                            relationship_id, source_memory_id, target_memory_id, relationship_type,
                            confidence, reason, actor, idempotency_key, created_at, metadata
                        ) VALUES (%s, %s, %s, %s, 1.0, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                        ON CONFLICT (idempotency_key) DO NOTHING;
                    """, (rel_id, new_rec.memory_id, clean_old_id, RelationshipType.SUPERSEDES.value, reason, act_str, edge_idem, json.dumps({"decomposed_from": clean_old_id})))

                    if new_rec.status == MemoryStatus.ACTIVE and new_rec.privacy_class != PrivacyClass.SENSITIVE:
                        s_up = vector_sync_engine.enqueue_sync_work(
                            cur=cur,
                            memory_id=new_rec.memory_id,
                            operation="UPSERT",
                            target_generation=1,
                            target_status=new_rec.status.value,
                        )
                        if s_up:
                            sync_ids_to_dispatch.append(s_up)

        # Dispatch outbox
        from memory.sync_engine import vector_sync_engine
        for sid in sync_ids_to_dispatch:
            try:
                vector_sync_engine.trigger_post_commit(sid)
            except Exception as pc_err:
                print(f"[RELATIONSHIP ENGINE] Post-commit sync deferred: {pc_err}")

        return LifecycleTransitionResult(
            success=True,
            memory_id=clean_old_id,
            previous_status=MemoryStatus.ACTIVE,
            new_status=MemoryStatus.SUPERSEDED,
            idempotent_replay=False,
        )

    # ------------------------------------------------------------------
    # 5. Lineage & Successor Traversal
    # ------------------------------------------------------------------
    def resolve_lineage(self, memory_ids: List[str], max_hops: int = 15) -> Dict[str, str]:
        """
        For each memory ID in memory_ids, follows outgoing SUPERSEDES edges
        (i.e. find active memories that superseded this memory) to discover the
        active terminal successor.
        Returns mapping: obsolete_memory_id -> active_terminal_memory_id.
        """
        clean_ids = [str(m).strip() for m in memory_ids if m and str(m).strip()]
        if not clean_ids:
            return {}

        conn = postgres_manager.get_connection()
        if not conn:
            return {}

        resolved: Dict[str, str] = {}
        try:
            with conn.cursor() as cur:
                # Query: follow reverse-supersedes (where target_memory_id is the obsolete node and source_memory_id is the newer node)
                sql = """
                    WITH RECURSIVE forward_chain AS (
                        SELECT target_memory_id AS origin_id, source_memory_id AS current_id, 1 AS depth
                        FROM memory_relationships
                        WHERE target_memory_id = ANY(%s) AND relationship_type = 'SUPERSEDES'
                        UNION ALL
                        SELECT fc.origin_id, r.source_memory_id AS current_id, fc.depth + 1
                        FROM memory_relationships r
                        JOIN forward_chain fc ON r.target_memory_id = fc.current_id
                        WHERE r.relationship_type = 'SUPERSEDES' AND fc.depth < %s
                    )
                    SELECT fc.origin_id, fc.current_id, m.status
                    FROM forward_chain fc
                    JOIN memory_records m ON fc.current_id = m.memory_id
                    WHERE m.status = 'ACTIVE'
                    ORDER BY fc.depth DESC;
                """
                cur.execute(sql, (clean_ids, max_depth := max_hops))
                rows = cur.fetchall()
                for origin_id, current_id, st in rows:
                    if origin_id not in resolved:
                        resolved[origin_id] = current_id
        except Exception as e:
            print(f"[RELATIONSHIP ENGINE] resolve_lineage error: {e}")
        finally:
            postgres_manager.release_connection(conn)

        return resolved

    # ------------------------------------------------------------------
    # 6. Candidate Discovery (Duplicate & Conflict Candidates)
    # ------------------------------------------------------------------
    def detect_duplicate_candidates(
        self,
        record: MemoryRecord,
        top_k: int = 5,
        threshold: float = 0.88,
    ) -> List[RelationshipCandidate]:
        """
        Detect potential duplicate candidates using vector search + lexical token match.
        CRITICAL: Similarity is evidence of candidate status, NOT proof of duplicate.
        Does NOT automatically merge or delete records.
        """
        if not record.content or record.privacy_class == PrivacyClass.SENSITIVE:
            return []

        try:
            from memory.embedding.router import embedding_router
            from memory.vector_store import vector_store

            emb_res = embedding_router.embed(record.content, check_policy=True)
            if not emb_res:
                return []

            matches = vector_store.search_similar(
                query_vector=emb_res.vector,
                top_k=top_k + 1,
                model=emb_res.model,
                model_version=emb_res.model_version,
            )

            src_tokens = _tokenize(record.content)
            candidates: List[RelationshipCandidate] = []

            for m in matches:
                if m.memory_id == record.memory_id:
                    continue
                if m.similarity < threshold:
                    continue

                conn = postgres_manager.get_connection()
                if not conn:
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT memory_id, content, memory_type, status, privacy_class
                            FROM memory_records WHERE memory_id = %s;
                        """, (m.memory_id,))
                        row = cur.fetchone()
                        if not row or row[3] != MemoryStatus.ACTIVE.value:
                            continue
                        if row[4] == PrivacyClass.SENSITIVE.value:
                            continue

                        tgt_tokens = _tokenize(row[1])
                        intersection = src_tokens.intersection(tgt_tokens)
                        union = src_tokens.union(tgt_tokens)
                        jaccard = len(intersection) / max(len(union), 1)

                        # Require both high cosine similarity AND token lemma overlap
                        if jaccard >= 0.65 and str(row[2]) == record.memory_type.value:
                            cand = RelationshipCandidate(
                                candidate_type=RelationshipCandidateType.DUPLICATE_CANDIDATE,
                                source_memory_id=record.memory_id,
                                candidate_memory_id=row[0],
                                similarity=float(m.similarity),
                                confidence=min(float(m.similarity) * jaccard, 1.0),
                                evidence={
                                    "cosine_similarity": float(m.similarity),
                                    "jaccard_overlap": jaccard,
                                    "matched_tokens": list(intersection)[:10],
                                },
                            )
                            candidates.append(cand)
                finally:
                    postgres_manager.release_connection(conn)

            return candidates
        except Exception as e:
            print(f"[RELATIONSHIP ENGINE] detect_duplicate_candidates error: {e}")
            return []

    def detect_conflict_candidates(
        self,
        record: MemoryRecord,
        top_k: int = 10,
        min_similarity: float = 0.60,
    ) -> List[RelationshipCandidate]:
        """
        Detect potential contradiction/conflict candidates.
        Requires high semantic similarity on topic + opposing predicate values.
        CRITICAL: Similarity alone is NOT contradiction.
        """
        if not record.content or record.privacy_class == PrivacyClass.SENSITIVE:
            return []

        try:
            from memory.embedding.router import embedding_router
            from memory.vector_store import vector_store

            emb_res = embedding_router.embed(record.content, check_policy=True)
            if not emb_res:
                return []

            matches = vector_store.search_similar(
                query_vector=emb_res.vector,
                top_k=top_k + 1,
                model=emb_res.model,
                model_version=emb_res.model_version,
            )

            candidates: List[RelationshipCandidate] = []
            opposing_markers = [
                ("prefer", "prefer"),
                ("like", "dislike"),
                ("enable", "disable"),
                ("always", "never"),
                ("true", "false"),
                ("yes", "no"),
                ("migrated to", "uses"),
                ("switched to", "uses"),
            ]

            src_content_lower = record.content.lower()

            for m in matches:
                if m.memory_id == record.memory_id:
                    continue
                if m.similarity < min_similarity:
                    continue

                conn = postgres_manager.get_connection()
                if not conn:
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT memory_id, content, memory_type, status, privacy_class
                            FROM memory_records WHERE memory_id = %s;
                        """, (m.memory_id,))
                        row = cur.fetchone()
                        if not row or row[3] != MemoryStatus.ACTIVE.value:
                            continue
                        if row[4] == PrivacyClass.SENSITIVE.value:
                            continue

                        tgt_content_lower = str(row[1]).lower()

                        # Detect conflicting polarity or differing values under same preference/fact subject
                        is_conflict = False
                        evidence_type = "topical_divergence"

                        # Entity/subject alignment
                        src_tokens = _tokenize(src_content_lower)
                        tgt_tokens = _tokenize(tgt_content_lower)
                        common_tokens = src_tokens.intersection(tgt_tokens)

                        # If they share subject tokens (e.g. 'python', 'backend') but express different values
                        for mark_a, mark_b in opposing_markers:
                            if mark_a in src_content_lower and mark_b in tgt_content_lower:
                                is_conflict = True
                                evidence_type = f"opposing_predicate: '{mark_a}' vs '{mark_b}'"
                                break

                        # Differing explicit preferences: "prefer python" vs "prefer rust"
                        if "prefer " in src_content_lower and "prefer " in tgt_content_lower and not is_conflict:
                            src_pref = src_content_lower.split("prefer ")[-1].split()[0]
                            tgt_pref = tgt_content_lower.split("prefer ")[-1].split()[0]
                            if src_pref != tgt_pref and src_pref and tgt_pref:
                                is_conflict = True
                                evidence_type = f"differing_preference_values: '{src_pref}' != '{tgt_pref}'"

                        if is_conflict:
                            cand = RelationshipCandidate(
                                candidate_type=RelationshipCandidateType.CONFLICT_CANDIDATE,
                                source_memory_id=record.memory_id,
                                candidate_memory_id=row[0],
                                similarity=float(m.similarity),
                                confidence=0.75,
                                evidence={
                                    "conflict_reason": evidence_type,
                                    "shared_tokens": list(common_tokens)[:5],
                                    "cosine_similarity": float(m.similarity),
                                },
                            )
                            candidates.append(cand)
                finally:
                    postgres_manager.release_connection(conn)

            return candidates
        except Exception as e:
            print(f"[RELATIONSHIP ENGINE] detect_conflict_candidates error: {e}")
            return []

    # ------------------------------------------------------------------
    # 7. Inspection Helpers
    # ------------------------------------------------------------------
    def get_relationships(
        self,
        memory_id: str,
        relationship_type: Optional[Any] = None,
        direction: str = "both",
    ) -> List[MemoryRelationship]:
        """Fetch all authoritative relationships for memory_id."""
        clean_mid = str(memory_id).strip()
        conn = postgres_manager.get_connection()
        if not conn:
            return []

        rel_str = relationship_type.value if hasattr(relationship_type, "value") else (str(relationship_type).upper() if relationship_type else None)
        try:
            from psycopg2 import extras
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                where_clauses = []
                params: List[Any] = []

                if direction == "outgoing":
                    where_clauses.append("source_memory_id = %s")
                    params.append(clean_mid)
                elif direction == "incoming":
                    where_clauses.append("target_memory_id = %s")
                    params.append(clean_mid)
                else:
                    where_clauses.append("(source_memory_id = %s OR target_memory_id = %s)")
                    params.extend([clean_mid, clean_mid])

                if rel_str:
                    where_clauses.append("relationship_type = %s")
                    params.append(rel_str)

                where_sql = " AND ".join(where_clauses)
                cur.execute(f"SELECT * FROM memory_relationships WHERE {where_sql} ORDER BY created_at DESC;", params)
                rows = cur.fetchall()
                return [MemoryRelationship.from_dict(dict(r)) for r in rows]
        except Exception as e:
            print(f"[RELATIONSHIP ENGINE] get_relationships error: {e}")
            return []
        finally:
            postgres_manager.release_connection(conn)


relationship_engine = MemoryRelationshipEngine()
