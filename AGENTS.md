# DOOM � Agent Instructions

## Current Verified Git/Version State
The official version lineage is:

\\
DOOM-V8
    ?
DOOM-V9
    ?
DOOM-V10
\\

Current frozen/published state:

**DOOM-V8**
- Branch: DOOM-V8
- Remote: origin/DOOM-V8
- Frozen commit: d70e0edc378b4f035ef170d0f5aa53fcd1045194
- V8 is historical and must remain unchanged.

**DOOM-V9**
- Branch: DOOM-V9
- Remote: origin/DOOM-V9
- Frozen/published commit: fb8fac938f0a411801ab2595f32d3169aec09d6b
- Commit message: V9 final voice architecture and acceptance
- V9 is officially FROZEN.

DOOM-V10 � ACTIVE DEVELOPMENT

V10.0:
- Cognitive Core Architecture is complete.
- Architecture/design phase is complete.

- Current phase: V10.10 Readiness (next phase after V10.9 completion)
- V10.1 Context Fusion.
- V10.1 is complete / accepted.
- V10.1 must be fully implemented and accepted before V10.2.
- The previously attempted V10.1 implementation was rejected and removed.
- Do not assume previous V10.1 code exists.

Approved sequence:
V10.1 ? V10.2 ? V10.3 ? V10.4 ? V10.5 ? V10.6 ? V10.7 ? V10.8 ? V10.9 ? V10.10

Important V10 rules:
- Read AGENTS.md first in every OpenCode session.
- Inspect Git status before changing anything.
- Preserve unrelated dirty working-tree changes.
- No reset.
- No clean.
- No broad revert.
- No force push.
- No history rewrite.
- Do not modify unrelated files.
- Do not commit unless explicitly authorized.
- Do not push unless explicitly authorized.
- HARD $0 recurring-cost requirement.
- STT remains frozen.
- Browser automation remains permanently OFF.
- No secrets in source, reports, or AGENTS.md.
- Reuse existing V8 foundations rather than creating duplicate systems.
- V9 remains frozen unless explicitly authorized maintenance is requested.

V10 architecture pipeline:
1. Input Normalization
2. Context Fusion
3. Goal Understanding
4. Memory + User Model Retrieval
5. Bounded Reasoning
6. Decision
7. Planning when required
8. Authorization / Safety
9. Verification
10. Response Generation
11. V9 Voice / Text Output
12. Outcome / Memory Update

Never assume a later version exists merely because a branch name exists.



## DOOM Project Identity
DOOM is a Personal AI OS / personal AI assistant.

DOOM is NOT intended to be treated as a simple chatbot.

The long-term architecture is intended to behave more like an intelligent personal operating layer with:
- voice interaction
- local speech recognition
- cognitive context
- memory
- user model
- goals
- reasoning
- planning
- verification
- orchestration
- controlled tools/actions
- durable personal context

The system should behave like a human-oriented assistant while maintaining strict architectural safety and cost boundaries.



## V8 Historical Cognitive Foundation
V8 established the foundational cognitive architecture that later versions build upon.

Important V8 capabilities include:
- situational intelligence
- continuous context
- grounded references
- bounded reasoning
- decision engine
- informational next-step planning
- plan refinement and validation
- durable recovery
- adaptive goal continuity
- Durable Active Goal Registry
- User Model / Personal Cognitive Profile
- Durable Goal Experience / Outcome Memory

Important architectural concepts include:
- context
- goals
- durable goal state
- user model
- planning
- bounded reasoning
- verification
- orchestration
- local persistence
- owner-scoped state

Do not casually replace these foundations with a new parallel architecture.

Before modifying an existing subsystem, inspect how it currently works.



## V9 Status � FROZEN
V9 is COMPLETE and FROZEN.

V9 focused on the DOOM voice architecture and conversational voice delivery.

V9 official commit: fb8fac938f0a411801ab2595f32d3169aec09d6b
Commit message: V9 final voice architecture and acceptance

V9 must be treated as a frozen baseline.

Do NOT redesign V9 merely because a future improvement could be imagined.

Future changes should normally belong in V10 or a later version unless explicitly authorized as maintenance.



## V9 Voice Architecture
The V9 voice architecture is:

\\
DOOM Brain
    ?
Voice Personality
    ?
Speech Style / Prosody
    ?
Voice Engine
    ?
TTS Backend
    ?
Optional Tactical DSP
    ?
Audio Output
    ?
Speaker
\\

Important components include:
- core/voice/engine.py
- core/voice/personality.py
- core/voice/prosody.py
- core/voice/delivery.py
- core/voice/dsp.py
- core/voice/backends/
- core/voice/outputs/
- core/cinematic_voice.py

V9 provides:
- VoiceEngine facade
- TTSBackend abstraction
- AudioOutput abstraction
- Voice Personality
- Speech Style
- deterministic ProsodyController
- conversational Delivery classification
- bounded voice queue
- generation-based cancellation
- backend fallback
- audio output validation
- graceful shutdown/restart
- local Cost Guard enforcement



