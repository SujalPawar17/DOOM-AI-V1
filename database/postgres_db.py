import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from contextlib import contextmanager

try:
    import psycopg2
    from psycopg2 import pool, extras
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


class DatabaseConnectionError(Exception):
    """Raised when unable to establish or checkout a database connection."""
    pass


class LockTimeoutError(Exception):
    """Raised when a database row-level lock timeout occurs."""
    pass


class DeadlockDetectedError(Exception):
    """Raised when a PostgreSQL deadlock (error code 40P01) is detected."""
    pass


class PostgresManager:
    """
    PostgreSQL Database Manager for DOOM V2
    Handles connection lifecycle, auto-database creation, table schema migrations,
    telemetry logging, and Memory 2.0 relational synchronization.
    """
    def __init__(self):
        self.user = os.getenv("DB_USER", "postgres")
        self.password = os.getenv("DB_PASSWORD", "Admin@123")
        self.host = os.getenv("DB_HOST", "localhost")
        self.port = int(os.getenv("DB_PORT", "5432"))
        self.dbname = os.getenv("DB_NAME", "Doom")
        self.sslmode = os.getenv("DB_SSLMODE", "disable")
        self._pool = None
        self._connected = False
        self._initialized = False

        if PSYCOPG2_AVAILABLE:
            self._init_db()

    def _get_connection_params(self, dbname: Optional[str] = None) -> Dict[str, Any]:
        return {
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "dbname": dbname or self.dbname,
            "sslmode": self.sslmode,
            "connect_timeout": 5
        }

    def _ensure_database_exists(self):
        """Connects to the default 'postgres' db and creates 'Doom' if it doesn't exist."""
        try:
            admin_conn = psycopg2.connect(**self._get_connection_params(dbname="postgres"))
            admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = admin_conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE LOWER(datname) = LOWER(%s)", (self.dbname,))
            exists = cur.fetchone()
            if not exists:
                print(f"[POSTGRES] Database '{self.dbname}' not found. Creating database now...")
                safe_db_name = self.dbname.replace('"', '""')
                cur.execute(f'CREATE DATABASE "{safe_db_name}"')
                print(f"[POSTGRES] [OK] Database '{self.dbname}' created successfully.")
            cur.close()
            admin_conn.close()
        except Exception as e:
            print(f"[POSTGRES NOTE] Check/Create database step: {e}")

    def _init_db(self):
        """Initializes connection pool and ensures schemas are present."""
        if not PSYCOPG2_AVAILABLE:
            return

        try:
            self._ensure_database_exists()

            # Create connection pool
            self._pool = pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                **self._get_connection_params()
            )
            self._connected = True
            self._create_tables()
            self._initialized = True
            print(f"[POSTGRES] [OK] Connected to PostgreSQL '{self.dbname}' on {self.host}:{self.port}")
        except Exception as e:
            self._connected = False
            print(f"[POSTGRES ERROR] Failed to connect to PostgreSQL: {e}")

    def _create_tables(self):
        """Initializes all DOOM relational tables if they do not exist."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                id VARCHAR(50) PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                role VARCHAR(100),
                title VARCHAR(50),
                preferences JSONB,
                projects JSONB,
                custom_notes JSONB,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id SERIAL PRIMARY KEY,
                episode_id VARCHAR(100) UNIQUE,
                goal TEXT NOT NULL,
                plan_steps JSONB,
                tools_called JSONB,
                outcome TEXT,
                success BOOLEAN DEFAULT TRUE,
                recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS semantic_facts (
                key VARCHAR(150) PRIMARY KEY,
                value JSONB NOT NULL,
                category VARCHAR(100) DEFAULT 'general',
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS system_telemetry (
                id SERIAL PRIMARY KEY,
                cpu_percent REAL,
                ram_percent REAL,
                disk_percent REAL,
                raw_metrics JSONB,
                recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS command_logs (
                id SERIAL PRIMARY KEY,
                user_command TEXT NOT NULL,
                response_text TEXT,
                tools_used JSONB,
                latency_ms REAL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            # V3.3: Task checkpoints for resume/recovery
            """
            CREATE TABLE IF NOT EXISTS task_checkpoints (
                task_id VARCHAR(100) PRIMARY KEY,
                goal TEXT NOT NULL,
                task_type VARCHAR(50),
                status VARCHAR(50),
                current_step TEXT,
                completed_steps JSONB,
                remaining_steps JSONB,
                failed_steps JSONB,
                blocked_steps JSONB,
                artifacts JSONB,
                tool_results JSONB,
                verification_results JSONB,
                models_used JSONB,
                retry_counts JSONB,
                termination_reason VARCHAR(100),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                final_response_status VARCHAR(50),
                resume_available BOOLEAN DEFAULT TRUE
            );
            """,
            # V5.1: Canonical memory records (Memory Foundation)
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                memory_id VARCHAR(100) PRIMARY KEY,
                memory_type VARCHAR(50) NOT NULL,
                content TEXT NOT NULL,
                source VARCHAR(50) NOT NULL DEFAULT 'DERIVED_CONTEXT',
                confidence VARCHAR(20) NOT NULL DEFAULT 'MEDIUM',
                importance REAL NOT NULL DEFAULT 0.5,
                status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
                project_id VARCHAR(100),
                task_id VARCHAR(100),
                entity_ids JSONB DEFAULT '[]',
                tags JSONB DEFAULT '[]',
                supersedes_memory_id VARCHAR(100),
                source_event_id VARCHAR(100),
                verification_status VARCHAR(30) DEFAULT 'UNVERIFIED',
                privacy_class VARCHAR(20) DEFAULT 'NORMAL',
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TIMESTAMP WITH TIME ZONE
            );
            """,
            # V5.1: Indexes for efficient memory retrieval
            "CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_records(memory_type);",
            "CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_records(status);",
            "CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_records(project_id);",
            "CREATE INDEX IF NOT EXISTS idx_memory_task ON memory_records(task_id);",
            "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_records(created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory_records(importance DESC);",
            "CREATE INDEX IF NOT EXISTS idx_memory_privacy ON memory_records(privacy_class);",
            # V5.3.1: Lifecycle audit events table
            """
            CREATE TABLE IF NOT EXISTS memory_lifecycle_events (
                event_id VARCHAR(100) PRIMARY KEY,
                memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                previous_status VARCHAR(30) NOT NULL,
                new_status VARCHAR(30) NOT NULL,
                transition_reason VARCHAR(255) NOT NULL,
                actor VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
                related_memory_id VARCHAR(100),
                source_event_id VARCHAR(100),
                task_id VARCHAR(100),
                correlation_id VARCHAR(100),
                confidence_before VARCHAR(20),
                confidence_after VARCHAR(20),
                importance_before REAL,
                importance_after REAL,
                metadata JSONB DEFAULT '{}',
                idempotency_key VARCHAR(100),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_mem_id ON memory_lifecycle_events(memory_id);",
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_created ON memory_lifecycle_events(created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_task ON memory_lifecycle_events(task_id);",
            "ALTER TABLE memory_lifecycle_events ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(100);",
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_idempotency ON memory_lifecycle_events(idempotency_key);",
            # V5.3.7.4: bounded operational telemetry (metadata only — not a content store)
            """
            CREATE TABLE IF NOT EXISTS operational_events (
                event_id VARCHAR(64) PRIMARY KEY,
                ts_unix_ms BIGINT NOT NULL,
                doom_request_id VARCHAR(64),
                task_id VARCHAR(100),
                cognitive_cycle_id VARCHAR(100),
                step_id VARCHAR(100),
                tool_execution_id VARCHAR(120),
                provider_call_id VARCHAR(120),
                category VARCHAR(32) NOT NULL,
                name VARCHAR(120) NOT NULL,
                status VARCHAR(32) NOT NULL,
                latency_ms REAL,
                component VARCHAR(80),
                operation VARCHAR(80),
                error_type VARCHAR(64),
                retryable BOOLEAN,
                attributes JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_opev_request ON operational_events(doom_request_id);",
            "CREATE INDEX IF NOT EXISTS idx_opev_task ON operational_events(task_id);",
            "CREATE INDEX IF NOT EXISTS idx_opev_ts ON operational_events(ts_unix_ms DESC);",
            "CREATE INDEX IF NOT EXISTS idx_opev_cat_name ON operational_events(category, name);",
            # V6.1: bounded proactive intelligence (not memory; INFORM-only)
            """
            CREATE TABLE IF NOT EXISTS proactive_signals (
                signal_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL DEFAULT 'sujal',
                signal_type VARCHAR(40) NOT NULL,
                source VARCHAR(40) NOT NULL,
                entity_type VARCHAR(40) NOT NULL DEFAULT '',
                entity_id VARCHAR(120) NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                privacy_class VARCHAR(16) NOT NULL DEFAULT 'NORMAL'
                    CHECK (privacy_class IN ('NORMAL', 'PRIVATE', 'SENSITIVE')),
                idempotency_key VARCHAR(64) NOT NULL UNIQUE,
                payload JSONB NOT NULL DEFAULT '{}',
                status VARCHAR(16) NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','CLAIMED','PROCESSED','DEAD','DROPPED','DEDUPED')),
                worker_id VARCHAR(64),
                lease_until TIMESTAMPTZ,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error_type VARCHAR(64),
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_psig_status_lease ON proactive_signals(status, lease_until);",
            "CREATE INDEX IF NOT EXISTS idx_psig_owner_ingested ON proactive_signals(owner_id, ingested_at DESC);",
            """
            CREATE TABLE IF NOT EXISTS proactive_insights (
                insight_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL DEFAULT 'sujal',
                insight_type VARCHAR(64) NOT NULL,
                source_signal_ids JSONB NOT NULL DEFAULT '[]',
                entity_type VARCHAR(40),
                entity_id VARCHAR(120),
                project_id VARCHAR(64),
                significance_score REAL NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                urgency VARCHAR(16) NOT NULL DEFAULT 'low',
                privacy_class VARCHAR(16) NOT NULL DEFAULT 'NORMAL'
                    CHECK (privacy_class IN ('NORMAL', 'PRIVATE', 'SENSITIVE')),
                recommended_intervention VARCHAR(16) NOT NULL
                    CHECK (recommended_intervention IN ('IGNORE','INFORM')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_until TIMESTAMPTZ NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN','EXPIRED','DELIVERED','SUPPRESSED')),
                dedupe_key VARCHAR(200) NOT NULL UNIQUE,
                template_id VARCHAR(64) NOT NULL DEFAULT '',
                safe_params JSONB NOT NULL DEFAULT '{}',
                proactive_cycle_id VARCHAR(64)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_pins_owner_valid ON proactive_insights(owner_id, valid_until DESC);",
            """
            CREATE TABLE IF NOT EXISTS proactive_deliveries (
                delivery_id VARCHAR(64) PRIMARY KEY,
                insight_id VARCHAR(64) NOT NULL REFERENCES proactive_insights(insight_id),
                channel VARCHAR(16) NOT NULL DEFAULT 'hud',
                status VARCHAR(16) NOT NULL DEFAULT 'DELIVERED',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (insight_id, channel)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS proactive_attention (
                owner_id VARCHAR(64) NOT NULL,
                day_key DATE NOT NULL,
                inform_count INTEGER NOT NULL DEFAULT 0,
                cooldowns JSONB NOT NULL DEFAULT '{}',
                PRIMARY KEY (owner_id, day_key)
            );
            """,
            # V6.2.2: connector accounts + fenced external fact cache (no secrets)
            """
            CREATE TABLE IF NOT EXISTS connector_accounts (
                account_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL DEFAULT 'sujal',
                connector_type VARCHAR(32) NOT NULL
                    CHECK (connector_type IN ('calendar_google','github','gmail')),
                project_id VARCHAR(64),
                secret_ref VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE','DISABLED','EXPIRED')),
                privacy_class_default VARCHAR(16) NOT NULL DEFAULT 'NORMAL'
                    CHECK (privacy_class_default IN ('NORMAL','PRIVATE','SENSITIVE')),
                health VARCHAR(16) NOT NULL DEFAULT 'OK'
                    CHECK (health IN ('OK','DEGRADED','DOWN')),
                health_detail VARCHAR(64) NOT NULL DEFAULT '',
                policy_version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_connector_acct_owner_type_proj ON connector_accounts (owner_id, connector_type, (COALESCE(project_id, '')));",
            "CREATE INDEX IF NOT EXISTS idx_connector_acct_owner_status ON connector_accounts (owner_id, status);",
            """
            CREATE TABLE IF NOT EXISTS connector_sync_state (
                account_id VARCHAR(64) PRIMARY KEY
                    REFERENCES connector_accounts(account_id) ON DELETE CASCADE,
                cursor TEXT,
                last_success_at TIMESTAMPTZ,
                last_error_type VARCHAR(64),
                backoff_until TIMESTAMPTZ
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS external_facts (
                fact_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL DEFAULT 'sujal',
                account_id VARCHAR(64) NOT NULL
                    REFERENCES connector_accounts(account_id) ON DELETE CASCADE,
                connector_type VARCHAR(32) NOT NULL,
                source_record_id VARCHAR(128) NOT NULL,
                fact_kind VARCHAR(32) NOT NULL
                    CHECK (fact_kind IN ('CAL_EVENT','GH_ISSUE','GH_PR','GH_REVIEW','EMAIL_META')),
                project_id VARCHAR(64),
                privacy_class VARCHAR(16) NOT NULL DEFAULT 'NORMAL'
                    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
                occurred_at TIMESTAMPTZ,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_until TIMESTAMPTZ,
                confidence REAL NOT NULL DEFAULT 0.8,
                payload JSONB NOT NULL DEFAULT '{}',
                content_sha256 VARCHAR(64) NOT NULL,
                idempotency_key VARCHAR(160) NOT NULL UNIQUE
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_ext_facts_owner_kind_occ ON external_facts (owner_id, fact_kind, occurred_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_ext_facts_valid ON external_facts (valid_until);",
            # V6.2.3: durable PRIVATE email/calendar-derived commitments (not memory)
            """
            CREATE TABLE IF NOT EXISTS proactive_commitments (
                commitment_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL DEFAULT 'sujal',
                account_id VARCHAR(64) NOT NULL
                    REFERENCES connector_accounts(account_id) ON DELETE CASCADE,
                source_connector VARCHAR(32) NOT NULL,
                source_message_id VARCHAR(128) NOT NULL,
                source_thread_id VARCHAR(128) NOT NULL DEFAULT '',
                source_ts TIMESTAMPTZ,
                commitment_type VARCHAR(32) NOT NULL
                    CHECK (commitment_type IN (
                        'DEADLINE','PROMISE','FOLLOW_UP','MEETING','REPLY_REQUIRED',
                        'DELIVERY','ACTION_REQUIRED','PAYMENT','REVIEW_REQUIRED'
                    )),
                normalized_code VARCHAR(32) NOT NULL DEFAULT '',
                due_at TIMESTAMPTZ,
                timezone VARCHAR(40) NOT NULL DEFAULT '',
                status VARCHAR(16) NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN','CANCELLED','COMPLETED')),
                confidence REAL NOT NULL DEFAULT 0,
                privacy_class VARCHAR(16) NOT NULL DEFAULT 'PRIVATE'
                    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
                provenance JSONB NOT NULL DEFAULT '{}',
                evidence_ref VARCHAR(64),
                fingerprint VARCHAR(48) NOT NULL,
                valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_until TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (account_id, fingerprint)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_pcommit_owner_status_due ON proactive_commitments (owner_id, status, due_at);",
            "CREATE INDEX IF NOT EXISTS idx_pcommit_account_msg ON proactive_commitments (account_id, source_message_id);",
            # V6.2.4: world evidence + predictions (not memory_evidence)
            """
            CREATE TABLE IF NOT EXISTS world_evidence (
                evidence_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL,
                project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE SET NULL,
                source_kind VARCHAR(32) NOT NULL
                    CHECK (source_kind IN ('EXTERNAL_FACT','COMMITMENT','TASK_CHECKPOINT')),
                source_id VARCHAR(128) NOT NULL,
                source_record_id VARCHAR(128) NOT NULL DEFAULT '',
                connector_type VARCHAR(32) NOT NULL DEFAULT '',
                reliability_class VARCHAR(32) NOT NULL,
                strength REAL NOT NULL CHECK (strength >= 0 AND strength <= 1),
                privacy_class VARCHAR(16) NOT NULL
                    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
                occurred_at TIMESTAMPTZ,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_until TIMESTAMPTZ,
                independence_key VARCHAR(80) NOT NULL,
                transform_id VARCHAR(40) NOT NULL DEFAULT 'cite_v624',
                transform_version VARCHAR(16) NOT NULL DEFAULT 'v624.1',
                status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE','SUPERSEDED','INVALIDATED','EXPIRED')),
                idempotency_key VARCHAR(160) NOT NULL UNIQUE,
                content_sha256 VARCHAR(64) NOT NULL DEFAULT '',
                provenance JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_we_owner_status ON world_evidence (owner_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_we_indep ON world_evidence (independence_key);",
            "CREATE INDEX IF NOT EXISTS idx_we_source ON world_evidence (source_kind, source_id);",
            """
            CREATE TABLE IF NOT EXISTS world_predictions (
                prediction_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL,
                project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE SET NULL,
                prediction_type VARCHAR(40) NOT NULL
                    CHECK (prediction_type IN (
                        'DEADLINE_HORIZON','STALE_OPEN_COMMITMENT','CAL_VS_COMMITMENT_CONFLICT',
                        'TASK_BLOCKED_NEAR_DEADLINE','OPEN_REVIEW_AGING')),
                subject_key VARCHAR(160) NOT NULL,
                claim_code VARCHAR(40) NOT NULL,
                horizon_start TIMESTAMPTZ,
                horizon_end TIMESTAMPTZ,
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                probability REAL CHECK (probability IS NULL OR (probability >= 0 AND probability <= 1)),
                risk_class VARCHAR(16) NOT NULL
                    CHECK (risk_class IN ('NONE','LOW','MEDIUM','HIGH')),
                status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE','SUPERSEDED','EXPIRED','INVALIDATED')),
                privacy_class VARCHAR(16) NOT NULL
                    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
                fingerprint VARCHAR(64) NOT NULL,
                rule_id VARCHAR(40) NOT NULL,
                rule_version VARCHAR(16) NOT NULL,
                generation INTEGER NOT NULL DEFAULT 1,
                evaluated_at TIMESTAMPTZ NOT NULL,
                valid_until TIMESTAMPTZ NOT NULL,
                provenance JSONB NOT NULL DEFAULT '{}',
                outcome VARCHAR(16),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (owner_id, fingerprint)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wp_owner_status_valid ON world_predictions (owner_id, status, valid_until);",
            "CREATE INDEX IF NOT EXISTS idx_wp_owner_type_horizon ON world_predictions (owner_id, prediction_type, horizon_end);",
            """
            CREATE TABLE IF NOT EXISTS world_prediction_evidence (
                prediction_id VARCHAR(64) NOT NULL
                    REFERENCES world_predictions(prediction_id) ON DELETE CASCADE,
                evidence_id VARCHAR(64) NOT NULL
                    REFERENCES world_evidence(evidence_id) ON DELETE CASCADE,
                role VARCHAR(16) NOT NULL CHECK (role IN ('SUPPORTING','CONFLICTING')),
                PRIMARY KEY (prediction_id, evidence_id)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS world_prediction_events (
                event_id VARCHAR(64) PRIMARY KEY,
                prediction_id VARCHAR(64) NOT NULL
                    REFERENCES world_predictions(prediction_id) ON DELETE CASCADE,
                from_status VARCHAR(16),
                to_status VARCHAR(16) NOT NULL,
                reason VARCHAR(40) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wpe_pred ON world_prediction_events (prediction_id, created_at);",
            # V6.2.5: world suggestions (not proactive_insights)
            """
            CREATE TABLE IF NOT EXISTS world_suggestions (
                suggestion_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL,
                project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE SET NULL,
                prediction_id VARCHAR(64) NOT NULL
                    REFERENCES world_predictions(prediction_id) ON DELETE CASCADE,
                suggestion_type VARCHAR(40) NOT NULL
                    CHECK (suggestion_type IN (
                        'CONSIDER_REVIEW_WORK','CONSIDER_CONFIRM_OPEN',
                        'CONSIDER_RECONCILE_TIME','CONSIDER_UNBLOCK','CONSIDER_COMPLETE_REVIEW')),
                claim_code VARCHAR(40) NOT NULL,
                template_id VARCHAR(64) NOT NULL,
                safe_params JSONB NOT NULL DEFAULT '{}',
                priority VARCHAR(16) NOT NULL
                    CHECK (priority IN ('LOW','MEDIUM','HIGH')),
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                risk_class VARCHAR(16) NOT NULL
                    CHECK (risk_class IN ('NONE','LOW','MEDIUM','HIGH')),
                privacy_class VARCHAR(16) NOT NULL
                    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
                fingerprint VARCHAR(64) NOT NULL,
                rule_id VARCHAR(40) NOT NULL,
                rule_version VARCHAR(16) NOT NULL DEFAULT 'v625.1',
                status VARCHAR(16) NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN','DELIVERED','DISMISSED','EXPIRED','SUPERSEDED')),
                valid_until TIMESTAMPTZ NOT NULL,
                provenance JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                evaluated_at TIMESTAMPTZ NOT NULL,
                dismissed_at TIMESTAMPTZ,
                UNIQUE (owner_id, fingerprint)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wsug_owner_status_valid ON world_suggestions (owner_id, status, valid_until);",
            "CREATE INDEX IF NOT EXISTS idx_wsug_prediction ON world_suggestions (prediction_id);",
            """
            CREATE TABLE IF NOT EXISTS world_suggestion_deliveries (
                delivery_id VARCHAR(64) PRIMARY KEY,
                suggestion_id VARCHAR(64) NOT NULL
                    REFERENCES world_suggestions(suggestion_id) ON DELETE CASCADE,
                channel VARCHAR(16) NOT NULL DEFAULT 'hud'
                    CHECK (channel IN ('hud')),
                status VARCHAR(16) NOT NULL DEFAULT 'DELIVERED'
                    CHECK (status IN ('DELIVERED','FAILED')),
                attempts INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (suggestion_id, channel)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS world_suggestion_events (
                event_id VARCHAR(64) PRIMARY KEY,
                suggestion_id VARCHAR(64) NOT NULL
                    REFERENCES world_suggestions(suggestion_id) ON DELETE CASCADE,
                from_status VARCHAR(16),
                to_status VARCHAR(16) NOT NULL,
                reason VARCHAR(40) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wse_sug ON world_suggestion_events (suggestion_id, created_at);",
            "ALTER TABLE proactive_attention ADD COLUMN IF NOT EXISTS suggest_count INTEGER NOT NULL DEFAULT 0;",
            # V6.2.6: ASK sessions + preparations + approval requests (not TaskEngine)
            """
            CREATE TABLE IF NOT EXISTS ask_sessions (
                session_id_hash VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL,
                csrf_token VARCHAR(64) NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_ask_sessions_owner_exp ON ask_sessions (owner_id, expires_at);",
            """
            CREATE TABLE IF NOT EXISTS world_preparations (
                preparation_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL,
                project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE SET NULL,
                suggestion_id VARCHAR(64) NOT NULL
                    REFERENCES world_suggestions(suggestion_id) ON DELETE CASCADE,
                prediction_id VARCHAR(64) NOT NULL
                    REFERENCES world_predictions(prediction_id) ON DELETE CASCADE,
                preparation_type VARCHAR(40) NOT NULL
                    CHECK (preparation_type IN (
                        'PREPARE_REVIEW_OUTLINE','PREPARE_CONFIRM_PROMPT',
                        'PREPARE_SCHEDULE_DIFF','PREPARE_UNBLOCK_NOTE','PREPARE_REVIEW_OPTIONS')),
                action_type VARCHAR(40) NOT NULL
                    CHECK (action_type IN (
                        'NONE','FUTURE_CAL_RECONCILE','FUTURE_GH_REVIEW',
                        'FUTURE_EMAIL_DRAFT','FUTURE_TASK_NOTE')),
                future_act_class VARCHAR(16) NOT NULL DEFAULT 'NONE'
                    CHECK (future_act_class IN ('NONE','MUTATION')),
                template_id VARCHAR(64) NOT NULL,
                safe_params JSONB NOT NULL DEFAULT '{}',
                param_hash VARCHAR(64) NOT NULL,
                preview_key VARCHAR(64) NOT NULL DEFAULT '',
                risk_class VARCHAR(16) NOT NULL
                    CHECK (risk_class IN ('NONE','LOW','MEDIUM','HIGH')),
                privacy_class VARCHAR(16) NOT NULL
                    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
                fingerprint VARCHAR(64) NOT NULL,
                rule_id VARCHAR(40) NOT NULL,
                rule_version VARCHAR(16) NOT NULL DEFAULT 'v626.1',
                status VARCHAR(16) NOT NULL DEFAULT 'READY'
                    CHECK (status IN ('DRAFT','READY','ASKED','EXPIRED','CANCELLED','SUPERSEDED')),
                valid_until TIMESTAMPTZ NOT NULL,
                provenance JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                evaluated_at TIMESTAMPTZ NOT NULL,
                UNIQUE (owner_id, fingerprint)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wprep_owner_status_valid ON world_preparations (owner_id, status, valid_until);",
            "CREATE INDEX IF NOT EXISTS idx_wprep_suggestion ON world_preparations (suggestion_id);",
            """
            CREATE TABLE IF NOT EXISTS world_approval_requests (
                approval_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL,
                preparation_id VARCHAR(64) NOT NULL
                    REFERENCES world_preparations(preparation_id) ON DELETE CASCADE,
                action_type VARCHAR(40) NOT NULL,
                param_hash VARCHAR(64) NOT NULL,
                binding_hash VARCHAR(64) NOT NULL,
                csrf_binding_id VARCHAR(64) NOT NULL,
                risk_class VARCHAR(16) NOT NULL,
                privacy_class VARCHAR(16) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','APPROVED','REJECTED','EXPIRED','REVOKED','CANCELLED')),
                valid_until TIMESTAMPTZ NOT NULL,
                approval_valid_until TIMESTAMPTZ,
                decided_at TIMESTAMPTZ,
                decision_session_id VARCHAR(64),
                rule_version VARCHAR(16) NOT NULL DEFAULT 'v626.1',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_war_prep_pending ON world_approval_requests (preparation_id) WHERE status = 'PENDING';",
            "CREATE INDEX IF NOT EXISTS idx_war_owner_status ON world_approval_requests (owner_id, status);",
            """
            CREATE TABLE IF NOT EXISTS world_preparation_events (
                event_id VARCHAR(64) PRIMARY KEY,
                preparation_id VARCHAR(64) NOT NULL
                    REFERENCES world_preparations(preparation_id) ON DELETE CASCADE,
                from_status VARCHAR(16),
                to_status VARCHAR(16) NOT NULL,
                reason VARCHAR(40) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wpre_prep ON world_preparation_events (preparation_id, created_at);",
            """
            CREATE TABLE IF NOT EXISTS world_approval_events (
                event_id VARCHAR(64) PRIMARY KEY,
                approval_id VARCHAR(64) NOT NULL
                    REFERENCES world_approval_requests(approval_id) ON DELETE CASCADE,
                from_status VARCHAR(16),
                to_status VARCHAR(16) NOT NULL,
                reason VARCHAR(40) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wae_appr ON world_approval_events (approval_id, created_at);",
            """
            CREATE TABLE IF NOT EXISTS world_prepare_deliveries (
                delivery_id VARCHAR(64) PRIMARY KEY,
                preparation_id VARCHAR(64) NOT NULL
                    REFERENCES world_preparations(preparation_id) ON DELETE CASCADE,
                channel VARCHAR(16) NOT NULL DEFAULT 'hud'
                    CHECK (channel IN ('hud')),
                status VARCHAR(16) NOT NULL DEFAULT 'DELIVERED'
                    CHECK (status IN ('DELIVERED','FAILED')),
                attempts INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (preparation_id, channel)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS world_ask_deliveries (
                delivery_id VARCHAR(64) PRIMARY KEY,
                approval_id VARCHAR(64) NOT NULL
                    REFERENCES world_approval_requests(approval_id) ON DELETE CASCADE,
                channel VARCHAR(16) NOT NULL DEFAULT 'hud'
                    CHECK (channel IN ('hud')),
                status VARCHAR(16) NOT NULL DEFAULT 'DELIVERED'
                    CHECK (status IN ('DELIVERED','FAILED')),
                attempts INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (approval_id, channel)
            );
            """,
            "ALTER TABLE proactive_attention ADD COLUMN IF NOT EXISTS prepare_count INTEGER NOT NULL DEFAULT 0;",
            "ALTER TABLE proactive_attention ADD COLUMN IF NOT EXISTS ask_count INTEGER NOT NULL DEFAULT 0;",
            """
            CREATE TABLE IF NOT EXISTS world_preparation_drafts (
                draft_id VARCHAR(64) PRIMARY KEY,
                owner_id VARCHAR(64) NOT NULL,
                preparation_id VARCHAR(64) NOT NULL
                    REFERENCES world_preparations(preparation_id) ON DELETE CASCADE,
                draft_type VARCHAR(64) NOT NULL
                    CHECK (draft_type IN (
                        'prepare_review_outline','prepare_confirm_prompt',
                        'prepare_schedule_diff','prepare_unblock_note','prepare_review_options')),
                structured_output JSONB NOT NULL DEFAULT '{}',
                provider VARCHAR(32) NOT NULL DEFAULT '',
                model VARCHAR(80) NOT NULL DEFAULT '',
                model_version VARCHAR(40) NOT NULL DEFAULT '',
                prompt_version VARCHAR(16) NOT NULL DEFAULT 'v628.1',
                rule_version VARCHAR(16) NOT NULL DEFAULT 'v626.1',
                param_hash_at_generation VARCHAR(64) NOT NULL,
                validation_status VARCHAR(16) NOT NULL
                    CHECK (validation_status IN ('ACCEPTED','REJECTED','SUPERSEDED','EXPIRED')),
                reject_reason VARCHAR(40) NOT NULL DEFAULT '',
                privacy_class VARCHAR(16) NOT NULL
                    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
                fingerprint VARCHAR(64) NOT NULL,
                correlation_id VARCHAR(64) NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (owner_id, fingerprint)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_wpd_owner_status ON world_preparation_drafts (owner_id, validation_status);",
            "CREATE INDEX IF NOT EXISTS idx_wpd_prep_status ON world_preparation_drafts (preparation_id, validation_status);",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_wpd_prep_accepted
            ON world_preparation_drafts (preparation_id) WHERE validation_status = 'ACCEPTED';
            """,
            """
            CREATE TABLE IF NOT EXISTS world_draft_deliveries (
                delivery_id VARCHAR(64) PRIMARY KEY,
                draft_id VARCHAR(64) NOT NULL
                    REFERENCES world_preparation_drafts(draft_id) ON DELETE CASCADE,
                channel VARCHAR(16) NOT NULL DEFAULT 'hud'
                    CHECK (channel IN ('hud')),
                status VARCHAR(16) NOT NULL DEFAULT 'DELIVERED'
                    CHECK (status IN ('DELIVERED','FAILED')),
                attempts INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (draft_id, channel)
            );
            """,
            # V5.3.2: Status CHECK constraint on memory_records
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_status') THEN
                    ALTER TABLE memory_records ADD CONSTRAINT chk_memory_status CHECK (status IN ('PENDING_VERIFICATION', 'ACTIVE', 'SUPERSEDED', 'ARCHIVED', 'DELETED'));
                END IF;
            END $$;
            """,
            # V5.3.3: Monotonic generation column and index on memory_records
            "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1;",
            "CREATE INDEX IF NOT EXISTS idx_memory_generation ON memory_records(generation);",
            # V5.3.3 / V5.3.7.2: Transactional vector synchronization queue with worker leasing
            """
            CREATE TABLE IF NOT EXISTS vector_sync_queue (
                sync_id VARCHAR(100) PRIMARY KEY,
                memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                operation VARCHAR(20) NOT NULL,
                target_generation INTEGER NOT NULL,
                target_status VARCHAR(30) NOT NULL,
                idempotency_key VARCHAR(150) UNIQUE,
                sync_status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                available_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                locked_until TIMESTAMP WITH TIME ZONE,
                worker_id VARCHAR(100),
                lease_acquired_at TIMESTAMP WITH TIME ZONE,
                lease_expires_at TIMESTAMP WITH TIME ZONE,
                heartbeat_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                last_error_class VARCHAR(100),
                last_error_message_safe VARCHAR(500)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_vsq_status_avail ON vector_sync_queue(sync_status, available_at);",
            "CREATE INDEX IF NOT EXISTS idx_vsq_mem_id ON vector_sync_queue(memory_id);",
            "CREATE INDEX IF NOT EXISTS idx_vsq_idempotency ON vector_sync_queue(idempotency_key);",
            "CREATE INDEX IF NOT EXISTS idx_vsq_locked_until ON vector_sync_queue(locked_until);",
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS worker_id VARCHAR(100);",
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS lease_acquired_at TIMESTAMP WITH TIME ZONE;",
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITH TIME ZONE;",
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITH TIME ZONE;",
            "CREATE INDEX IF NOT EXISTS idx_vsq_lease_expiry ON vector_sync_queue(sync_status, lease_expires_at);",
            "CREATE INDEX IF NOT EXISTS idx_vsq_worker_id ON vector_sync_queue(worker_id);",
            # V5.3.3: Durable vector generation and tombstone state registry
            """
            CREATE TABLE IF NOT EXISTS memory_vector_state (
                memory_id VARCHAR(100) PRIMARY KEY REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                max_generation INTEGER NOT NULL DEFAULT 0,
                vector_present BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_mem_vec_state_pres ON memory_vector_state(memory_id, vector_present);",
            "CREATE INDEX IF NOT EXISTS idx_mem_vec_state_gen ON memory_vector_state(max_generation);",
            # V5.3.4: Memory relationships table (Knowledge graph & DAG)
            """
            CREATE TABLE IF NOT EXISTS memory_relationships (
                relationship_id VARCHAR(100) PRIMARY KEY,
                source_memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                target_memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                relationship_type VARCHAR(50) NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                reason VARCHAR(500),
                actor VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
                idempotency_key VARCHAR(150) UNIQUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                metadata JSONB DEFAULT '{}',
                CONSTRAINT chk_relationship_no_self CHECK (source_memory_id <> target_memory_id),
                CONSTRAINT chk_relationship_type CHECK (
                    relationship_type IN ('SUPERSEDES', 'DUPLICATE_OF', 'CONFLICTS_WITH', 'RELATED_TO', 'DERIVED_FROM')
                )
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_rel_source ON memory_relationships(source_memory_id);",
            "CREATE INDEX IF NOT EXISTS idx_rel_target ON memory_relationships(target_memory_id);",
            "CREATE INDEX IF NOT EXISTS idx_rel_type ON memory_relationships(relationship_type);",
            "CREATE INDEX IF NOT EXISTS idx_rel_pair ON memory_relationships(source_memory_id, target_memory_id, relationship_type);",
            "CREATE INDEX IF NOT EXISTS idx_rel_idempotency ON memory_relationships(idempotency_key);",
            # V5.3.5: Memory Freshness, Temporal Fields & Confidence Evolution
            "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS freshness_class VARCHAR(30) NOT NULL DEFAULT 'PROJECT_STABLE';",
            "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS confidence_score REAL NOT NULL DEFAULT 0.50;",
            "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS is_foundational BOOLEAN NOT NULL DEFAULT FALSE;",
            "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
            "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP WITH TIME ZONE;",
            "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;",
            "CREATE INDEX IF NOT EXISTS idx_memory_freshness_class ON memory_records(freshness_class);",
            "CREATE INDEX IF NOT EXISTS idx_memory_conf_score ON memory_records(confidence_score);",
            "CREATE INDEX IF NOT EXISTS idx_memory_valid_until ON memory_records(valid_until);",
            "CREATE INDEX IF NOT EXISTS idx_memory_last_confirmed ON memory_records(last_confirmed_at DESC);",
            # V5.3.5: Constraints on memory_records
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_conf_score') THEN
                    ALTER TABLE memory_records ADD CONSTRAINT chk_memory_conf_score CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_importance') THEN
                    ALTER TABLE memory_records ADD CONSTRAINT chk_memory_importance CHECK (importance >= 0.0 AND importance <= 1.0);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_freshness_class') THEN
                    ALTER TABLE memory_records ADD CONSTRAINT chk_memory_freshness_class CHECK (
                        freshness_class IN ('PERMANENT', 'FOUNDATIONAL', 'PROJECT_STABLE', 'DYNAMIC_FACT', 'EPHEMERAL')
                    );
                END IF;
            END $$;
            """,
            # V5.3.5: Normalized memory evidence table
            """
            CREATE TABLE IF NOT EXISTS memory_evidence (
                evidence_id VARCHAR(100) PRIMARY KEY,
                memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                evidence_type VARCHAR(50) NOT NULL,
                polarity VARCHAR(20) NOT NULL CHECK (polarity IN ('SUPPORTING', 'CONTRADICTING', 'AMBIGUOUS')),
                strength REAL NOT NULL CHECK (strength >= 0.0 AND strength <= 1.0),
                source VARCHAR(50) NOT NULL,
                actor VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
                source_task_id VARCHAR(100),
                observation_hash VARCHAR(64) NOT NULL,
                idempotency_key VARCHAR(150) UNIQUE NOT NULL,
                summary VARCHAR(255) NOT NULL,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_evidence_mem_id ON memory_evidence(memory_id);",
            "CREATE INDEX IF NOT EXISTS idx_evidence_obs_hash ON memory_evidence(observation_hash);",
            "CREATE INDEX IF NOT EXISTS idx_evidence_idempotency ON memory_evidence(idempotency_key);",
            "CREATE INDEX IF NOT EXISTS idx_evidence_created ON memory_evidence(created_at DESC);",
            # V5.3.5: Memory evolution audit events table
            """
            CREATE TABLE IF NOT EXISTS memory_evolution_events (
                event_id VARCHAR(100) PRIMARY KEY,
                memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                evidence_id VARCHAR(100) REFERENCES memory_evidence(evidence_id) ON DELETE SET NULL,
                evolution_type VARCHAR(50) NOT NULL,
                confidence_before REAL NOT NULL,
                confidence_after REAL NOT NULL,
                importance_before REAL NOT NULL,
                importance_after REAL NOT NULL,
                delta_confidence REAL NOT NULL,
                delta_importance REAL NOT NULL,
                reason VARCHAR(255) NOT NULL,
                actor VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
                idempotency_key VARCHAR(150) UNIQUE NOT NULL,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_evo_mem_id ON memory_evolution_events(memory_id);",
            "CREATE INDEX IF NOT EXISTS idx_evo_created ON memory_evolution_events(created_at DESC);",
            "CREATE INDEX IF NOT EXISTS idx_evo_idempotency ON memory_evolution_events(idempotency_key);",
            # V5.3.6: First-class projects table
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                root_path VARCHAR(512),
                git_remote VARCHAR(512),
                tech_stack JSONB NOT NULL DEFAULT '[]',
                lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' CHECK (lifecycle_status IN ('ACTIVE', 'ON_HOLD', 'COMPLETED', 'ARCHIVED')),
                privacy_class VARCHAR(32) NOT NULL DEFAULT 'NORMAL' CHECK (privacy_class IN ('NORMAL', 'PRIVATE', 'SENSITIVE')),
                parent_project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE SET NULL,
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT chk_projects_parent_not_self CHECK (parent_project_id IS NULL OR parent_project_id <> project_id)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(lifecycle_status);",
            "CREATE INDEX IF NOT EXISTS idx_projects_parent ON projects(parent_project_id);",
            # V5.3.6: Seed default project 'doom' if not existing
            """
            INSERT INTO projects (project_id, name, description, lifecycle_status, privacy_class)
            VALUES ('doom', 'DOOM Core OS', 'Core DOOM AI Operating System workspace and memory namespace', 'ACTIVE', 'NORMAL')
            ON CONFLICT (project_id) DO NOTHING;
            """,
            # V5.3.6: Experiences table
            """
            CREATE TABLE IF NOT EXISTS experiences (
                experience_id VARCHAR(64) PRIMARY KEY,
                task_id VARCHAR(64) NOT NULL,
                project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
                goal_intent TEXT NOT NULL,
                context_conditions JSONB NOT NULL DEFAULT '{}',
                strategy_applied JSONB NOT NULL DEFAULT '{}',
                execution_trace JSONB NOT NULL DEFAULT '[]',
                outcome_status VARCHAR(32) NOT NULL CHECK (outcome_status IN ('SUCCESS', 'PARTIAL_SUCCESS', 'FAILURE', 'ABORTED', 'UNKNOWN')),
                outcome_metrics JSONB NOT NULL DEFAULT '{}',
                error_signature VARCHAR(256),
                root_cause_analysis TEXT,
                verification_evidence JSONB NOT NULL DEFAULT '{}',
                confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.50 CHECK (confidence_score >= 0.01 AND confidence_score <= 1.00),
                importance DOUBLE PRECISION NOT NULL DEFAULT 0.50 CHECK (importance >= 0.00 AND importance <= 1.00),
                privacy_class VARCHAR(32) NOT NULL DEFAULT 'NORMAL' CHECK (privacy_class IN ('NORMAL', 'PRIVATE', 'SENSITIVE')),
                idempotency_key VARCHAR(150) UNIQUE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_experiences_project_outcome ON experiences(project_id, outcome_status);",
            "CREATE INDEX IF NOT EXISTS idx_experiences_task ON experiences(task_id);",
            "CREATE INDEX IF NOT EXISTS idx_experiences_error_sig ON experiences(error_signature) WHERE error_signature IS NOT NULL;",
            "CREATE INDEX IF NOT EXISTS idx_experiences_idempotency ON experiences(idempotency_key);",
            # V5.3.6: Lessons table
            """
            CREATE TABLE IF NOT EXISTS lessons (
                lesson_id VARCHAR(64) PRIMARY KEY,
                title VARCHAR(256) NOT NULL,
                summary TEXT NOT NULL,
                domain VARCHAR(64) NOT NULL,
                scope VARCHAR(32) NOT NULL DEFAULT 'PROJECT_LOCAL' CHECK (scope IN ('PROJECT_LOCAL', 'CROSS_PROJECT_ELIGIBLE', 'UNIVERSAL')),
                prerequisites JSONB NOT NULL DEFAULT '[]',
                anti_patterns JSONB NOT NULL DEFAULT '[]',
                supporting_experience_ids JSONB NOT NULL DEFAULT '[]',
                supporting_experience_count INT NOT NULL DEFAULT 1 CHECK (supporting_experience_count >= 0),
                contradicting_experience_count INT NOT NULL DEFAULT 0 CHECK (contradicting_experience_count >= 0),
                confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.60 CHECK (confidence_score >= 0.01 AND confidence_score <= 1.00),
                importance DOUBLE PRECISION NOT NULL DEFAULT 0.50 CHECK (importance >= 0.00 AND importance <= 1.00),
                freshness_class VARCHAR(32) NOT NULL DEFAULT 'PROJECT_STABLE',
                last_confirmed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "ALTER TABLE lessons ADD COLUMN IF NOT EXISTS supporting_experience_ids JSONB NOT NULL DEFAULT '[]';",
            "CREATE INDEX IF NOT EXISTS idx_lessons_domain_scope ON lessons(domain, scope);",
            # V5.3.6: Strategies table
            """
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                intent_category VARCHAR(64) NOT NULL,
                procedure_template JSONB NOT NULL,
                recommended_tools JSONB NOT NULL DEFAULT '[]',
                disallowed_tools JSONB NOT NULL DEFAULT '[]',
                environmental_preconditions JSONB NOT NULL DEFAULT '{}',
                total_attempts INT NOT NULL DEFAULT 0 CHECK (total_attempts >= 0),
                successful_attempts INT NOT NULL DEFAULT 0 CHECK (successful_attempts >= 0),
                failed_attempts INT NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
                reliability_score DOUBLE PRECISION NOT NULL DEFAULT 0.50 CHECK (reliability_score >= 0.00 AND reliability_score <= 1.00),
                is_deprecated BOOLEAN NOT NULL DEFAULT FALSE,
                deprecation_reason TEXT,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            """,
            "CREATE INDEX IF NOT EXISTS idx_strategies_category ON strategies(intent_category);",
            # V5.3.6: Cross-Project Transfer Matrix
            """
            CREATE TABLE IF NOT EXISTS project_transfer_matrix (
                transfer_id VARCHAR(64) PRIMARY KEY,
                source_project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id),
                target_project_id VARCHAR(64) NOT NULL REFERENCES projects(project_id),
                lesson_id VARCHAR(64) NOT NULL REFERENCES lessons(lesson_id),
                strategy_id VARCHAR(64) REFERENCES strategies(strategy_id),
                semantic_similarity DOUBLE PRECISION NOT NULL CHECK (semantic_similarity >= 0.00 AND semantic_similarity <= 1.00),
                tech_stack_overlap DOUBLE PRECISION NOT NULL CHECK (tech_stack_overlap >= 0.00 AND tech_stack_overlap <= 1.00),
                transfer_confidence DOUBLE PRECISION NOT NULL CHECK (transfer_confidence >= 0.00 AND transfer_confidence <= 1.00),
                status VARCHAR(32) NOT NULL DEFAULT 'EVALUATED' CHECK (status IN ('EVALUATED', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
                rejection_reason TEXT,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT chk_transfer_no_self CHECK (source_project_id <> target_project_id)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_transfer_matrix_pair ON project_transfer_matrix(source_project_id, target_project_id);",
        ]

        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(q)
            conn.commit()
            self._migrate_v5371_constraints(conn)
            self._migrate_v5372_outbox_schema(conn)
            self._migrate_v5373_governance_schema(conn)
            self._init_v52_vector_schema(conn)
            print("[POSTGRES] [OK] Schema tables initialized: user_profiles, episodic_memory, semantic_facts, system_telemetry, command_logs, memory_records, memory_lifecycle_events")
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Failed to create schema tables: {e}")
        finally:
            self.release_connection(conn)

    def _migrate_v5371_constraints(self, conn):
        """V5.3.7.1: Idempotently applies database-level integrity constraints to existing tables."""
        constraints = [
            ("projects", "chk_projects_parent_not_self",
             "ALTER TABLE projects ADD CONSTRAINT chk_projects_parent_not_self CHECK (parent_project_id IS NULL OR parent_project_id <> project_id);"),
            ("lessons", "chk_lessons_supporting_count",
             "ALTER TABLE lessons ADD CONSTRAINT chk_lessons_supporting_count CHECK (supporting_experience_count >= 0);"),
            ("lessons", "chk_lessons_contradicting_count",
             "ALTER TABLE lessons ADD CONSTRAINT chk_lessons_contradicting_count CHECK (contradicting_experience_count >= 0);"),
            ("strategies", "chk_strategies_total_attempts",
             "ALTER TABLE strategies ADD CONSTRAINT chk_strategies_total_attempts CHECK (total_attempts >= 0);"),
            ("strategies", "chk_strategies_success_attempts",
             "ALTER TABLE strategies ADD CONSTRAINT chk_strategies_success_attempts CHECK (successful_attempts >= 0);"),
            ("strategies", "chk_strategies_failed_attempts",
             "ALTER TABLE strategies ADD CONSTRAINT chk_strategies_failed_attempts CHECK (failed_attempts >= 0);"),
            ("project_transfer_matrix", "chk_transfer_no_self",

             "ALTER TABLE project_transfer_matrix ADD CONSTRAINT chk_transfer_no_self CHECK (source_project_id <> target_project_id);"),
            ("project_transfer_matrix", "chk_transfer_semantic_sim",
             "ALTER TABLE project_transfer_matrix ADD CONSTRAINT chk_transfer_semantic_sim CHECK (semantic_similarity >= 0.00 AND semantic_similarity <= 1.00);"),
            ("project_transfer_matrix", "chk_transfer_tech_overlap",
             "ALTER TABLE project_transfer_matrix ADD CONSTRAINT chk_transfer_tech_overlap CHECK (tech_stack_overlap >= 0.00 AND tech_stack_overlap <= 1.00);"),
            ("project_transfer_matrix", "chk_transfer_confidence",
             "ALTER TABLE project_transfer_matrix ADD CONSTRAINT chk_transfer_confidence CHECK (transfer_confidence >= 0.00 AND transfer_confidence <= 1.00);"),
        ]
        with conn.cursor() as cur:
            for tbl, conname, alter_sql in constraints:
                try:
                    cur.execute("SELECT 1 FROM pg_constraint WHERE conname = %s;", (conname,))
                    if not cur.fetchone():
                        cur.execute(alter_sql)
                except Exception as ce:
                    pass
        conn.commit()

    def _migrate_v5372_outbox_schema(self, conn):
        """V5.3.7.2: Idempotently extends vector_sync_queue with worker leasing and heartbeat columns."""
        if not conn:
            return
        migrations = [
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS worker_id VARCHAR(100);",
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS lease_acquired_at TIMESTAMP WITH TIME ZONE;",
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITH TIME ZONE;",
            "ALTER TABLE vector_sync_queue ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITH TIME ZONE;",
            "CREATE INDEX IF NOT EXISTS idx_vsq_lease_expiry ON vector_sync_queue(sync_status, lease_expires_at);",
            "CREATE INDEX IF NOT EXISTS idx_vsq_worker_id ON vector_sync_queue(worker_id);",
        ]
        with conn.cursor() as cur:
            for sql in migrations:
                try:
                    cur.execute(sql)
                except Exception as ce:
                    pass
        conn.commit()

    def _migrate_v5373_governance_schema(self, conn):
        """V5.3.7.3: Idempotently extends project_transfer_matrix with policy versioning and risk penalty."""
        if not conn:
            return
        migrations = [
            "ALTER TABLE project_transfer_matrix ADD COLUMN IF NOT EXISTS policy_version VARCHAR(32) NOT NULL DEFAULT 'GOV_POLICY_V1';",
            "ALTER TABLE project_transfer_matrix ADD COLUMN IF NOT EXISTS risk_penalty DOUBLE PRECISION NOT NULL DEFAULT 0.00;",
            "ALTER TABLE project_transfer_matrix ALTER COLUMN lesson_id DROP NOT NULL;",
            "ALTER TABLE project_transfer_matrix DROP CONSTRAINT IF EXISTS project_transfer_matrix_status_check;",
            "ALTER TABLE project_transfer_matrix ADD CONSTRAINT project_transfer_matrix_status_check CHECK (status IN ('EVALUATED', 'APPROVED', 'REJECTED', 'SUPERSEDED', 'ABSTAIN'));",
            "CREATE INDEX IF NOT EXISTS idx_transfer_matrix_policy ON project_transfer_matrix(policy_version);",
            # V5.3.7.3: Idempotently extend experiences table for atomic evidence recording
            "ALTER TABLE experiences ADD COLUMN IF NOT EXISTS strategy_id VARCHAR(64);",
            "ALTER TABLE experiences ADD COLUMN IF NOT EXISTS idempotency_hash VARCHAR(64);",
            "ALTER TABLE experiences ADD COLUMN IF NOT EXISTS client_transaction_id VARCHAR(64);",
            "ALTER TABLE experiences ADD COLUMN IF NOT EXISTS quarantine_reason VARCHAR(256);",
            "ALTER TABLE experiences ADD COLUMN IF NOT EXISTS privacy_class VARCHAR(32) NOT NULL DEFAULT 'NORMAL';",
            "CREATE INDEX IF NOT EXISTS idx_experiences_idempotency_hash ON experiences(idempotency_hash);",
            "CREATE INDEX IF NOT EXISTS idx_experiences_client_tx ON experiences(client_transaction_id);",
            "CREATE INDEX IF NOT EXISTS idx_experiences_strat_tx ON experiences(strategy_id, client_transaction_id);",
        ]
        for sql in migrations:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            except Exception:
                conn.rollback()

    def _init_v52_vector_schema(self, conn):
        """Initializes V5.2 memory_embeddings table if pgvector extension is available."""
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector';")
                has_ext = cur.fetchone()
                if not has_ext:
                    cur.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector';")
                    if cur.fetchone():
                        try:
                            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                            conn.commit()
                            has_ext = True
                        except Exception:
                            conn.rollback()
                if has_ext:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS memory_embeddings (
                            embedding_id VARCHAR(100) PRIMARY KEY,
                            memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                            model VARCHAR(100) NOT NULL,
                            model_version VARCHAR(30) NOT NULL,
                            dimension INTEGER NOT NULL,
                            embedding vector(384) NOT NULL,
                            content_hash VARCHAR(64) NOT NULL,
                            generation INTEGER NOT NULL DEFAULT 1,
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT uq_memory_model_version UNIQUE (memory_id, model, model_version)
                        );
                        ALTER TABLE memory_embeddings ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1;
                        CREATE INDEX IF NOT EXISTS idx_mem_emb_memory_id ON memory_embeddings(memory_id);
                        CREATE INDEX IF NOT EXISTS idx_mem_emb_model ON memory_embeddings(model, model_version);
                    """)
                    conn.commit()
                    print("[POSTGRES] [OK] V5.2 memory_embeddings initialized with pgvector")
                else:
                    print("[POSTGRES] [NOTE] pgvector not available; V5.2 will use NumPy fallback adapter.")
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES NOTE] V5.2 pgvector schema check: {e}")

    def get_connection(self):
        """Retrieves a connection from the pool or creates a standalone connection."""
        if not PSYCOPG2_AVAILABLE:
            return None
        if self._pool:
            try:
                return self._pool.getconn()
            except Exception:
                pass
        try:
            return psycopg2.connect(**self._get_connection_params())
        except Exception as e:
            print(f"[POSTGRES ERROR] Could not get connection: {e}")
            return None

    def release_connection(self, conn):
        """Releases a connection back to the pool."""
        if not conn:
            return
        if self._pool:
            try:
                self._pool.putconn(conn)
                return
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass

    @contextmanager
    def transaction(self, lock_timeout_ms: int = 3000):
        """
        V5.3.2: Transaction context manager for atomic lifecycle state changes.
        - Checks out exactly one connection from the pool.
        - Enforces explicit transaction boundaries (BEGIN ... COMMIT/ROLLBACK).
        - Sets lock_timeout to prevent indefinite waiting.
        - Guarantees rollback on any exception before releasing the connection.
        - Yields the connection to the caller.
        """
        conn = self.get_connection()
        if not conn:
            raise DatabaseConnectionError("Failed to acquire connection from pool for transaction.")

        committed = False
        try:
            with conn.cursor() as cur:
                if lock_timeout_ms and lock_timeout_ms > 0:
                    cur.execute(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms';")
            yield conn
            conn.commit()
            committed = True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            err_str = str(e).lower()
            if "lock_timeout" in err_str or "canceling statement due to lock timeout" in err_str:
                raise LockTimeoutError(f"Database lock timeout after {lock_timeout_ms}ms: {e}") from e
            elif "deadlock detected" in err_str:
                raise DeadlockDetectedError(f"Database deadlock detected: {e}") from e
            raise
        finally:
            if not committed:
                try:
                    conn.rollback()
                except Exception:
                    pass
            self.release_connection(conn)

    def is_connected(self) -> bool:
        """Health check for active database connection."""
        conn = self.get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                res = cur.fetchone()
                return res is not None and res[0] == 1
        except Exception:
            return False
        finally:
            self.release_connection(conn)

    def test_connection(self) -> Dict[str, Any]:
        """Returns diagnostic statistics about database and table rows."""
        if not PSYCOPG2_AVAILABLE:
            return {"status": "error", "message": "psycopg2 library not available"}

        conn = self.get_connection()
        if not conn:
            return {"status": "disconnected", "database": self.dbname, "host": f"{self.host}:{self.port}"}

        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM user_profiles) AS profile_count,
                        (SELECT COUNT(*) FROM episodic_memory) AS episode_count,
                        (SELECT COUNT(*) FROM semantic_facts) AS fact_count,
                        (SELECT COUNT(*) FROM system_telemetry) AS telemetry_count,
                        (SELECT COUNT(*) FROM command_logs) AS log_count,
                        (SELECT COUNT(*) FROM task_checkpoints) AS checkpoint_count;
                """)
                counts = cur.fetchone()

                return {
                    "status": "connected",
                    "database": self.dbname,
                    "host": f"{self.host}:{self.port}",
                    "user": self.user,
                    "tables": dict(counts) if counts else {}
                }
        except Exception as e:
            return {"status": "error", "error": str(e)}
        finally:
            self.release_connection(conn)

    # -------------------------------------------------------------
    # User Profile Operations
    # -------------------------------------------------------------
    def save_user_profile(self, data: Dict[str, Any], user_id: str = "sujal"):
        """Saves or updates Sujal's persistent profile in PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_profiles (id, name, role, title, preferences, projects, custom_notes, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        role = EXCLUDED.role,
                        title = EXCLUDED.title,
                        preferences = EXCLUDED.preferences,
                        projects = EXCLUDED.projects,
                        custom_notes = EXCLUDED.custom_notes,
                        last_updated = CURRENT_TIMESTAMP;
                """, (
                    user_id,
                    data.get("name", "Sujal"),
                    data.get("role", "Creator, Boss, and Lead AI Engineer"),
                    data.get("title", "Sir"),
                    json.dumps(data.get("preferences", {})),
                    json.dumps(data.get("projects", [])),
                    json.dumps(data.get("custom_notes", {}))
                ))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Failed to save user profile: {e}")
        finally:
            self.release_connection(conn)

    def load_user_profile(self, user_id: str = "sujal") -> Optional[Dict[str, Any]]:
        """Loads user profile from PostgreSQL if available."""
        conn = self.get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_profiles WHERE id = %s;", (user_id,))
                row = cur.fetchone()
                if row:
                    return {
                        "name": row["name"],
                        "role": row["role"],
                        "title": row["title"],
                        "preferences": row["preferences"] if isinstance(row["preferences"], dict) else json.loads(row["preferences"] or "{}"),
                        "projects": row["projects"] if isinstance(row["projects"], list) else json.loads(row["projects"] or "[]"),
                        "custom_notes": row["custom_notes"] if isinstance(row["custom_notes"], dict) else json.loads(row["custom_notes"] or "{}"),
                        "last_updated": row["last_updated"].isoformat() if row["last_updated"] else None
                    }
        except Exception as e:
            print(f"[POSTGRES ERROR] Failed to load user profile: {e}")
        finally:
            self.release_connection(conn)
        return None

    # -------------------------------------------------------------
    # Episodic Memory Operations
    # -------------------------------------------------------------
    def record_episode(self, episode_id: str, goal: str, plan_steps: List[str], tools_called: List[Dict[str, Any]], outcome: str, success: bool = True):
        """Records an action episode into PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO episodic_memory (episode_id, goal, plan_steps, tools_called, outcome, success, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (episode_id) DO UPDATE SET
                        goal = EXCLUDED.goal,
                        plan_steps = EXCLUDED.plan_steps,
                        tools_called = EXCLUDED.tools_called,
                        outcome = EXCLUDED.outcome,
                        success = EXCLUDED.success;
                """, (
                    episode_id,
                    goal,
                    json.dumps(plan_steps, default=str),
                    json.dumps(tools_called, default=str),
                    outcome,
                    success
                ))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Failed to record episode: {e}")
        finally:
            self.release_connection(conn)

    def get_recent_episodes(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieves recent action episodes from PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM episodic_memory ORDER BY recorded_at DESC LIMIT %s;", (limit,))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    results.append({
                        "id": r["episode_id"],
                        "goal": r["goal"],
                        "plan_steps": r["plan_steps"],
                        "tools_called": r["tools_called"],
                        "outcome": r["outcome"],
                        "success": r["success"],
                        "timestamp": r["recorded_at"].isoformat() if r["recorded_at"] else ""
                    })
                return results
        except Exception as e:
            print(f"[POSTGRES ERROR] Failed to fetch episodes: {e}")
            return []
        finally:
            self.release_connection(conn)

    # -------------------------------------------------------------
    # Semantic Facts Operations
    # -------------------------------------------------------------
    def save_semantic_fact(self, key: str, value: Any, category: str = "general"):
        """Saves a permanent fact to PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO semantic_facts (key, value, category, updated_at)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        category = EXCLUDED.category,
                        updated_at = CURRENT_TIMESTAMP;
                """, (key.lower().strip(), json.dumps(value), category))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Failed to save semantic fact: {e}")
        finally:
            self.release_connection(conn)

    def load_semantic_facts(self) -> Dict[str, Any]:
        """Loads all semantic facts from PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return {}
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT key, value FROM semantic_facts;")
                rows = cur.fetchall()
                return {r["key"]: r["value"] for r in rows}
        except Exception as e:
            print(f"[POSTGRES ERROR] Failed to load semantic facts: {e}")
            return {}
        finally:
            self.release_connection(conn)

    # -------------------------------------------------------------
    # Telemetry & Command Logging (Dashboard Data)
    # -------------------------------------------------------------
    def log_telemetry(self, cpu_percent: float, ram_percent: float, disk_percent: float, raw_metrics: Optional[Dict[str, Any]] = None):
        """Logs hardware telemetry snapshot for dashboard monitoring."""
        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO system_telemetry (cpu_percent, ram_percent, disk_percent, raw_metrics, recorded_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP);
                """, (cpu_percent, ram_percent, disk_percent, json.dumps(raw_metrics or {})))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Telemetry log error: {e}")
        finally:
            self.release_connection(conn)

    def log_command(self, user_command: str, response_text: str, tools_used: Optional[List[str]] = None, latency_ms: float = 0.0):
        """Logs user command query and DOOM response for dashboard auditing."""
        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO command_logs (user_command, response_text, tools_used, latency_ms, created_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP);
                """, (user_command, response_text, json.dumps(tools_used or []), latency_ms))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Command log error: {e}")
        finally:
            self.release_connection(conn)

    def get_table_counts(self) -> Dict[str, int]:
        """Returns row counts for all core tables including checkpoints."""
        conn = self.get_connection()
        if not conn:
            return {}
        try:
            with conn.cursor() as cur:
                counts = {}
                for tbl in ["user_profiles", "episodic_memory", "semantic_facts", "system_telemetry", "command_logs", "task_checkpoints"]:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl};")
                    counts[tbl] = cur.fetchone()[0]
                return counts
        except Exception as e:
            print(f"[POSTGRES ERROR] Failed to fetch table counts: {e}")
            return {}
        finally:
            self.release_connection(conn)

    # -------------------------------------------------------------
    # V3.3: Task Checkpoint Operations
    # -------------------------------------------------------------
    def save_checkpoint(self, checkpoint: Dict[str, Any]):
        """Saves or updates a task checkpoint to PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO task_checkpoints (
                        task_id, goal, task_type, status, current_step,
                        completed_steps, remaining_steps, failed_steps, blocked_steps,
                        artifacts, tool_results, verification_results,
                        models_used, retry_counts, termination_reason,
                        final_response_status, resume_available, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (task_id) DO UPDATE SET
                        goal = EXCLUDED.goal,
                        task_type = EXCLUDED.task_type,
                        status = EXCLUDED.status,
                        current_step = EXCLUDED.current_step,
                        completed_steps = EXCLUDED.completed_steps,
                        remaining_steps = EXCLUDED.remaining_steps,
                        failed_steps = EXCLUDED.failed_steps,
                        blocked_steps = EXCLUDED.blocked_steps,
                        artifacts = EXCLUDED.artifacts,
                        tool_results = EXCLUDED.tool_results,
                        verification_results = EXCLUDED.verification_results,
                        models_used = EXCLUDED.models_used,
                        retry_counts = EXCLUDED.retry_counts,
                        termination_reason = EXCLUDED.termination_reason,
                        final_response_status = EXCLUDED.final_response_status,
                        resume_available = EXCLUDED.resume_available,
                        updated_at = CURRENT_TIMESTAMP;
                """, (
                    checkpoint.get("task_id"),
                    checkpoint.get("goal"),
                    checkpoint.get("task_type"),
                    checkpoint.get("status"),
                    checkpoint.get("current_step"),
                    json.dumps(checkpoint.get("completed_steps", []), default=str),
                    json.dumps(checkpoint.get("remaining_steps", []), default=str),
                    json.dumps(checkpoint.get("failed_steps", []), default=str),
                    json.dumps(checkpoint.get("blocked_steps", []), default=str),
                    json.dumps(checkpoint.get("artifacts", []), default=str),
                    json.dumps(checkpoint.get("tool_results", []), default=str),
                    json.dumps(checkpoint.get("verification_results", []), default=str),
                    json.dumps(checkpoint.get("models_used", []), default=str),
                    json.dumps(checkpoint.get("retry_counts", {}), default=str),
                    checkpoint.get("termination_reason"),
                    checkpoint.get("final_response_status", "success"),
                    checkpoint.get("resume_available", True),
                ))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Failed to save checkpoint: {e}")
        finally:
            self.release_connection(conn)

    def load_checkpoint(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Loads a task checkpoint from PostgreSQL."""
        conn = self.get_connection()
        if not conn:
            return None
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM task_checkpoints WHERE task_id = %s;", (task_id,))
                row = cur.fetchone()
                if row:
                    return dict(row)
                return None
        except Exception as e:
            print(f"[POSTGRES ERROR] Failed to load checkpoint: {e}")
            return None
        finally:
            self.release_connection(conn)

    def delete_checkpoint(self, task_id: str) -> bool:
        """Deletes a task checkpoint after successful completion."""
        conn = self.get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM task_checkpoints WHERE task_id = %s;", (task_id,))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Failed to delete checkpoint: {e}")
            return False
        finally:
            self.release_connection(conn)

    def get_recent_checkpoints(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieves recent checkpoints for dashboard."""
        conn = self.get_connection()
        if not conn:
            return []
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT task_id, goal, status, current_step, updated_at FROM task_checkpoints ORDER BY updated_at DESC LIMIT %s;", (limit,))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            print(f"[POSTGRES ERROR] Failed to fetch recent checkpoints: {e}")
            return []
        finally:
            self.release_connection(conn)

    def execute_query(self, query: str, params: Optional[tuple] = None, readonly: bool = True) -> List[Dict[str, Any]]:
        """Executes a SQL query safely and returns list of dictionaries."""
        conn = self.get_connection()
        if not conn:
            return [{"error": "Database not connected"}]
        try:
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(query, params or ())
                if readonly or query.strip().upper().startswith("SELECT"):
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
                else:
                    conn.commit()
                    return [{"rows_affected": cur.rowcount, "status": "success"}]
        except Exception as e:
            if not readonly:
                conn.rollback()
            return [{"error": str(e)}]
        finally:
            self.release_connection(conn)


    # -------------------------------------------------------------
    # V5.1: Memory Records Operations
    # -------------------------------------------------------------
    def get_memory_count(self) -> int:
        """Return count of ACTIVE memory_records for telemetry."""
        conn = self.get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_records WHERE status = 'ACTIVE';")
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0
        finally:
            self.release_connection(conn)

    def get_memory_table_stats(self) -> Dict[str, Any]:
        """Return basic stats for the memory_records table."""
        conn = self.get_connection()
        if not conn:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT status, COUNT(*) as cnt
                    FROM memory_records
                    GROUP BY status;
                """)
                rows = cur.fetchall()
                return {r[0]: r[1] for r in rows}
        except Exception as e:
            return {"error": str(e)}
        finally:
            self.release_connection(conn)


# Global singleton instance
postgres_manager = PostgresManager()
