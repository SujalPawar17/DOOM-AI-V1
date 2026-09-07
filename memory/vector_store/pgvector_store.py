"""
DOOM V5.2.2 — PostgreSQL + pgvector Vector Storage Adapter
Implements durable vector persistence in PostgreSQL using the pgvector extension.
Manages the memory_embeddings table, foreign key cascade, HNSW vector indexing,
and hardware-accelerated cosine similarity search.
"""
from datetime import datetime, timezone
import hashlib
import time
from typing import List, Optional, Dict, Any, Tuple

from memory.vector_store.base import (
    VectorStore,
    VectorStorageBackend,
    StoredVectorRecord,
    VectorSearchResult,
    VectorStorageError,
    VectorValidationError,
    validate_vector_for_storage,
)
from database.postgres_db import postgres_manager


class PgVectorStorageAdapter(VectorStore):
    """
    PostgreSQL vector store leveraging pgvector.
    Provides ACID transaction guarantees, ON DELETE CASCADE foreign key integrity,
    and indexed cosine nearest neighbor queries.
    """

    def __init__(self, expected_dimension: int = 384):
        self._dimension = expected_dimension
        self._pgvector_available: Optional[bool] = None
        self._availability_reason: str = ""
        self._initialized = False

    @property
    def backend(self) -> VectorStorageBackend:
        return VectorStorageBackend.PGVECTOR

    # ------------------------------------------------------------------
    # Capability Detection & Schema Management
    # ------------------------------------------------------------------
    def check_pgvector_available(self) -> Tuple[bool, str]:
        """
        Safely probe PostgreSQL to check if the pgvector extension is installed
        or can be created. Never raises an unhandled exception.
        """
        conn = postgres_manager.get_connection()
        if not conn:
            self._pgvector_available = False
            self._availability_reason = "PostgreSQL connection unavailable."
            return False, self._availability_reason

        try:
            with conn.cursor() as cur:
                # 1. Check if extension is already active
                cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector';")
                if cur.fetchone():
                    self._pgvector_available = True
                    self._availability_reason = "pgvector extension is active."
                    return True, self._availability_reason

                # 2. Check if extension binary is available in pg_available_extensions
                cur.execute(
                    "SELECT default_version FROM pg_available_extensions WHERE name = 'vector';"
                )
                row = cur.fetchone()
                if not row:
                    self._pgvector_available = False
                    self._availability_reason = (
                        "Extension 'vector' not installed in PostgreSQL libraries."
                    )
                    return False, self._availability_reason

                # 3. Attempt controlled creation
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    conn.commit()
                    self._pgvector_available = True
                    self._availability_reason = "pgvector extension successfully created."
                    return True, self._availability_reason
                except Exception as e:
                    conn.rollback()
                    self._pgvector_available = False
                    self._availability_reason = f"Failed to create pgvector extension: {e}"
                    return False, self._availability_reason

        except Exception as e:
            self._pgvector_available = False
            self._availability_reason = f"Capability probe failed: {e}"
            return False, self._availability_reason
        finally:
            postgres_manager.release_connection(conn)

    def init_schema(self) -> bool:
        """
        Create memory_embeddings table and indexes if pgvector is available.
        Returns True on success, False if pgvector is absent.
        """
        is_avail, reason = self.check_pgvector_available()
        if not is_avail:
            return False

        conn = postgres_manager.get_connection()
        if not conn:
            return False

        queries = [
            f"""
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                embedding_id VARCHAR(100) PRIMARY KEY,
                memory_id VARCHAR(100) NOT NULL REFERENCES memory_records(memory_id) ON DELETE CASCADE,
                model VARCHAR(100) NOT NULL,
                model_version VARCHAR(30) NOT NULL,
                dimension INTEGER NOT NULL,
                embedding vector({self._dimension}) NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_memory_model_version UNIQUE (memory_id, model, model_version)
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_mem_emb_memory_id ON memory_embeddings(memory_id);",
            "CREATE INDEX IF NOT EXISTS idx_mem_emb_model ON memory_embeddings(model, model_version);",
        ]

        try:
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(q)
                # Attempt HNSW cosine index creation
                try:
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_mem_emb_cosine ON memory_embeddings 
                        USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
                    """)
                except Exception as idx_e:
                    # Index may fail if table is empty or resources low; non-fatal
                    print(f"[PGVECTOR NOTE] HNSW index creation note: {idx_e}")

            conn.commit()
            self._initialized = True
            return True
        except Exception as e:
            conn.rollback()
            print(f"[PGVECTOR ERROR] Failed to initialize schema: {e}")
            return False
        finally:
            postgres_manager.release_connection(conn)

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------
    def store_embedding(
        self,
        memory_id: str,
        embedding: Optional[List[float]] = None,
        model: str = "",
        model_version: str = "",
        content_hash: str = "",
        dimension: int = 384,
        generation: int = 1,
        vector: Optional[List[float]] = None,
    ) -> StoredVectorRecord:
        """
        Store embedding in PostgreSQL with atomic UPSERT semantics and generation check.
        Supports both embedding= and vector= keyword arguments.
        """
        if not self._pgvector_available:
            raise VectorStorageError(f"pgvector unavailable: {self._availability_reason}")

        actual_vector = embedding if embedding is not None else vector
        if actual_vector is None:
            raise VectorValidationError("Either embedding or vector must be provided.")

        clean_vector = validate_vector_for_storage(actual_vector, expected_dimension=dimension)
        clean_mid = memory_id.strip()
        clean_model = model.strip() if model else "default"
        clean_ver = model_version.strip() if model_version else "v1"
        clean_hash = content_hash.strip()

        # Format vector string for pgvector '[0.1, 0.2, ...]'
        vec_str = "[" + ",".join(f"{x:.8f}" for x in clean_vector) + "]"
        emb_id = f"emb_{clean_mid[:16]}_{hash((clean_mid, clean_model, clean_ver)) & 0xFFFFFFFF:08x}"

        conn = postgres_manager.get_connection()
        if not conn:
            raise VectorStorageError("PostgreSQL connection lost.")

        sql = """
            INSERT INTO memory_embeddings (
                embedding_id, memory_id, model, model_version, dimension,
                embedding, content_hash, generation, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s::vector, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (memory_id, model, model_version) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                content_hash = EXCLUDED.content_hash,
                generation = EXCLUDED.generation,
                dimension = EXCLUDED.dimension,
                updated_at = CURRENT_TIMESTAMP
            WHERE EXCLUDED.generation >= memory_embeddings.generation
            RETURNING created_at, updated_at, generation;
        """

        try:
            with conn.cursor() as cur:
                # 1. Monotonic Vector Generation Invariant: Inspect durable memory_vector_state
                cur.execute("""
                    SELECT max_generation, vector_present
                    FROM memory_vector_state
                    WHERE memory_id = %s
                    FOR UPDATE;
                """, (clean_mid,))
                state_row = cur.fetchone()

                if state_row is not None:
                    max_observed_gen = int(state_row[0])
                    if generation < max_observed_gen:
                        # Stale UPSERT strictly rejected by durable vector state!
                        # Physical insertion into memory_embeddings is PREVENTED.
                        conn.commit()
                        return StoredVectorRecord(
                            embedding_id=f"emb_stale_{clean_mid[:16]}",
                            memory_id=clean_mid,
                            model=clean_model,
                            model_version=clean_ver,
                            dimension=dimension,
                            embedding=clean_vector,
                            content_hash=clean_hash,
                            generation=generation,
                            backend=self.backend.value,
                        )

                # Update durable vector state: max_generation never decreases, vector_present becomes true
                cur.execute("""
                    INSERT INTO memory_vector_state (memory_id, max_generation, vector_present, updated_at)
                    VALUES (%s, %s, TRUE, CURRENT_TIMESTAMP)
                    ON CONFLICT (memory_id) DO UPDATE SET
                        max_generation = GREATEST(memory_vector_state.max_generation, EXCLUDED.max_generation),
                        vector_present = TRUE,
                        updated_at = CURRENT_TIMESTAMP;
                """, (clean_mid, generation))

                # 2. Insert or update memory_embeddings
                cur.execute(sql, (
                    emb_id, clean_mid, clean_model, clean_ver,
                    dimension, vec_str, clean_hash, generation
                ))
                row = cur.fetchone()
                if not row:
                    # Stale UPSERT was rejected by WHERE EXCLUDED.generation >= memory_embeddings.generation
                    cur.execute("""
                        SELECT created_at, updated_at, generation
                        FROM memory_embeddings
                        WHERE memory_id = %s AND model = %s AND model_version = %s;
                    """, (clean_mid, clean_model, clean_ver))
                    row = cur.fetchone()
                created_at = row[0].isoformat() if row else datetime.now(timezone.utc).isoformat()
                updated_at = row[1].isoformat() if row else datetime.now(timezone.utc).isoformat()
                persisted_gen = int(row[2]) if row and row[2] is not None else generation

            conn.commit()

            return StoredVectorRecord(
                embedding_id=emb_id,
                memory_id=clean_mid,
                model=clean_model,
                model_version=clean_ver,
                dimension=dimension,
                embedding=clean_vector,
                content_hash=clean_hash,
                generation=persisted_gen,
                created_at=created_at,
                updated_at=updated_at,
                backend=self.backend.value,
            )
        except Exception as e:
            conn.rollback()
            raise VectorStorageError(f"Failed to store embedding: {e}") from e
        finally:
            postgres_manager.release_connection(conn)

    def delete_embedding(
        self,
        memory_id: str,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
        generation: Optional[int] = None,
    ) -> bool:
        """
        Delete embedding for a memory_id.
        Durable tombstone update ensures stale UPSERTs cannot overwrite deletion.
        """
        if not self._pgvector_available:
            return False

        conn = postgres_manager.get_connection()
        if not conn:
            return False

        clean_mid = memory_id.strip()
        try:
            with conn.cursor() as cur:
                target_gen = generation
                if target_gen is None:
                    cur.execute("SELECT generation FROM memory_records WHERE memory_id = %s;", (clean_mid,))
                    grow = cur.fetchone()
                    target_gen = int(grow[0]) if grow and grow[0] is not None else 0

                # Monotonic tombstone update in memory_vector_state
                cur.execute("""
                    INSERT INTO memory_vector_state (memory_id, max_generation, vector_present, updated_at)
                    VALUES (%s, %s, FALSE, CURRENT_TIMESTAMP)
                    ON CONFLICT (memory_id) DO UPDATE SET
                        max_generation = GREATEST(memory_vector_state.max_generation, EXCLUDED.max_generation),
                        vector_present = FALSE,
                        updated_at = CURRENT_TIMESTAMP;
                """, (clean_mid, target_gen))

                if model is not None and model_version is not None:
                    cur.execute("""
                        DELETE FROM memory_embeddings
                        WHERE memory_id = %s AND model = %s AND model_version = %s;
                    """, (clean_mid, model.strip(), model_version.strip()))
                else:
                    cur.execute("""
                        DELETE FROM memory_embeddings
                        WHERE memory_id = %s;
                    """, (clean_mid,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except Exception as e:
            conn.rollback()
            print(f"[PGVECTOR ERROR] Delete failed: {e}")
            return False
        finally:
            postgres_manager.release_connection(conn)

    def get_max_generation(self, memory_id: str) -> int:
        """Return the highest generation observed for memory_id from memory_vector_state."""
        conn = postgres_manager.get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT max_generation FROM memory_vector_state WHERE memory_id = %s;", (memory_id.strip(),))
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0
        finally:
            postgres_manager.release_connection(conn)

    def get_embedding(
        self,
        memory_id: str,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> Optional[StoredVectorRecord]:
        """Retrieve stored record by memory_id, model, and version."""
        if not self._pgvector_available:
            return None

        conn = postgres_manager.get_connection()
        if not conn:
            return None

        sql = """
            SELECT embedding_id, memory_id, model, model_version, dimension,
                   embedding::text, content_hash, created_at, updated_at, generation
            FROM memory_embeddings
            WHERE memory_id = %s AND model = %s AND model_version = %s;
        """

        try:
            with conn.cursor() as cur:
                cur.execute(sql, (memory_id.strip(), model.strip(), model_version.strip()))
                row = cur.fetchone()
                if not row:
                    return None

                # Parse pgvector string '[0.1,0.2,...]'
                raw_vec_str = row[5].strip("[]")
                parsed_vec = [float(x) for x in raw_vec_str.split(",") if x]
                row_gen = int(row[9]) if len(row) > 9 and row[9] is not None else 1

                return StoredVectorRecord(
                    embedding_id=row[0],
                    memory_id=row[1],
                    model=row[2],
                    model_version=row[3],
                    dimension=row[4],
                    embedding=parsed_vec,
                    content_hash=row[6],
                    generation=row_gen,
                    created_at=row[7].isoformat() if row[7] else "",
                    updated_at=row[8].isoformat() if row[8] else "",
                    backend=self.backend.value,
                )
        except Exception as e:
            print(f"[PGVECTOR ERROR] get_embedding failed: {e}")
            return None
        finally:
            postgres_manager.release_connection(conn)

    def has_embedding(
        self,
        memory_id: str,
        model: str,
        model_version: str,
    ) -> bool:
        """Check if embedding exists in PostgreSQL."""
        if not self._pgvector_available:
            return False

        conn = postgres_manager.get_connection()
        if not conn:
            return False

        sql = "SELECT 1 FROM memory_embeddings WHERE memory_id = %s AND model = %s AND model_version = %s;"
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (memory_id.strip(), model.strip(), model_version.strip()))
                return cur.fetchone() is not None
        except Exception:
            return False
        finally:
            postgres_manager.release_connection(conn)

    # ------------------------------------------------------------------
    # Similarity Search
    # ------------------------------------------------------------------
    def search_similar(
        self,
        query_vector: List[float],
        top_k: int = 5,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[VectorSearchResult]:
        """
        Execute cosine similarity search via pgvector operator (<=>).
        """
        if not self._pgvector_available:
            return []

        clean_vector = validate_vector_for_storage(query_vector, expected_dimension=self._dimension)
        vec_str = "[" + ",".join(f"{x:.8f}" for x in clean_vector) + "]"

        conn = postgres_manager.get_connection()
        if not conn:
            return []

        conditions = []
        params: List[Any] = []

        if model:
            conditions.append("model = %s")
            params.append(model.strip())
        if model_version:
            conditions.append("model_version = %s")
            params.append(model_version.strip())

        where_clause = " AND ".join(conditions) if conditions else "TRUE"

        # Cosine distance: (embedding <=> query)
        # Cosine similarity: 1.0 - (embedding <=> query)
        sql = f"""
            SELECT memory_id, model, model_version, content_hash,
                   (1.0 - (embedding <=> %s::vector)) AS similarity,
                   (embedding <=> %s::vector) AS distance
            FROM memory_embeddings
            WHERE {where_clause}
            ORDER BY embedding <=> %s::vector ASC
            LIMIT %s;
        """
        params_full = [vec_str, vec_str] + params + [vec_str, top_k]

        try:
            with conn.cursor() as cur:
                cur.execute(sql, params_full)
                rows = cur.fetchall()
                results = []
                for r in rows:
                    results.append(
                        VectorSearchResult(
                            memory_id=r[0],
                            model=r[1],
                            model_version=r[2],
                            content_hash=r[3],
                            similarity=float(r[4]),
                            distance=float(r[5]),
                        )
                    )
                return results
        except Exception as e:
            print(f"[PGVECTOR ERROR] search_similar failed: {e}")
            return []
        finally:
            postgres_manager.release_connection(conn)

    def count(self, model: Optional[str] = None) -> int:
        """Count rows in memory_embeddings."""
        if not self._pgvector_available:
            return 0

        conn = postgres_manager.get_connection()
        if not conn:
            return 0

        sql = "SELECT COUNT(*) FROM memory_embeddings"
        params = ()
        if model:
            sql += " WHERE model = %s"
            params = (model.strip(),)

        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0
        finally:
            postgres_manager.release_connection(conn)

    def health_check(self) -> Dict[str, Any]:
        """Return pgvector status and health metadata."""
        is_avail, reason = self.check_pgvector_available()
        return {
            "status": "HEALTHY" if is_avail else "UNAVAILABLE",
            "backend": self.backend.value,
            "pgvector_available": is_avail,
            "reason": reason,
            "dimension": self._dimension,
            "table_initialized": self._initialized,
        }
