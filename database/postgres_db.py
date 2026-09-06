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
            # V5.3.2: Status CHECK constraint on memory_records
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_status') THEN
                    ALTER TABLE memory_records ADD CONSTRAINT chk_memory_status CHECK (status IN ('PENDING_VERIFICATION', 'ACTIVE', 'SUPERSEDED', 'ARCHIVED', 'DELETED'));
                END IF;
            END $$;
            """,
        ]

        conn = self.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(q)
            conn.commit()
            self._init_v52_vector_schema(conn)
            print("[POSTGRES] [OK] Schema tables initialized: user_profiles, episodic_memory, semantic_facts, system_telemetry, command_logs, memory_records, memory_lifecycle_events")
        except Exception as e:
            conn.rollback()
            print(f"[POSTGRES ERROR] Failed to create schema tables: {e}")
        finally:
            self.release_connection(conn)

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
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT uq_memory_model_version UNIQUE (memory_id, model, model_version)
                        );
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
