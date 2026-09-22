# DOOM Tactical Voice — Personality & Conversational Delivery

## Overview

This document describes the V9 Voice Personality & Conversational Delivery layer for DOOM Tactical voice. This layer determines **HOW DOOM speaks** while preserving the existing Tactical voice identity (WHAT DOOM sounds like).

## Voice Identity (Unchanged)

The Tactical voice foundation remains:
- **Base Kokoro voice**: `bm_george`
- **Pitch**: -3.0 semitones
- **Low-mid boost**: +2.2 dB
- **Compression**: 2.5:1 @ -20 dB
- **Saturation**: 5%
- **Spectral tilt**: -1.0 dB
- **HF air**: 0 dB
- **Synthetic layer**: 0%

## Architecture

```
DOOM Brain
    ↓ Response + Context
Voice Delivery Classifier (deterministic)
    ↓ ResponseCategory + DeliveryProfile
Voice Personality Layer (contextual prosody)
    ↓ SpeechStyle (rate, pitch, volume, pauses, emphasis)
ProsodyController
    ↓ Adjusted SpeechStyle
VoiceEngine
    ↓ Backend (Kokoro/Piper/pyttsx3)
Tactical DSP Chain
    ↓ Processed Audio
SoundDeviceOutput
```

## Response Categories

The system classifies responses into 15 deterministic categories:

| Category | Speaking Mode | Use Case |
|----------|--------------|----------|
| `NORMAL_RESPONSE` | NORMAL | Default informational responses |
| `QUESTION` | NORMAL | Questions ending with `?` |
| `CONFIRMATION` | CONFIDENT | "Understood", "Confirmed", "Yes" |
| `EXPLANATION` | NORMAL | Explanatory responses with "because", "therefore" |
| `INSTRUCTION` | COMMAND | Step-by-step guidance |
| `COMMAND` | COMMAND | Imperative commands: "Run", "Execute", "Stop" |
| `WARNING` | ALERT | Warnings about dangerous operations |
| `ERROR` | NORMAL | Error messages (calm, informative) |
| `SUCCESS` | CONFIDENT | Completion confirmations |
| `STATUS` | NORMAL | System status reports |
| `THINKING` | THINKING | Processing/analysis states |
| `CRITICAL` | CRITICAL | Emergency/critical situations |
| `HUMOR` | HUMOR | Dry, restrained humor |
| `GREETING` | NORMAL | Startup greetings |
| `FAREWELL` | NORMAL | Shutdown/goodbye |

## Classification Priority (Deterministic)

1. **Explicit flags**: `is_thinking`, `is_error`, `is_warning`
2. **Intent metadata**: From orchestration (if available)
3. **Context parameter**: From cinematic_voice personality contexts
4. **Text heuristics**: Keywords, punctuation, structure

**No external LLM/API calls** — all classification is local and deterministic.

## Delivery Profiles

Each category has a `DeliveryProfile` with adjustments applied on top of the base SpeakingMode:

```python
@dataclass
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
    emphasis_keywords: Tuple[str, ...] = ()
```

### Example: WARNING Profile
- Mode: ALERT (faster, higher pitch)
- Rate: 0.88x (slower for gravity)
- Pitch: -0.5 semitones (darker)
- Sentence pauses: 1.2x (more deliberate)
- Emphasis: 1.3x on keywords like "warning", "overwrite", "critical"

### Example: CONFIRMATION Profile
- Mode: CONFIDENT
- Rate: 0.95x (concise)
- Pitch: -0.2 semitones
- Emphasis: 1.15x on "understood", "confirmed"

## Response Length Awareness

| Length | Word Count | Adjustments |
|--------|-----------|-------------|
| Short | < 10 | Faster rate (1.05x), shorter pauses |
| Medium | 10-50 | Baseline |
| Long | 50-150 | Longer pauses (1.15x) |
| Very Long | > 150 | Longer pauses, segment if needed |

## Sentence & Clause Segmentation

The `segment_for_delivery()` function safely segments text for prosodic delivery:

### Technical Content Protection

