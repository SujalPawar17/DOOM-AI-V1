"""Voice Delivery & Personality Layer — V9 Conversational Delivery.

Deterministic behavioral layer that determines HOW DOOM speaks.
Does not change WHAT DOOM says — only delivery parameters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
from typing import Final


class ResponseCategory(Enum):
    """Deterministic response categories for voice delivery."""
    NORMAL_RESPONSE = "normal_response"
    QUESTION = "question"
    CONFIRMATION = "confirmation"
    EXPLANATION = "explanation"
    INSTRUCTION = "instruction"
    COMMAND = "command"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"
    STATUS = "status"
    THINKING = "thinking"
    CRITICAL = "critical"
    HUMOR = "humor"
    GREETING = "greeting"
    FAREWELL = "farewell"


@dataclass(frozen=True, slots=True)
class DeliveryProfile:
    """
    Speaking profile for a response category.
    Applied on top of VoicePersonality + SpeakingMode.
    """
    # Base mode to use
    speaking_mode: str = "NORMAL"
    
    # Rate adjustments (multiplicative)
    rate_mult: float = 1.0
    
    # Pitch adjustments (additive, semitones)
    pitch_shift: float = 0.0
    
    # Volume adjustments (multiplicative)
    volume_mult: float = 1.0
    
    # Pause adjustments (additive, seconds)
    pause_before_add: float = 0.0
    pause_after_add: float = 0.0
    sentence_pause_mult: float = 1.0
    clause_pause_mult: float = 1.0
    dramatic_pause_mult: float = 1.0
    
    # Emphasis adjustments (multiplicative)
    emphasis_mult: float = 1.0
    keyword_boost_mult: float = 1.0
    
    # Length-based adjustments
    short_response_rate_mult: float = 1.05
    long_response_pause_mult: float = 1.15
    very_long_response_segment: bool = False
    
    # Emphasis targets for this category
    emphasis_keywords: Tuple[str, ...] = field(default_factory=tuple)
    emphasis_patterns: Tuple[str, ...] = field(default_factory=tuple)


# Deterministic delivery profiles per response category
DELIVERY_PROFILES: Final[dict[ResponseCategory, DeliveryProfile]] = {
    ResponseCategory.NORMAL_RESPONSE: DeliveryProfile(
        speaking_mode="NORMAL",
        rate_mult=1.0,
        pitch_shift=0.0,
        volume_mult=1.0,
        sentence_pause_mult=1.0,
        clause_pause_mult=1.0,
        emphasis_mult=1.0,
        keyword_boost_mult=1.0,
        emphasis_keywords=("done", "completed", "ready", "finished", "success"),
    ),
    
    ResponseCategory.QUESTION: DeliveryProfile(
        speaking_mode="NORMAL",
        rate_mult=1.02,
        pitch_shift=0.2,
        volume_mult=1.0,
        sentence_pause_mult=0.95,
        clause_pause_mult=1.0,
        emphasis_mult=1.0,
        keyword_boost_mult=1.05,
        emphasis_keywords=("what", "why", "how", "when", "where", "which", "who"),
    ),
    
    ResponseCategory.CONFIRMATION: DeliveryProfile(
        speaking_mode="CONFIDENT",
        rate_mult=0.95,
        pitch_shift=-0.2,
        volume_mult=1.0,
        pause_before_add=0.05,
        pause_after_add=0.05,
        sentence_pause_mult=0.9,
        clause_pause_mult=0.9,
        emphasis_mult=1.15,
        keyword_boost_mult=1.1,
        emphasis_keywords=("understood", "confirmed", "acknowledged", "affirmative", "yes", "correct"),
    ),
    
    ResponseCategory.EXPLANATION: DeliveryProfile(
        speaking_mode="NORMAL",
        rate_mult=0.95,
        pitch_shift=0.0,
        volume_mult=1.0,
        pause_before_add=0.0,
        pause_after_add=0.0,
        sentence_pause_mult=1.1,
        clause_pause_mult=1.05,
        emphasis_mult=1.05,
        keyword_boost_mult=1.1,
        emphasis_keywords=("because", "therefore", "thus", "means", "indicates", "shows"),
    ),
    
    ResponseCategory.INSTRUCTION: DeliveryProfile(
        speaking_mode="COMMAND",
        rate_mult=0.98,
        pitch_shift=-0.1,
        volume_mult=1.02,
        sentence_pause_mult=1.0,
        clause_pause_mult=1.0,
        emphasis_mult=1.2,
        keyword_boost_mult=1.15,
        emphasis_keywords=("first", "then", "next", "finally", "must", "should", "run", "execute"),
    ),
    
    ResponseCategory.COMMAND: DeliveryProfile(
        speaking_mode="COMMAND",
        rate_mult=0.95,
        pitch_shift=-0.3,
        volume_mult=1.05,
        pause_before_add=0.0,
        pause_after_add=0.0,
        sentence_pause_mult=0.85,
        clause_pause_mult=0.9,
        emphasis_mult=1.25,
        keyword_boost_mult=1.2,
        emphasis_keywords=("execute", "run", "stop", "start", "initiate", "terminate", "deploy"),
    ),
    
    ResponseCategory.WARNING: DeliveryProfile(
        speaking_mode="ALERT",
        rate_mult=0.88,
        pitch_shift=-0.5,
        volume_mult=1.0,
        pause_before_add=0.1,
        pause_after_add=0.05,
        sentence_pause_mult=1.2,
        clause_pause_mult=1.15,
        dramatic_pause_mult=1.3,
        emphasis_mult=1.3,
        keyword_boost_mult=1.25,
        emphasis_keywords=("warning", "caution", "alert", "overwrite", "delete", "destroy", "irreversible", "critical"),
    ),
    
    ResponseCategory.ERROR: DeliveryProfile(
        speaking_mode="NORMAL",
        rate_mult=0.9,
        pitch_shift=-0.3,
        volume_mult=0.95,
        pause_before_add=0.05,
        pause_after_add=0.05,
        sentence_pause_mult=1.1,
        clause_pause_mult=1.05,
        emphasis_mult=1.1,
        keyword_boost_mult=1.1,
        emphasis_keywords=("error", "failed", "failure", "exception", "crash", "unable", "cannot"),
    ),
    
    ResponseCategory.SUCCESS: DeliveryProfile(
        speaking_mode="CONFIDENT",
        rate_mult=0.95,
        pitch_shift=-0.2,
        volume_mult=1.0,
        pause_before_add=0.05,
        pause_after_add=0.05,
        sentence_pause_mult=1.0,
        clause_pause_mult=1.0,
        emphasis_mult=1.15,
        keyword_boost_mult=1.1,
        emphasis_keywords=("success", "completed", "done", "finished", "operational", "verified"),
    ),
    
    ResponseCategory.STATUS: DeliveryProfile(
        speaking_mode="NORMAL",
        rate_mult=1.0,
        pitch_shift=0.0,
        volume_mult=1.0,
        sentence_pause_mult=1.05,
        clause_pause_mult=1.0,
        emphasis_mult=1.0,
        keyword_boost_mult=1.05,
        emphasis_keywords=("status", "online", "offline", "active", "idle", "running", "stopped"),
    ),
    
    ResponseCategory.THINKING: DeliveryProfile(
        speaking_mode="THINKING",
        rate_mult=0.85,
        pitch_shift=-0.3,
        volume_mult=0.95,
        pause_before_add=0.1,
        pause_after_add=0.1,
        sentence_pause_mult=1.3,
        clause_pause_mult=1.25,
        dramatic_pause_mult=1.2,
        emphasis_mult=0.85,
        keyword_boost_mult=0.9,
        emphasis_keywords=("analyzing", "processing", "computing", "evaluating", "considering"),
    ),
    
    ResponseCategory.CRITICAL: DeliveryProfile(
        speaking_mode="CRITICAL",
        rate_mult=0.85,
        pitch_shift=-0.8,
        volume_mult=1.1,
        pause_before_add=0.15,
        pause_after_add=0.1,
        sentence_pause_mult=1.25,
        clause_pause_mult=1.2,
        dramatic_pause_mult=1.5,
        emphasis_mult=1.35,
        keyword_boost_mult=1.3,
        emphasis_keywords=("critical", "emergency", "immediate", "urgent", "failure", "breach", "compromised"),
    ),
    
    ResponseCategory.HUMOR: DeliveryProfile(
        speaking_mode="HUMOR",
        rate_mult=1.02,
        pitch_shift=0.2,
        volume_mult=1.0,
        pause_before_add=0.02,
        pause_after_add=0.05,
        sentence_pause_mult=0.95,
        clause_pause_mult=0.95,
        emphasis_mult=1.05,
        keyword_boost_mult=1.05,
        emphasis_keywords=(),
    ),
    
    ResponseCategory.GREETING: DeliveryProfile(
        speaking_mode="NORMAL",
        rate_mult=0.95,
        pitch_shift=0.1,
        volume_mult=1.0,
        pause_before_add=0.05,
        pause_after_add=0.1,
        sentence_pause_mult=1.1,
        clause_pause_mult=1.05,
        emphasis_mult=1.1,
        keyword_boost_mult=1.05,
        emphasis_keywords=("hello", "greetings", "welcome", "online", "ready"),
    ),
    
    ResponseCategory.FAREWELL: DeliveryProfile(
        speaking_mode="NORMAL",
        rate_mult=0.92,
        pitch_shift=-0.2,
        volume_mult=0.95,
        pause_before_add=0.1,
        pause_after_add=0.15,
        sentence_pause_mult=1.15,
        clause_pause_mult=1.1,
        emphasis_mult=1.05,
        keyword_boost_mult=1.0,
        emphasis_keywords=("goodbye", "farewell", "shutdown", "offline", "later"),
    ),
}


# Technical content patterns that must NOT be corrupted by segmentation
TECHNICAL_PATTERNS: Final[List[re.Pattern]] = [
    # File paths (Windows and Unix)
    re.compile(r'(?:[A-Za-z]:)?(?:[\\/][\w\-\.]+)+'),
    # URLs
    re.compile(r'https?://[^\s]+'),
    # IP addresses
    re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
    # Version numbers
    re.compile(r'\bv?\d+(?:\.\d+)+(?:-[a-zA-Z0-9]+)?\b'),
    # Environment variables
    re.compile(r'\$\w+|\$\{\w+\}|%\w+%'),
    # Code identifiers (functions, variables, classes)
    re.compile(r'\b[a-zA-Z_][a-zA-Z0-9_]*\(\)'),
    re.compile(r'\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*'),
    # Commands with flags (must have at least one --flag)
    re.compile(r'\b\w+(?:-\w+)*(?:\s+--\w+(?:=\S+)?)+'),
    # Database/API names
    re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b'),
    # Numbers with units
    re.compile(r'\b\d+(?:\.\d+)?\s*(?:ms|ms|kb|mb|gb|tb|hz|mhz|ghz|%|db)\b', re.IGNORECASE),
    # Hex addresses
    re.compile(r'\b0x[0-9a-fA-F]+\b'),
    # UUIDs
    re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'),
    # Email addresses
    re.compile(r'\b[\w\.-]+@[\w\.-]+\.\w+\b'),
]


# Clause/sentence boundary patterns
SENTENCE_END_PATTERN: Final[re.Pattern] = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
CLAUSE_BOUNDARY_PATTERN: Final[re.Pattern] = re.compile(r'(?<=[,;:])\s+(?=[A-Za-z])')
EM_DASH_PATTERN: Final[re.Pattern] = re.compile(r'\s*—\s*')


def _is_technical_content(text: str, start: int, end: int) -> bool:
    """Check if a text span contains technical content that should not be split."""
    span = text[start:end]
    for pattern in TECHNICAL_PATTERNS:
        if pattern.search(span):
            return True
    return False


def _find_safe_sentence_boundaries(text: str) -> List[int]:
    """Find safe sentence boundaries that don't split technical content."""
    boundaries = [0]
    
    for match in SENTENCE_END_PATTERN.finditer(text):
        end_pos = match.start()
        start_pos = boundaries[-1]
        
        # Check if this boundary splits technical content
        if not _is_technical_content(text, start_pos, end_pos + 1):
            boundaries.append(end_pos + 1)
    
    boundaries.append(len(text))
    return boundaries


