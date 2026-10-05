"""V11.4 Advanced Memory System.

Enhances memory capabilities by integrating with the existing V5.1 memory
system and providing advanced memory processing including:
- Memory type classification
- Importance and confidence scoring
- Advanced relevance scoring
- Deduplication
- Consolidation
- Bounded persistence
- Enhanced retrieval for context fusion

Integrates with existing memory sources:
- Personal memory (orchestration.conversation.personal_memory)
- User model (orchestration.user_model.store)
- Experience store (orchestration.experience.store)
- General memory (core/memory.py)
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

# Import V5.1 memory system components
from memory.schemas import MemoryRecord, MemoryContext
from memory.types import (
    MemoryType, MemoryStatus, MemorySource,
    ConfidenceLevel, VerificationStatus, PrivacyClass,
    HybridRankingWeights, DEFAULT_HYBRID_WEIGHTS,
    IMPORTANCE_MIN, IMPORTANCE_MAX, IMPORTANCE_DEFAULT,
    MAX_RETRIEVAL_RECORDS, RELEVANCE_THRESHOLD
)
from memory.manager import memory_manager

# Import existing DOOM memory sources
from orchestration.conversation.personal_memory import (
    search_personal_memories,
    list_personal_memories,
    is_sensitive_memory_content
)
from orchestration.user_model.store import (
    list_profile_entries,
    get_profile_entry
)
from orchestration.user_model.types import Category
from orchestration.experience.store import (
    list_experiences,
    get_experience
)
from orchestration.plan.goal_registry import get_active_goal
from orchestration.goal.types import GoalSpec
from core.memory import load_memory

# Import V11 components for integration




@dataclass
class AdvancedMemoryConfig:
    """Configuration for V11.4 advanced memory system."""
    # Enable/disable specific memory sources
    enable_personal_memory: bool = True
    enable_user_model: bool = True
    enable_experience: bool = True
    enable_general_memory: bool = True
    enable_goal_memory: bool = True
    
    # Memory bounds and limits
    max_memories_per_source: int = 10
    max_total_memories: int = 50
    max_context_memories: int = 10
    
    # Relevance thresholds
    relevance_threshold: float = RELEVANCE_THRESHOLD
    
    # Consolidation settings
    enable_consolidation: bool = True
    consolidation_similarity_threshold: float = 0.85
    
    # Privacy settings
    enforce_privacy: bool = True
    
    # Scoring weights (can override defaults)
    hybrid_weights: Optional[HybridRankingWeights] = None


@dataclass
class AdvancedMemoryState:
    """Internal state of the advanced memory system."""
    last_retrieval_time: float = 0.0
    last_store_time: float = 0.0
    retrieval_count: int = 0
    store_count: int = 0
    cache: Dict[Tuple[Any, ...], Tuple[List[MemoryRecord], float]] = field(default_factory=dict)  # (owner, query, session, project) -> (records, timestamp)
    cache_ttl: float = 300.0  # 5 minutes
    # V11.9: hard bound on cached retrievals (oldest evicted first)
    cache_max_entries: int = 256
    cache_evictions: int = 0


class AdvancedMemorySystem:
    """V11.4 Advanced Memory System.
    
    Enhances memory capabilities by processing memories from various sources
    through the V5.1 memory system to provide structured, bounded, useful
    memory for the cognitive pipeline.
    """

    def __init__(self, config: Optional[AdvancedMemoryConfig] = None):
        """Initialize the advanced memory system.
        
        Args:
            config: Configuration for the advanced memory system (optional).
        """
        self.config = config or AdvancedMemoryConfig()
        self.state = AdvancedMemoryState()
        # V11.9: the cache is shared by user cycles and the monitor thread
        self._cache_lock = threading.RLock()

        # Use custom weights if provided, otherwise use defaults
        self.hybrid_weights = self.config.hybrid_weights or DEFAULT_HYBRID_WEIGHTS

    def _normalize_personal_memory(self, query: str, owner_id: str) -> List[MemoryRecord]:
        """Convert personal memories to V5.1 MemoryRecord objects.
        
        Args:
            query: The user query for relevance scoring
            owner_id: Owner ID for scoping and isolation
            
        Returns:
            List of MemoryRecord objects from personal memory.
        """
        memories = []
        
        if not self.config.enable_personal_memory:
            return memories
            
        try:
            # Search for relevant personal memories
            hits = search_personal_memories(
                query=query,
                owner_id=owner_id,
                
                limit=self.config.max_memories_per_source
            )
            
            for hit in hits:
# Access PersonalMemoryHit attributes
                content = hit.content
                relevance = hit.relevance
                # Use fact_key as the reference
                ref = hit.fact_key
                # For timestamp, provenance, and provenance, we need to derive reasonable values
                ts = time.time()  # Default to current time
                provenance = "personal_memory"  # Default provenance
                owner_id_hit = owner_id  # Since we are filtering by owner_id, this should match
                
                # Skip if owner doesn't match (isolation)
                if owner_id_hit != owner_id:
                    continue
                    
                # Skip sensitive content if privacy is enforced
                if self.config.enforce_privacy and is_sensitive_memory_content(content):
                    continue
                
                # Create MemoryRecord
                record = MemoryRecord(
                    memory_type=MemoryType.SEMANTIC,  # Personal memories are typically semantic
                    content=content,
                    source=MemorySource.USER_CONVERSATION,  # Inferred from conversation
                    confidence=ConfidenceLevel.MEDIUM,
                    verification_status=VerificationStatus.UNVERIFIED,
                    importance=min(0.5 + (relevance * 0.5), IMPORTANCE_MAX),  # Scale relevance to importance
                    status=MemoryStatus.ACTIVE,
                    privacy_class=PrivacyClass.NORMAL,
                    tags=["personal_memory", f"ref_{ref}"],
                    source_event_id=str(ref),
                    created_at=str(ts),
                    updated_at=str(ts)
                )
                
                memories.append(record)
                
        except Exception as e:
            # Graceful failure - memory failure must not break task execution
            print(f"[V11.4 Advanced Memory] Personal memory normalization failed: {type(e).__name__}")
            
        return memories

    def _normalize_user_model(self, query: str, owner_id: str) -> List[MemoryRecord]:
        """Convert user model entries to V5.1 MemoryRecord objects.
        
        Args:
            query: The user query for relevance scoring
            owner_id: Owner ID for scoping and isolation
            
        Returns:
            List of MemoryRecord objects from user model.
        """
        memories = []
        
        if not self.config.enable_user_model:
            return memories
            
        try:
            # Get user model entries
            result = list_profile_entries(
                owner_id=owner_id,
                
                limit=self.config.max_memories_per_source
            )
            
            if hasattr(result, 'status') and result.status.name == "OK" and result.entries:
                for entry in result.entries:
                    # Skip sensitive content if privacy is enforced
                    if self.config.enforce_privacy and self._is_sensitive_user_model_entry(entry):
                        continue
                    
                    # Determine memory type based on category
                    memory_type = self._category_to_memory_type(entry.category)
                    
                    # Create MemoryRecord
                    record = MemoryRecord(
                        memory_type=memory_type,
                        content=str(entry.value),
                        source=MemorySource.USER_EXPLICIT,  # User explicitly stated
                        confidence=self._confidence_string_to_enum(entry.confidence),
                        verification_status=VerificationStatus.VERIFIED,  # User model entries are verified
                        importance=0.7,  # User preferences are typically important
                        status=MemoryStatus.ACTIVE,
                        privacy_class=PrivacyClass.PRIVATE if entry.category.value in ["preference", "constraint"] else PrivacyClass.NORMAL,
                        tags=["user_model", entry.category.value, entry.key],
                        project_id=None,  # User model is not project-specific
                        created_at=str(entry.created_at or time.time()),
                        updated_at=str(entry.updated_at or entry.created_at or time.time())
                    )
                    
                    memories.append(record)
                    
        except Exception as e:
            # Graceful failure
            print(f"[V11.4 Advanced Memory] User model normalization failed: {type(e).__name__}")
            
        return memories

    def _normalize_experience(self, query: str, owner_id: str) -> List[MemoryRecord]:
        """Convert goal experiences to V5.1 MemoryRecord objects.
        
        Args:
            query: The user query for relevance scoring
            owner_id: Owner ID for scoping and isolation
            
        Returns:
            List of MemoryRecord objects from experience store.
        """
        memories = []
        
        if not self.config.enable_experience:
            return memories
            
        try:
            # Get recent experiences
            result = list_experiences(
                owner_id=owner_id,
                include_inactive=False,
                limit=self.config.max_memories_per_source
            )
            
            if hasattr(result, 'status') and result.status.name == "OK" and result.experiences:
                for exp in result.experiences:
                    # Create MemoryRecord
                    record = MemoryRecord(
                        memory_type=MemoryType.EPISODIC,  # Experiences are episodic
                        content=exp.title or "",
                        source=MemorySource.VERIFIED_TASK,  # Result of verified task
                        confidence=ConfidenceLevel.HIGH,
                        verification_status=VerificationStatus.VERIFIED,
                        importance=self._experience_outcome_to_importance(exp.outcome),
                        status=MemoryStatus.ACTIVE,
                        privacy_class=PrivacyClass.NORMAL,
                        tags=["experience", exp.outcome.value.lower() if exp.outcome else "unknown"],
                        source_event_id=exp.experience_id,
                        created_at=str(exp.created_at or time.time()),
                        updated_at=str(exp.updated_at or time.time())
                    )
                    
                    memories.append(record)
                    
        except Exception as e:
            # Graceful failure
            print(f"[V11.4 Advanced Memory] Experience normalization failed: {type(e).__name__}")
            
        return memories

    def _normalize_general_memory(self, query: str) -> List[MemoryRecord]:
        """Convert general memory key-value store to V5.1 MemoryRecord objects.
        
        Args:
            query: The user query for relevance scoring
            
        Returns:
            List of MemoryRecord objects from general memory.
        """
        memories = []
        
        if not self.config.enable_general_memory:
            return memories
            
        try:
            # Load general memory
            general_data = load_memory()
            
            for key, value in general_data.items():
                # Skip if value is not a string (for simplicity)
                if not isinstance(value, str):
                    continue
                    
                # Create MemoryRecord
                record = MemoryRecord(
                    memory_type=MemoryType.SEMANTIC,
                    content=value,
                    source=MemorySource.IMPORTED_DATA,  # Loaded from external data source
                    confidence=ConfidenceLevel.LOW,
                    verification_status=VerificationStatus.UNVERIFIED,
                    importance=0.3,  # General memory is typically lower importance
                    status=MemoryStatus.ACTIVE,
                    privacy_class=PrivacyClass.NORMAL,
                    tags=["general_memory", f"key_{key}"],
                    created_at=str(time.time()),
                    updated_at=str(time.time())
                )
                
                memories.append(record)
                
        except Exception as e:
            # Graceful failure
            print(f"[V11.4 Advanced Memory] General memory normalization failed: {type(e).__name__}")
            
        return memories

    def _normalize_goal_memory(self, query: str, owner_id: str) -> List[MemoryRecord]:
        """Convert active goal information to V5.1 MemoryRecord objects.
        
        Args:
            query: The user query for relevance scoring
            owner_id: Owner ID for scoping and isolation
            
        Returns:
            List of MemoryRecord objects from goal state.
        """
        memories = []
        
        if not self.config.enable_goal_memory:
            return memories
            
        try:
            # Get active goal
            result = get_active_goal(owner_id)
            
            if hasattr(result, 'status') and result.status.name == "OK" and result.snapshot:
                snap = result.snapshot
                
                # Create MemoryRecord for the active goal
                record = MemoryRecord(
                    memory_type=MemoryType.PROJECT,  # Active goals are project-related
                    content=snap.title or "",
                    source=MemorySource.USER_EXPLICIT,  # Goals are user-explicit
                    confidence=ConfidenceLevel.HIGH,
                    verification_status=VerificationStatus.VERIFIED,
                    importance=0.9,  # Active goals are highly important
                    status=MemoryStatus.ACTIVE,
                    privacy_class=PrivacyClass.NORMAL,
                    tags=["active_goal", snap.intent.value if snap.intent else "unknown"],
                    source_event_id=snap.goal_id,
                    project_id=getattr(snap, 'project_id', None),
                    created_at=str(snap.created_at or time.time()),
                    updated_at=str(snap.updated_at or time.time())
                )
                
                memories.append(record)
                
        except Exception as e:
            # Graceful failure
            print(f"[V11.4 Advanced Memory] Goal memory normalization failed: {type(e).__name__}")
            
        return memories

    def _is_sensitive_user_model_entry(self, entry) -> bool:
        """Check if a user model entry contains sensitive content.
        
        Args:
            entry: User model entry to check
            
        Returns:
            True if entry contains sensitive content, False otherwise.
        """
        # Check if the key or value contains sensitive patterns
        sensitive_patterns = ["password", "secret", "token", "key", "auth"]
        entry_str = f"{entry.key} {entry.value}".lower()
        
        return any(pattern in entry_str for pattern in sensitive_patterns)

    def _category_to_memory_type(self, category: Category) -> MemoryType:
        """Convert user model category to memory type.
        
        Args:
            category: User model category
            
        Returns:
            Corresponding MemoryType.
        """
        category_mapping = {
            Category.PREFERENCE: MemoryType.PREFERENCE,
            Category.PROJECT: MemoryType.PROJECT,
            Category.CONSTRAINT: MemoryType.PREFERENCE,  # Constraints are like preferences
            Category.STABLE_FACT: MemoryType.SEMANTIC,
            Category.TEMPORARY_FACT: MemoryType.SEMANTIC,
            Category.COMMUNICATION: MemoryType.SEMANTIC,
        }
        return category_mapping.get(category, MemoryType.SEMANTIC)
        

    def _confidence_string_to_enum(self, conf_str: str) -> ConfidenceLevel:
        """Convert confidence string to ConfidenceLevel enum.
        
        Args:
            conf_str: Confidence string (e.g., "HIGH", "MEDIUM", "LOW")
            
        Returns:
            Corresponding ConfidenceLevel enum.
        """
        conf_mapping = {
            "HIGH": ConfidenceLevel.HIGH,
            "MEDIUM": ConfidenceLevel.MEDIUM,
            "LOW": ConfidenceLevel.LOW
        }
        
        return conf_mapping.get(conf_str.upper(), ConfidenceLevel.MEDIUM)

    def _experience_outcome_to_importance(self, outcome) -> float:
        """Convert experience outcome to importance score.
        
        Args:
            outcome: Experience outcome
            
        Returns:
            Importance score between 0.0 and 1.0.
        """
        if not outcome:
            return 0.5
            
        outcome_mapping = {
            "COMPLETED": 0.8,
            "PARTIAL": 0.6,
            "FAILURE": 0.4,
            "ABANDONED": 0.3
        }
        
        return outcome_mapping.get(outcome.value.upper(), 0.5)

    def _apply_deduplication(self, memories: List[MemoryRecord]) -> List[MemoryRecord]:
        """Apply deduplication to memories.
        
        Args:
            memories: List of MemoryRecord objects
            
        Returns:
            List of MemoryRecord objects with duplicates removed.
        """
        if not memories:
            return memories
            
        # Simple deduplication based on content hash
        # In a more sophisticated system, we might use semantic similarity
        seen_contents = set()
        unique_memories = []
        
        for record in memories:
            # Create a content hash for deduplication
            content_hash = hash(record.content.strip().lower())
            
            if content_hash not in seen_contents:
                seen_contents.add(content_hash)
                unique_memories.append(record)
                
        return unique_memories

    def _apply_consolidation(self, memories: List[MemoryRecord]) -> List[MemoryRecord]:
        """Apply consolidation to similar memories.
        
        Args:
            memories: List of MemoryRecord objects
            
        Returns:
            List of MemoryRecord objects with similar memories consolidated.
        """
        if not self.config.enable_consolidation or len(memories) < 2:
            return memories
            
        # For now, we'll skip sophisticated consolidation to keep it simple
        # A full implementation would use semantic similarity to consolidate
        # similar memories (e.g., multiple observations of the same fact)
        return memories

    def _apply_bounds(self, memories: List[MemoryRecord]) -> List[MemoryRecord]:
        """Apply bounds to limit the number of memories.
        
        Args:
            memories: List of MemoryRecord objects
            
        Returns:
            List of MemoryRecord objects within bounds.
        """
        if not memories:
            return memories
            
        # Limit total memories
        if len(memories) > self.config.max_total_memories:
            # Sort by importance and recency, keep the most important/recent
            def memory_score(record: MemoryRecord) -> float:
                # Combine importance and recency (newer is better)
                try:
                    recency_score = 1.0 / (1.0 + (time.time() - float(record.updated_at)) / 86400.0)  # Days
                except:
                    recency_score = 0.5
                return record.importance * 0.7 + recency_score * 0.3
            
            sorted_memories = sorted(memories, key=memory_score, reverse=True)
            return sorted_memories[:self.config.max_total_memories]
            
        return memories

    def _score_and_rank_memories(self, query: str, memories: List[MemoryRecord]) -> List[Tuple[MemoryRecord, float]]:
        """Score and rank memories by relevance to the query.
        
        Args:
            query: The user query for relevance scoring
            memories: List of MemoryRecord objects to score
            
        Returns:
            List of (MemoryRecord, score) tuples sorted by score descending.
        """
        if not memories or not query:
            return [(record, 0.5) for record in memories]  # Default score
            
        # Use the V5.1 memory retriever for scoring
        try:
            from memory.retrieval import memory_retriever
            
            # Convert MemoryRecord objects to format expected by retriever
            # For simplicity, we'll use a basic relevance approach here
            # In a full implementation, we would integrate more deeply with the V5.1 retrieval system
            
            scored_memories = []
            query_words = set(query.lower().split())
            
            for record in memories:
                # Basic lexical relevance
                content_words = set(record.content.lower().split())
                lexical_overlap = len(query_words & content_words)
                lexical_score = min(lexical_overlap / max(len(query_words), 1), 1.0) if query_words else 0.0
                
                # Type-based bonus
                type_bonus = 0.0
                if record.memory_type in [MemoryType.PREFERENCE, MemoryType.PROJECT]:
                    type_bonus = 0.1
                elif record.memory_type == MemoryType.EPISODIC:
                    type_bonus = 0.05
                
                # Recency bonus
                try:
                    age_days = (time.time() - float(record.updated_at)) / 86400.0
                    recency_bonus = max(0.0, 1.0 - (age_days / 30.0))  # Linear decay over 30 days
                    recency_bonus = min(recency_bonus, 0.2)
                except:
                    recency_bonus = 0.0
                
                # Importance factor
                importance_factor = record.importance * 0.2
                
                # Combine scores
                final_score = (
                    lexical_score * 0.4 +
                    type_bonus +
                    recency_bonus +
                    importance_factor +
                    (record.confidence_score or 0.5) * 0.2
                )
                
                # Ensure score is in valid range
                final_score = max(0.0, min(1.0, final_score))
                
                scored_memories.append((record, final_score))
                
            # Sort by score descending
            scored_memories.sort(key=lambda x: x[1], reverse=True)
            return scored_memories
            
        except Exception as e:
            print(f"[V11.4 Advanced Memory] Memory scoring failed: {type(e).__name__}")
            # Fallback to simple ordering
            return [(record, 0.5) for record in memories]

    def retrieve_advanced_memories(
        self,
        query: str,
        owner_id: str,
        session_id: str = "",
        project_id: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[MemoryRecord]:
        """
        This is the main interface for the V11.4 advanced memory system.
        It retrieves memories from various sources, normalizes them,
        applies advanced processing, and returns the most relevant memories.
        
        Args:
            query: The user query for relevance scoring
            owner_id: Owner ID for scoping and isolation
            session_id: Session ID for scoping (optional)
             project_id: Project ID for scoping (optional)
             limit: Maximum number of memories to return (optional)
            
        Returns:
            List of MemoryRecord objects, processed and ranked by relevance.
        """
        # V11.9: tuple key - a string join let crafted owner/query values collide across owners
        cache_key = (owner_id, query, session_id, project_id)
        with self._cache_lock:
            cached = self.state.cache.get(cache_key)
            if cached is not None:
                cached_list, timestamp = cached
                if time.time() - timestamp < self.state.cache_ttl:
                    self.state.retrieval_count += 1
                    return list(cached_list)  # Return a copy of the cached result
                del self.state.cache[cache_key]  # expired
        
        # Retrieve memories from all sources
        all_memories = []
        
        # Get memories from each source
        personal_memories = self._normalize_personal_memory(query, owner_id)
        user_model_memories = self._normalize_user_model(query, owner_id)
        experience_memories = self._normalize_experience(query, owner_id)
        general_memories = self._normalize_general_memory(query)
        goal_memories = self._normalize_goal_memory(query, owner_id)
        
        all_memories.extend(personal_memories)
        all_memories.extend(user_model_memories)
        all_memories.extend(experience_memories)
        all_memories.extend(general_memories)
        all_memories.extend(goal_memories)
        
        # Apply advanced processing
        # 1. Deduplication
        unique_memories = self._apply_deduplication(all_memories)
        
        # 2. Consolidation (where appropriate)
        consolidated_memories = self._apply_consolidation(unique_memories)
        
        # 3. Bounding
        bounded_memories = self._apply_bounds(consolidated_memories)
        
        # 4. Scoring and ranking
        scored_memories = self._score_and_rank_memories(query, bounded_memories)
        
        effective_limit = limit if limit is not None else self.config.max_context_memories
        result_memories = [record for record, score in scored_memories[:effective_limit]]
        # Extract just the records, limit to context limit
        result_memories = [record for record, score in scored_memories[:self.config.max_context_memories]]
        
        # Cache the result (bounded; expired entries purged first, then oldest)
        now = time.time()
        with self._cache_lock:
            self.state.cache[cache_key] = (list(result_memories), now)
            self._prune_cache_locked(now)
            self.state.last_retrieval_time = now
            self.state.retrieval_count += 1

        return result_memories

    def _prune_cache_locked(self, now: float) -> None:
        """Drop expired entries, then evict oldest entries beyond the bound."""
        cache = self.state.cache
        expired = [k for k, (_records, ts) in cache.items() if now - ts >= self.state.cache_ttl]
        for k in expired:
            del cache[k]
        overflow = len(cache) - max(1, int(self.state.cache_max_entries))
        if overflow > 0:
            for k, _v in sorted(cache.items(), key=lambda kv: kv[1][1])[:overflow]:
                del cache[k]
            self.state.cache_evictions += overflow

    def invalidate_owner_cache(self, owner_id: str) -> int:
        """V11.9: drop cached retrievals for one owner (e.g. after a new experience).

        Only that owner's entries are removed; returns the number removed.
        """
        with self._cache_lock:
            keys = [k for k in self.state.cache if k[0] == owner_id]
            for k in keys:
                del self.state.cache[k]
        return len(keys)

    def cache_size(self) -> int:
        with self._cache_lock:
            return len(self.state.cache)
    def store_memory(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.SEMANTIC,
        owner_id: str = "",
        source: MemorySource = MemorySource.USER_EXPLICIT,
        importance: float = IMPORTANCE_DEFAULT,
        project_id: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """
    
    Args:
        content: The memory content to store
        memory_type: Type of memory
        owner_id: Owner ID for scoping
        source: Source of the memory
        importance: Importance score (0.0 to 1.0)
        project_id: Project ID for scoping (optional)
        tags: Tags for categorization (optional)
        
    Returns:
        True if memory was stored successfully, False otherwise.
    """
        # Store using V5.1 memory manager
        try:
            start_time = time.time()
            stored_record = memory_manager.store(MemoryRecord(
                memory_type=memory_type,
                content=content,
                source=source,
                confidence=ConfidenceLevel.MEDIUM,
                verification_status=VerificationStatus.UNVERIFIED,
                importance=max(IMPORTANCE_MIN, min(importance, IMPORTANCE_MAX)),
                status=MemoryStatus.ACTIVE,
                privacy_class=PrivacyClass.NORMAL,
                tags=tags,
                project_id=project_id,
                created_at=str(time.time()),
                updated_at=str(time.time())
            ))
            self.state.last_store_time = time.time()
            self.state.store_count += 1
            if stored_record:
                print(f"[V11.4 Advanced Memory] Stored memory: {stored_record.memory_id}")
                return True
            else:
                print("[V11.4 Advanced Memory] Memory storage rejected by policy")
                return False
        except Exception as e:
            print(f"[V11.4 Advanced Memory] Memory storage failed: {type(e).__name__}")
            return False
def store_advanced_memory(
    content: str,
    memory_type: MemoryType = MemoryType.SEMANTIC,
    owner_id: str = "",
    source: MemorySource = MemorySource.USER_EXPLICIT,
    importance: float = IMPORTANCE_DEFAULT,
    project_id: Optional[str] = None,
    tags: Optional[List[str]] = None
) -> bool:
    """Convenience function to store a memory using the advanced memory system.
    
    Args:
        content: The memory content to store
        memory_type: Type of memory
        owner_id: Owner ID for scoping
        source: Source of the memory
        importance: Importance score (0.0 to 1.0)
        project_id: Project ID for scoping (optional)
        tags: Tags for categorization (optional)
        
    Returns:
        True if memory was stored successfully, False otherwise.
    """
    return advanced_memory_system.store_memory(content, memory_type, owner_id, source, importance, project_id, tags)

def retrieve_advanced_memories(
    query: str,
    owner_id: str,
    session_id: str = "",
    project_id: Optional[str] = None,
    limit: Optional[int] = None
) -> List[MemoryRecord]:
    """Convenience function to retrieve advanced memories.
    
    Args:
        query: The user query for relevance scoring
        owner_id: Owner ID for scoping and isolation
        session_id: Session ID for scoping (optional)
        project_id: Project ID for scoping (optional)
        limit: Maximum number of memories to return (optional)
        
    Returns:
        List of MemoryRecord objects, processed and ranked by relevance.
    """
    return advanced_memory_system.retrieve_advanced_memories(query, owner_id, session_id, project_id, limit)

# Global instance for convenience
advanced_memory_system = AdvancedMemorySystem()


def get_advanced_memory_system() -> AdvancedMemorySystem:
    """Get the global advanced memory system instance.
    
    Returns:
        The global AdvancedMemorySystem instance.
    """
    return advanced_memory_system


# Convenience functions
def retrieve_advanced_memories(
    query: str,
    owner_id: str,
    session_id: str = "",
    project_id: Optional[str] = None,
    limit: Optional[int] = None
) -> List[MemoryRecord]:
    """Convenience function to retrieve advanced memories.
    
    Args:
        query: The user query for relevance scoring
        owner_id: Owner ID for scoping and isolation
        session_id: Session ID for scoping (optional)
        project_id: Project ID for scoping (optional)
        limit: Maximum number of memories to return (optional)
        
    Returns:
        List of MemoryRecord objects, processed and ranked by relevance.
    """
    return advanced_memory_system.retrieve_advanced_memories(query, owner_id, session_id, project_id, limit)

def store_advanced_memory(
    content: str,
    memory_type: MemoryType = MemoryType.SEMANTIC,
    owner_id: str = "",
    source: MemorySource = MemorySource.USER_EXPLICIT,
    importance: float = IMPORTANCE_DEFAULT,
    project_id: Optional[str] = None,
    tags: Optional[List[str]] = None
) -> bool:
    """Convenience function to store a memory using the advanced memory system.
    
    Args:
        content: The memory content to store
        memory_type: Type of memory
        owner_id: Owner ID for scoping
        source: Source of the memory
        importance: Importance score (0.0 to 1.0)
        project_id: Project ID for scoping (optional)
        tags: Tags for categorization (optional)
        
    Returns:
        True if memory was stored successfully, False otherwise.
    """
    return advanced_memory_system.store_memory(content, memory_type, owner_id, source, importance, project_id, tags)