The following are **never split** across segments:
- File paths: `C:\Users\file.py`, `/home/user/file.txt`
- URLs: `https://example.com/api`
- IP addresses: `192.168.1.1`
- Version numbers: `1.2.3`, `v2.0.0-beta`
- Environment variables: `$HOME`, `${PATH}`, `%TEMP%`
- Code identifiers: `my_function()`, `obj.method()`
- Commands: `python script.py --flag=value`
- Numbers with units: `50ms`, `512mb`
- Hex addresses: `0x7fff1234`
- UUIDs: `123e4567-e89b-12d3-a456-426614174000`
- Emails: `user@example.com`

### Pause Architecture

| Boundary | Base Pause | Multipliers Applied |
|----------|-----------|---------------------|
| Sentence (`.!?`) | 350ms | Category × Length |
| Clause (`,;:`) | 180ms | Category × Length |
| Em-dash (`—`) | 180ms | Category × Length |
| Dramatic | 800ms | Category × Length |

## Emphasis System

Keywords per category receive controlled emphasis boost:

```python
# CONFIRMATION keywords
("understood", "confirmed", "acknowledged", "affirmative", "yes", "correct")

# WARNING keywords  
("warning", "caution", "alert", "overwrite", "delete", "irreversible", "critical")

# COMMAND keywords
("execute", "run", "stop", "start", "deploy", "terminate")
```

Emphasis is **not applied** inside technical content spans.

## Integration Points

### VoiceEngine.speak_with_delivery()
```python
engine.speak_with_delivery(
    text="Task completed.",
    context="completion",        # Maps to SUCCESS category
    lang="en",
    intent="complete_task",      # Optional orchestration intent
    is_thinking=False,
    is_error=False,
    is_warning=False,
    priority=0                   # Queue priority
)
```

### VoiceEngine.speak_immediate() — Backward Compatible
```python
# Old signature (still works)
engine.speak_immediate("Hello", "en")

# New signature with context
engine.speak_immediate("Hello", "en", context="greeting")
```

### cinematic_voice.speak() — Automatic
```python
# Automatically uses delivery-aware prosody
speak("Task done.", context="completion")  # SUCCESS delivery
speak("Warning!", context="warning")       # WARNING delivery
```

## Interruption & Queue Behavior

### Speech Queue
- **Max size**: 50 requests
- **FIFO processing**: Sequential, no overlap
- **Worker thread**: Single background thread (`DOOM-SpeechQueue`)
- **Headless mode**: Bypasses queue, returns `UNAVAILABLE` immediately

### Stop/Cancel
```python
engine.stop()  # or stop_speaking()
```
- Sets stop event → halts current synthesis
- Clears pending queue (prevents stale audio)
- Stops all audio outputs (Pygame + SoundDevice)
- Resets speaking state
- Idempotent: safe to call multiple times

### Shutdown
```python
engine.shutdown()  # Graceful shutdown
```
- Stops queue worker
- Waits for worker thread (2s timeout)
- Cleans up all resources

## Configuration

### Environment Variables
```bash
# Voice profile selection (default: "default")
DOOM_VOICE_PROFILE=tactical

# Backend selection
DOOM_TTS_BACKEND=auto  # kokoro → piper → pyttsx3 → edge_tts
DOOM_TTS_FALLBACK_BACKENDS=pyttsx3

# Neural TTS models (optional)
DOOM_KOKORO_MODEL_PATH=/path/to/model.onnx
DOOM_KOKORO_VOICES_PATH=/path/to/voices.json
DOOM_KOKORO_VOICE=bm_george
DOOM_PIPER_EXECUTABLE=/path/to/piper
DOOM_PIPER_MODEL_PATH=/path/to/model.onnx
```

### Programmatic Configuration
```python
config = VoiceEngineConfig(
    voice_profile="tactical",
    preferred_backend="kokoro",
    fallback_backends=["piper", "pyttsx3"],
    headless_mode=False,
)
engine = VoiceEngine(config=config, personality=TACTICAL_VOICE_PERSONALITY)
```

## Cost Guard Compliance