def _find_safe_clause_boundaries(text: str, sentence_start: int, sentence_end: int) -> List[int]:
    """Find safe clause boundaries within a sentence."""
    boundaries = [sentence_start]
    
    for match in CLAUSE_BOUNDARY_PATTERN.finditer(text[sentence_start:sentence_end]):
        abs_pos = sentence_start + match.start()
        prev_boundary = boundaries[-1]
        
        if not _is_technical_content(text, prev_boundary, abs_pos + 1):
            boundaries.append(abs_pos + 1)
    
    # Also check em-dash boundaries
    for match in EM_DASH_PATTERN.finditer(text[sentence_start:sentence_end]):
        abs_pos = sentence_start + match.start()
        prev_boundary = boundaries[-1]
        
        if not _is_technical_content(text, prev_boundary, abs_pos + 1):
            boundaries.append(abs_pos + 1)
    
    boundaries.append(sentence_end)
    return sorted(set(boundaries))


def segment_for_delivery(text: str) -> List[Tuple[str, str]]:
    """
    Segment text into (segment_type, segment_text) for delivery.
    
    Returns list of segments where segment_type is:
    - "sentence": complete sentence
    - "clause": clause within sentence
    - "technical": protected technical content
    - "dramatic_pause": explicit dramatic pause marker
    
    Technical content is preserved as atomic segments.
    """
    if not text or not text.strip():
        return []
    
    # First pass: identify technical content spans
    technical_spans: List[Tuple[int, int]] = []
    for pattern in TECHNICAL_PATTERNS:
        for match in pattern.finditer(text):
            technical_spans.append((match.start(), match.end()))
    
    # Merge overlapping technical spans
    technical_spans.sort()
    merged_spans: List[Tuple[int, int]] = []
    for start, end in technical_spans:
        if merged_spans and start <= merged_spans[-1][1]:
            merged_spans[-1] = (merged_spans[-1][0], max(merged_spans[-1][1], end))
        else:
            merged_spans.append((start, end))
    
    # Second pass: segment around technical content
    segments: List[Tuple[str, str]] = []
    last_end = 0
    
    for tech_start, tech_end in merged_spans:
        # Process text before technical content
        if tech_start > last_end:
            pre_text = text[last_end:tech_start].strip()
            if pre_text:
                sentence_bounds = _find_safe_sentence_boundaries(pre_text)
                for i in range(len(sentence_bounds) - 1):
                    sent_text = pre_text[sentence_bounds[i]:sentence_bounds[i+1]].strip()
                    if sent_text:
                        segments.append(("sentence", sent_text))
        
        # Add technical content as atomic segment
        tech_text = text[tech_start:tech_end]
        segments.append(("technical", tech_text))
        last_end = tech_end
    
    # Process remaining text
    if last_end < len(text):
        remaining = text[last_end:].strip()
        if remaining:
            sentence_bounds = _find_safe_sentence_boundaries(remaining)
            for i in range(len(sentence_bounds) - 1):
                sent_text = remaining[sentence_bounds[i]:sentence_bounds[i+1]].strip()
                if sent_text:
                    segments.append(("sentence", sent_text))
    
    # If no segments found (e.g., pure technical text), treat as single technical segment
    if not segments and text.strip():
        segments.append(("technical", text.strip()))
    
    return segments