## V9 Voice Identity
DOOM's voice must remain an ORIGINAL voice identity.

The design may use broad characteristics such as:
- deep male
- powerful
- commanding
- authoritative
- intimidating
- sophisticated
- theatrical
- calm confidence
- deliberate speech
- controlled emotion
- dramatic pauses
- mature
- dark/guttural
- subtle synthetic/metallic qualities
- futuristic/intelligent

DO NOT attempt to clone, impersonate, or reproduce the exact recognizable voice of:
- JARVIS
- Tony Stark's JARVIS
- Robert Downey Jr.
- Ultron
- an actor's specific performance
- Marvel's Doctor Doom
- another identifiable real person's voice

The Tactical profile is an original DOOM voice identity.



## Tactical Voice Profile
The selected V9 production voice profile is:

tactical

Current architecture:
\\
DOOM Brain
? VoiceEngine
? Tactical Personality
? ProsodyController
? Kokoro bm_george
? Tactical DSP
? SoundDeviceOutput
\\

Primary tactical parameters include:
- pitch: approximately -3.0 semitones
- low-mid: +2.2 dB
- compression: 2.5:1 at -20
- saturation: 5%
- HF adjustment: 0
- spectral tilt: -1.0
- ring modulation: 0

Do not change the Tactical identity casually.

Any future voice identity modification requires explicit version scope.



## V9 Delivery System
V9 contains 15 response categories:
- NORMAL
- QUESTION
- CONFIRMATION
- EXPLANATION
- INSTRUCTION
- COMMAND
- WARNING
- ERROR
- SUCCESS
- STATUS
- THINKING
- CRITICAL
- HUMOR
- GREETING
- FAREWELL

Delivery classification is deterministic and local.

Technical content must remain protected from inappropriate conversational transformations.

V9 also contains deterministic prosody modes:
- NORMAL
- ALERT
- URGENT
- CONFIDENT
- HUMOR
- THINKING
- COMMAND
- CRITICAL

Prosody must remain deterministic.

No random emotional behavior should be introduced without explicit authorization.



## V9 TTS Backends
Current architecture includes:
- Kokoro
- Piper
- pyttsx3
- Edge-TTS interface/gating

Important Cost Guard behavior:
- Kokoro: LOCAL_FREE
- Piper: LOCAL_FREE
- pyttsx3: LOCAL_FREE
- Edge-TTS: blocked when Cost Guard classifies it as non-free/unknown
- paid cloud TTS: blocked
- legacy paid/unknown providers must not bypass Cost Guard

The project operates under a HARD  recurring-cost requirement.

Never bypass Cost Guard simply to make a feature work.



## STT � FROZEN
Speech-to-text uses local Whisper/faster-whisper infrastructure.

STT is FROZEN unless explicitly included in a future version scope.

Do NOT redesign, replace, or casually modify the STT pipeline while working on unrelated features.

Current important component:
- core/stt/local_whisper.py



## Browser / Computer Automation
Browser automation is permanently OFF after the V8.11 NO-GO decision.

Do not enable browser automation.

Do not work around the browser-off policy.

Do not silently introduce browser drivers or browser execution paths.

If a future version explicitly revisits this, it must be an explicit architectural decision.



## Cost Requirement
DOOM has a HARD  Cost Guard requirement.

Default principle:
LOCAL + FREE + OFFLINE where possible.

Never introduce:
- paid APIs
- paid cloud inference
- paid TTS
- paid STT
- hidden recurring services

without explicit project authorization and version scope.

Never weaken or bypass Cost Guard.



## Important Entry Points
**Desktop/voice entry point:**
\\
python doom.py
\\

Do NOT use doom.py blindly for automated live testing because it can activate voice/STT behavior and may interact with unrelated runtime dependencies.

**Dashboard:**
\\
python .\run_dashboard.py
\\

**Safe Test Window:**
\\
python tools/doom_safe_test_window.py
\\

The Safe Test Window provides controlled testing through native Windows UI behavior.

Important test identifiers include:
- DOOM_TEST_BUTTON
- DOOM_TEST_INPUT

Typical state transition:
READY ? CLICKED

Do not request, print, expose, or persist:
DOOM_ASK_UNLOCK

Do not expose cookies, CSRF tokens, credentials, or secrets.



## Testing Principles
Every meaningful implementation must include:
1. focused tests
2. regression tests
3. architecture verification
4. security/cost verification where relevant
5. final implementation report

Do not declare a phase complete merely because one focused test passes.

When possible:
- run focused tests first
- then relevant regression suites
- investigate failures
- distinguish new failures from known baseline failures
- do not silently ignore regressions
- document limitations honestly

Do not modify unrelated baseline behavior just to make a new phase appear green.



