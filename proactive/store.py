"""PostgreSQL outbox for V6.1 signals/insights/deliveries. Bounded. Fail-open."""

from __future__ import annotations

import json
import time
import uuid
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
            return {"inform_count": 0, "suggest_count": 0, "prepare_count": 0, "ask_count": 0, "cooldowns": {}}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT inform_count, cooldowns, suggest_count, prepare_count, ask_count
                    FROM proactive_attention
                    WHERE owner_id = %s AND day_key = %s::date
                    """,
                    (owner_id, day_key),
                )
                row = cur.fetchone()
            if not row:
                return {"inform_count": 0, "suggest_count": 0, "prepare_count": 0, "ask_count": 0, "cooldowns": {}}
            cd = row[1] if isinstance(row[1], dict) else (json.loads(row[1]) if row[1] else {})
            def _i(v):
                try:
                    return int(v or 0)
                except (TypeError, ValueError):
                    return 0
            return {
                "inform_count": _i(row[0]),
                "suggest_count": _i(row[2] if len(row) > 2 else 0),
                "prepare_count": _i(row[3] if len(row) > 3 else 0),
                "ask_count": _i(row[4] if len(row) > 4 else 0),
                "cooldowns": cd,
            }
        except Exception:
            return {"inform_count": 0, "suggest_count": 0, "prepare_count": 0, "ask_count": 0, "cooldowns": {}}
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
                    "record_kind": "COMMITMENT",
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def list_eval_commitments(self, owner_id: str = OWNER_ID, limit: int = 80) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.commitment_id, c.commitment_type, EXTRACT(EPOCH FROM c.due_at),
                           c.privacy_class, c.source_connector, c.status, c.evidence_ref,
                           c.fingerprint, EXTRACT(EPOCH FROM c.source_ts),
                           EXTRACT(EPOCH FROM c.valid_until), c.source_message_id, a.project_id
                    FROM proactive_commitments c
                    LEFT JOIN connector_accounts a ON a.account_id = c.account_id
                    WHERE c.owner_id = %s AND c.status = 'OPEN'
                      AND (c.valid_until IS NULL OR c.valid_until > NOW())
                      AND c.due_at IS NOT NULL
                      AND c.due_at >= NOW() - INTERVAL '7 days'
                      AND c.due_at <= NOW() + INTERVAL '72 hours'
                    ORDER BY c.due_at NULLS LAST
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
                    "privacy_class": r[3],
                    "source_connector": r[4],
                    "status": r[5],
                    "evidence_ref": r[6],
                    "fingerprint": r[7] or "",
                    "source_ts": float(r[8]) if r[8] is not None else None,
                    "valid_until": float(r[9]) if r[9] is not None else None,
                    "source_message_id": r[10] or "",
                    "project_id": r[11] or "",
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def list_eval_cal_facts(self, owner_id: str = OWNER_ID, limit: int = 80) -> List[Dict[str, Any]]:
        return self._list_eval_facts(
            owner_id, ("CAL_EVENT",), limit,
            extra_sql=" AND occurred_at IS NOT NULL AND occurred_at >= NOW() - INTERVAL '7 days' AND occurred_at <= NOW() + INTERVAL '72 hours'",
        )

    def list_eval_review_facts(self, owner_id: str = OWNER_ID, limit: int = 80) -> List[Dict[str, Any]]:
        return self._list_eval_facts(
            owner_id, ("GH_REVIEW", "GH_PR"), limit,
            extra_sql=" AND occurred_at IS NOT NULL AND occurred_at <= NOW() - INTERVAL '7 days'",
        )

    def _list_eval_facts(self, owner_id: str, kinds: tuple, limit: int, extra_sql: str = "") -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT fact_id, fact_kind, connector_type, source_record_id, project_id,
                           privacy_class, content_sha256,
                           EXTRACT(EPOCH FROM occurred_at), EXTRACT(EPOCH FROM valid_until)
                    FROM external_facts
                    WHERE owner_id = %s AND fact_kind = ANY(%s)
                    {extra_sql}
                    ORDER BY occurred_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (owner_id, list(kinds), int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                out.append({
                    "fact_id": r[0],
                    "fact_kind": r[1],
                    "connector_type": r[2],
                    "source_record_id": r[3] or "",
                    "project_id": r[4] or "",
                    "privacy_class": r[5],
                    "content_sha256": r[6] or "",
                    "occurred_at": float(r[7]) if r[7] is not None else None,
                    "start_ts": float(r[7]) if r[7] is not None else None,
                    "valid_until": float(r[8]) if r[8] is not None else None,
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def list_eval_blocked_tasks(self, limit: int = 80) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT task_id, status, EXTRACT(EPOCH FROM updated_at), artifacts
                    FROM task_checkpoints
                    WHERE UPPER(COALESCE(status,'')) IN
                          ('FAILED','PAUSED','WAITING_FOR_APPROVAL','ERROR')
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                art = r[3]
                if isinstance(art, str):
                    try:
                        art = json.loads(art)
                    except Exception:
                        art = {}
                if not isinstance(art, dict):
                    art = {}
                pid = str(art.get("project_id") or "").strip()
                out.append({
                    "task_id": r[0],
                    "status": r[1],
                    "updated_at": float(r[2]) if r[2] is not None else None,
                    "project_id": pid,
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def upsert_world_evidence(self, row: Dict[str, Any]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        try:
            occ = row.get("occurred_at")
            vu = row.get("valid_until")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_evidence (
                        evidence_id, owner_id, project_id, source_kind, source_id,
                        source_record_id, connector_type, reliability_class, strength,
                        privacy_class, occurred_at, valid_until, independence_key,
                        transform_id, transform_version, status, idempotency_key,
                        content_sha256, provenance
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        %s,%s,%s,'ACTIVE',%s,%s,%s::jsonb
                    )
                    ON CONFLICT (idempotency_key) DO UPDATE SET source_id = world_evidence.source_id
                    RETURNING evidence_id
                    """,
                    (
                        str(row.get("evidence_id") or "")[:64],
                        str(row.get("owner_id") or OWNER_ID)[:64],
                        (str(row.get("project_id"))[:64] if row.get("project_id") else None),
                        str(row.get("source_kind") or "")[:32],
                        str(row.get("source_id") or "")[:128],
                        str(row.get("source_record_id") or "")[:128],
                        str(row.get("connector_type") or "")[:32],
                        str(row.get("reliability_class") or "")[:32],
                        float(row.get("strength") or 0),
                        str(row.get("privacy_class") or "NORMAL")[:16],
                        occ, occ, vu, vu,
                        str(row.get("independence_key") or "")[:80],
                        str(row.get("transform_id") or "cite_v624")[:40],
                        str(row.get("transform_version") or "v624.1")[:16],
                        str(row.get("idempotency_key") or "")[:160],
                        str(row.get("content_sha256") or "")[:64],
                        json.dumps(row.get("provenance") or {}),
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

    def upsert_world_prediction(self, row: Dict[str, Any], links: List[Dict[str, Any]]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        try:
            hs, he, evl, vu = row.get("horizon_start"), row.get("horizon_end"), row.get("evaluated_at"), row.get("valid_until")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_predictions (
                        prediction_id, owner_id, project_id, prediction_type, subject_key,
                        claim_code, horizon_start, horizon_end, confidence, probability,
                        risk_class, status, privacy_class, fingerprint, rule_id, rule_version,
                        generation, evaluated_at, valid_until, provenance
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s) END,
                        %s,NULL,%s,'ACTIVE',%s,%s,%s,%s,1,
                        to_timestamp(%s), to_timestamp(%s), %s::jsonb
                    )
                    ON CONFLICT (owner_id, fingerprint) DO UPDATE SET
                        confidence = EXCLUDED.confidence,
                        risk_class = EXCLUDED.risk_class,
                        evaluated_at = EXCLUDED.evaluated_at,
                        valid_until = EXCLUDED.valid_until,
                        provenance = EXCLUDED.provenance,
                        claim_code = EXCLUDED.claim_code,
                        status = 'ACTIVE',
                        generation = world_predictions.generation + 1
                    RETURNING prediction_id
                    """,
                    (
                        str(row.get("prediction_id") or "")[:64],
                        str(row.get("owner_id") or OWNER_ID)[:64],
                        (str(row.get("project_id"))[:64] if row.get("project_id") else None),
                        str(row.get("prediction_type") or "")[:40],
                        str(row.get("subject_key") or "")[:160],
                        str(row.get("claim_code") or "")[:40],
                        hs, hs, he, he,
                        float(row.get("confidence") or 0),
                        str(row.get("risk_class") or "NONE")[:16],
                        str(row.get("privacy_class") or "NORMAL")[:16],
                        str(row.get("fingerprint") or "")[:64],
                        str(row.get("rule_id") or "")[:40],
                        str(row.get("rule_version") or "v624.1")[:16],
                        float(evl or time.time()),
                        float(vu or time.time() + 86400),
                        json.dumps(row.get("provenance") or {}),
                    ),
                )
                got = cur.fetchone()
                pid = got[0] if got else ""
                if pid:
                    cur.execute("DELETE FROM world_prediction_evidence WHERE prediction_id = %s", (pid,))
                    for ln in links or []:
                        cur.execute(
                            """
                            INSERT INTO world_prediction_evidence (prediction_id, evidence_id, role)
                            VALUES (%s,%s,%s)
                            ON CONFLICT DO NOTHING
                            """,
                            (pid, str(ln.get("evidence_id") or "")[:64], str(ln.get("role") or "SUPPORTING")[:16]),
                        )
                    cur.execute(
                        """
                        INSERT INTO world_prediction_events (event_id, prediction_id, from_status, to_status, reason)
                        VALUES (%s,%s,NULL,'ACTIVE','eval')
                        """,
                        (str(uuid.uuid4())[:64], pid),
                    )
            conn.commit()
            return pid
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def list_active_predictions(self, owner_id: str = OWNER_ID, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT prediction_id, prediction_type, claim_code, confidence, risk_class,
                           privacy_class, provenance, EXTRACT(EPOCH FROM horizon_end)
                    FROM world_predictions
                    WHERE owner_id = %s AND status = 'ACTIVE'
                      AND valid_until > NOW()
                      AND privacy_class <> 'SENSITIVE'
                    ORDER BY horizon_end NULLS LAST
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                prov = r[6] if isinstance(r[6], dict) else (json.loads(r[6]) if r[6] else {})
                out.append({
                    "record_kind": "PREDICTION",
                    "prediction_id": r[0],
                    "prediction_type": r[1],
                    "claim_code": r[2],
                    "confidence": float(r[3] or 0),
                    "risk_class": r[4],
                    "privacy_class": r[5],
                    "evidence_ids": (prov or {}).get("evidence_ids") or [],
                    "horizon_end": float(r[7]) if r[7] is not None else None,
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def expire_world_predictions(self, owner_id: str = OWNER_ID) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE world_predictions SET status = 'EXPIRED'
                    WHERE owner_id = %s AND status = 'ACTIVE' AND valid_until <= NOW()
                    """,
                    (owner_id,),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)

    def count_world_predictions(self, owner_id: str = OWNER_ID) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM world_predictions WHERE owner_id = %s",
                    (owner_id,),
                )
                row = cur.fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0
        finally:
            self._release(conn)

    def count_world_evidence(self, owner_id: str = OWNER_ID) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM world_evidence WHERE owner_id = %s", (owner_id,))
                row = cur.fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0
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

    def list_suggest_candidates(self, owner_id: str = OWNER_ID, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT prediction_id, prediction_type, claim_code, confidence, risk_class,
                           privacy_class, provenance, fingerprint, subject_key, status, project_id,
                           EXTRACT(EPOCH FROM horizon_end), EXTRACT(EPOCH FROM valid_until)
                    FROM world_predictions
                    WHERE owner_id = %s AND status = 'ACTIVE'
                      AND valid_until > NOW()
                      AND privacy_class <> 'SENSITIVE'
                    ORDER BY horizon_end NULLS LAST
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                prov = r[6] if isinstance(r[6], dict) else (json.loads(r[6]) if r[6] else {})
                out.append({
                    "prediction_id": r[0],
                    "prediction_type": r[1],
                    "claim_code": r[2],
                    "confidence": float(r[3] or 0),
                    "risk_class": r[4],
                    "privacy_class": r[5],
                    "provenance": prov or {},
                    "evidence_ids": (prov or {}).get("evidence_ids") or [],
                    "fingerprint": r[7],
                    "subject_key": r[8],
                    "status": r[9],
                    "project_id": r[10],
                    "horizon_end": float(r[11]) if r[11] is not None else None,
                    "valid_until": float(r[12]) if r[12] is not None else None,
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def get_suggestion_by_fingerprint(self, owner_id: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT suggestion_id, owner_id, prediction_id, suggestion_type, template_id,
                           status, privacy_class, fingerprint, EXTRACT(EPOCH FROM valid_until)
                    FROM world_suggestions
                    WHERE owner_id = %s AND fingerprint = %s
                    """,
                    (owner_id, fingerprint),
                )
                r = cur.fetchone()
            if not r:
                return None
            return {
                "suggestion_id": r[0], "owner_id": r[1], "prediction_id": r[2],
                "suggestion_type": r[3], "template_id": r[4], "status": r[5],
                "privacy_class": r[6], "fingerprint": r[7],
                "valid_until": float(r[8]) if r[8] is not None else None,
            }
        except Exception:
            return None
        finally:
            self._release(conn)

    def touch_suggestion_evaluated(self, suggestion_id: str, owner_id: str, now: float) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE world_suggestions
                    SET evaluated_at = to_timestamp(%s)
                    WHERE suggestion_id = %s AND owner_id = %s
                    """,
                    (float(now), suggestion_id, owner_id),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)

    def upsert_world_suggestion(self, row: Dict[str, Any]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        sid = str(row.get("suggestion_id") or uuid.uuid4())[:64]
        try:
            params = row.get("safe_params") if isinstance(row.get("safe_params"), dict) else {}
            prov = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
            vu = float(row.get("valid_until") or time.time() + 86400)
            evl = float(row.get("evaluated_at") or time.time())
            owner = str(row.get("owner_id") or OWNER_ID)[:64]
            fp = str(row.get("fingerprint") or "")[:64]
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_suggestions (
                        suggestion_id, owner_id, project_id, prediction_id, suggestion_type,
                        claim_code, template_id, safe_params, priority, confidence, risk_class,
                        privacy_class, fingerprint, rule_id, rule_version, status,
                        valid_until, provenance, evaluated_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,'OPEN',
                        to_timestamp(%s), %s::jsonb, to_timestamp(%s)
                    )
                    ON CONFLICT (owner_id, fingerprint) DO UPDATE SET
                        evaluated_at = EXCLUDED.evaluated_at,
                        confidence = EXCLUDED.confidence,
                        risk_class = EXCLUDED.risk_class,
                        valid_until = EXCLUDED.valid_until,
                        claim_code = EXCLUDED.claim_code,
                        safe_params = EXCLUDED.safe_params
                    WHERE world_suggestions.status IN ('OPEN', 'DELIVERED')
                    RETURNING suggestion_id, (xmax = 0) AS inserted
                    """,
                    (
                        sid, owner,
                        (str(row.get("project_id"))[:64] if row.get("project_id") else None),
                        str(row.get("prediction_id") or "")[:64],
                        str(row.get("suggestion_type") or "")[:40],
                        str(row.get("claim_code") or "")[:40],
                        str(row.get("template_id") or "")[:64],
                        json.dumps(params),
                        str(row.get("priority") or "MEDIUM")[:16],
                        float(row.get("confidence") or 0),
                        str(row.get("risk_class") or "NONE")[:16],
                        str(row.get("privacy_class") or "NORMAL")[:16],
                        fp,
                        str(row.get("rule_id") or "")[:40],
                        str(row.get("rule_version") or "v625.1")[:16],
                        vu, json.dumps(prov), evl,
                    ),
                )
                got = cur.fetchone()
                inserted = False
                out_id = ""
                if got:
                    out_id = got[0]
                    inserted = bool(got[1])
                    if inserted:
                        cur.execute(
                            """
                            INSERT INTO world_suggestion_events
                            (event_id, suggestion_id, from_status, to_status, reason)
                            VALUES (%s,%s,NULL,'OPEN','eval')
                            """,
                            (str(uuid.uuid4())[:64], out_id),
                        )
                else:
                    cur.execute(
                        """
                        SELECT suggestion_id FROM world_suggestions
                        WHERE owner_id = %s AND fingerprint = %s
                        """,
                        (owner, fp),
                    )
                    r2 = cur.fetchone()
                    out_id = r2[0] if r2 else ""
            conn.commit()
            return out_id
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def sync_suggestion_lifecycle(self, owner_id: str = OWNER_ID) -> int:
        conn = self._conn()
        if not conn:
            return 0
        n = 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.suggestion_id, s.status
                    FROM world_suggestions s
                    JOIN world_predictions p ON s.prediction_id = p.prediction_id
                    WHERE s.owner_id = %s
                      AND s.status IN ('OPEN', 'DELIVERED')
                      AND (p.status = 'EXPIRED' OR s.valid_until <= NOW())
                    """,
                    (owner_id,),
                )
                expire_rows = list(cur.fetchall() or [])
                for sid, from_st in expire_rows:
                    cur.execute(
                        """
                        UPDATE world_suggestions SET status = 'EXPIRED'
                        WHERE suggestion_id = %s AND owner_id = %s AND status IN ('OPEN','DELIVERED')
                        """,
                        (sid, owner_id),
                    )
                    if cur.rowcount:
                        cur.execute(
                            """
                            INSERT INTO world_suggestion_events
                            (event_id, suggestion_id, from_status, to_status, reason)
                            VALUES (%s,%s,%s,'EXPIRED','lifecycle')
                            """,
                            (str(uuid.uuid4())[:64], sid, from_st),
                        )
                        n += 1
                cur.execute(
                    """
                    SELECT s.suggestion_id, s.status
                    FROM world_suggestions s
                    JOIN world_predictions p ON s.prediction_id = p.prediction_id
                    WHERE s.owner_id = %s
                      AND s.status IN ('OPEN', 'DELIVERED')
                      AND p.status IN ('SUPERSEDED', 'INVALIDATED')
                    """,
                    (owner_id,),
                )
                sup_rows = list(cur.fetchall() or [])
                for sid, from_st in sup_rows:
                    cur.execute(
                        """
                        UPDATE world_suggestions SET status = 'SUPERSEDED'
                        WHERE suggestion_id = %s AND owner_id = %s AND status IN ('OPEN','DELIVERED')
                        """,
                        (sid, owner_id),
                    )
                    if cur.rowcount:
                        cur.execute(
                            """
                            INSERT INTO world_suggestion_events
                            (event_id, suggestion_id, from_status, to_status, reason)
                            VALUES (%s,%s,%s,'SUPERSEDED','lifecycle')
                            """,
                            (str(uuid.uuid4())[:64], sid, from_st),
                        )
                        n += 1
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

    def list_hud_suggestions(self, owner_id: str = OWNER_ID, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT suggestion_id, suggestion_type, template_id, safe_params, priority,
                           privacy_class, status, fingerprint
                    FROM world_suggestions
                    WHERE owner_id = %s
                      AND privacy_class = 'NORMAL'
                      AND status IN ('OPEN', 'DELIVERED')
                      AND valid_until > NOW()
                    ORDER BY evaluated_at DESC
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                params = r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {})
                out.append({
                    "suggestion_id": r[0],
                    "suggestion_type": r[1],
                    "template_id": r[2],
                    "safe_params": params if isinstance(params, dict) else {},
                    "priority": r[4],
                    "privacy_class": r[5],
                    "status": r[6],
                    "fingerprint": r[7],
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def dismiss_suggestion(self, suggestion_id: str, owner_id: str) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status FROM world_suggestions
                    WHERE suggestion_id = %s AND owner_id = %s
                    """,
                    (suggestion_id, owner_id),
                )
                before = cur.fetchone()
                cur.execute(
                    """
                    UPDATE world_suggestions
                    SET status = 'DISMISSED', dismissed_at = NOW()
                    WHERE suggestion_id = %s AND owner_id = %s
                      AND status IN ('OPEN', 'DELIVERED')
                    RETURNING suggestion_id
                    """,
                    (suggestion_id, owner_id),
                )
                got = cur.fetchone()
                if got:
                    from_st = before[0] if before else "OPEN"
                    cur.execute(
                        """
                        INSERT INTO world_suggestion_events
                        (event_id, suggestion_id, from_status, to_status, reason)
                        VALUES (%s,%s,%s,'DISMISSED','dismiss')
                        """,
                        (str(uuid.uuid4())[:64], suggestion_id, from_st),
                    )
            conn.commit()
            return bool(got)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def persist_normal_suggestion_delivery(self, suggestion_id: str, owner_id: str) -> tuple:
        did = str(uuid.uuid4())
        conn = self._conn()
        if not conn:
            return "", False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_suggestion_deliveries
                    (delivery_id, suggestion_id, channel, status, attempts)
                    VALUES (%s,%s,'hud','DELIVERED',1)
                    ON CONFLICT (suggestion_id, channel) DO NOTHING
                    RETURNING delivery_id
                    """,
                    (did, suggestion_id),
                )
                row = cur.fetchone()
                created = bool(row)
                if row:
                    cur.execute(
                        """
                        UPDATE world_suggestions
                        SET status = 'DELIVERED'
                        WHERE suggestion_id = %s AND owner_id = %s AND status = 'OPEN'
                        """,
                        (suggestion_id, owner_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO world_suggestion_events
                        (event_id, suggestion_id, from_status, to_status, reason)
                        VALUES (%s,%s,'OPEN','DELIVERED','deliver')
                        """,
                        (str(uuid.uuid4())[:64], suggestion_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT delivery_id FROM world_suggestion_deliveries
                        WHERE suggestion_id = %s AND channel = 'hud'
                        """,
                        (suggestion_id,),
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

    def upsert_suggestion_delivery(self, suggestion_id: str, channel: str = "hud") -> tuple:
        return self.persist_normal_suggestion_delivery(suggestion_id, OWNER_ID)

    def suggestion_delivery_exists(self, suggestion_id: str, channel: str = "hud") -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM world_suggestion_deliveries
                    WHERE suggestion_id = %s AND channel = %s
                    """,
                    (suggestion_id, channel),
                )
                return bool(cur.fetchone())
        except Exception:
            return False
        finally:
            self._release(conn)

    def insert_ask_session(self, session_id_hash: str, owner_id: str, csrf_token: str, expires_at: float) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ask_sessions (session_id_hash, owner_id, csrf_token, expires_at)
                    VALUES (%s,%s,%s, to_timestamp(%s))
                    """,
                    (session_id_hash[:64], owner_id[:64], csrf_token[:64], float(expires_at)),
                )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def get_ask_session(self, session_id_hash: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id_hash, owner_id, csrf_token,
                           EXTRACT(EPOCH FROM expires_at), EXTRACT(EPOCH FROM revoked_at)
                    FROM ask_sessions WHERE session_id_hash = %s
                    """,
                    (session_id_hash,),
                )
                r = cur.fetchone()
            if not r:
                return None
            return {
                "session_id_hash": r[0],
                "owner_id": r[1],
                "csrf_token": r[2],
                "expires_at": float(r[3]) if r[3] is not None else 0.0,
                "revoked_at": float(r[4]) if r[4] is not None else None,
            }
        except Exception:
            return None
        finally:
            self._release(conn)

    def revoke_ask_session(self, session_id_hash: str) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ask_sessions SET revoked_at = NOW()
                    WHERE session_id_hash = %s AND revoked_at IS NULL
                    """,
                    (session_id_hash,),
                )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def revoke_ask_sessions_for_owner(self, owner_id: str) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ask_sessions SET revoked_at = NOW()
                    WHERE owner_id = %s AND revoked_at IS NULL
                    """,
                    (owner_id,),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)

    def list_prepare_candidates(self, owner_id: str = OWNER_ID, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.suggestion_id, s.owner_id, s.project_id, s.prediction_id,
                           s.suggestion_type, s.claim_code, s.template_id, s.status,
                           s.privacy_class, s.risk_class, s.confidence, s.fingerprint,
                           EXTRACT(EPOCH FROM s.valid_until),
                           p.prediction_type, p.status, p.privacy_class, p.risk_class,
                           p.confidence, p.provenance, p.claim_code,
                           EXTRACT(EPOCH FROM p.valid_until), EXTRACT(EPOCH FROM p.horizon_end)
                    FROM world_suggestions s
                    JOIN world_predictions p ON s.prediction_id = p.prediction_id
                    WHERE s.owner_id = %s
                      AND s.status IN ('OPEN', 'DELIVERED')
                      AND s.valid_until > NOW()
                      AND p.status = 'ACTIVE'
                      AND p.valid_until > NOW()
                      AND s.privacy_class <> 'SENSITIVE'
                    ORDER BY s.evaluated_at DESC
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                prov = r[18] if isinstance(r[18], dict) else (json.loads(r[18]) if r[18] else {})
                sug = {
                    "suggestion_id": r[0], "owner_id": r[1], "project_id": r[2],
                    "prediction_id": r[3], "suggestion_type": r[4], "claim_code": r[5],
                    "template_id": r[6], "status": r[7], "privacy_class": r[8],
                    "risk_class": r[9], "confidence": float(r[10] or 0),
                    "fingerprint": r[11],
                    "valid_until": float(r[12]) if r[12] is not None else None,
                }
                pred = {
                    "prediction_id": r[3], "prediction_type": r[13], "status": r[14],
                    "privacy_class": r[15], "risk_class": r[16],
                    "confidence": float(r[17] or 0), "provenance": prov or {},
                    "evidence_ids": (prov or {}).get("evidence_ids") or [],
                    "claim_code": r[19],
                    "valid_until": float(r[20]) if r[20] is not None else None,
                    "horizon_end": float(r[21]) if r[21] is not None else None,
                }
                out.append({"suggestion": sug, "prediction": pred})
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def get_preparation(self, preparation_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT preparation_id, owner_id, suggestion_id, prediction_id, preparation_type,
                           action_type, future_act_class, template_id, safe_params, param_hash,
                           status, privacy_class, risk_class, fingerprint, rule_version,
                           EXTRACT(EPOCH FROM valid_until)
                    FROM world_preparations
                    WHERE preparation_id = %s AND owner_id = %s
                    """,
                    (str(preparation_id)[:64], str(owner_id)[:64]),
                )
                r = cur.fetchone()
            if not r:
                return None
            params = r[8] if isinstance(r[8], dict) else (json.loads(r[8]) if r[8] else {})
            return {
                "preparation_id": r[0], "owner_id": r[1], "suggestion_id": r[2],
                "prediction_id": r[3], "preparation_type": r[4], "action_type": r[5],
                "future_act_class": r[6], "template_id": r[7],
                "safe_params": params if isinstance(params, dict) else {},
                "param_hash": r[9], "status": r[10], "privacy_class": r[11],
                "risk_class": r[12], "fingerprint": r[13], "rule_version": r[14],
                "valid_until": float(r[15]) if r[15] is not None else None,
            }
        except Exception:
            return None
        finally:
            self._release(conn)

    def get_preparation_by_fingerprint(self, owner_id: str, fingerprint: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT preparation_id, owner_id, suggestion_id, prediction_id, preparation_type,
                           action_type, future_act_class, template_id, safe_params, param_hash,
                           status, privacy_class, risk_class, fingerprint, rule_version,
                           EXTRACT(EPOCH FROM valid_until)
                    FROM world_preparations
                    WHERE owner_id = %s AND fingerprint = %s
                    """,
                    (owner_id, fingerprint),
                )
                r = cur.fetchone()
            if not r:
                return None
            params = r[8] if isinstance(r[8], dict) else (json.loads(r[8]) if r[8] else {})
            return {
                "preparation_id": r[0], "owner_id": r[1], "suggestion_id": r[2],
                "prediction_id": r[3], "preparation_type": r[4], "action_type": r[5],
                "future_act_class": r[6], "template_id": r[7],
                "safe_params": params if isinstance(params, dict) else {},
                "param_hash": r[9], "status": r[10], "privacy_class": r[11],
                "risk_class": r[12], "fingerprint": r[13], "rule_version": r[14],
                "valid_until": float(r[15]) if r[15] is not None else None,
            }
        except Exception:
            return None
        finally:
            self._release(conn)

    def has_conflicting_pending_ask(self, owner_id: str, suggestion_id: str) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM world_approval_requests a
                    JOIN world_preparations p ON a.preparation_id = p.preparation_id
                    WHERE a.owner_id = %s AND p.suggestion_id = %s AND a.status = 'PENDING'
                    LIMIT 1
                    """,
                    (owner_id, suggestion_id),
                )
                return bool(cur.fetchone())
        except Exception:
            return False
        finally:
            self._release(conn)

    def upsert_world_preparation(self, row: Dict[str, Any]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        pid = str(row.get("preparation_id") or uuid.uuid4())[:64]
        try:
            params = row.get("safe_params") if isinstance(row.get("safe_params"), dict) else {}
            extra = set(params.keys()) - {
                "risk_class", "horizon_hours", "prediction_type",
                "suggestion_type", "action_type", "claim_code",
            }
            if extra:
                return ""
            prov = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
            vu = float(row.get("valid_until") or time.time() + 86400)
            evl = float(row.get("evaluated_at") or time.time())
            owner = str(row.get("owner_id") or OWNER_ID)[:64]
            fp = str(row.get("fingerprint") or "")[:64]
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_preparations (
                        preparation_id, owner_id, project_id, suggestion_id, prediction_id,
                        preparation_type, action_type, future_act_class, template_id,
                        safe_params, param_hash, preview_key, risk_class, privacy_class,
                        fingerprint, rule_id, rule_version, status, valid_until,
                        provenance, evaluated_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,'READY',
                        to_timestamp(%s), %s::jsonb, to_timestamp(%s)
                    )
                    ON CONFLICT (owner_id, fingerprint) DO UPDATE SET
                        evaluated_at = EXCLUDED.evaluated_at
                    WHERE world_preparations.status IN ('READY', 'ASKED')
                    RETURNING preparation_id, (xmax = 0) AS inserted
                    """,
                    (
                        pid, owner,
                        (str(row.get("project_id"))[:64] if row.get("project_id") else None),
                        str(row.get("suggestion_id") or "")[:64],
                        str(row.get("prediction_id") or "")[:64],
                        str(row.get("preparation_type") or "")[:40],
                        str(row.get("action_type") or "NONE")[:40],
                        str(row.get("future_act_class") or "NONE")[:16],
                        str(row.get("template_id") or "")[:64],
                        json.dumps(params),
                        str(row.get("param_hash") or "")[:64],
                        str(row.get("preview_key") or "")[:64],
                        str(row.get("risk_class") or "NONE")[:16],
                        str(row.get("privacy_class") or "NORMAL")[:16],
                        fp,
                        str(row.get("rule_id") or "")[:40],
                        str(row.get("rule_version") or "v626.1")[:16],
                        vu, json.dumps(prov), evl,
                    ),
                )
                got = cur.fetchone()
                out_id = ""
                inserted = False
                if got:
                    out_id = got[0]
                    inserted = bool(got[1])
                    if inserted:
                        cur.execute(
                            """
                            INSERT INTO world_preparation_events
                            (event_id, preparation_id, from_status, to_status, reason)
                            VALUES (%s,%s,NULL,'READY','eval')
                            """,
                            (str(uuid.uuid4())[:64], out_id),
                        )
                else:
                    cur.execute(
                        """
                        SELECT preparation_id FROM world_preparations
                        WHERE owner_id = %s AND fingerprint = %s
                        """,
                        (owner, fp),
                    )
                    r2 = cur.fetchone()
                    out_id = r2[0] if r2 else ""
            conn.commit()
            return out_id
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def sync_preparation_lifecycle(self, owner_id: str = OWNER_ID) -> int:
        conn = self._conn()
        if not conn:
            return 0
        n = 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.preparation_id, p.status
                    FROM world_preparations p
                    JOIN world_suggestions s ON p.suggestion_id = s.suggestion_id
                    WHERE p.owner_id = %s
                      AND p.status IN ('READY', 'ASKED')
                      AND (s.status IN ('EXPIRED','DISMISSED','SUPERSEDED')
                           OR p.valid_until <= NOW() OR s.valid_until <= NOW())
                    """,
                    (owner_id,),
                )
                rows = list(cur.fetchall() or [])
                for pid, from_st in rows:
                    cur.execute(
                        """
                        SELECT CASE
                            WHEN p.valid_until <= NOW() OR s.status = 'EXPIRED' THEN 'EXPIRED'
                            WHEN s.status IN ('DISMISSED','SUPERSEDED') THEN 'SUPERSEDED'
                            ELSE 'EXPIRED'
                        END
                        FROM world_preparations p
                        JOIN world_suggestions s ON p.suggestion_id = s.suggestion_id
                        WHERE p.preparation_id = %s
                        """,
                        (pid,),
                    )
                    dest = cur.fetchone()
                    to_st = dest[0] if dest else "EXPIRED"
                    cur.execute(
                        """
                        UPDATE world_preparations SET status = %s
                        WHERE preparation_id = %s AND owner_id = %s AND status IN ('READY','ASKED')
                        """,
                        (to_st, pid, owner_id),
                    )
                    if cur.rowcount:
                        cur.execute(
                            """
                            INSERT INTO world_preparation_events
                            (event_id, preparation_id, from_status, to_status, reason)
                            VALUES (%s,%s,%s,%s,%s)
                            """,
                            (str(uuid.uuid4())[:64], pid, from_st, to_st, "lifecycle"),
                        )
                        cur.execute(
                            """
                            UPDATE world_approval_requests SET status = 'CANCELLED'
                            WHERE preparation_id = %s AND owner_id = %s AND status = 'PENDING'
                            RETURNING approval_id
                            """,
                            (pid, owner_id),
                        )
                        for ar in cur.fetchall() or []:
                            cur.execute(
                                """
                                INSERT INTO world_approval_events
                                (event_id, approval_id, from_status, to_status, reason)
                                VALUES (%s,%s,'PENDING','CANCELLED','lifecycle')
                                """,
                                (str(uuid.uuid4())[:64], ar[0]),
                            )
                        n += 1
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

    def list_hud_preparations(self, owner_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT preparation_id, preparation_type, action_type, template_id, safe_params,
                           privacy_class, status, fingerprint, param_hash, suggestion_id,
                           EXTRACT(EPOCH FROM valid_until), risk_class
                    FROM world_preparations
                    WHERE owner_id = %s
                      AND privacy_class = 'NORMAL'
                      AND status IN ('READY', 'ASKED')
                      AND valid_until > NOW()
                    ORDER BY evaluated_at DESC
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                params = r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else {})
                out.append({
                    "preparation_id": r[0], "preparation_type": r[1], "action_type": r[2],
                    "template_id": r[3], "safe_params": params if isinstance(params, dict) else {},
                    "privacy_class": r[5], "status": r[6], "fingerprint": r[7],
                    "param_hash": r[8], "suggestion_id": r[9],
                    "valid_until": float(r[10]) if r[10] is not None else None,
                    "risk_class": r[11],
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def cancel_preparation(self, preparation_id: str, owner_id: str) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status FROM world_preparations
                    WHERE preparation_id = %s AND owner_id = %s
                    """,
                    (preparation_id, owner_id),
                )
                before = cur.fetchone()
                cur.execute(
                    """
                    UPDATE world_preparations SET status = 'CANCELLED'
                    WHERE preparation_id = %s AND owner_id = %s AND status IN ('READY','ASKED')
                    RETURNING preparation_id
                    """,
                    (preparation_id, owner_id),
                )
                got = cur.fetchone()
                if got:
                    from_st = before[0] if before else "READY"
                    cur.execute(
                        """
                        INSERT INTO world_preparation_events
                        (event_id, preparation_id, from_status, to_status, reason)
                        VALUES (%s,%s,%s,'CANCELLED','cancel')
                        """,
                        (str(uuid.uuid4())[:64], preparation_id, from_st),
                    )
                    cur.execute(
                        """
                        UPDATE world_approval_requests SET status = 'CANCELLED'
                        WHERE preparation_id = %s AND owner_id = %s AND status = 'PENDING'
                        RETURNING approval_id
                        """,
                        (preparation_id, owner_id),
                    )
                    for ar in cur.fetchall() or []:
                        cur.execute(
                            """
                            INSERT INTO world_approval_events
                            (event_id, approval_id, from_status, to_status, reason)
                            VALUES (%s,%s,'PENDING','CANCELLED','cancel')
                            """,
                            (str(uuid.uuid4())[:64], ar[0]),
                        )
            conn.commit()
            return bool(got)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def insert_approval_request(self, row: Dict[str, Any]) -> str:
        conn = self._conn()
        if not conn:
            return ""
        aid = str(uuid.uuid4())[:64]
        try:
            owner = str(row.get("owner_id") or OWNER_ID)[:64]
            prep = str(row.get("preparation_id") or "")[:64]
            vu = float(row.get("valid_until") or time.time() + 3600)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_approval_requests (
                        approval_id, owner_id, preparation_id, action_type, param_hash,
                        binding_hash, csrf_binding_id, risk_class, privacy_class, status,
                        valid_until, rule_version, action_hash
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING', to_timestamp(%s), %s, %s
                    )
                    ON CONFLICT (preparation_id) WHERE status = 'PENDING' DO NOTHING
                    RETURNING approval_id
                    """,
                    (
                        aid, owner, prep,
                        str(row.get("action_type") or "")[:40],
                        str(row.get("param_hash") or "")[:64],
                        str(row.get("binding_hash") or "")[:64],
                        str(row.get("csrf_binding_id") or "worker")[:64],
                        str(row.get("risk_class") or "NONE")[:16],
                        str(row.get("privacy_class") or "NORMAL")[:16],
                        vu,
                        str(row.get("rule_version") or "v626.1")[:16],
                        str(row.get("action_hash") or "")[:64],
                    ),
                )
                got = cur.fetchone()
                if not got:
                    cur.execute(
                        """
                        SELECT approval_id FROM world_approval_requests
                        WHERE preparation_id = %s AND owner_id = %s AND status = 'PENDING'
                        """,
                        (prep, owner),
                    )
                    r2 = cur.fetchone()
                    conn.commit()
                    return r2[0] if r2 else ""
                out_id = got[0]
                cur.execute(
                    """
                    INSERT INTO world_approval_events
                    (event_id, approval_id, from_status, to_status, reason)
                    VALUES (%s,%s,NULL,'PENDING','request')
                    """,
                    (str(uuid.uuid4())[:64], out_id),
                )
                cur.execute(
                    """
                    UPDATE world_preparations SET status = 'ASKED'
                    WHERE preparation_id = %s AND owner_id = %s AND status = 'READY'
                    """,
                    (prep, owner),
                )
                if cur.rowcount:
                    cur.execute(
                        """
                        INSERT INTO world_preparation_events
                        (event_id, preparation_id, from_status, to_status, reason)
                        VALUES (%s,%s,'READY','ASKED','ask')
                        """,
                        (str(uuid.uuid4())[:64], prep),
                    )
            conn.commit()
            return out_id
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def _approval_row_from_tuple(self, r) -> Dict[str, Any]:
        return {
            "approval_id": r[0], "owner_id": r[1], "preparation_id": r[2],
            "action_type": r[3], "param_hash": r[4], "binding_hash": r[5],
            "csrf_binding_id": r[6], "risk_class": r[7], "privacy_class": r[8],
            "status": r[9],
            "valid_until": float(r[10]) if r[10] is not None else None,
            "rule_version": r[11],
            "decision_session_id": r[12],
            "action_hash": r[13] if len(r) > 13 else "",
        }

    def get_approval(self, approval_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT approval_id, owner_id, preparation_id, action_type, param_hash,
                           binding_hash, csrf_binding_id, risk_class, privacy_class, status,
                           EXTRACT(EPOCH FROM valid_until), rule_version, decision_session_id,
                           action_hash
                    FROM world_approval_requests
                    WHERE approval_id = %s AND owner_id = %s
                    """,
                    (approval_id, owner_id),
                )
                r = cur.fetchone()
            if not r:
                return None
            return self._approval_row_from_tuple(r)
        except Exception:
            return None
        finally:
            self._release(conn)

    def list_hud_approvals(self, owner_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT approval_id, owner_id, preparation_id, action_type, param_hash,
                           binding_hash, csrf_binding_id, risk_class, privacy_class, status,
                           EXTRACT(EPOCH FROM valid_until), rule_version, decision_session_id,
                           action_hash
                    FROM world_approval_requests
                    WHERE owner_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            return [self._approval_row_from_tuple(r) for r in rows]
        except Exception:
            return []
        finally:
            self._release(conn)

    def expire_pending_asks(self, owner_id: str = OWNER_ID) -> int:
        conn = self._conn()
        if not conn:
            return 0
        n = 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT approval_id, preparation_id FROM world_approval_requests
                    WHERE owner_id = %s AND status = 'PENDING' AND valid_until <= NOW()
                    FOR UPDATE
                    """,
                    (owner_id,),
                )
                rows = list(cur.fetchall() or [])
                for aid, prep in rows:
                    cur.execute(
                        """
                        UPDATE world_approval_requests SET status = 'EXPIRED', decided_at = NOW()
                        WHERE approval_id = %s AND owner_id = %s AND status = 'PENDING'
                        """,
                        (aid, owner_id),
                    )
                    if cur.rowcount:
                        cur.execute(
                            """
                            INSERT INTO world_approval_events
                            (event_id, approval_id, from_status, to_status, reason)
                            VALUES (%s,%s,'PENDING','EXPIRED','ttl')
                            """,
                            (str(uuid.uuid4())[:64], aid),
                        )
                        cur.execute(
                            """
                            UPDATE world_preparations SET status = 'EXPIRED'
                            WHERE preparation_id = %s AND owner_id = %s AND status = 'ASKED'
                            """,
                            (prep, owner_id),
                        )
                        if cur.rowcount:
                            cur.execute(
                                """
                                INSERT INTO world_preparation_events
                                (event_id, preparation_id, from_status, to_status, reason)
                                VALUES (%s,%s,'ASKED','EXPIRED','ask_ttl')
                                """,
                                (str(uuid.uuid4())[:64], prep),
                            )
                        n += 1
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

    def decide_approval(
        self,
        kind: str,
        approval_id: str,
        owner_id: str,
        session_id_hash: str,
        client_binding_hash: str,
        recompute,
        hashes_match,
        emit,
    ) -> Dict[str, Any]:
        conn = self._conn()
        if not conn:
            return {"ok": False, "error": "store_unavailable"}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT approval_id, owner_id, preparation_id, action_type, param_hash,
                           binding_hash, csrf_binding_id, risk_class, privacy_class, status,
                           EXTRACT(EPOCH FROM valid_until), rule_version, decision_session_id,
                           action_hash
                    FROM world_approval_requests
                    WHERE approval_id = %s AND owner_id = %s
                    FOR UPDATE
                    """,
                    (approval_id, owner_id),
                )
                r = cur.fetchone()
                if not r:
                    conn.commit()
                    emit("proactive.ask.owner_mismatch", status="skipped", attributes={"reason": "not_found"})
                    return {"ok": False, "error": "not_found", "http": 404}
                row = self._approval_row_from_tuple(r)
                computed = recompute(row)
                stored = str(row.get("binding_hash") or "")
                if not hashes_match(computed, stored):
                    conn.commit()
                    emit(
                        "proactive.ask.parameter_mismatch",
                        status="skipped",
                        attributes={"approval_id": approval_id[:36], "binding_ok": False},
                    )
                    return {"ok": False, "error": "parameter_mismatch", "http": 409}
                if not hashes_match(str(client_binding_hash or ""), computed):
                    conn.commit()
                    emit(
                        "proactive.ask.parameter_mismatch",
                        status="skipped",
                        attributes={"approval_id": approval_id[:36], "binding_ok": False},
                    )
                    return {"ok": False, "error": "parameter_mismatch", "http": 409}
                st = str(row.get("status") or "")
                if kind == "approve":
                    if st == "APPROVED":
                        cur.execute(
                            """
                            SELECT event_id FROM world_approval_events
                            WHERE approval_id = %s AND to_status = 'APPROVED'
                            ORDER BY created_at ASC LIMIT 1
                            """,
                            (approval_id,),
                        )
                        ev = cur.fetchone()
                        conn.commit()
                        emit(
                            "proactive.ask.approved",
                            attributes={"approval_id": approval_id[:36], "binding_ok": True, "reason": "replay"},
                        )
                        return {"ok": True, "replay": True, "event_id": ev[0] if ev else "", "status": "APPROVED"}
                    if st != "PENDING":
                        conn.commit()
                        return {"ok": False, "error": "conflict", "status": st, "http": 409}
                    eid = str(uuid.uuid4())[:64]
                    cur.execute(
                        """
                        UPDATE world_approval_requests
                        SET status = 'APPROVED', decided_at = NOW(), decision_session_id = %s
                        WHERE approval_id = %s AND owner_id = %s AND status = 'PENDING'
                        """,
                        (session_id_hash[:64], approval_id, owner_id),
                    )
                    if not cur.rowcount:
                        conn.commit()
                        return {"ok": False, "error": "conflict", "http": 409}
                    cur.execute(
                        """
                        INSERT INTO world_approval_events
                        (event_id, approval_id, from_status, to_status, reason)
                        VALUES (%s,%s,'PENDING','APPROVED','approve')
                        """,
                        (eid, approval_id),
                    )
                    cur.execute(
                        """
                        UPDATE world_actions
                        SET status = 'APPROVED_NOT_RUN', approval_id = %s, updated_at = NOW()
                        WHERE owner_id = %s AND preparation_id = %s
                          AND status IN ('READY','APPROVAL_REQUIRED')
                          AND action_hash = %s AND %s <> ''
                        """,
                        (
                            approval_id, owner_id, str(row.get("preparation_id") or ""),
                            str(row.get("action_hash") or ""),
                            str(row.get("action_hash") or ""),
                        ),
                    )
                    conn.commit()
                    emit(
                        "proactive.ask.approved",
                        attributes={
                            "approval_id": approval_id[:36],
                            "action_type": str(row.get("action_type") or "")[:40],
                            "binding_ok": True,
                        },
                    )
                    return {"ok": True, "replay": False, "event_id": eid, "status": "APPROVED"}
                if kind == "reject":
                    if st != "PENDING":
                        conn.commit()
                        return {"ok": False, "error": "conflict", "status": st, "http": 409}
                    eid = str(uuid.uuid4())[:64]
                    cur.execute(
                        """
                        UPDATE world_approval_requests
                        SET status = 'REJECTED', decided_at = NOW(), decision_session_id = %s
                        WHERE approval_id = %s AND owner_id = %s AND status = 'PENDING'
                        """,
                        (session_id_hash[:64], approval_id, owner_id),
                    )
                    if not cur.rowcount:
                        conn.commit()
                        return {"ok": False, "error": "conflict", "http": 409}
                    cur.execute(
                        """
                        INSERT INTO world_approval_events
                        (event_id, approval_id, from_status, to_status, reason)
                        VALUES (%s,%s,'PENDING','REJECTED','reject')
                        """,
                        (eid, approval_id),
                    )
                    conn.commit()
                    emit(
                        "proactive.ask.rejected",
                        attributes={"approval_id": approval_id[:36], "binding_ok": True},
                    )
                    return {"ok": True, "event_id": eid, "status": "REJECTED"}
                if kind == "revoke":
                    if st != "APPROVED":
                        conn.commit()
                        return {"ok": False, "error": "conflict", "status": st, "http": 409}
                    eid = str(uuid.uuid4())[:64]
                    cur.execute(
                        """
                        UPDATE world_approval_requests
                        SET status = 'REVOKED', decided_at = NOW(), decision_session_id = %s
                        WHERE approval_id = %s AND owner_id = %s AND status = 'APPROVED'
                        """,
                        (session_id_hash[:64], approval_id, owner_id),
                    )
                    if not cur.rowcount:
                        conn.commit()
                        return {"ok": False, "error": "conflict", "http": 409}
                    cur.execute(
                        """
                        INSERT INTO world_approval_events
                        (event_id, approval_id, from_status, to_status, reason)
                        VALUES (%s,%s,'APPROVED','REVOKED','revoke')
                        """,
                        (eid, approval_id),
                    )
                    cur.execute(
                        """
                        UPDATE world_actions
                        SET status = 'REVOKED', updated_at = NOW()
                        WHERE approval_id = %s AND owner_id = %s
                          AND status IN ('READY','APPROVAL_REQUIRED','APPROVED_NOT_RUN','RUN_REQUESTED')
                        """,
                        (approval_id, owner_id),
                    )
                    conn.commit()
                    emit(
                        "proactive.ask.revoked",
                        attributes={"approval_id": approval_id[:36], "binding_ok": True},
                    )
                    return {"ok": True, "event_id": eid, "status": "REVOKED"}
                conn.commit()
                emit("proactive.ask.policy_rejected", status="skipped", attributes={"reason": "unknown_kind"})
                return {"ok": False, "error": "conflict", "http": 409}
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return {"ok": False, "error": "conflict", "http": 409}
        finally:
            self._release(conn)

    def persist_prepare_delivery(self, preparation_id: str, owner_id: str) -> tuple:
        did = str(uuid.uuid4())
        conn = self._conn()
        if not conn:
            return "", False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_prepare_deliveries
                    (delivery_id, preparation_id, channel, status, attempts)
                    VALUES (%s,%s,'hud','DELIVERED',1)
                    ON CONFLICT (preparation_id, channel) DO NOTHING
                    RETURNING delivery_id
                    """,
                    (did, preparation_id),
                )
                row = cur.fetchone()
                created = bool(row)
                if not row:
                    cur.execute(
                        """
                        SELECT delivery_id FROM world_prepare_deliveries
                        WHERE preparation_id = %s AND channel = 'hud'
                        """,
                        (preparation_id,),
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

    def persist_ask_delivery(self, approval_id: str, owner_id: str) -> tuple:
        did = str(uuid.uuid4())
        conn = self._conn()
        if not conn:
            return "", False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_ask_deliveries
                    (delivery_id, approval_id, channel, status, attempts)
                    VALUES (%s,%s,'hud','DELIVERED',1)
                    ON CONFLICT (approval_id, channel) DO NOTHING
                    RETURNING delivery_id
                    """,
                    (did, approval_id),
                )
                row = cur.fetchone()
                created = bool(row)
                if not row:
                    cur.execute(
                        """
                        SELECT delivery_id FROM world_ask_deliveries
                        WHERE approval_id = %s AND channel = 'hud'
                        """,
                        (approval_id,),
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

    def bump_prepare_attention(self, owner_id: str, dedupe_key: str, day_key: str) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_attention
                    (owner_id, day_key, inform_count, suggest_count, prepare_count, ask_count, cooldowns)
                    VALUES (%s, %s::date, 0, 0, 1, 0, jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW())))
                    ON CONFLICT (owner_id, day_key) DO UPDATE SET
                        prepare_count = COALESCE(proactive_attention.prepare_count, 0) + 1,
                        cooldowns = COALESCE(proactive_attention.cooldowns, '{}'::jsonb)
                            || jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW()))
                    RETURNING prepare_count
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

    def bump_ask_attention(self, owner_id: str, dedupe_key: str, day_key: str) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_attention
                    (owner_id, day_key, inform_count, suggest_count, prepare_count, ask_count, cooldowns)
                    VALUES (%s, %s::date, 0, 0, 0, 1, jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW())))
                    ON CONFLICT (owner_id, day_key) DO UPDATE SET
                        ask_count = COALESCE(proactive_attention.ask_count, 0) + 1,
                        cooldowns = COALESCE(proactive_attention.cooldowns, '{}'::jsonb)
                            || jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW()))
                    RETURNING ask_count
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

    def bump_suggest_attention(self, owner_id: str, dedupe_key: str, day_key: str) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proactive_attention
                    (owner_id, day_key, inform_count, suggest_count, cooldowns)
                    VALUES (%s, %s::date, 0, 1, jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW())))
                    ON CONFLICT (owner_id, day_key) DO UPDATE SET
                        suggest_count = COALESCE(proactive_attention.suggest_count, 0) + 1,
                        cooldowns = COALESCE(proactive_attention.cooldowns, '{}'::jsonb)
                            || jsonb_build_object(%s, EXTRACT(EPOCH FROM NOW()))
                    RETURNING suggest_count
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

    def _draft_row(self, r) -> Dict[str, Any]:
        outp = r[4]
        if isinstance(outp, str):
            try:
                outp = json.loads(outp)
            except Exception:
                outp = {}
        if not isinstance(outp, dict):
            outp = {}
        return {
            "draft_id": r[0],
            "owner_id": r[1],
            "preparation_id": r[2],
            "draft_type": r[3],
            "structured_output": outp,
            "provider": r[5],
            "model": r[6],
            "prompt_version": r[7],
            "rule_version": r[8],
            "param_hash_at_generation": r[9],
            "validation_status": r[10],
            "reject_reason": r[11],
            "privacy_class": r[12],
            "fingerprint": r[13],
            "title": (outp or {}).get("title") or "",
            "summary": (outp or {}).get("summary") or "",
            "body": (outp or {}).get("body") or "",
        }

    def list_draft_candidates(self, owner_id: str = OWNER_ID, limit: int = 5) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.preparation_id, p.owner_id, p.preparation_type, p.action_type,
                           p.template_id, p.safe_params, p.param_hash, p.status,
                           p.privacy_class, p.risk_class, p.rule_version, p.provenance,
                           pr.provenance
                    FROM world_preparations p
                    LEFT JOIN world_predictions pr ON p.prediction_id = pr.prediction_id
                    WHERE p.owner_id = %s
                      AND p.status IN ('READY', 'ASKED')
                      AND p.valid_until > NOW()
                      AND p.privacy_class <> 'SENSITIVE'
                      AND NOT EXISTS (
                        SELECT 1 FROM world_preparation_drafts d
                        WHERE d.preparation_id = p.preparation_id
                          AND d.validation_status = 'ACCEPTED'
                          AND d.param_hash_at_generation = p.param_hash
                      )
                    ORDER BY p.evaluated_at DESC
                    LIMIT %s
                    """,
                    (owner_id, int(limit)),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                params = r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else {})
                prov = r[11] if isinstance(r[11], dict) else (json.loads(r[11]) if r[11] else {})
                pprov = r[12] if isinstance(r[12], dict) else (json.loads(r[12]) if r[12] else {})
                eids = []
                if isinstance(prov, dict):
                    eids = list(prov.get("evidence_ids") or [])
                if not eids and isinstance(pprov, dict):
                    eids = list(pprov.get("evidence_ids") or [])
                out.append({
                    "preparation_id": r[0],
                    "owner_id": r[1],
                    "preparation_type": r[2],
                    "action_type": r[3],
                    "template_id": r[4],
                    "safe_params": params or {},
                    "param_hash": r[6],
                    "status": r[7],
                    "privacy_class": r[8],
                    "risk_class": r[9],
                    "rule_version": r[10],
                    "provenance": prov or {},
                    "evidence_ids": eids,
                })
            return out
        except Exception:
            return []
        finally:
            self._release(conn)

    def supersede_stale_drafts(self, owner_id: str = OWNER_ID) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE world_preparation_drafts d
                    SET validation_status = 'SUPERSEDED', updated_at = NOW()
                    FROM world_preparations p
                    WHERE d.preparation_id = p.preparation_id
                      AND d.owner_id = %s
                      AND d.validation_status = 'ACCEPTED'
                      AND d.param_hash_at_generation <> p.param_hash
                    """,
                    (owner_id,),
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

    def insert_world_draft(self, row: Dict[str, Any]) -> str:
        if str(row.get("privacy_class") or "") == "SENSITIVE":
            return ""
        conn = self._conn()
        if not conn:
            return ""
        did = str(row.get("draft_id") or uuid.uuid4())[:64]
        owner = str(row.get("owner_id") or OWNER_ID)[:64]
        pid = str(row.get("preparation_id") or "")[:64]
        status = str(row.get("validation_status") or "REJECTED")[:16]
        payload = row.get("structured_output") if isinstance(row.get("structured_output"), dict) else {}
        if status != "ACCEPTED":
            payload = {}
        try:
            with conn.cursor() as cur:
                if status == "ACCEPTED":
                    cur.execute(
                        """
                        UPDATE world_preparation_drafts
                        SET validation_status = 'SUPERSEDED', updated_at = NOW()
                        WHERE preparation_id = %s AND owner_id = %s
                          AND validation_status = 'ACCEPTED'
                        """,
                        (pid, owner),
                    )
                cur.execute(
                    """
                    INSERT INTO world_preparation_drafts (
                        draft_id, owner_id, preparation_id, draft_type, structured_output,
                        provider, model, model_version, prompt_version, rule_version,
                        param_hash_at_generation, validation_status, reject_reason,
                        privacy_class, fingerprint, correlation_id
                    ) VALUES (
                        %s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    ON CONFLICT (owner_id, fingerprint) DO UPDATE SET
                        updated_at = world_preparation_drafts.updated_at
                    RETURNING draft_id
                    """,
                    (
                        did, owner, pid,
                        str(row.get("draft_type") or "")[:64],
                        json.dumps(payload),
                        str(row.get("provider") or "")[:32],
                        str(row.get("model") or "")[:80],
                        str(row.get("model_version") or "")[:40],
                        str(row.get("prompt_version") or "v628.1")[:16],
                        str(row.get("rule_version") or "v626.1")[:16],
                        str(row.get("param_hash_at_generation") or "")[:64],
                        status,
                        str(row.get("reject_reason") or "")[:40],
                        str(row.get("privacy_class") or "NORMAL")[:16],
                        str(row.get("fingerprint") or "")[:64],
                        str(row.get("correlation_id") or "")[:64],
                    ),
                )
                got = cur.fetchone()
            conn.commit()
            return str(got[0]) if got else ""
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def get_current_draft(self, preparation_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT d.draft_id, d.owner_id, d.preparation_id, d.draft_type,
                           d.structured_output, d.provider, d.model, d.prompt_version,
                           d.rule_version, d.param_hash_at_generation, d.validation_status,
                           d.reject_reason, d.privacy_class, d.fingerprint
                    FROM world_preparation_drafts d
                    JOIN world_preparations p ON p.preparation_id = d.preparation_id
                    WHERE d.preparation_id = %s AND d.owner_id = %s
                      AND d.validation_status = 'ACCEPTED'
                      AND d.param_hash_at_generation = p.param_hash
                    LIMIT 1
                    """,
                    (str(preparation_id)[:64], str(owner_id)[:64]),
                )
                r = cur.fetchone()
            return self._draft_row(r) if r else None
        except Exception:
            return None
        finally:
            self._release(conn)

    def persist_draft_delivery(self, draft_id: str, owner_id: str) -> tuple:
        did = str(uuid.uuid4())
        conn = self._conn()
        if not conn:
            return "", False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_draft_deliveries
                    (delivery_id, draft_id, channel, status, attempts)
                    VALUES (%s,%s,'hud','DELIVERED',1)
                    ON CONFLICT (draft_id, channel) DO NOTHING
                    RETURNING delivery_id
                    """,
                    (did, str(draft_id)[:64]),
                )
                row = cur.fetchone()
                created = bool(row)
                if not row:
                    cur.execute(
                        """
                        SELECT delivery_id FROM world_draft_deliveries
                        WHERE draft_id = %s AND channel = 'hud'
                        """,
                        (str(draft_id)[:64],),
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

    def _action_row(self, r) -> Dict[str, Any]:
        tref = r[6]
        epar = r[7]
        if isinstance(tref, str):
            try:
                tref = json.loads(tref)
            except Exception:
                tref = {}
        if isinstance(epar, str):
            try:
                epar = json.loads(epar)
            except Exception:
                epar = {}
        return {
            "action_id": r[0], "owner_id": r[1], "preparation_id": r[2], "approval_id": r[3] or "",
            "capability_id": r[4], "action_type": r[5],
            "target_ref": tref if isinstance(tref, dict) else {},
            "exec_params": epar if isinstance(epar, dict) else {},
            "param_hash": r[8], "action_hash": r[9], "risk_class": r[10], "privacy_class": r[11],
            "reversibility": r[12], "idempotency_key": r[13], "policy_version": r[14],
            "status": r[15],
            "valid_until": float(r[16]) if r[16] is not None else None,
            "attempt_n": int(r[17] or 0),
            "lease_owner": r[18] or "",
        }

    def insert_world_action(self, row: Dict[str, Any]) -> str:
        if str(row.get("privacy_class") or "") == "SENSITIVE":
            return ""
        conn = self._conn()
        if not conn:
            return ""
        aid = str(row.get("action_id") or uuid.uuid4())[:64]
        owner = str(row.get("owner_id") or OWNER_ID)[:64]
        vu = float(row.get("valid_until") or time.time() + 3600)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_actions (
                        action_id, owner_id, preparation_id, approval_id, capability_id, action_type,
                        target_ref, exec_params, param_hash, action_hash, risk_class, privacy_class,
                        reversibility, idempotency_key, policy_version, status, valid_until, provenance
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,'READY',
                        to_timestamp(%s), %s::jsonb
                    )
                    ON CONFLICT (owner_id, action_hash) DO NOTHING
                    RETURNING action_id
                    """,
                    (
                        aid, owner, str(row.get("preparation_id") or "")[:64],
                        (str(row.get("approval_id"))[:64] if row.get("approval_id") else None),
                        str(row.get("capability_id") or "")[:40],
                        str(row.get("action_type") or "")[:40],
                        json.dumps(row.get("target_ref") or {}),
                        json.dumps(row.get("exec_params") or {}),
                        str(row.get("param_hash") or "")[:64],
                        str(row.get("action_hash") or "")[:64],
                        str(row.get("risk_class") or "LOW")[:16],
                        str(row.get("privacy_class") or "NORMAL")[:16],
                        str(row.get("reversibility") or "REVERSIBLE")[:32],
                        str(row.get("idempotency_key") or "")[:64],
                        str(row.get("policy_version") or "v63.1")[:16],
                        vu,
                        json.dumps(row.get("provenance") or {}),
                    ),
                )
                got = cur.fetchone()
                if not got:
                    cur.execute(
                        """
                        SELECT action_id FROM world_actions
                        WHERE owner_id = %s AND action_hash = %s
                        """,
                        (owner, str(row.get("action_hash") or "")[:64]),
                    )
                    r2 = cur.fetchone()
                    conn.commit()
                    return str(r2[0]) if r2 else ""
                out = str(got[0])
                cur.execute(
                    """
                    INSERT INTO world_action_events (event_id, action_id, from_status, to_status, reason)
                    VALUES (%s,%s,NULL,'READY','created')
                    """,
                    (str(uuid.uuid4())[:64], out),
                )
                cur.execute(
                    """
                    INSERT INTO world_action_idempotency
                    (owner_id, idempotency_key, action_id, state)
                    VALUES (%s,%s,%s,'OPEN')
                    ON CONFLICT (owner_id, idempotency_key) DO NOTHING
                    """,
                    (owner, str(row.get("idempotency_key") or "")[:64], out),
                )
            conn.commit()
            return out
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ""
        finally:
            self._release(conn)

    def get_action(self, action_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT action_id, owner_id, preparation_id, approval_id, capability_id, action_type,
                           target_ref, exec_params, param_hash, action_hash, risk_class, privacy_class,
                           reversibility, idempotency_key, policy_version, status,
                           EXTRACT(EPOCH FROM valid_until), attempt_n, lease_owner
                    FROM world_actions
                    WHERE action_id = %s AND owner_id = %s
                    """,
                    (str(action_id)[:64], str(owner_id)[:64]),
                )
                r = cur.fetchone()
            return self._action_row(r) if r else None
        except Exception:
            return None
        finally:
            self._release(conn)

    def get_action_by_preparation(self, preparation_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT action_id, owner_id, preparation_id, approval_id, capability_id, action_type,
                           target_ref, exec_params, param_hash, action_hash, risk_class, privacy_class,
                           reversibility, idempotency_key, policy_version, status,
                           EXTRACT(EPOCH FROM valid_until), attempt_n, lease_owner
                    FROM world_actions
                    WHERE preparation_id = %s AND owner_id = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (str(preparation_id)[:64], str(owner_id)[:64]),
                )
                r = cur.fetchone()
            return self._action_row(r) if r else None
        except Exception:
            return None
        finally:
            self._release(conn)

    def bind_action_approval(self, action_id: str, approval_id: str, owner_id: str) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE world_actions
                    SET approval_id = %s, status = 'APPROVAL_REQUIRED', updated_at = NOW()
                    WHERE action_id = %s AND owner_id = %s AND status IN ('READY','CREATED')
                    """,
                    (str(approval_id)[:64], str(action_id)[:64], str(owner_id)[:64]),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)

    def enqueue_run(self, action_id: str, owner_id: str) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE world_actions
                    SET status = 'RUN_REQUESTED', updated_at = NOW()
                    WHERE action_id = %s AND owner_id = %s AND status = 'APPROVED_NOT_RUN'
                    """,
                    (str(action_id)[:64], str(owner_id)[:64]),
                )
                n = cur.rowcount
                if n:
                    cur.execute(
                        """
                        INSERT INTO world_action_events (event_id, action_id, from_status, to_status, reason)
                        VALUES (%s,%s,'APPROVED_NOT_RUN','RUN_REQUESTED','run_requested')
                        """,
                        (str(uuid.uuid4())[:64], str(action_id)[:64]),
                    )
            conn.commit()
            return bool(n)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def claim_run(self, owner_id: str, worker_id: str) -> Optional[Dict[str, Any]]:
        from proactive.config import ACT_LEASE_SECONDS
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT action_id FROM world_actions
                    WHERE owner_id = %s AND status = 'RUN_REQUESTED'
                      AND (lease_until IS NULL OR lease_until < NOW())
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (owner_id,),
                )
                r = cur.fetchone()
                if not r:
                    conn.commit()
                    return None
                cur.execute(
                    """
                    UPDATE world_actions
                    SET status = 'PRECONDITION_CHECK', lease_owner = %s,
                        lease_until = NOW() + (%s || ' seconds')::interval, updated_at = NOW()
                    WHERE action_id = %s AND status = 'RUN_REQUESTED'
                    RETURNING action_id, owner_id, preparation_id, approval_id, capability_id, action_type,
                           target_ref, exec_params, param_hash, action_hash, risk_class, privacy_class,
                           reversibility, idempotency_key, policy_version, status,
                           EXTRACT(EPOCH FROM valid_until), attempt_n, lease_owner
                    """,
                    (str(worker_id)[:64], str(int(ACT_LEASE_SECONDS)), r[0]),
                )
                got = cur.fetchone()
            conn.commit()
            return self._action_row(got) if got else None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            self._release(conn)

    def begin_attempt(self, action_id: str, owner_id: str, worker_id: str) -> Optional[int]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE world_actions
                    SET attempt_n = attempt_n + 1, status = 'EXECUTING', updated_at = NOW()
                    WHERE action_id = %s AND owner_id = %s AND lease_owner = %s
                      AND status = 'PRECONDITION_CHECK'
                    RETURNING attempt_n
                    """,
                    (str(action_id)[:64], str(owner_id)[:64], str(worker_id)[:64]),
                )
                r = cur.fetchone()
                if not r:
                    conn.commit()
                    return None
                n = int(r[0])
                cur.execute(
                    """
                    INSERT INTO world_action_attempts
                    (attempt_id, action_id, attempt_n, state)
                    VALUES (%s,%s,%s,'EXECUTING')
                    """,
                    (str(uuid.uuid4())[:64], str(action_id)[:64], n),
                )
                cur.execute(
                    """
                    UPDATE world_action_idempotency SET state = 'CLAIMED', updated_at = NOW()
                    WHERE action_id = %s AND owner_id = %s
                    """,
                    (str(action_id)[:64], str(owner_id)[:64]),
                )
            conn.commit()
            return n
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            self._release(conn)

    def finalize_action(
        self,
        action_id: str,
        owner_id: str,
        status: str,
        reason: str,
        worker_id: str,
        attempt_n: int,
        receipt_ref: str = "",
    ) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                if worker_id:
                    cur.execute(
                        """
                        UPDATE world_actions
                        SET status = %s, lease_owner = '', lease_until = NULL, updated_at = NOW()
                        WHERE action_id = %s AND owner_id = %s
                          AND (lease_owner = %s OR lease_owner = '' OR status = 'PRECONDITION_CHECK')
                          AND attempt_n = %s
                        """,
                        (str(status)[:24], str(action_id)[:64], str(owner_id)[:64], str(worker_id)[:64], int(attempt_n)),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE world_actions
                        SET status = %s, lease_owner = '', lease_until = NULL, updated_at = NOW()
                        WHERE action_id = %s AND owner_id = %s
                          AND status NOT IN ('COMPLETED','EXECUTING','VERIFYING')
                        """,
                        (str(status)[:24], str(action_id)[:64], str(owner_id)[:64]),
                    )
                n = cur.rowcount
                cur.execute(
                    """
                    INSERT INTO world_action_events (event_id, action_id, from_status, to_status, reason)
                    VALUES (%s,%s,NULL,%s,%s)
                    """,
                    (str(uuid.uuid4())[:64], str(action_id)[:64], str(status)[:24], str(reason or "")[:40]),
                )
                ide = "COMPLETED" if status == "COMPLETED" else (
                    "UNKNOWN" if status == "UNKNOWN_OUTCOME" else "OPEN"
                )
                if status == "FAILED" and reason in ("flag_off", "capability_disabled", "writer", "payload", "exec_params"):
                    ide = "OPEN"
                cur.execute(
                    """
                    UPDATE world_action_idempotency
                    SET state = %s, receipt_ref = %s, updated_at = NOW()
                    WHERE action_id = %s AND owner_id = %s
                    """,
                    (ide, str(receipt_ref or "")[:80], str(action_id)[:64], str(owner_id)[:64]),
                )
            conn.commit()
            return bool(n)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def append_action_event(self, action_id: str, frm: str, to: str, reason: str) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_action_events (event_id, action_id, from_status, to_status, reason)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (str(uuid.uuid4())[:64], str(action_id)[:64], str(frm or "")[:24] or None, str(to)[:24], str(reason)[:40]),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._release(conn)

    def insert_verification(self, action_id: str, attempt_n: int, method: str, verdict: str, observed: str) -> None:
        conn = self._conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT attempt_id FROM world_action_attempts WHERE action_id = %s AND attempt_n = %s",
                    (str(action_id)[:64], int(attempt_n)),
                )
                ar = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO world_action_verifications
                    (verification_id, action_id, attempt_id, method, verdict, observed_ref)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(uuid.uuid4())[:64], str(action_id)[:64],
                        ar[0] if ar else None, str(method)[:32], str(verdict)[:24], str(observed or "")[:80],
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

    def insert_action_receipt(self, row: Dict[str, Any]) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO world_action_receipts
                    (receipt_id, action_id, owner_id, content_hash, note_template_id, claim_code)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(row.get("receipt_id") or uuid.uuid4())[:64],
                        str(row.get("action_id") or "")[:64],
                        str(row.get("owner_id") or OWNER_ID)[:64],
                        str(row.get("content_hash") or "")[:64],
                        str(row.get("note_template_id") or "")[:40],
                        str(row.get("claim_code") or "")[:40],
                    ),
                )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def get_action_receipt(self, action_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT receipt_id, content_hash, note_template_id, claim_code
                    FROM world_action_receipts
                    WHERE action_id = %s AND owner_id = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (str(action_id)[:64], str(owner_id)[:64]),
                )
                r = cur.fetchone()
            if not r:
                return None
            return {"receipt_id": r[0], "content_hash": r[1], "note_template_id": r[2], "claim_code": r[3]}
        except Exception:
            return None
        finally:
            self._release(conn)

    def get_idempotency(self, owner_id: str, key: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT action_id, state, receipt_ref FROM world_action_idempotency
                    WHERE owner_id = %s AND idempotency_key = %s
                    """,
                    (str(owner_id)[:64], str(key)[:64]),
                )
                r = cur.fetchone()
            if not r:
                return None
            return {"action_id": r[0], "state": r[1], "receipt_ref": r[2]}
        except Exception:
            return None
        finally:
            self._release(conn)

    def count_actions_today(self, owner_id: str, capability_id: str) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM world_actions
                    WHERE owner_id = %s AND capability_id = %s AND status = 'COMPLETED'
                      AND created_at >= date_trunc('day', NOW())
                    """,
                    (str(owner_id)[:64], str(capability_id)[:40]),
                )
                r = cur.fetchone()
            return int(r[0] or 0) if r else 0
        except Exception:
            return 0
        finally:
            self._release(conn)

    def recover_act_leases(self, owner_id: str) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE world_actions
                    SET status = 'UNKNOWN_OUTCOME', lease_owner = '', lease_until = NULL, updated_at = NOW()
                    WHERE owner_id = %s AND status IN ('EXECUTING','VERIFYING')
                      AND lease_until IS NOT NULL AND lease_until < NOW()
                    """,
                    (str(owner_id)[:64],),
                )
                n = cur.rowcount
            conn.commit()
            return int(n or 0)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            self._release(conn)

    def _computer_session_row(self, r) -> Dict[str, Any]:
        row = {
            "session_id": r[0],
            "owner_id": r[1],
            "status": r[2],
            "privacy_class": r[3],
            "emergency_stop": bool(r[4]),
            "created_at": r[5],
            "updated_at": r[6],
            "expires_at": r[7],
            "bound_hwnd": 0,
            "bound_pid": 0,
            "bound_exe_path_norm": "",
            "bound_window_class": "",
            "bound_title_advisory": "",
        }
        if r is not None and len(r) > 8:
            row["bound_hwnd"] = int(r[8] or 0)
            row["bound_pid"] = int(r[9] or 0)
            row["bound_exe_path_norm"] = str(r[10] or "")
            row["bound_window_class"] = str(r[11] or "")
            row["bound_title_advisory"] = str(r[12] or "")
        return row

    def expire_computer_sessions(self, owner_id: str) -> int:
        conn = self._conn()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE computer_sessions
                    SET status = 'EXPIRED', updated_at = NOW()
                    WHERE owner_id = %s AND status = 'OBSERVING' AND expires_at <= NOW()
                    """,
                    (str(owner_id)[:64],),
                )
                n = cur.rowcount
            conn.commit()
            return int(n or 0)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            self._release(conn)

    def insert_computer_session(
        self,
        session_id: str,
        owner_id: str,
        privacy_class: str,
        ttl_sec: int,
    ) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        sid = str(session_id)[:64]
        owner = str(owner_id)[:64]
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE computer_sessions
                    SET status = 'EXPIRED', updated_at = NOW()
                    WHERE owner_id = %s AND status = 'OBSERVING' AND expires_at <= NOW()
                    """,
                    (owner,),
                )
                cur.execute(
                    """
                    INSERT INTO computer_sessions (
                        session_id, owner_id, status, privacy_class, emergency_stop, expires_at
                    ) VALUES (%s, %s, 'OBSERVING', %s, FALSE, NOW() + (%s || ' seconds')::interval)
                    RETURNING session_id, owner_id, status, privacy_class, emergency_stop,
                              EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM updated_at),
                              EXTRACT(EPOCH FROM expires_at),
                              COALESCE(bound_hwnd, 0), COALESCE(bound_pid, 0),
                              COALESCE(bound_exe_path_norm, ''), COALESCE(bound_window_class, ''),
                              COALESCE(bound_title_advisory, '')
                    """,
                    (sid, owner, str(privacy_class)[:16], str(int(ttl_sec))),
                )
                r = cur.fetchone()
            conn.commit()
            return self._computer_session_row(r) if r else None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            self._release(conn)

    def get_observing_computer_session(self, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, owner_id, status, privacy_class, emergency_stop,
                           EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM updated_at),
                           EXTRACT(EPOCH FROM expires_at),
                           COALESCE(bound_hwnd, 0), COALESCE(bound_pid, 0),
                           COALESCE(bound_exe_path_norm, ''), COALESCE(bound_window_class, ''),
                           COALESCE(bound_title_advisory, '')
                    FROM computer_sessions
                    WHERE owner_id = %s AND status = 'OBSERVING'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (str(owner_id)[:64],),
                )
                r = cur.fetchone()
            return self._computer_session_row(r) if r else None
        except Exception:
            return None
        finally:
            self._release(conn)

    def get_computer_session(self, session_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, owner_id, status, privacy_class, emergency_stop,
                           EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM updated_at),
                           EXTRACT(EPOCH FROM expires_at),
                           COALESCE(bound_hwnd, 0), COALESCE(bound_pid, 0),
                           COALESCE(bound_exe_path_norm, ''), COALESCE(bound_window_class, ''),
                           COALESCE(bound_title_advisory, '')
                    FROM computer_sessions
                    WHERE session_id = %s AND owner_id = %s
                    LIMIT 1
                    """,
                    (str(session_id)[:64], str(owner_id)[:64]),
                )
                r = cur.fetchone()
            return self._computer_session_row(r) if r else None
        except Exception:
            return None
        finally:
            self._release(conn)

    def list_computer_sessions(self, owner_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return []
        cap = min(max(int(limit or 20), 1), 50)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, owner_id, status, privacy_class, emergency_stop,
                           EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM updated_at),
                           EXTRACT(EPOCH FROM expires_at),
                           COALESCE(bound_hwnd, 0), COALESCE(bound_pid, 0),
                           COALESCE(bound_exe_path_norm, ''), COALESCE(bound_window_class, ''),
                           COALESCE(bound_title_advisory, '')
                    FROM computer_sessions
                    WHERE owner_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(owner_id)[:64], cap),
                )
                rows = cur.fetchall() or []
            return [self._computer_session_row(r) for r in rows]
        except Exception:
            return []
        finally:
            self._release(conn)

    def stop_computer_session(self, session_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        sid = str(session_id)[:64]
        owner = str(owner_id)[:64]
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE computer_sessions
                    SET status = 'STOPPED', emergency_stop = TRUE, updated_at = NOW()
                    WHERE session_id = %s AND owner_id = %s
                      AND status IN ('CREATED','OBSERVING','PAUSED')
                    RETURNING session_id, owner_id, status, privacy_class, emergency_stop,
                              EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM updated_at),
                              EXTRACT(EPOCH FROM expires_at),
                              COALESCE(bound_hwnd, 0), COALESCE(bound_pid, 0),
                              COALESCE(bound_exe_path_norm, ''), COALESCE(bound_window_class, ''),
                              COALESCE(bound_title_advisory, '')
                    """,
                    (sid, owner),
                )
                r = cur.fetchone()
                if r:
                    cur.execute(
                        """
                        INSERT INTO computer_observation_events (
                            event_id, session_id, owner_id, kind, from_status, to_status, reason
                        ) VALUES (%s, %s, %s, 'stopped', 'OBSERVING', 'STOPPED', 'stop')
                        """,
                        (str(uuid.uuid4())[:64], sid, owner),
                    )
            conn.commit()
            return self._computer_session_row(r) if r else None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            self._release(conn)

    def bind_computer_session(
        self,
        session_id: str,
        owner_id: str,
        *,
        hwnd: int,
        pid: int,
        exe_path_norm: str,
        window_class: str,
        title_advisory: str,
    ) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        sid = str(session_id)[:64]
        owner = str(owner_id)[:64]
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE computer_sessions
                    SET bound_hwnd = %s, bound_pid = %s, bound_exe_path_norm = %s,
                        bound_window_class = %s, bound_title_advisory = %s, updated_at = NOW()
                    WHERE session_id = %s AND owner_id = %s AND status = 'OBSERVING'
                      AND bound_hwnd IS NULL
                    RETURNING session_id, owner_id, status, privacy_class, emergency_stop,
                              EXTRACT(EPOCH FROM created_at), EXTRACT(EPOCH FROM updated_at),
                              EXTRACT(EPOCH FROM expires_at),
                              COALESCE(bound_hwnd, 0), COALESCE(bound_pid, 0),
                              COALESCE(bound_exe_path_norm, ''), COALESCE(bound_window_class, ''),
                              COALESCE(bound_title_advisory, '')
                    """,
                    (
                        int(hwnd),
                        int(pid),
                        str(exe_path_norm or "")[:512],
                        str(window_class or "")[:64],
                        str(title_advisory or "")[:80],
                        sid,
                        owner,
                    ),
                )
                r = cur.fetchone()
            conn.commit()
            return self._computer_session_row(r) if r else None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            self._release(conn)

    def insert_computer_observation(self, row: Dict[str, Any]) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO computer_observations (
                        observation_id, session_id, owner_id, observation_hash, capability_id,
                        authoritative_json, title_advisory, outcome_code, node_count, latency_ms
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s
                    )
                    """,
                    (
                        str(row.get("observation_id") or "")[:64],
                        str(row.get("session_id") or "")[:64],
                        str(row.get("owner_id") or "")[:64],
                        str(row.get("observation_hash") or "")[:64],
                        str(row.get("capability_id") or "")[:40],
                        json.dumps(row.get("authoritative_json") or {}),
                        str(row.get("title_advisory") or "")[:80],
                        str(row.get("outcome_code") or "")[:40],
                        int(row.get("node_count") or 0),
                        float(row.get("latency_ms") or 0),
                    ),
                )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def prune_computer_observations(self, session_id: str, keep: int) -> int:
        conn = self._conn()
        if not conn:
            return 0
        nkeep = max(int(keep or 20), 1)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM computer_observations
                    WHERE session_id = %s AND observation_id NOT IN (
                        SELECT observation_id FROM computer_observations
                        WHERE session_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    )
                    """,
                    (str(session_id)[:64], str(session_id)[:64], nkeep),
                )
                n = cur.rowcount
            conn.commit()
            return int(n or 0)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            self._release(conn)

    def get_latest_computer_observation(
        self, session_id: str, owner_id: str,
    ) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT observation_id, session_id, owner_id, observation_hash, capability_id,
                           authoritative_json, title_advisory, outcome_code, node_count, latency_ms,
                           EXTRACT(EPOCH FROM created_at)
                    FROM computer_observations
                    WHERE session_id = %s AND owner_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (str(session_id)[:64], str(owner_id)[:64]),
                )
                r = cur.fetchone()
            if not r:
                return None
            auth = r[5]
            if isinstance(auth, str):
                try:
                    auth = json.loads(auth)
                except Exception:
                    auth = {}
            return {
                "observation_id": r[0],
                "session_id": r[1],
                "owner_id": r[2],
                "observation_hash": r[3],
                "capability_id": r[4],
                "authoritative_json": auth if isinstance(auth, dict) else {},
                "title_advisory": r[6] or "",
                "outcome_code": r[7],
                "node_count": r[8],
                "latency_ms": r[9],
                "created_at": r[10],
                "data_only": True,
                "screenshot_present": False,
            }
        except Exception:
            return None
        finally:
            self._release(conn)

    def insert_computer_observation_event(
        self,
        session_id: str,
        owner_id: str,
        kind: str,
        reason: str = "",
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
    ) -> bool:
        conn = self._conn()
        if not conn:
            return False
        k = str(kind or "")[:16]
        if k not in ("created", "stopped", "dropped"):
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO computer_observation_events (
                        event_id, session_id, owner_id, kind, from_status, to_status, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4())[:64],
                        str(session_id)[:64],
                        str(owner_id)[:64],
                        k,
                        (str(from_status)[:24] if from_status else None),
                        (str(to_status)[:24] if to_status else None),
                        str(reason or "")[:40],
                    ),
                )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            self._release(conn)

    def count_memory_records(self, owner_id: str) -> int:
        conn = self._conn()
        if not conn:
            return -1
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM memory_records",
                )
                r = cur.fetchone()
            return int(r[0] or 0) if r else 0
        except Exception:
            return -1
        finally:
            self._release(conn)

    def count_world_actions(self, owner_id: str) -> int:
        conn = self._conn()
        if not conn:
            return -1
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM world_actions WHERE owner_id = %s",
                    (str(owner_id)[:64],),
                )
                r = cur.fetchone()
            return int(r[0] or 0) if r else 0
        except Exception:
            return -1
        finally:
            self._release(conn)


proactive_store = ProactiveStore()

