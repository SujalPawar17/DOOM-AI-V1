"""V11.5 Continuous Monitoring System.

Enhances the V11.3 Proactive Behavior System to provide continuous monitoring
of internal and external state, detecting meaningful changes, and initiating
cognitive cycles through the V11 cognitive orchestrator when warranted.

This system evolves DOOM from an agent that executes cognitive cycles on demand
into a reliable continuous-processing system that observes relevant state over
time, detects meaningful changes, evaluates whether action is warranted, and
safely routes those changes through the existing cognitive architecture.

All monitoring-triggered actions remain subject to the existing safety,
authorization, verification, and cost boundaries.
"""

from __future__ import annotations

import time
import threading
import hashlib
import json
from typing import Dict, Any, Optional, List, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum

from core.v11.cognitive_orchestrator import V11CognitiveOrchestrator, v11_cognitive_orchestrator
from core.v11.experience_integration import ExperienceResult, ExperienceResultStatus
from orchestration.experience.store import list_experiences, ExperienceResult as ExpListResult
from orchestration.user_model.store import get_profile_entry, ProfileResult, list_profile_entries
from core.verifier import verifier
from core.cost_guard.guard import cost_guard
from orchestration.experience.types import GoalExperience, ExperienceStatus
from orchestration.user_model.types import ProfileEntry, ProfileStatus
from orchestration.plan.goal_registry import get_active_goal, RegistryResult
from core.v11.advanced_memory import AdvancedMemorySystem, advanced_memory_system, MemoryRecord, MemoryType


class MonitoringEventType(Enum):
    """Types of monitoring events that can be detected."""
    STATE_CHANGE = "state_change"
    GOAL_STALE = "goal_stale"
    USER_MODEL_UPDATE = "user_model_update"
    EXPERIENCE_OUTCOME = "experience_outcome"
    SYSTEM_HEALTH = "system_health"
    MEMORY_PRESSURE = "memory_pressure"
    QUESTION_UNRESOLVED = "question_unresolved"
    PLAN_PENDING = "plan_pending"
    RESOURCE_CONSTRAINT = "resource_constraint"


class MonitoringPriority(Enum):
    """Priority levels for monitoring events."""
    INFO = 1
    LOW = 2
    NORMAL = 3
    HIGH = 4
    CRITICAL = 5


@dataclass
class MonitoringEvent:
    """Represents a detected monitoring event."""
    event_id: str
    event_type: MonitoringEventType
    priority: MonitoringPriority
    owner_id: str
    session_id: str
    timestamp: float
    source: str
    description: str
    previous_state: Optional[Any] = None
    current_state: Optional[Any] = None
    state_fingerprint: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None


@dataclass
class ContinuousMonitoringConfig:
    """Configuration for continuous monitoring."""
    # Polling interval in seconds
    polling_interval: float = 10.0
    # Cooldown period after a monitoring-triggered cycle in seconds
    cooldown_period: float = 60.0  # 1 minute
    # Maximum number of monitoring-triggered cycles per hour
    max_cycles_per_hour: int = 30
    # Enable/disable specific monitoring sources
    enable_state_fingerprinting: bool = True
    enable_goal_monitoring: bool = True
    enable_user_model_monitoring: bool = True
    enable_experience_monitoring: bool = True
    enable_memory_monitoring: bool = True
    enable_system_monitoring: bool = True
    # Change detection thresholds
    significant_change_threshold: float = 0.7  # Similarity threshold for considering changes significant
    # History size for change detection
    history_size: int = 10
    # Enable integration with V11.4 advanced memory for monitoring state
    enable_advanced_memory_integration: bool = True
    # Maximum age of monitoring state in advanced memory (seconds)
    monitoring_state_ttl: float = 3600.0  # 1 hour