def classify_response_category(
    text: str,
    context: str = "",
    intent: Optional[str] = None,
    is_thinking: bool = False,
    is_error: bool = False,
    is_warning: bool = False,
) -> ResponseCategory:
    """
    Deterministic local response category classification.
    
    Uses text analysis + context signals — NO external LLM/API.
    """
    # Priority 1: Explicit context signals
    if is_thinking:
        return ResponseCategory.THINKING
    if is_error:
        return ResponseCategory.ERROR
    if is_warning:
        return ResponseCategory.WARNING
    
    # Priority 2: Intent from orchestration (if available)
    if intent:
        intent_lower = intent.lower()
        if "command" in intent_lower or "execute" in intent_lower:
            return ResponseCategory.COMMAND
        if "question" in intent_lower or "query" in intent_lower:
            return ResponseCategory.QUESTION
        if "confirm" in intent_lower or "acknowledge" in intent_lower:
            return ResponseCategory.CONFIRMATION
        if "explain" in intent_lower or "describe" in intent_lower:
            return ResponseCategory.EXPLANATION
        if "instruct" in intent_lower or "guide" in intent_lower:
            return ResponseCategory.INSTRUCTION
        if "status" in intent_lower or "report" in intent_lower:
            return ResponseCategory.STATUS
        if "greet" in intent_lower or "hello" in intent_lower:
            return ResponseCategory.GREETING
        if "farewell" in intent_lower or "goodbye" in intent_lower:
            return ResponseCategory.FAREWELL
        if "critical" in intent_lower or "emergency" in intent_lower:
            return ResponseCategory.CRITICAL
        if "humor" in intent_lower or "joke" in intent_lower:
            return ResponseCategory.HUMOR
        if "success" in intent_lower or "complete" in intent_lower:
            return ResponseCategory.SUCCESS
    
    # Priority 3: Context parameter (from cinematic_voice personality contexts)
    context_lower = context.lower()
    if context_lower in ("warning", "alert"):
        return ResponseCategory.WARNING
    if context_lower in ("error", "failure"):
        return ResponseCategory.ERROR
    if context_lower in ("confirmation", "acknowledgment", "ack"):
        return ResponseCategory.CONFIRMATION
    if context_lower in ("completion", "success", "done"):
        return ResponseCategory.SUCCESS
    if context_lower in ("thinking", "processing", "analyzing"):
        return ResponseCategory.THINKING
    if context_lower in ("greeting", "hello", "startup"):
        return ResponseCategory.GREETING
    if context_lower in ("farewell", "goodbye", "shutdown"):
        return ResponseCategory.FAREWELL
    if context_lower in ("command", "execute"):
        return ResponseCategory.COMMAND
    if context_lower in ("instruction", "guide"):
        return ResponseCategory.INSTRUCTION
    if context_lower in ("explanation", "explain"):
        return ResponseCategory.EXPLANATION
    if context_lower in ("status", "report"):
        return ResponseCategory.STATUS
    if context_lower in ("humor", "joke"):
        return ResponseCategory.HUMOR
    if context_lower in ("critical", "emergency"):
        return ResponseCategory.CRITICAL
    
    # Priority 4: Text-based heuristic classification
    text_lower = text.lower().strip()
    text_stripped = text.strip()
    
    # Question detection
    if text_stripped.endswith('?'):
        # But not rhetorical or embedded questions in technical content
        # Check if the question mark is part of technical content
        is_technical_question = False
        for pattern in TECHNICAL_PATTERNS:
            for match in pattern.finditer(text):
                if match.start() <= len(text_stripped) - 1 <= match.end():
                    is_technical_question = True
                    break
            if is_technical_question:
                break
        if not is_technical_question:
            return ResponseCategory.QUESTION
    
    # Command detection (imperative mood at start)
    command_starts = (
        "run ", "execute ", "start ", "stop ", "kill ", "restart ",
        "deploy ", "build ", "test ", "install ", "update ",
        "create ", "delete ", "remove ", "clear ", "reset ",
        "show ", "list ", "get ", "set ", "config ", "configure "
    )
    if text_lower.startswith(command_starts):
        return ResponseCategory.COMMAND
    
    # Greeting detection
    greeting_words = ("hello", "hi", "hey", "greetings", "good morning", "good evening", "good day")
    if any(text_lower.startswith(g) for g in greeting_words):
        return ResponseCategory.GREETING
    
    # Farewell detection
    farewell_words = ("goodbye", "bye", "farewell", "shutdown", "shut down", "going offline")
    if any(text_lower.startswith(f) for f in farewell_words):
        return ResponseCategory.FAREWELL
    
    # Confirmation detection (short affirmative responses)
    confirm_words = ("understood", "confirmed", "acknowledged", "affirmative", "yes", "correct", "right", "exactly", "precisely")
    if text_lower in confirm_words or text_lower.startswith(("understood", "confirmed", "acknowledged")):
        return ResponseCategory.CONFIRMATION
    
    # Success/completion detection
    success_words = ("done", "completed", "finished", "success", "successful", "verified", "operational")
    if any(text_lower.startswith(s) for s in success_words):
        return ResponseCategory.SUCCESS
    
    # Warning detection
    warning_words = ("warning", "caution", "alert", "beware", "careful")
    if any(w in text_lower for w in warning_words):
        return ResponseCategory.WARNING
    
    # Critical detection (before ERROR to catch "critical failure" etc.)
    critical_words = ("critical", "emergency", "immediate", "urgent", "breach", "compromised")
    if any(w in text_lower for w in critical_words):
        return ResponseCategory.CRITICAL
    
    # Error detection
    error_words = ("error", "failed", "failure", "exception", "crash", "unable", "cannot", "impossible")
    if any(w in text_lower for w in error_words):
        return ResponseCategory.ERROR
    
    # Instruction detection (educational/instructional tone)
    instruction_markers = ("first,", "then,", "next,", "finally,", "step 1", "step 2", "to do this", "you need to", "you should")
    if any(m in text_lower for m in instruction_markers):
        return ResponseCategory.INSTRUCTION
    
    # Explanation detection (explanatory tone)
    explanation_markers = ("because", "since", "therefore", "thus", "this means", "in other words", "the reason")
    if any(m in text_lower for m in explanation_markers):
        return ResponseCategory.EXPLANATION
    
    # Status detection
    status_markers = ("status:", "status is", "currently", "running at", "load is", "usage is")
    if any(m in text_lower for m in status_markers):
        return ResponseCategory.STATUS
    
    # Humor detection (very conservative - only explicit markers)
    humor_markers = ("just kidding", "lol", "haha", "ironic", "sarcastic")
    if any(m in text_lower for m in humor_markers):
        return ResponseCategory.HUMOR
    
    # Thinking detection (text-based heuristic)
    thinking_keywords = ("analyzing", "processing", "computing", "evaluating", "considering", "let me", "one moment")
    if any(w in text_lower for w in thinking_keywords):
        return ResponseCategory.THINKING
    
    # Default: normal response
    return ResponseCategory.NORMAL_RESPONSE


