"""PostgreSQL outbox for V6.1 signals/insights/deliveries. Bounded. Fail-open."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from proactive.config import (
    INSIGHT_TTL_SECONDS,
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    OWNER_ID,
    PENDING_QUEUE_MAX,
    SIGNAL_RETENTION_HOURS,
    DELIVERY_RETENTION_HOURS,
)
from proactive.schemas import ProactiveSignal

# Authoritative ownership predicate (SQL, not a Python-only if).
_OWNER_PRED = (
    "signal_id = %s AND worker_id = %s AND status = 'CLAIMED' "
    "AND lease_until IS NOT NULL AND lease_until > NOW()"
)


class ProactiveStore:
    def _conn(self):
        from database.postgres_db import postgres_manager
        if not postgres_manager.is_connected():
            return None
        return postgres_manager.get_connection()

    def _release(self, conn) -> None:
        try:
            from database.postgres_db import postgres_manager
            postgres_manager.release_connection(conn)
        except Exception:
            pass

    def enqueue_signal(self, signal: ProactiveSignal) -> str:
        """Insert or no-op on idempotency conflict. Returns signal_id of stored row."""
        conn = self._conn()
        if not conn:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_signals (
                        signal_id, owner_id, signal_type, source, entity_type, entity_id,
                        occurred_at, ingested_at, privacy_class, idempotency_key, payload,
                        status, metadata
                    )
                    SELECT %s,%s,%s,%s,%s,%s, to_timestamp(%s), to_timestamp(%s), %s,%s,%s::jsonb, 'PENDING', %s::jsonb
                    WHERE (
                        SELECT COUNT(*) FROM proactive_signals
                        WHERE status IN ('PENDING', 'CLAIMED')
                    ) < %s
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING signal_id
                    """,
                    (
                        signal.signal_id, signal.owner_id, signal.signal_type, signal.source,
                        signal.entity_type, signal.entity_id, signal.occurred_at, signal.ingested_at,
                        signal.privacy_class, signal.idempotency_key,
                        json.dumps(signal.payload), json.dumps(signal.metadata),
                        int(PENDING_QUEUE_MAX),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            if row:
                return row[0]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT signal_id FROM proactive_signals WHERE idempotency_key = %s",
                    (signal.idempotency_key,),
                )
                existing = cur.fetchone()
            return existing[0] if existing else ""
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def claim_batch(self, worker_id: str, limit: int = 8, lease_seconds: int = LEASE_SECONDS) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        claimed: List[Dict[str, Any]] = []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT signal_id FROM proactive_signals
                    WHERE status = 'PENDING'
                      AND (lease_until IS NULL OR lease_until < NOW())
                    ORDER BY ingested_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                    """,
                    (limit,),
                )
                ids = [r[0] for r in cur.fetchall()]
                if not ids:
                    conn.commit()
                    return []
                cur.execute(
                    """
                    UPDATE proactive_signals
                    SET status = 'CLAIMED', worker_id = %s,
                        lease_until = NOW() + (%s || ' seconds')::interval,
                        attempt_count = attempt_count + 1
                    WHERE signal_id = ANY(%s)
                    RETURNING signal_id, owner_id, signal_type, source, entity_type, entity_id,
                              EXTRACT(EPOCH FROM occurred_at), EXTRACT(EPOCH FROM ingested_at),
                              privacy_class, idempotency_key, payload, attempt_count, metadata
                    """,
                    (worker_id, str(int(lease_seconds)), ids),
                )
                for r in cur.fetchall():
                    payload = r[10] if isinstance(r[10], dict) else (json.loads(r[10]) if r[10] else {})
                    meta = r[12] if isinstance(r[12], dict) else (json.loads(r[12]) if r[12] else {})
                    claimed.append({
                        "signal_id": r[0], "owner_id": r[1], "signal_type": r[2], "source": r[3],
                        "entity_type": r[4], "entity_id": r[5], "occurred_at": float(r[6] or 0),
                        "ingested_at": float(r[7] or 0), "privacy_class": r[8],
                        "idempotency_key": r[9], "payload": payload, "attempt_count": r[11],
                        "metadata": meta, "worker_id": worker_id,
                    })
            conn.commit()
            return claimed
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return []
        finally:
            self._release(conn)

    def ack(self, signal_id: str, worker_id: str, status: str = "PROCESSED") -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE proactive_signals SET status = %s, lease_until = NULL
                    WHERE """ + _OWNER_PRED + """
                    """,
                    (status, signal_id, worker_id),
                )
                ok = cur.rowcount > 0
            conn.commit()
            return ok
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def retry_or_dead(self, signal_id: str, worker_id: str, error_type: str = "UNKNOWN_ERROR") -> str:
        """RETRY or DEAD only if this worker still owns a live CLAIMED lease. Else FENCED."""
        conn = self._conn()
        if not conn:
            return "DROP"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT attempt_count FROM proactive_signals WHERE "
                    + _OWNER_PRED
                    + " FOR UPDATE",
                    (signal_id, worker_id),
                )
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    return "FENCED"
                attempts = int(row[0] or 0)
                backoff = min(60, 2 ** min(attempts, 6))
                err = (error_type or "UNKNOWN_ERROR")[:64]
                if attempts >= MAX_ATTEMPTS:
                    cur.execute(
                        """
                        UPDATE proactive_signals
                        SET status = 'DEAD', last_error_type = %s, lease_until = NULL
                        WHERE """
                        + _OWNER_PRED,
                        (err, signal_id, worker_id),
                    )
                    outcome = "DEAD" if cur.rowcount else "FENCED"
                else:
                    cur.execute(
                        """
                        UPDATE proactive_signals
                        SET status = 'PENDING', last_error_type = %s,
                            lease_until = NOW() + (%s || ' seconds')::interval, worker_id = NULL
                        WHERE """
                        + _OWNER_PRED,
                        (err, str(backoff), signal_id, worker_id),
                    )
                    outcome = "RETRY" if cur.rowcount else "FENCED"
            conn.commit()
            return outcome
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return "DROP"
        finally:
            self._release(conn)

    def recover_expired_leases(self) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE proactive_signals
                    SET status = 'PENDING', worker_id = NULL
                    WHERE status = 'CLAIMED' AND lease_until < NOW()
                    """
                )
                n = cur.rowcount
            conn.commit()
            return n
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            self._release(conn)

    def insert_insight(self, ins) -> str:
        """Insert or return existing insight_id for dedupe_key. Empty only on hard failure."""
        conn = self._conn()
        if not conn:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_insights (
                        insight_id, owner_id, insight_type, source_signal_ids, entity_type, entity_id,
                        project_id, significance_score, confidence, urgency, privacy_class,
                        recommended_intervention, created_at, valid_until, status, dedupe_key,
                        template_id, safe_params, proactive_cycle_id
                    ) VALUES (
                        %s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,
                        to_timestamp(%s), to_timestamp(%s), %s,%s,%s,%s::jsonb,%s
                    )
                    ON CONFLICT (dedupe_key) DO NOTHING
                    RETURNING insight_id
                    """,
                    (
                        ins.insight_id, ins.owner_id, ins.insight_type,
                        json.dumps(ins.source_signal_ids), ins.entity_type, ins.entity_id,
                        ins.project_id, ins.significance_score, ins.confidence, ins.urgency,
                        ins.privacy_class, ins.recommended_intervention, ins.created_at,
                        ins.valid_until, ins.status, ins.dedupe_key, ins.template_id,
                        json.dumps(ins.safe_params), ins.proactive_cycle_id,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        """
                        SELECT insight_id FROM proactive_insights
                        WHERE dedupe_key = %s AND status IN ('OPEN','DELIVERED')
                        """,
                        (ins.dedupe_key,),
                    )
                    row = cur.fetchone()
            conn.commit()
            return row[0] if row else ""
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def get_open_insight_by_dedupe(self, dedupe_key: str) -> Optional[str]:
        row = self.find_valid_inform_insight_by_dedupe(dedupe_key)
        return row["insight_id"] if row else None

    def find_valid_inform_insight(
        self,
        owner_id: str,
        entity_id: str,
        signal_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Canonical INFORM insight for this entity+signal type. Expired rows excluded."""
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT insight_id, insight_type, entity_type, entity_id, project_id,
                           template_id, safe_params, urgency, privacy_class, dedupe_key,
                           owner_id, recommended_intervention, status
                    FROM proactive_insights
                    WHERE owner_id = %s
                      AND entity_id = %s
                      AND insight_type = %s
                      AND recommended_intervention = 'INFORM'
                      AND privacy_class = 'NORMAL'
                      AND status IN ('OPEN','DELIVERED')
                      AND valid_until > NOW()
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (owner_id, entity_id, signal_type),
                )
                r = cur.fetchone()
            if not r:
                return None
            params = r[6] if isinstance(r[6], dict) else (json.loads(r[6]) if r[6] else {})
            return {
                "insight_id": r[0], "insight_type": r[1], "entity_type": r[2],
                "entity_id": r[3], "project_id": r[4], "template_id": r[5],
                "safe_params": params or {}, "urgency": r[7], "privacy_class": r[8],
                "dedupe_key": r[9], "owner_id": r[10],
                "recommended_intervention": r[11], "status": r[12],
            }
        except Exception:
            return None
        finally:
            self._release(conn)

    def find_valid_inform_insight_by_dedupe(self, dedupe_key: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT insight_id FROM proactive_insights
                    WHERE dedupe_key = %s AND status IN ('OPEN','DELIVERED')
                      AND recommended_intervention = 'INFORM'
                      AND valid_until > NOW()
                    """,
                    (dedupe_key,),
                )
                row = cur.fetchone()
            return {"insight_id": row[0]} if row else None
        except Exception:
            return None
        finally:
            self._release(conn)

    def delivery_exists(self, insight_id: str, channel: str = "hud") -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM proactive_deliveries WHERE insight_id = %s AND channel = %s",
                    (insight_id, channel),
                )
                return cur.fetchone() is not None
        except Exception:
            return False
        finally:
            self._release(conn)

    def insert_delivery(self, insight_id: str, channel: str = "hud") -> str:
        """Return delivery_id if a row exists after the call (new or prior). Empty on failure."""
        did, _created = self.upsert_delivery(insight_id, channel)
        return did

    def upsert_delivery(self, insight_id: str, channel: str = "hud") -> tuple:
        """(delivery_id, created). created=True only for a new unique row."""
        import uuid
        did = str(uuid.uuid4())
        conn = self._conn()
        if not conn:
            return "", False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_deliveries (delivery_id, insight_id, channel, status, attempts)
                    VALUES (%s,%s,%s,'DELIVERED',1)
                    ON CONFLICT (insight_id, channel) DO NOTHING
                    RETURNING delivery_id
                    """,
                    (did, insight_id, channel),
                )
                row = cur.fetchone()
                created = bool(row)
                if row:
                    cur.execute(
                        "UPDATE proactive_insights SET status = 'DELIVERED' WHERE insight_id = %s",
                        (insight_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT delivery_id FROM proactive_deliveries
                        WHERE insight_id = %s AND channel = %s
                        """,
                        (insight_id, channel),
                    )
                    row = cur.fetchone()
            conn.commit()
            return (row[0], created) if row else ("", False)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return "", False
        finally:
            self._release(conn)

    def get_signal(self, signal_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT signal_id, status, worker_id, attempt_count,
                           EXTRACT(EPOCH FROM lease_until), idempotency_key, entity_id
                    FROM proactive_signals WHERE signal_id = %s
                    """,
                    (signal_id,),
                )
                r = cur.fetchone()
            if not r:
                return None
            return {
                "signal_id": r[0], "status": r[1], "worker_id": r[2],
                "attempt_count": r[3], "lease_until_epoch": float(r[4]) if r[4] is not None else None,
                "idempotency_key": r[5], "entity_id": r[6],
            }
        except Exception:
            return None
        finally:
            self._release(conn)

    def expire_lease_now(self, signal_id: str) -> bool:
        """Test/ops helper: expire a live lease without mutating worker_id."""
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE proactive_signals
                    SET lease_until = NOW() - INTERVAL '2 seconds'
                    WHERE signal_id = %s AND status = 'CLAIMED'
                    """,
                    (signal_id,),
                )
                ok = cur.rowcount > 0
            conn.commit()
            return ok
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def count_active_queue(self) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM proactive_signals WHERE status IN ('PENDING','CLAIMED')"
                )
                row = cur.fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0
        finally:
            self._release(conn)

    def count_deliveries(self, insight_id: str, channel: str = "hud") -> int:
        conn = self._conn()
        if not conn:
            return -1
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM proactive_deliveries WHERE insight_id = %s AND channel = %s",
                    (insight_id, channel),
                )
                row = cur.fetchone()
            return int(row[0] or 0)
        except Exception:
            return -1
        finally:
            self._release(conn)

    def list_hud_insights(self, owner_id: str = OWNER_ID, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT insight_id, insight_type, entity_type, entity_id, project_id,
                           recommended_intervention, template_id, safe_params, urgency,
                           EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM valid_until),
                           status, privacy_class, proactive_cycle_id
                    FROM proactive_insights
                    WHERE owner_id = %s AND recommended_intervention = 'INFORM'
                      AND status IN ('OPEN','DELIVERED') AND valid_until > NOW()
                      AND privacy_class = 'NORMAL'
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (owner_id, limit),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                params = r[7] if isinstance(r[7], dict) else (json.loads(r[7]) if r[7] else {})
                out.append({
                    "insight_id": r[0], "insight_type": r[1], "entity_type": r[2],
                    "entity_id": r[3], "project_id": r[4], "intervention": r[5],
                    "template_id": r[6], "safe_params": params, "urgency": r[8],
                    "created_at": float(r[9] or 0), "valid_until": float(r[10] or 0),
                    "status": r[11], "privacy_class": r[12], "proactive_cycle_id": r[13],
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def bump_attention(self, owner_id: str, dedupe_key: str, day_key: str) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_attention (owner_id, day_key, inform_count, cooldowns)
                    VALUES (%s, %s::date, 1, jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW())))
                    ON CONFLICT (owner_id, day_key) DO UPDATE SET
                        inform_count = proactive_attention.inform_count + 1,
                        cooldowns = COALESCE(proactive_attention.cooldowns, '{}'::jsonb)
                            || jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW()))
                    RETURNING inform_count
                    """,
                    (owner_id, day_key, dedupe_key, dedupe_key),
                )
                row = cur.fetchone()
            conn.commit()
            return int(row[0]) if row else 0
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            self._release(conn)

    def get_attention(self, owner_id: str, day_key: str) -> Dict[str, Any]:
        conn = self._conn()
        if not conn:
            return {"inform_count": 0, "cooldowns": {}}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT inform_count, cooldowns FROM proactive_attention
                    WHERE owner_id = %s AND day_key = %s::date
                    """,
                    (owner_id, day_key),
                )
                row = cur.fetchone()
            if not row:
                return {"inform_count": 0, "cooldowns": {}}
            cd = row[1] if isinstance(row[1], dict) else (json.loads(row[1]) if row[1] else {})
            return {"inform_count": int(row[0] or 0), "cooldowns": cd}
        except Exception:
            return {"inform_count": 0, "cooldowns": {}}
        finally:
            self._release(conn)

    def expire_stale(self) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE proactive_insights
                    SET status = 'EXPIRED',
                        dedupe_key = dedupe_key || '#' || insight_id
                    WHERE valid_until < NOW() AND status = 'OPEN'
                    """
                )
                cur.execute(
                    """
                    DELETE FROM proactive_signals
                    WHERE ingested_at < NOW() - (%s || ' hours')::interval
                      AND status IN ('PROCESSED','DEAD','DROPPED','DEDUPED')
                    """,
                    (str(SIGNAL_RETENTION_HOURS),),
                )
                cur.execute(
                    """
                    DELETE FROM proactive_deliveries
                    WHERE created_at < NOW() - (%s || ' hours')::interval
                    """,
                    (str(DELIVERY_RETENTION_HOURS),),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)


    def upsert_connector_account(self, account: Dict[str, Any]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        aid = str(account.get("account_id") or "")[:64]
        if not aid:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO connector_accounts (
                        account_id, owner_id, connector_type, project_id, secret_ref,
                        status, privacy_class_default, health, health_detail, policy_version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (account_id) DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        secret_ref = EXCLUDED.secret_ref,
                        status = EXCLUDED.status,
                        privacy_class_default = EXCLUDED.privacy_class_default,
                        health = EXCLUDED.health,
                        health_detail = EXCLUDED.health_detail,
                        policy_version = EXCLUDED.policy_version,
                        updated_at = NOW()
                    RETURNING account_id
                    """,
                    (
                        aid,
                        str(account.get("owner_id") or OWNER_ID)[:64],
                        str(account.get("connector_type") or "")[:32],
                        account.get("project_id"),
                        str(account.get("secret_ref") or "")[:64],
                        str(account.get("status") or "ACTIVE")[:16],
                        str(account.get("privacy_class_default") or "NORMAL")[:16],
                        str(account.get("health") or "OK")[:16],
                        str(account.get("health_detail") or "")[:64],
                        int(account.get("policy_version") or 1),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else ""
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def list_active_accounts(self, connector_type: str, owner_id: str = OWNER_ID) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT account_id, owner_id, connector_type, project_id, secret_ref,
                           status, privacy_class_default, health, health_detail, policy_version
                    FROM connector_accounts
                    WHERE owner_id = %s AND connector_type = %s AND status = 'ACTIVE'
                    """,
                    (owner_id, connector_type),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                out.append({
                    "account_id": r[0], "owner_id": r[1], "connector_type": r[2],
                    "project_id": r[3], "secret_ref": r[4], "status": r[5],
                    "privacy_class_default": r[6], "health": r[7],
                    "health_detail": r[8], "policy_version": r[9],
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def set_account_health(self, account_id: str, health: str, detail: str = "") -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE connector_accounts
                    SET health = %s, health_detail = %s, updated_at = NOW()
                    WHERE account_id = %s
                    """,
                    (str(health)[:16], str(detail)[:64], account_id),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)

    def get_sync_state(self, account_id: str) -> Dict[str, Any]:
        conn = self._conn()
        if not conn:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT cursor, EXTRACT(EPOCH FROM last_success_at), last_error_type,
                           EXTRACT(EPOCH FROM backoff_until)
                    FROM connector_sync_state WHERE account_id = %s
                    """,
                    (account_id,),
                )
                row = cur.fetchone()
            if not row:
                return {}
            return {
                "cursor": row[0] or "",
                "last_success_at": float(row[1] or 0),
                "last_error_type": row[2] or "",
                "backoff_until": float(row[3] or 0),
            }
        except Exception:
            return {}
        finally:
            self._release(conn)

    def upsert_sync_state(
        self,
        account_id: str,
        cursor: Optional[str] = None,
        last_success: bool = False,
        last_error_type: Optional[str] = None,
        backoff_until: Optional[float] = None,
    ) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO connector_sync_state (account_id, cursor, last_success_at, last_error_type, backoff_until)
                    VALUES (
                        %s, %s,
                        CASE WHEN %s THEN NOW() ELSE NULL END,
                        %s,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END
                    )
                    ON CONFLICT (account_id) DO UPDATE SET
                        cursor = COALESCE(EXCLUDED.cursor, connector_sync_state.cursor),
                        last_success_at = CASE
                            WHEN EXCLUDED.last_success_at IS NOT NULL THEN EXCLUDED.last_success_at
                            ELSE connector_sync_state.last_success_at
                        END,
                        last_error_type = COALESCE(EXCLUDED.last_error_type, connector_sync_state.last_error_type),
                        backoff_until = EXCLUDED.backoff_until
                    """,
                    (
                        account_id,
                        cursor,
                        last_success,
                        last_error_type,
                        backoff_until,
                        backoff_until,
                    ),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)

    def upsert_external_fact(self, fact: Dict[str, Any]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        fid = str(fact.get("fact_id") or "")[:64]
        if not fid:
            return ""
        try:
            occurred = fact.get("occurred_at")
            valid_until = fact.get("valid_until")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO external_facts (
                        fact_id, owner_id, account_id, connector_type, source_record_id,
                        fact_kind, project_id, privacy_class, occurred_at, observed_at,
                        valid_until, confidence, payload, content_sha256, idempotency_key
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        NOW(),
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        content_sha256 = EXCLUDED.content_sha256,
                        occurred_at = EXCLUDED.occurred_at,
                        observed_at = NOW(),
                        valid_until = EXCLUDED.valid_until,
                        confidence = EXCLUDED.confidence,
                        privacy_class = EXCLUDED.privacy_class,
                        project_id = EXCLUDED.project_id
                    RETURNING fact_id
                    """,
                    (
                        fid,
                        str(fact.get("owner_id") or OWNER_ID)[:64],
                        str(fact.get("account_id") or "")[:64],
                        str(fact.get("connector_type") or "")[:32],
                        str(fact.get("source_record_id") or "")[:128],
                        str(fact.get("fact_kind") or "")[:32],
                        fact.get("project_id"),
                        str(fact.get("privacy_class") or "NORMAL")[:16],
                        occurred, occurred,
                        valid_until, valid_until,
                        float(fact.get("confidence") or 0.8),
                        json.dumps(fact.get("payload") or {}),
                        str(fact.get("content_sha256") or "")[:64],
                        str(fact.get("idempotency_key") or "")[:160],
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else ""
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def map_github_repo_to_project(self, owner_repo: str) -> Optional[str]:
        needle = str(owner_repo or "").strip().lower().rstrip("/")
        if not needle or "/" not in needle:
            return None
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT project_id, git_remote FROM projects WHERE git_remote IS NOT NULL AND git_remote <> ''"
                )
                rows = cur.fetchall()
            for pid, remote in rows:
                rem = str(remote or "").lower().replace("\\", "/")
                if rem.endswith(".git"):
                    rem = rem[:-4]
                if f"github.com/{needle}" in rem or rem.endswith(f":{needle}"):
                    return str(pid)
            return None
        except Exception:
            return None
        finally:
            self._release(conn)

    def upsert_commitment(self, row: Dict[str, Any]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        cid = str(row.get("commitment_id") or "")[:64]
        if not cid:
            return ""
        try:
            due = row.get("due_at")
            src_ts = row.get("source_ts")
            valid_until = row.get("valid_until")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_commitments (
                        commitment_id, owner_id, account_id, source_connector,
                        source_message_id, source_thread_id, source_ts,
                        commitment_type, normalized_code, due_at, timezone,
                        status, confidence, privacy_class, provenance, evidence_ref,
                        fingerprint, valid_from, valid_until
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        %s,%s,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        %s,'OPEN',%s,'PRIVATE',%s::jsonb,%s,%s,NOW(),
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END
                    )
                    ON CONFLICT (account_id, fingerprint) DO UPDATE SET
                        due_at = EXCLUDED.due_at,
                        confidence = EXCLUDED.confidence,
                        evidence_ref = EXCLUDED.evidence_ref,
                        timezone = EXCLUDED.timezone,
                        updated_at = NOW()
                    RETURNING commitment_id
                    """,
                    (
                        cid,
                        str(row.get("owner_id") or OWNER_ID)[:64],
                        str(row.get("account_id") or "")[:64],
                        str(row.get("source_connector") or "")[:32],
                        str(row.get("source_message_id") or "")[:128],
                        str(row.get("source_thread_id") or "")[:128],
                        src_ts, src_ts,
                        str(row.get("commitment_type") or "")[:32],
                        str(row.get("normalized_code") or "")[:32],
                        due, due,
                        str(row.get("timezone") or "")[:40],
                        float(row.get("confidence") or 0),
                        json.dumps(row.get("provenance") or {}),
                        row.get("evidence_ref"),
                        str(row.get("fingerprint") or "")[:48],
                        valid_until, valid_until,
                    ),
                )
                got = cur.fetchone()
            conn.commit()
            return got[0] if got else ""
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def list_open_commitments(self, owner_id: str = OWNER_ID, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT commitment_id, commitment_type, EXTRACT(EPOCH FROM due_at),
                           confidence, privacy_class, source_connector, status,
                           evidence_ref, account_id
                    FROM proactive_commitments
                    WHERE owner_id = %s AND status = 'OPEN'
                      AND (valid_until IS NULL OR valid_until > NOW())
                      AND privacy_class = 'PRIVATE'
                    ORDER BY due_at NULLS LAST, updated_at DESC
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                out.append({
                    "commitment_id": r[0],
                    "commitment_type": r[1],
                    "due_at": float(r[2]) if r[2] is not None else None,
                    "confidence": float(r[3] or 0),
                    "privacy_class": r[4],
                    "source": r[5],
                    "status": r[6],
                    "evidence_ref": r[7],
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def count_email_unread_facts(self, owner_id: str = OWNER_ID) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM external_facts
                    WHERE owner_id = %s AND fact_kind = 'EMAIL_META'
                      AND privacy_class = 'PRIVATE'
                      AND COALESCE((payload->>'unread')::int, 0) = 1
                    """,
                    (owner_id,),
                )
                row = cur.fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0
        finally:
            self._release(conn)

    def gmail_connector_health(self, owner_id: str = OWNER_ID) -> str:
        conn = self._conn()
        if not conn:
            return ""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT health FROM connector_accounts
                    WHERE owner_id = %s AND connector_type = 'gmail' AND status = 'ACTIVE'
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (owner_id,),
                )
                row = cur.fetchone()
            return str(row[0] or "") if row else ""
        except Exception:
            return ""
        finally:
            self._release(conn)


proactive_store = ProactiveStore()