@dataclass
class ContinuousMonitoringState:
    """Internal state of the continuous monitoring system."""
    last_poll_time: float = 0.0
    last_cycle_time: float = 0.0
    cycles_in_last_hour: List[float] = field(default_factory=list)
    # State fingerprint history for change detection
    state_fingerprints: Dict[str, List[str]] = field(default_factory=lambda: {
        "goal_state": [],
        "user_model_state": [],
        "experience_state": [],
        "memory_state": [],
        "system_state": []
    })
    # Last known states for change detection
    last_known_states: Dict[str, Any] = field(default_factory=dict)
    # Event deduplication cache
    recent_event_fingerprints: Set[str] = field(default_factory=set)
    # Monitoring health metrics
    monitoring_errors: int = 0
    last_error_time: float = 0.0
    # Integration with V11.4 advanced memory
    advanced_memory_system: Optional[AdvancedMemorySystem] = None


class ContinuousMonitoringEnhancement:
    """Enhances the proactive behavior monitor with continuous monitoring capabilities."""

    def __init__(self, owner_id: str, session_id: str = "", config: Optional[ContinuousMonitoringConfig] = None):
        """Initialize the continuous monitoring enhancement.
        
        Args:
            owner_id: The owner ID for scoping and isolation.
            session_id: The session ID for scoping (optional).
            config: Configuration for monitoring (optional, uses defaults if not provided).
        """
        self.owner_id = owner_id
        self.session_id = session_id
        self.config = config or ContinuousMonitoringConfig()
        self.state = ContinuousMonitoringState()
        self.state.advanced_memory_system = advanced_memory_system if self.config.enable_advanced_memory_integration else None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()  # Reentrant lock for nested locking scenarios

    def start(self) -> None:
        """Start the continuous monitoring system in a background thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return  # Already running
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop the continuous monitoring system."""
        with self._lock:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=5.0)
                self._thread = None

    def _monitor_loop(self) -> None:
        """Main continuous monitoring loop."""
        while not self._stop_event.is_set():
            try:
                self._continuous_monitor_cycle()
                # Sleep for the polling interval, but check for stop event frequently
                for _ in range(int(self.config.polling_interval * 10)):  # Check 10 times per second
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.1)
            except Exception as e:
                # Log error but continue monitoring (failure containment)
                self._record_monitoring_error(e)
                time.sleep(min(5.0, self.config.polling_interval))  # Back off on error

    def _continuous_monitor_cycle(self) -> None:
        """Perform one cycle of continuous monitoring."""
        # Check if we're in cooldown or rate limited
        if not self._is_cooldown_complete() or not self._is_rate_limit_ok():
            return

        # Collect current state from all monitoring sources
        current_states = self._collect_all_states()
        
        # Detect meaningful changes
        events = self._detect_meaningful_changes(current_states)
        
        # Process any detected events
        for event in events:
            if self._should_process_event(event):
                self._handle_monitoring_event(event)

    def _collect_all_states(self) -> Dict[str, Any]:
        """Collect current state from all monitoring sources."""
        states = {}
        timestamp = time.time()
        
        try:
            # Goal state
            if self.config.enable_goal_monitoring:
                states["goal_state"] = self._get_goal_state()
            
            # User model state
            if self.config.enable_user_model_monitoring:
                states["user_model_state"] = self._get_user_model_state()
            
            # Experience state
            if self.config.enable_experience_monitoring:
                states["experience_state"] = self._get_experience_state()
            
            # Memory state
            if self.config.enable_memory_monitoring:
                states["memory_state"] = self._get_memory_state()
            
            # System state
            if self.config.enable_system_monitoring:
                states["system_state"] = self._get_system_state()
                
        except Exception as e:
            # Record error but continue with whatever states we managed to collect
            self._record_monitoring_error(e)
            
        # Add timestamp for change detection
        states["_timestamp"] = timestamp
        return states

    def _get_goal_state(self) -> Dict[str, Any]:
        """Get current goal state for monitoring."""
        try:
            result = get_active_goal(self.owner_id)
            if result.status == "OK" and result.snapshot:
                snap = result.snapshot
                return {
                    "goal_id": snap.goal_id,
                    "title": snap.title or "",
                    "intent": snap.intent.value if snap.intent else None,
                    "status": getattr(snap, 'status', None),
                    "progress": getattr(snap, 'progress', 0.0),
                    "updated_at": getattr(snap, 'updated_at', 0.0),
                    "created_at": getattr(snap, 'created_at', 0.0)
                }
            return {"no_active_goal": True}
        except Exception as e:
            self._record_monitoring_error(e)
            return {"error": str(e)}

    def _get_user_model_state(self) -> Dict[str, Any]:
        """Get current user model state for monitoring."""
        try:
            # Get entries in commonly monitored categories
            categories = ["preference", "project", "constraint", "fact", "skill", "relationship"]
            result = list_profile_entries(
                owner_id=self.owner_id,
                categories=categories,
                include_expired=False,
                limit=100  # Reasonable limit
            )
            
            if result.status == "OK" and result.entries:
                # Convert entries to a serializable format
                entries_data = []
                for entry in result.entries:
                    entries_data.append({
                        "category": entry.category.value,
                        "key": entry.key,
                        "value": str(entry.value)[:100],  # Truncate long values
                        "created_at": entry.created_at,
                        "confirmed_at": entry.confirmed_at,
                        "updated_at": entry.updated_at
                    })
                return {
                    "entries": entries_data,
                    "entry_count": len(entries_data),
                    "last_updated": max([e.get("updated_at", 0) for e in entries_data] or [0])
                }
            return {"entries": [], "entry_count": 0}
        except Exception as e:
            self._record_monitoring_error(e)
            return {"error": str(e)}

    def _get_experience_state(self) -> Dict[str, Any]:
        """Get current experience state for monitoring."""
        try:
            result = list_experiences(
                owner_id=self.owner_id,
                include_inactive=False,
                limit=50  # Recent experiences
            )
            
            if result.status == "OK" and result.experiences:
                # Convert experiences to a serializable format
                exp_data = []
                for exp in result.experiences:
                    exp_data.append({
                        "experience_id": exp.experience_id,
                        "title": exp.title or "",
                        "source_goal_id": exp.source_goal_id,
                        "outcome": exp.outcome.value if hasattr(exp.outcome, 'value') else str(exp.outcome),
                        "updated_at": exp.updated_at,
                        "created_at": exp.created_at,
                        "blocker_count": len(exp.blockers) if hasattr(exp, 'blockers') else 0,
                        "step_count": len(exp.steps) if hasattr(exp, 'steps') else 0
                    })
                return {
                    "experiences": exp_data,
                    "experience_count": len(exp_data),
                    "last_updated": max([e.get("updated_at", 0) for e in exp_data] or [0]),
                    "outcomes": [e.get("outcome") for e in exp_data if e.get("outcome")]
                }
            return {"experiences": [], "experience_count": 0}
        except Exception as e:
            self._record_monitoring_error(e)
            return {"error": str(e)}

    def _get_memory_state(self) -> Dict[str, Any]:
        """Get current memory system state for monitoring."""
        try:
            if self.state.advanced_memory_system:
                # Get basic stats from the advanced memory system
                # We avoid doing full retrievals to prevent performance impact
                return {
                    "system_available": True,
                    "last_retrieval_time": getattr(self.state.advanced_memory_system.state, 'last_retrieval_time', 0),
                    "retrieval_count": getattr(self.state.advanced_memory_system.state, 'retrieval_count', 0),
                    "store_count": getattr(self.state.advanced_memory_system.state, 'store_count', 0),
                    "cache_size": len(getattr(self.state.advanced_memory_system.state, 'cache', {}))
                }
            return {"system_available": False}
        except Exception as e:
            self._record_monitoring_error(e)
            return {"error": str(e)}

    def _get_system_state(self) -> Dict[str, Any]:
        """Get current system state for monitoring."""
        try:
            # Get basic system information that's safe to monitor
            return {
                "timestamp": time.time(),
                "monitoring_uptime": time.time() - self.state.last_poll_time if self.state.last_poll_time > 0 else 0,
                "error_count": self.state.monitoring_errors,
                "last_error_time": self.state.last_error_time
            }
        except Exception as e:
            self._record_monitoring_error(e)
            return {"error": str(e)}

    def _compute_state_fingerprint(self, state: Dict[str, Any]) -> str:
        """Compute a deterministic fingerprint of a state for change detection."""
        # Remove volatile fields that change frequently but don't represent meaningful state
        state_for_fingerprint = self._prepare_state_for_fingerprinting(state.copy())
        
        # Create a deterministic JSON representation
        try:
            # Sort keys to ensure deterministic ordering
            json_str = json.dumps(state_for_fingerprint, sort_keys=True, default=str)
        except (TypeError, ValueError):
            # Fallback if JSON serialization fails
            json_str = str(sorted(state_for_fingerprint.items()))
        
        # Create a hash of the state
        return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

    def _prepare_state_for_fingerprinting(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare state for fingerprinting by removing volatile/non-meaningful fields."""
        # Remove fields that change too frequently to be meaningful for change detection
        volatile_fields = {
            '_timestamp', 'timestamp', 'last_poll_time', 'last_cycle_time',
            'monitoring_uptime', 'error_count', 'last_error_time'
        }
        
        # Remove volatile fields
        for field in volatile_fields:
            state.pop(field, None)
             
        # For nested dictionaries, recursively clean them
        for key, value in list(state.items()):
            if isinstance(value, dict):
                state[key] = self._prepare_state_for_fingerprinting(value)
            elif isinstance(value, list):
                # For lists of dictionaries, clean each dictionary
                state[key] = [
                    self._prepare_state_for_fingerprinting(item) if isinstance(item, dict) else item
                    for item in value
                ]
                
        return state

    def _detect_meaningful_changes(self, current_states: Dict[str, Any]) -> List[MonitoringEvent]:
        """Detect meaningful changes between current and previous states."""
        events = []
        timestamp = time.time()
        
        if not self.config.enable_state_fingerprinting:
            # Fallback to original trigger-based approach if fingerprinting is disabled
            return self._detect_changes_legacy(current_states, timestamp)
        
        # Check each state type for changes
        state_types = ["goal_state", "user_model_state", "experience_state", "memory_state", "system_state"]
        
        for state_type in state_types:
            if state_type not in current_states:
                continue
                
            current_state = current_states[state_type]
            current_fingerprint = self._compute_state_fingerprint(current_state)
            
            # Get history of fingerprints for this state type
            fingerprint_history = self.state.state_fingerprints.get(state_type, [])
            
            # Check if this represents a meaningful change
            if self._is_meaningful_change(state_type, current_fingerprint, fingerprint_history):
                # Get previous state for comparison
                previous_state = self.state.last_known_states.get(state_type)
                
                # Create monitoring event
                event = self._create_monitoring_event(
                    event_type=self._state_type_to_event_type(state_type),
                    priority=self._determine_event_priority(state_type, current_state, previous_state),
                    source=state_type,
                    description=self._generate_change_description(state_type, current_state, previous_state),
                    current_state=current_state,
                    previous_state=previous_state,
                    state_fingerprint=current_fingerprint,
                    timestamp=timestamp
                )
                events.append(event)
                
                # Update history and last known state
                self.state.state_fingerprints[state_type].append(current_fingerprint)
                if len(self.state.state_fingerprints[state_type]) > self.config.history_size:
                    self.state.state_fingerprints[state_type].pop(0)  # Remove oldest
                    
                self.state.last_known_states[state_type] = current_state
        
        return events

    def _detect_changes_legacy(self, current_states: Dict[str, Any], timestamp: float) -> List[MonitoringEvent]:
        """Legacy change detection based on original V11.3 triggers (for backward compatibility)."""
        events = []
        
        # This maintains compatibility with the original V11.3 trigger system
        # We'll simulate the original triggers as events
        
        # Check for stale goals (legacy trigger)
        goal_state = current_states.get("goal_state", {})
        if goal_state and not goal_state.get("no_active_goal", False):
            updated_at = goal_state.get("updated_at", 0)
            if updated_at > 0:
                hours_stale = (timestamp - updated_at) / 3600.0
                if hours_stale >= 24.0:  # Default from original config
                    event = self._create_monitoring_event(
                        event_type=MonitoringEventType.GOAL_STALE,
                        priority=MonitoringPriority.NORMAL,
                        source="goal_state",
                        description=f"Goal stale for {hours_stale:.1f} hours: {goal_state.get('title', 'Unknown')}",
                        current_state=goal_state,
                        timestamp=timestamp
                    )
                    events.append(event)
        
        # Check for unresolved questions/PARTIAL outcomes (legacy trigger)
        experience_state = current_states.get("experience_state", {})
        if experience_state:
            experiences = experience_state.get("experiences", [])
            for exp in experiences:
                if exp.get("outcome") == "PARTIAL":
                    event = self._create_monitoring_event(
                        event_type=MonitoringEventType.QUESTION_UNRESOLVED,
                        priority=MonitoringPriority.LOW,
                        source="experience_state",
                        description=f"Unresolved question detected: {exp.get('title', 'Unknown experience')}",
                        current_state=exp,
                        timestamp=timestamp
                    )
                    events.append(event)
                    break  # Just report one for legacy compatibility
                    
        return events

    def _is_meaningful_change(self, state_type: str, current_fingerprint: str, fingerprint_history: List[str]) -> bool:
        """Determine if a state change is meaningful enough to warrant an event."""
        if not fingerprint_history:
            # First time seeing this state type - consider it meaningful if not empty state
            return current_fingerprint != self._compute_state_fingerprint({})
        
        # Check if we've seen this exact fingerprint before (exact match)
        if current_fingerprint in fingerprint_history:
            return False  # No change if we've seen this state before
            
        # If we have history, check similarity to recent states
        # For now, we'll consider any new fingerprint as a meaningful change
        # In a more sophisticated implementation, we could use similarity metrics
        return len(fingerprint_history) > 0

    def _state_type_to_event_type(self, state_type: str) -> MonitoringEventType:
        """Convert state type to monitoring event type."""
        mapping = {
            "goal_state": MonitoringEventType.GOAL_STALE,
            "user_model_state": MonitoringEventType.USER_MODEL_UPDATE,
            "experience_state": MonitoringEventType.EXPERIENCE_OUTCOME,
            "memory_state": MonitoringEventType.MEMORY_PRESSURE,
            "system_state": MonitoringEventType.SYSTEM_HEALTH
        }
        return mapping.get(state_type, MonitoringEventType.STATE_CHANGE)

    def _determine_event_priority(self, state_type: str, current_state: Dict[str, Any], previous_state: Optional[Dict[str, Any]]) -> MonitoringPriority:
        """Determine the priority of a monitoring event based on the change."""
        # Default priority
        priority = MonitoringPriority.NORMAL
        
        # Adjust based on state type and change significance
        if state_type == "goal_state":
            # Check if goal became completely stalled or failed
            if current_state.get("progress", 1.0) < 0.1 and previous_state:
                if previous_state.get("progress", 0.0) > 0.5:
                    priority = MonitoringPriority.HIGH
            elif current_state.get("no_active_goal", False) and not previous_state.get("no_active_goal", True):
                priority = MonitoringPriority.LOW  # Goal completed
                
        elif state_type == "experience_state":
            # Check for failures or partial outcomes
            experiences = current_state.get("experiences", [])
            for exp in experiences:
                outcome = exp.get("outcome")
                if outcome in ["FAILED", "PARTIAL", "TIMEOUT"]:
                    priority = MonitoringPriority.HIGH
                    break
                elif outcome == "ABORTED":
                    priority = MonitoringPriority.CRITICAL
                    break
                    
        elif state_type == "memory_state":
            # Check for memory pressure
            if not current_state.get("system_available", True):
                priority = MonitoringPriority.HIGH
                
        elif state_type == "system_state":
            # Check for system errors
            error_count = current_state.get("error_count", 0)
            if error_count > 5:
                priority = MonitoringPriority.HIGH

        return priority

    def _generate_change_description(self, state_type: str, current_state: Dict[str, Any], previous_state: Optional[Dict[str, Any]]) -> str:
        """Generate a human-readable description of the change."""
        if state_type == "goal_state":
            if current_state.get("no_active_goal", False):
                if previous_state and not previous_state.get("no_active_goal", True):
                    return "Active goal completed"
                return "No active goal detected"
            elif previous_state and previous_state.get("no_active_goal", True):
                return f"New active goal detected: {current_state.get('title', 'Unknown')}"
            else:
                title = current_state.get("title", "Unknown")
                progress = current_state.get("progress", 0)
                prev_progress = previous_state.get("progress", 0) if previous_state else 0
                change = progress - prev_progress
                if abs(change) > 0.1:
                    return f"Goal progress changed: {title} ({change:+.1%})"
                return f"Goal state updated: {title}"
                
        elif state_type == "user_model_state":
            entry_count = current_state.get("entry_count", 0)
            prev_count = previous_state.get("entry_count", 0) if previous_state else 0
            if entry_count != prev_count:
                diff = entry_count - prev_count
                if diff > 0:
                    return f"{diff} new user model entries detected"
                else:
                    return f"{abs(diff)} user model entries removed"
            return f"User model state updated ({entry_count} entries)"
            
        elif state_type == "experience_state":
            exp_count = current_state.get("experience_count", 0)
            prev_count = previous_state.get("experience_count", 0) if previous_state else 0
            if exp_count != prev_count:
                diff = exp_count - prev_count
                if diff > 0:
                    return f"{diff} new experiences recorded"
                else:
                    return f"{abs(diff)} experiences archived"
            
            # Check for outcome changes
            outcomes = current_state.get("outcomes", [])
            if outcomes:
                recent_outcome = outcomes[-1] if outcomes else None
                if recent_outcome in ["PARTIAL", "FAILED", "TIMEOUT"]:
                    return f"Experience with outcome '{recent_outcome}' detected"
            return f"Experience state updated ({exp_count} experiences)"
            
        elif state_type == "memory_state":
            if not current_state.get("system_available", True):
                return "Advanced memory system unavailable"
            retrieval_count = current_state.get("retrieval_count", 0)
            store_count = current_state.get("store_count", 0)
            return f"Memory system active ({retrieval_count} retrievals, {store_count} stores)"
            
        elif state_type == "system_state":
            error_count = current_state.get("error_count", 0)
            prev_count = previous_state.get("error_count", 0) if previous_state else 0
            if error_count > prev_count:
                return f"{error_count - prev_count} new monitoring errors"
            return f"System state updated ({error_count} errors)"
            
        return f"{state_type} changed"

    def _create_monitoring_event(self, event_type: MonitoringEventType, priority: MonitoringPriority, 
                                source: str, description: str, **kwargs) -> MonitoringEvent:
        """Create a monitoring event with the specified parameters."""
        # Generate a unique event ID
        timestamp = kwargs.get("timestamp", time.time())
        event_id = hashlib.md5(
            f"{self.owner_id}:{self.session_id}:{source}:{timestamp}".encode()
        ).hexdigest()[:16]
        
        # Generate correlation ID for related events
        correlation_id = kwargs.get("correlation_id") or hashlib.md5(
            f"{self.owner_id}:{source}:{int(timestamp // 60)}".encode()  # Changes every minute
        ).hexdigest()[:12]
        
        return MonitoringEvent(
            event_id=event_id,
            event_type=event_type,
            priority=priority,
            owner_id=self.owner_id,
            session_id=self.session_id,
            timestamp=timestamp,
            source=source,
            description=description,
            previous_state=kwargs.get("previous_state"),
            current_state=kwargs.get("current_state"),
            state_fingerprint=kwargs.get("state_fingerprint"),
            metadata=kwargs.get("metadata", {}),
            correlation_id=correlation_id
        )

    def _should_process_event(self, event: MonitoringEvent) -> bool:
        """Determine if a monitoring event should be processed (trigger a cognitive cycle)."""
        # Check for deduplication
        event_fingerprint = self._compute_event_fingerprint(event)
        if event_fingerprint in self.state.recent_event_fingerprints:
            return False  # Duplicate event
            
        # Add to recent fingerprints (with cleanup)
        self.state.recent_event_fingerprints.add(event_fingerprint)
        # Keep only recent fingerprints to prevent unbounded growth
        if len(self.state.recent_event_fingerprints) > 100:
            # Remove oldest entries (we don't have timestamps in the set, so we'll clear periodically)
            # In a more sophisticated implementation, we'd track timestamps
            if len(self.state.recent_event_fingerprints) > 150:
                self.state.recent_event_fingerprints.clear()
        
        # Check cooldown and rate limiting (same as original V11.3)
        if not self._is_cooldown_complete():
            return False
            
        if not self._is_rate_limit_ok():
            return False
            
        # Filter by priority - only process NORMAL and higher priority events by default
        # This can be configured if needed
        if event.priority.value < MonitoringPriority.NORMAL.value:
            return False
            
        return True

    def _compute_event_fingerprint(self, event: MonitoringEvent) -> str:
        """Compute a fingerprint for event deduplication."""
        # Create a fingerprint based on the essential aspects of the event
        # that would make it a duplicate
        fingerprint_data = {
            "owner_id": event.owner_id,
            "session_id": event.session_id,
            "event_type": event.event_type.value,
            "source": event.source,
            # Include key aspects of the description that indicate the same root cause
            "description_core": self._extract_description_core(event.description),
            # Exclude timestamp and specific IDs that would make each event unique
        }
        
        try:
            json_str = json.dumps(fingerprint_data, sort_keys=True, default=str)
        except (TypeError, ValueError):
            json_str = str(sorted(fingerprint_data.items()))
            
        return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

    def _extract_description_core(self, description: str) -> str:
        """Extract the core of a description for deduplication purposes."""
        # Remove specific values that change frequently but don't change the root cause
        # For example, change "Goal stale for 25.3 hours: 'Learn Python'" 
        # to "Goal stale for X hours: 'Learn Python'"
        import re
        
        # Replace numbers with X to make descriptions more generic for deduplication
        description = re.sub(r'\d+\.?\d*', 'X', description)
        # Replace specific goal titles with placeholder but keep the structure
        description = re.sub(r"''[^']*''", "'title'", description)
        description = re.sub(r'"[^"]*"', '"title"', description)
        return description.strip()

    def _is_cooldown_complete(self) -> bool:
        """Check if enough time has passed since the last monitoring-triggered cycle."""
        if self.state.last_cycle_time == 0.0:
            return True
        return (time.time() - self.state.last_cycle_time) >= self.config.cooldown_period

    def _is_rate_limit_ok(self) -> bool:
        """Check if we are within the allowed rate of monitoring-triggered cycles per hour."""
        now = time.time()
        # Remove timestamps older than 1 hour
        self.state.cycles_in_last_hour = [
            t for t in self.state.cycles_in_last_hour if now - t < 3600.0
        ]
        return len(self.state.cycles_in_last_hour) < self.config.max_cycles_per_hour

    def _handle_monitoring_event(self, event: MonitoringEvent) -> None:
        """Handle a monitoring event by initiating a cognitive cycle."""
        now = time.time()
        self.state.last_cycle_time = now
        self.state.cycles_in_last_hour.append(now)
        
        # Create user input for the cognitive cycle based on the monitoring event
        user_input = self._create_user_input_from_event(event)
        
        # We'll run the cognitive cycle in a separate thread to avoid blocking the monitor
        def run_cycle():
            try:
                result = v11_cognitive_orchestrator.process_cognitive_cycle(
                    user_input=user_input,
                    owner_id=self.owner_id,
                    session_id=self.session_id,
                    lang=None,  # Use default language
                    context={
                        "monitoring_trigger": True,
                        "event_id": event.event_id,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "source": event.source,
                        "description": event.description
                    },
                    project_id=None
                )
                # Optionally, we could log the result or update state based on the outcome
                # For now, we just note that the cycle was run.
                # In a more advanced implementation, we might update monitoring state based on outcome
            except Exception as e:
                # Log error but don't let it stop the monitoring system
                self._record_monitoring_error(e)

        cycle_thread = threading.Thread(target=run_cycle, daemon=True)
        cycle_thread.start()

    def _create_user_input_from_event(self, event: MonitoringEvent) -> str:
        """Create appropriate user input for a cognitive cycle based on a monitoring event."""
        # Format the user input to clearly indicate this is a monitoring-triggered cycle
        # and provide context about what was detected
        
        base_input = f"Monitoring alert: {event.description}"
        
        # Add context based on event type
        if event.event_type == MonitoringEventType.GOAL_STALE:
            base_input += ". Please review the stalled goal and determine if any action is needed to revive, modify, or conclude it."
        elif event.event_type == MonitoringEventType.USER_MODEL_UPDATE:
            base_input += ". Please consider how the new user model information might affect current goals or preferences."
        elif event.event_type == MonitoringEventType.EXPERIENCE_OUTCOME:
            base_input += ". Please review the recent experience outcome and determine if any follow-up actions are needed."
        elif event.event_type == MonitoringEventType.QUESTION_UNRESOLVED:
            base_input += ". Please address the unresolved question or determine if further investigation is warranted."
        elif event.event_type == MonitoringEventType.SYSTEM_HEALTH:
            base_input += ". Please assess the system health indication and determine if any maintenance actions are needed."
        elif event.event_type == MonitoringEventType.MEMORY_PRESSURE:
            base_input += ". Please review the memory system status and determine if any optimization or cleanup is needed."
        else:
            base_input += ". Please analyze this situation and determine if any action is warranted."
            
        return base_input

    def _record_monitoring_error(self, error: Exception) -> None:
        """Record a monitoring error for tracking and failure containment."""
        self.state.monitoring_errors += 1
        self.state.last_error_time = time.time()
        # In a production system, we might log this to a file or external system
        # For now, we'll just print it (in a real system, use proper logging)
        print(f"Continuous monitoring error: {error}")

    # Lifecycle and status methods
    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the continuous monitoring system."""
        with self._lock:
            return {
                "is_running": self._thread is not None and self._thread.is_alive(),
                "owner_id": self.owner_id,
                "session_id": self.session_id,
                "last_poll_time": self.state.last_poll_time,
                "last_cycle_time": self.state.last_cycle_time,
                "cycles_in_last_hour": len(self.state.cycles_in_last_hour),
                "monitoring_errors": self.state.monitoring_errors,
                "last_error_time": self.state.last_error_time,
                "in_cooldown": not self._is_cooldown_complete(),
                "at_rate_limit": not self._is_rate_limit_ok(),
                "config": {
                    "polling_interval": self.config.polling_interval,
                    "cooldown_period": self.config.cooldown_period,
                    "max_cycles_per_hour": self.config.max_cycles_per_hour
                }
            }


# Global instance for convenience (optional)
# continuous_monitor = ContinuousMonitoringEnhancement(owner_id="default_owner")