def get_delivery_profile(category: ResponseCategory) -> DeliveryProfile:
    """Get the delivery profile for a response category."""
    return DELIVERY_PROFILES.get(category, DELIVERY_PROFILES[ResponseCategory.NORMAL_RESPONSE])


def calculate_response_length_category(text: str) -> str:
    """Categorize response length for delivery adjustments."""
    word_count = len(text.split())
    if word_count < 10:
        return "short"
    elif word_count < 50:
        return "medium"
    elif word_count < 150:
        return "long"
    else:
        return "very_long"


def apply_delivery_profile(
    base_style: 'SpeechStyle',
    profile: DeliveryProfile,
    length_category: str = "medium"
) -> 'SpeechStyle':
    """
    Apply delivery profile adjustments to a base SpeechStyle.
    
    Returns a new SpeechStyle with combined adjustments.
    """
    from core.voice.personality import SpeechStyle, clamp_rate, clamp_pitch, clamp_volume, clamp_emphasis, clamp_keyword_boost, clamp_pause_seconds
    
    # Apply profile adjustments
    rate = clamp_rate(base_style.rate * profile.rate_mult)
    pitch = clamp_pitch(base_style.pitch + profile.pitch_shift)
    volume = clamp_volume(base_style.volume * profile.volume_mult)
    emphasis = clamp_emphasis(base_style.emphasis * profile.emphasis_mult)
    keyword_boost = clamp_keyword_boost(base_style.keyword_boost * profile.keyword_boost_mult)
    
    # Pause adjustments
    pause_before = clamp_pause_seconds(base_style.pause_before + profile.pause_before_add)
    pause_after = clamp_pause_seconds(base_style.pause_after + profile.pause_after_add)
    sentence_pause = clamp_pause_seconds(base_style.sentence_pause * profile.sentence_pause_mult)
    clause_pause = clamp_pause_seconds(base_style.clause_pause * profile.clause_pause_mult)
    
    # Length-based adjustments
    if length_category == "short":
        rate = clamp_rate(rate * profile.short_response_rate_mult)
        sentence_pause = clamp_pause_seconds(sentence_pause * 0.8)
        clause_pause = clamp_pause_seconds(clause_pause * 0.8)
    elif length_category == "long":
        sentence_pause = clamp_pause_seconds(sentence_pause * profile.long_response_pause_mult)
        clause_pause = clamp_pause_seconds(clause_pause * profile.long_response_pause_mult)
    elif length_category == "very_long":
        sentence_pause = clamp_pause_seconds(sentence_pause * profile.long_response_pause_mult)
        clause_pause = clamp_pause_seconds(clause_pause * profile.long_response_pause_mult)
    
    return SpeechStyle(
        rate=rate,
        pitch=pitch,
        volume=volume,
        pause_before=pause_before,
        pause_after=pause_after,
        sentence_pause=sentence_pause,
        clause_pause=clause_pause,
        emphasis=emphasis,
        keyword_boost=keyword_boost,
        mode=base_style.mode,
    )