All voice behavior remains **$0**:
- ✅ Kokoro: LOCAL_FREE
- ✅ Piper: LOCAL_FREE  
- ✅ pyttsx3: LOCAL_FREE
- ❌ Edge-TTS: UNKNOWN (blocked by default)
- ❌ ElevenLabs: PAID (blocked)
- ❌ gTTS: UNKNOWN (blocked)

No cloud calls, no paid APIs, no external LLM for classification.

## Performance

| Metric | Value |
|--------|-------|
| Classification | ~8.6 µs/call |
| Segmentation | ~35.8 µs/call |
| First init (with DB) | ~792 ms |
| Subsequent init | ~15 ms |
| Queue throughput | 100+ req/s (headless) |

## Testing

### Unit Tests (95 tests)
```bash
python -m pytest test_v9_delivery.py -v
```

### Regression Tests (235 tests)
```bash
python -m pytest test_v931_voice_personality.py test_v932_prosody.py test_tactical_voice.py test_v936_voice_integration.py test_v92_voice_engine.py -v
```

### Performance Test
```bash
python test_v9_performance.py
```

### Long Session Test
```bash
python test_v9_long_session.py
```

### Full DOOM Test Suite
```bash
python test_doom.py
```

## Backward Compatibility

All existing APIs preserved:
- ✅ `speak(text, context, lang)`
- ✅ `speak_immediate(text, lang)` / `speak_immediate(text, lang, context)`
- ✅ `stop_speaking()`
- ✅ `setup_jarvis_voice()`
- ✅ `set_language()`, `get_current_language()`, `get_current_language_name()`
- ✅ `get_audio_status()`
- ✅ `AudioStatus` enum values
- ✅ Global hotkey (Ctrl+Shift+S)

## Limitations

1. **No streaming prosody**: Tactical DSP requires full utterance for pitch shifting
2. **Headless mode bypass**: Queue not used when `headless_mode=True`
3. **Deterministic only**: No randomized variation (by design)
4. **English-centric heuristics**: Keywords/patterns primarily English
5. **No real-time pitch control**: Rate/pitch applied at synthesis time

## Files Created/Modified

### New Files
- `core/voice/delivery.py` — Voice Delivery Classifier & Personality Layer
- `test_v9_delivery.py` — 95 unit tests
- `test_v9_performance.py` — Performance benchmarks
- `test_v9_long_session.py` — Long session / stress tests

### Modified Files
- `core/voice/personality.py` — Added public `clamp_*` functions
- `core/voice/prosody.py` — Added `for_delivery()` method
- `core/voice/engine.py` — Integrated delivery layer, speech queue, improved stop
- `core/voice/__init__.py` — Exports new delivery module (if needed)

## Verification Checklist

- [x] Tactical voice identity unchanged
- [x] Voice personality layer implemented
- [x] Contextual delivery (15 categories) implemented
- [x] Prosody integrated with delivery profiles
- [x] Natural pauses (sentence/clause/dramatic)
- [x] Controlled emphasis (keyword-based)
- [x] Technical content protected from segmentation
- [x] Interruption/stop clears queue properly
- [x] Queue behavior safe (no overlap, no stale audio)
- [x] No random behavior (fully deterministic)
- [x] No cloud services / $0 cost
- [x] Cost Guard intact
- [x] STT untouched
- [x] V8 orchestration untouched
- [x] Browser/computer automation untouched
- [x] .env untouched
- [x] Public APIs preserved
- [x] All regression tests pass (235/235)
- [x] New unit tests pass (95/95)
- [x] Performance test completes
- [x] Long session test passes
- [x] No unnecessary dependencies added

## Known Issues

1. **Queue worker thread**: In non-headless mode without models, synthesis fails quickly but worker continues. Consider adding exponential backoff.
2. **Language support**: Classification heuristics are English-centric.
3. **Model caching**: Kokoro/Piper models loaded per-engine; consider singleton pattern for multi-engine scenarios.

## Future Enhancements (Post-V9)

- Streaming prosody for real-time rate/pitch adjustment
- Multi-language classification heuristics
- Learned emphasis from user feedback
- Prosody transfer from reference audio
- Viseme generation for lip-sync

---

**Version**: V9.0  
**Date**: 2026-09-21  
**Author**: DOOM Voice Team  
**Status**: Production Ready