# Voice Delivery Module — API Reference

## `core.voice.delivery`

Deterministic voice personality & conversational delivery layer for DOOM Tactical voice.

### Enums

#### `ResponseCategory`
```python
class ResponseCategory(Enum):
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
```

### Dataclasses

#### `DeliveryProfile`
```python
@dataclass(frozen=True, slots=True)
class DeliveryProfile:
    speaking_mode: str = "NORMAL"
    rate_mult: float = 1.0
    pitch_shift: float = 0.0
    volume_mult: float = 1.0
    pause_before_add: float = 0.0
    pause_after_add: float = 0.0
    sentence_pause_mult: float = 1.0
    clause_pause_mult: float = 1.0
    dramatic_pause_mult: float = 1.0
    emphasis_mult: float = 1.0
    keyword_boost_mult: float = 1.0
    short_response_rate_mult: float = 1.05
    long_response_pause_mult: float = 1.15
    very_long_response_segment: bool = False
    emphasis_keywords: Tuple[str, ...] = ()
    emphasis_patterns: Tuple[str, ...] = ()
```

### Functions

#### `classify_response_category()`
```python
def classify_response_category(
    text: str,
    context: str = "",
    intent: Optional[str] = None,
    is_thinking: bool = False,
    is_error: bool = False,
    is_warning: bool = False,
) -> ResponseCategory:
```
Deterministic local classification. **No external LLM/API**.

**Priority order:**
1. Explicit flags (`is_thinking`, `is_error`, `is_warning`)
2. Intent metadata from orchestration
3. Context parameter (cinematic_voice personality contexts)
4. Text heuristics (keywords, punctuation, structure)

#### `get_delivery_profile()`
```python
def get_delivery_profile(category: ResponseCategory) -> DeliveryProfile:
```
Returns the delivery profile for a category.

#### `calculate_response_length_category()`
```python
def calculate_response_length_category(text: str) -> str:
```
Returns: `"short"` | `"medium"` | `"long"` | `"very_long"`

#### `apply_delivery_profile()`
```python
def apply_delivery_profile(
    base_style: SpeechStyle,
    profile: DeliveryProfile,
    length_category: str = "medium"
) -> SpeechStyle:
```
Applies delivery profile adjustments to a base SpeechStyle.

#### `segment_for_delivery()`
```python
def segment_for_delivery(text: str) -> List[Tuple[str, str]]:
```
Segments text into (segment_type, segment_text) for prosodic delivery.

**Segment types:**
- `"sentence"` — Complete sentence
- `"clause"` — Clause within sentence  
- `"technical"` — Protected technical content (atomic)
- `"dramatic_pause"` — Explicit dramatic pause marker

**Technical content protected:**
- File paths (Windows/Unix)
- URLs
- IP addresses
- Version numbers
- Environment variables
- Code identifiers
- Commands with flags
- Numbers with units
- Hex addresses
- UUIDs
- Email addresses

#### `extract_emphasis_targets()`
```python
def extract_emphasis_targets(text: str, profile: DeliveryProfile) -> List[Tuple[int, int, float]]:
```
Returns list of (start, end, boost_factor) for emphasis regions.

### Constants

#### `DELIVERY_PROFILES`
```python
DELIVERY_PROFILES: dict[ResponseCategory, DeliveryProfile]
```
Pre-defined profiles for all 15 response categories.

#### `TECHNICAL_PATTERNS`
```python
TECHNICAL_PATTERNS: List[re.Pattern]
```
Regex patterns for technical content detection.

### Usage Example

```python
from core.voice.delivery import (
    classify_response_category,
    get_delivery_profile,
    calculate_response_length_category,
    apply_delivery_profile,
    segment_for_delivery,
    extract_emphasis_targets,
)
from core.voice.prosody import ProsodyController
from core.voice.personality import TACTICAL_VOICE_PERSONALITY, SpeakingMode

# 1. Classify response
text = "Warning: This will overwrite the config file."
category = classify_response_category(text, context="warning")
# -> ResponseCategory.WARNING

# 2. Get profile
profile = get_delivery_profile(category)
# -> DeliveryProfile(speaking_mode="ALERT", rate_mult=0.88, ...)

# 3. Get length category
length_cat = calculate_response_length_category(text)
# -> "short"

# 4. Compute prosody
controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
mode = SpeakingMode.ALERT  # from profile.speaking_mode
style = controller.for_delivery(
    mode=mode,
    delivery_profile=profile,
    length_category=length_cat
)

# 5. Segment for delivery
segments = segment_for_delivery(text)
# -> [("sentence", "Warning:"), ("technical", "This will overwrite the config file.")]

# 6. Extract emphasis targets
targets = extract_emphasis_targets(text, profile)
# -> [(0, 7, 1.25)]  # "Warning" emphasized
```

### Integration with VoiceEngine

```python
from core.voice import VoiceEngine, VoiceEngineConfig

engine = VoiceEngine(config=VoiceEngineConfig(voice_profile="tactical"))

# Delivery-aware speaking (queued)
engine.speak_with_delivery(
    text="Task completed.",
    context="completion",      # Maps to SUCCESS
    lang="en",
    intent="complete_task",
    priority=0
)

# Backward compatible
engine.speak_immediate("Hello", "en", context="greeting")
```

### Determinism Guarantee

All functions are **pure and deterministic**:
- Same inputs → Same outputs
- No randomness
- No external dependencies
- Testable and reproducible