def extract_emphasis_targets(text: str, profile: DeliveryProfile) -> List[Tuple[int, int, float]]:
    """
    Extract emphasis targets from text based on delivery profile.
    
    Returns list of (start, end, boost_factor) for emphasis regions.
    """
    targets: List[Tuple[int, int, float]] = []
    text_lower = text.lower()
    
    # Keyword-based emphasis
    for keyword in profile.emphasis_keywords:
        start = 0
        while True:
            idx = text_lower.find(keyword.lower(), start)
            if idx == -1:
                break
            # Check word boundaries
            before_ok = idx == 0 or not text_lower[idx - 1].isalnum()
            after_idx = idx + len(keyword)
            after_ok = after_idx >= len(text_lower) or not text_lower[after_idx].isalnum()
            
            if before_ok and after_ok:
                # Check not inside technical content
                if not _is_technical_content(text, idx, after_idx):
                    targets.append((idx, after_idx, profile.keyword_boost_mult))
            
            start = idx + 1
    
    # Pattern-based emphasis (regex)
    for pattern_str in profile.emphasis_patterns:
        pattern = re.compile(pattern_str, re.IGNORECASE)
        for match in pattern.finditer(text):
            if not _is_technical_content(text, match.start(), match.end()):
                targets.append((match.start(), match.end(), profile.keyword_boost_mult))
    
    # Sort by position and merge overlapping
    targets.sort(key=lambda x: x[0])
    merged: List[Tuple[int, int, float]] = []
    for start, end, boost in targets:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), max(merged[-1][2], boost))
        else:
            merged.append((start, end, boost))
    
    return merged


# Import here to avoid circular dependency
from core.voice.personality import SpeechStyle