## Unrelated Local Work
Previous V9 release work intentionally preserved unrelated local modifications, including areas such as:
- .gitignore
- older DOOM release reports
- doom.py command-handling changes
- orchestration/
- proactive/
- various V8 tests
- local scripts
- local test files
- STT models
- voice evaluation output
- generated/local assets

Do not assume these are part of V9.

Before committing anything, inspect Git status and identify exactly what belongs to the requested version/feature.



## AGENTS.md Rules
This AGENTS.md is permanent project guidance.

Keep it focused on stable information.

Do NOT turn AGENTS.md into a chronological transcript of every implementation step.

For detailed historical implementation information, prefer existing reports and documentation such as:
- V9_FINAL_ACCEPTANCE_REPORT.md
- V9_VOICE_HARDENING_REPORT.md
- V9_VOICE_PERSONALITY_IMPLEMENTATION_REPORT.md
- TACTICAL_VOICE_IMPLEMENTATION_REPORT.md

When future versions are completed, update AGENTS.md only with stable architectural facts that future OpenCode sessions need.



## DOOM-V10 � ACTIVE DEVELOPMENT

- Branch: DOOM-V10
- Current development baseline: V9 frozen architecture plus approved V10 architecture work.
- V10.0 Cognitive Core Architecture is COMPLETE.
- V10.1 Context Fusion is COMPLETE / ACCEPTED
- V10.2 Memory + User Model Integration is COMPLETE / ACCEPTED
- V10.3 Goal Understanding + Continuity is COMPLETE / ACCEPTED
- V10.4 Reasoning + Decision Integration is COMPLETE / ACCEPTED
- V10.5 Planning Integration is COMPLETE / ACCEPTED
- V10.6 Cognitive Orchestrator is COMPLETE / ACCEPTED
- V10.7 Safety / Verification / Cost Hardening is COMPLETE / ACCEPTED
- V10.8 End-to-End Cognitive Integration is COMPLETE / ACCEPTED
- V10.9 Performance / Reliability / Long-Session is COMPLETE / ACCEPTED
- Approved V10 sequence:
  V10.1 Context Fusion
  V10.2 Memory + User Model Integration
  V10.3 Goal Understanding + Continuity
  V10.4 Reasoning + Decision Integration
  V10.5 Planning Integration
  V10.6 Cognitive Orchestrator
  V10.7 Safety / Verification / Cost Hardening
  V10.8 End-to-End Cognitive Integration
  V10.9 Performance / Reliability / Long-Session
  V10.10 Final Acceptance / Freeze

The exact current Git HEAD may be checked from Git and must not be hardcoded as the V10 feature branch's permanent baseline unless verified.

Add/retain this rule:

V10 development is authorized for the approved V10.1 ? V10.10 scope.

Do NOT change V8 or frozen V9 architecture unless a V10 phase explicitly requires a compatibility-preserving integration and the change is within the active phase scope.



## V10 Development Task Rules

When V10 feature development is active, the following rules apply:

1. Read AGENTS.md first.
2. Inspect Git status and current branch.
3. Inspect the actual V10 architecture and current implementation.
4. Preserve unrelated pre-existing working-tree changes.
5. Never reset or clean the repository to make the tree appear clean.
6. Never modify unrelated files merely to satisfy tests.
7. Work only within the current V10 phase.
8. Complete the current phase end-to-end before advancing.
9. Run focused tests.
10. Run relevant regression tests.
11. Distinguish pre-existing failures from regressions introduced by V10.
12. Perform security/privacy/owner-isolation/Cost Guard checks where relevant.
13. Do not proceed to the next V10 phase if the current phase's mandatory acceptance gate fails.
14. Do not claim completion without evidence.
15. Do not commit or push unless explicitly authorized.
16. Do not modify AGENTS.md again unless a future stable architectural fact genuinely needs updating.

IMPORTANT:
The existing repository has a deliberately preserved dirty work tree containing earlier DOOM work and local artifacts. Do not clean, reset, revert, or delete those files.

# The V10.9 implementation was completed and accepted as part of V10.9 Performance / Reliability / Long-Session.
- core/v10/context_fusion.py (V10.1)
- test_v10_1_context_fusion.py (V10.1)
- test_v10_2_memory_user_model_integration.py (V10.2)
- test_v10_3_goal_understanding_continuity.py (V10.3)
- test_v10_4_reasoning_decision_integration.py (V10.4)
- test_v10_5_planning_integration.py (V10.5)
- test_v10_6_cognitive_orchestrator.py (V10.6)
- test_v10_6_planning.py (V10.6)
- test_v10_7_safety_verification_cost_hardening.py (V10.7)
- test_v10_8_end_to_end_integration.py (V10.8)
- test_v10_9_performance_reliability_long_session.py (V10.9)

# The V10.9 Performance / Reliability / Long-Session implementation is complete and accepted.
# Proceed to V10.10 Final Acceptance / Freeze.
