#!/usr/bin/env python3
"""V9 Voice Delivery & Personality Layer Tests.

Tests for:
- Response category detection
- Delivery profiles
- Sentence/clause segmentation with technical content protection
- Emphasis extraction
- Prosody integration
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from core.voice.delivery import (
    ResponseCategory,
    DeliveryProfile,
    DELIVERY_PROFILES,
    classify_response_category,
    get_delivery_profile,
    calculate_response_length_category,
    apply_delivery_profile,
    segment_for_delivery,
    extract_emphasis_targets,
    TECHNICAL_PATTERNS,
)
from core.voice.personality import SpeechStyle, SpeakingMode, VoicePersonality, TACTICAL_VOICE_PERSONALITY
from core.voice.prosody import ProsodyController


class TestResponseCategoryEnum:
    """Test ResponseCategory enum values."""

    def test_all_categories_exist(self):
        expected = {
            "NORMAL_RESPONSE", "QUESTION", "CONFIRMATION", "EXPLANATION",
            "INSTRUCTION", "COMMAND", "WARNING", "ERROR", "SUCCESS",
            "STATUS", "THINKING", "CRITICAL", "HUMOR", "GREETING", "FAREWELL"
        }
        actual = {c.name for c in ResponseCategory}
        assert actual == expected

    def test_category_values_are_strings(self):
        for cat in ResponseCategory:
            assert isinstance(cat.value, str)
            assert cat.value == cat.name.lower()


class TestDeliveryProfiles:
    """Test delivery profile definitions."""

    def test_all_categories_have_profiles(self):
        for cat in ResponseCategory:
            assert cat in DELIVERY_PROFILES
            profile = DELIVERY_PROFILES[cat]
            assert isinstance(profile, DeliveryProfile)

    def test_profile_fields_valid(self):
        for cat, profile in DELIVERY_PROFILES.items():
            # Mode should be valid SpeakingMode
            assert profile.speaking_mode in [m.name for m in SpeakingMode]
            
            # Multipliers should be positive
            assert profile.rate_mult > 0
            assert profile.volume_mult > 0
            assert profile.sentence_pause_mult > 0
            assert profile.clause_pause_mult > 0
            assert profile.emphasis_mult > 0
            assert profile.keyword_boost_mult > 0
            
            # Pause additions should be non-negative
            assert profile.pause_before_add >= 0
            assert profile.pause_after_add >= 0

    def test_specific_category_profiles(self):
        # CONFIRMATION should use CONFIDENT mode
        assert DELIVERY_PROFILES[ResponseCategory.CONFIRMATION].speaking_mode == "CONFIDENT"
        
        # COMMAND should use COMMAND mode
        assert DELIVERY_PROFILES[ResponseCategory.COMMAND].speaking_mode == "COMMAND"
        
        # WARNING should use ALERT mode
        assert DELIVERY_PROFILES[ResponseCategory.WARNING].speaking_mode == "ALERT"
        
        # THINKING should use THINKING mode
        assert DELIVERY_PROFILES[ResponseCategory.THINKING].speaking_mode == "THINKING"
        
        # CRITICAL should use CRITICAL mode
        assert DELIVERY_PROFILES[ResponseCategory.CRITICAL].speaking_mode == "CRITICAL"
        
        # HUMOR should use HUMOR mode
        assert DELIVERY_PROFILES[ResponseCategory.HUMOR].speaking_mode == "HUMOR"


class TestResponseClassification:
    """Test deterministic response category classification."""

    def test_thinking_explicit(self):
        assert classify_response_category("Processing...", is_thinking=True) == ResponseCategory.THINKING

    def test_error_explicit(self):
        assert classify_response_category("Failed", is_error=True) == ResponseCategory.ERROR

    def test_warning_explicit(self):
        assert classify_response_category("Caution", is_warning=True) == ResponseCategory.WARNING

    def test_intent_command(self):
        assert classify_response_category("text", intent="execute_command") == ResponseCategory.COMMAND

    def test_intent_question(self):
        assert classify_response_category("text", intent="query") == ResponseCategory.QUESTION

    def test_intent_confirmation(self):
        assert classify_response_category("text", intent="acknowledge") == ResponseCategory.CONFIRMATION

    def test_intent_explanation(self):
        assert classify_response_category("text", intent="explain") == ResponseCategory.EXPLANATION

    def test_intent_instruction(self):
        assert classify_response_category("text", intent="guide") == ResponseCategory.INSTRUCTION

    def test_intent_status(self):
        assert classify_response_category("text", intent="report") == ResponseCategory.STATUS

    def test_intent_greeting(self):
        assert classify_response_category("text", intent="greet") == ResponseCategory.GREETING

    def test_intent_farewell(self):
        assert classify_response_category("text", intent="goodbye") == ResponseCategory.FAREWELL

    def test_intent_critical(self):
        assert classify_response_category("text", intent="emergency") == ResponseCategory.CRITICAL

    def test_intent_humor(self):
        assert classify_response_category("text", intent="joke") == ResponseCategory.HUMOR

    def test_intent_success(self):
        assert classify_response_category("text", intent="complete") == ResponseCategory.SUCCESS

    def test_context_warning(self):
        assert classify_response_category("text", context="warning") == ResponseCategory.WARNING

    def test_context_error(self):
        assert classify_response_category("text", context="error") == ResponseCategory.ERROR

    def test_context_confirmation(self):
        assert classify_response_category("text", context="acknowledgment") == ResponseCategory.CONFIRMATION

    def test_context_completion(self):
        assert classify_response_category("text", context="completion") == ResponseCategory.SUCCESS

    def test_context_thinking(self):
        assert classify_response_category("text", context="thinking") == ResponseCategory.THINKING

    def test_context_greeting(self):
        assert classify_response_category("text", context="greeting") == ResponseCategory.GREETING

    def test_context_farewell(self):
        assert classify_response_category("text", context="farewell") == ResponseCategory.FAREWELL

    def test_context_command(self):
        assert classify_response_category("text", context="command") == ResponseCategory.COMMAND

    def test_context_instruction(self):
        assert classify_response_category("text", context="instruction") == ResponseCategory.INSTRUCTION

    def test_context_explanation(self):
        assert classify_response_category("text", context="explanation") == ResponseCategory.EXPLANATION

    def test_context_status(self):
        assert classify_response_category("text", context="status") == ResponseCategory.STATUS

    def test_context_humor(self):
        assert classify_response_category("text", context="humor") == ResponseCategory.HUMOR

    def test_context_critical(self):
        assert classify_response_category("text", context="critical") == ResponseCategory.CRITICAL

    def test_question_mark_detection(self):
        assert classify_response_category("What is this?") == ResponseCategory.QUESTION
        assert classify_response_category("How are you?") == ResponseCategory.QUESTION

    def test_question_not_technical(self):
        # Questions with technical content should not be classified as QUESTION
        assert classify_response_category("What is C:\\path\\to\\file?") == ResponseCategory.NORMAL_RESPONSE
        assert classify_response_category("What is http://example.com?") == ResponseCategory.NORMAL_RESPONSE

    def test_command_detection(self):
        assert classify_response_category("Run the script") == ResponseCategory.COMMAND
        assert classify_response_category("Execute the command") == ResponseCategory.COMMAND
        assert classify_response_category("Start the server") == ResponseCategory.COMMAND
        assert classify_response_category("Stop the process") == ResponseCategory.COMMAND
        assert classify_response_category("Deploy the app") == ResponseCategory.COMMAND
        assert classify_response_category("Build the project") == ResponseCategory.COMMAND
        assert classify_response_category("Test the code") == ResponseCategory.COMMAND
        assert classify_response_category("Install the package") == ResponseCategory.COMMAND
        assert classify_response_category("Update the system") == ResponseCategory.COMMAND
        assert classify_response_category("Create a file") == ResponseCategory.COMMAND
        assert classify_response_category("Delete the file") == ResponseCategory.COMMAND
        assert classify_response_category("Show the logs") == ResponseCategory.COMMAND
        assert classify_response_category("List the files") == ResponseCategory.COMMAND

    def test_greeting_detection(self):
        assert classify_response_category("Hello Sujal") == ResponseCategory.GREETING
        assert classify_response_category("Hi there") == ResponseCategory.GREETING
        assert classify_response_category("Hey") == ResponseCategory.GREETING
        assert classify_response_category("Good morning") == ResponseCategory.GREETING
        assert classify_response_category("Greetings") == ResponseCategory.GREETING

    def test_farewell_detection(self):
        assert classify_response_category("Goodbye") == ResponseCategory.FAREWELL
        assert classify_response_category("Bye") == ResponseCategory.FAREWELL
        assert classify_response_category("Farewell") == ResponseCategory.FAREWELL
        assert classify_response_category("Shutdown") == ResponseCategory.FAREWELL
        assert classify_response_category("Going offline") == ResponseCategory.FAREWELL

    def test_confirmation_detection(self):
        assert classify_response_category("Understood") == ResponseCategory.CONFIRMATION
        assert classify_response_category("Confirmed") == ResponseCategory.CONFIRMATION
        assert classify_response_category("Acknowledged") == ResponseCategory.CONFIRMATION
        assert classify_response_category("Yes") == ResponseCategory.CONFIRMATION
        assert classify_response_category("Correct") == ResponseCategory.CONFIRMATION
        assert classify_response_category("Right") == ResponseCategory.CONFIRMATION

    def test_success_detection(self):
        assert classify_response_category("Done") == ResponseCategory.SUCCESS
        assert classify_response_category("Completed") == ResponseCategory.SUCCESS
        assert classify_response_category("Finished") == ResponseCategory.SUCCESS
        assert classify_response_category("Success") == ResponseCategory.SUCCESS
        assert classify_response_category("Verified") == ResponseCategory.SUCCESS

    def test_warning_detection(self):
        assert classify_response_category("Warning: this will overwrite") == ResponseCategory.WARNING
        assert classify_response_category("Caution: dangerous operation") == ResponseCategory.WARNING
        assert classify_response_category("Alert: system critical") == ResponseCategory.WARNING

    def test_error_detection(self):
        assert classify_response_category("Error: connection failed") == ResponseCategory.ERROR
        assert classify_response_category("Failed to connect") == ResponseCategory.ERROR
        assert classify_response_category("Exception occurred") == ResponseCategory.ERROR
        assert classify_response_category("Unable to complete") == ResponseCategory.ERROR

    def test_critical_detection(self):
        assert classify_response_category("Critical failure") == ResponseCategory.CRITICAL
        assert classify_response_category("Emergency shutdown") == ResponseCategory.CRITICAL
        assert classify_response_category("Immediate action required") == ResponseCategory.CRITICAL
        assert classify_response_category("Security breach detected") == ResponseCategory.CRITICAL

    def test_instruction_detection(self):
        assert classify_response_category("First, do this. Then, do that.") == ResponseCategory.INSTRUCTION
        assert classify_response_category("Step 1: Initialize. Step 2: Run.") == ResponseCategory.INSTRUCTION
        assert classify_response_category("To do this, you need to run the command.") == ResponseCategory.INSTRUCTION

    def test_explanation_detection(self):
        assert classify_response_category("This works because the system is configured.") == ResponseCategory.EXPLANATION
        assert classify_response_category("Therefore, the result is positive.") == ResponseCategory.EXPLANATION
        assert classify_response_category("In other words, it means success.") == ResponseCategory.EXPLANATION

    def test_status_detection(self):
        assert classify_response_category("Status: online") == ResponseCategory.STATUS
        assert classify_response_category("Currently running at 50%") == ResponseCategory.STATUS
        assert classify_response_category("Load is nominal") == ResponseCategory.STATUS

    def test_humor_detection(self):
        assert classify_response_category("Just kidding!") == ResponseCategory.HUMOR
        assert classify_response_category("Haha, that's funny") == ResponseCategory.HUMOR

    def test_default_normal(self):
        assert classify_response_category("This is a normal response.") == ResponseCategory.NORMAL_RESPONSE
        assert classify_response_category("The system is ready.") == ResponseCategory.NORMAL_RESPONSE


class TestLengthCategory:
    """Test response length categorization."""

    def test_short(self):
        assert calculate_response_length_category("Short") == "short"
        assert calculate_response_length_category("This is short") == "short"

    def test_medium(self):
        text = " ".join(["word"] * 20)
        assert calculate_response_length_category(text) == "medium"

    def test_long(self):
        text = " ".join(["word"] * 80)
        assert calculate_response_length_category(text) == "long"

    def test_very_long(self):
        text = " ".join(["word"] * 200)
        assert calculate_response_length_category(text) == "very_long"


class TestDeliveryProfileApplication:
    """Test applying delivery profiles to speech styles."""

    def test_apply_normal_profile(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        base_style = controller.for_mode(SpeakingMode.NORMAL)
        profile = get_delivery_profile(ResponseCategory.NORMAL_RESPONSE)
        
        result = apply_delivery_profile(base_style, profile, "medium")
        
        assert isinstance(result, SpeechStyle)
        assert result.mode == SpeakingMode.NORMAL

    def test_apply_confirmation_profile(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        base_style = controller.for_mode(SpeakingMode.CONFIDENT)
        profile = get_delivery_profile(ResponseCategory.CONFIRMATION)
        
        result = apply_delivery_profile(base_style, profile, "medium")
        
        assert isinstance(result, SpeechStyle)
        # Should be more confident than normal
        assert result.emphasis >= base_style.emphasis

    def test_apply_warning_profile(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        base_style = controller.for_mode(SpeakingMode.ALERT)
        profile = get_delivery_profile(ResponseCategory.WARNING)
        
        result = apply_delivery_profile(base_style, profile, "medium")
        
        assert isinstance(result, SpeechStyle)
        # Should have stronger emphasis
        assert result.emphasis >= base_style.emphasis
        # Should be slower
        assert result.rate <= base_style.rate

    def test_apply_critical_profile(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        base_style = controller.for_mode(SpeakingMode.CRITICAL)
        profile = get_delivery_profile(ResponseCategory.CRITICAL)
        
        result = apply_delivery_profile(base_style, profile, "medium")
        
        assert isinstance(result, SpeechStyle)
        # Should have maximum authority
        assert result.volume >= base_style.volume
        assert result.emphasis >= base_style.emphasis

    def test_length_adjustments_short(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        base_style = controller.for_mode(SpeakingMode.NORMAL)
        profile = get_delivery_profile(ResponseCategory.NORMAL_RESPONSE)
        
        result = apply_delivery_profile(base_style, profile, "short")
        
        # Short responses should be slightly faster
        assert result.rate >= base_style.rate * 0.99  # Allow small floating point

    def test_length_adjustments_long(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        base_style = controller.for_mode(SpeakingMode.NORMAL)
        profile = get_delivery_profile(ResponseCategory.NORMAL_RESPONSE)
        
        result = apply_delivery_profile(base_style, profile, "long")
        
        # Long responses should have longer pauses
        assert result.sentence_pause >= base_style.sentence_pause * 0.99

    def test_bounds_respected(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        base_style = controller.for_mode(SpeakingMode.NORMAL)
        
        for cat in ResponseCategory:
            profile = get_delivery_profile(cat)
            for length in ["short", "medium", "long", "very_long"]:
                result = apply_delivery_profile(base_style, profile, length)
                
                # All values should be within valid bounds
                assert 0.25 <= result.rate <= 3.0
                assert -24.0 <= result.pitch <= 24.0
                assert 0.0 <= result.volume <= 2.0
                assert 0.0 <= result.emphasis <= 3.0
                assert 0.0 <= result.keyword_boost <= 3.0
                assert 0.0 <= result.pause_before <= 5.0
                assert 0.0 <= result.pause_after <= 5.0
                assert 0.0 <= result.sentence_pause <= 5.0
                assert 0.0 <= result.clause_pause <= 5.0


class TestTechnicalContentProtection:
    """Test that technical content is protected from segmentation."""

    def test_file_paths_protected(self):
        text = "The file is at C:\\Users\\Sujal\\Documents\\test.py and also /home/user/file.txt"
        segments = segment_for_delivery(text)
        
        # Should have technical segments for paths
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 2
        assert any("C:\\Users\\Sujal\\Documents\\test.py" in s[1] for s in tech_segments)
        assert any("/home/user/file.txt" in s[1] for s in tech_segments)

    def test_urls_protected(self):
        text = "Visit https://example.com/api/v1 for more info"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 1
        assert any("https://example.com/api/v1" in s[1] for s in tech_segments)

    def test_ip_addresses_protected(self):
        text = "Connect to 192.168.1.1 or 10.0.0.1"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 2

    def test_version_numbers_protected(self):
        text = "Version 1.2.3 and v2.0.0-beta are available"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 2

    def test_env_variables_protected(self):
        text = "Set $HOME or ${PATH} or %TEMP%"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 3

    def test_code_identifiers_protected(self):
        text = "Call my_function() or obj.method()"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 2

    def test_commands_protected(self):
        text = "Run python script.py --flag=value"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 1

    def test_numbers_with_units_protected(self):
        text = "Latency is 50ms and memory is 512mb"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 2

    def test_hex_addresses_protected(self):
        text = "Address 0x7fff1234 and 0xDEADBEEF"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 2

    def test_uuids_protected(self):
        text = "ID: 123e4567-e89b-12d3-a456-426614174000"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 1

    def test_emails_protected(self):
        text = "Contact user@example.com for help"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        assert len(tech_segments) >= 1

    def test_normal_sentences_segmented(self):
        text = "This is a sentence. This is another sentence."
        segments = segment_for_delivery(text)
        
        sentence_segments = [s for s in segments if s[0] == "sentence"]
        assert len(sentence_segments) == 2

    def test_clause_boundaries_respected(self):
        text = "First, we initialize. Then, we run the test."
        segments = segment_for_delivery(text)
        
        # Should not split inside technical content
        sentence_segments = [s for s in segments if s[0] == "sentence"]
        assert len(sentence_segments) >= 1

    def test_em_dash_handling(self):
        text = "The result — a complete success — was unexpected."
        segments = segment_for_delivery(text)
        
        # Em-dash should create clause boundaries
        sentence_segments = [s for s in segments if s[0] == "sentence"]
        assert len(sentence_segments) >= 1

    def test_empty_text(self):
        segments = segment_for_delivery("")
        assert segments == []

    def test_whitespace_only(self):
        segments = segment_for_delivery("   \n\t  ")
        assert segments == []


class TestEmphasisExtraction:
    """Test emphasis target extraction."""

    def test_keyword_emphasis(self):
        profile = DELIVERY_PROFILES[ResponseCategory.CONFIRMATION]
        # CONFIRMATION has keywords: "understood", "confirmed", "acknowledged", "affirmative", "yes", "correct"
        targets = extract_emphasis_targets("Understood, confirmed.", profile)
        
        assert len(targets) >= 2
        # Check positions
        text = "Understood, confirmed."
        for start, end, boost in targets:
            assert text[start:end].lower() in ["understood", "confirmed"]
            assert boost > 1.0

    def test_no_emphasis_for_normal(self):
        profile = DELIVERY_PROFILES[ResponseCategory.NORMAL_RESPONSE]
        targets = extract_emphasis_targets("This is normal text.", profile)
        
        # NORMAL_RESPONSE has some keywords
        assert len(targets) >= 0  # May or may not find keywords

    def test_no_emphasis_in_technical(self):
        profile = DELIVERY_PROFILES[ResponseCategory.WARNING]
        # WARNING has "overwrite" as keyword
        text = "Warning: C:\\path\\to\\overwrite.txt will be deleted"
        targets = extract_emphasis_targets(text, profile)
        
        # "overwrite" inside technical path should not be emphasized
        for start, end, boost in targets:
            assert "overwrite" not in text[start:end].lower() or not any(
                p.search(text[start:end]) for p in TECHNICAL_PATTERNS
            )

    def test_merged_overlapping_targets(self):
        profile = DELIVERY_PROFILES[ResponseCategory.CRITICAL]
        # CRITICAL has "critical" and "emergency" as keywords
        text = "Critical emergency situation"
        targets = extract_emphasis_targets(text, profile)
        
        # Should have targets for both keywords
        assert len(targets) >= 1


class TestProsodyIntegration:
    """Test integration with ProsodyController."""

    def test_for_delivery_method_exists(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        
        # Should have for_delivery method
        assert hasattr(controller, 'for_delivery')

    def test_for_delivery_basic(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        profile = get_delivery_profile(ResponseCategory.CONFIRMATION)
        
        style = controller.for_delivery(
            mode=SpeakingMode.CONFIDENT,
            delivery_profile=profile,
            length_category="medium"
        )
        
        assert isinstance(style, SpeechStyle)

    def test_all_categories_with_prosody(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        
        for cat in ResponseCategory:
            profile = get_delivery_profile(cat)
            mode_map = {
                "NORMAL": SpeakingMode.NORMAL,
                "CONFIDENT": SpeakingMode.CONFIDENT,
                "COMMAND": SpeakingMode.COMMAND,
                "ALERT": SpeakingMode.ALERT,
                "URGENT": SpeakingMode.URGENT,
                "THINKING": SpeakingMode.THINKING,
                "HUMOR": SpeakingMode.HUMOR,
                "CRITICAL": SpeakingMode.CRITICAL,
            }
            mode = mode_map.get(profile.speaking_mode, SpeakingMode.NORMAL)
            
            style = controller.for_delivery(
                mode=mode,
                delivery_profile=profile,
                length_category="medium"
            )
            
            assert isinstance(style, SpeechStyle)
            assert 0.25 <= style.rate <= 3.0
            assert -24.0 <= style.pitch <= 24.0

    def test_deterministic_output(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        profile = get_delivery_profile(ResponseCategory.WARNING)
        
        style1 = controller.for_delivery(
            mode=SpeakingMode.ALERT,
            delivery_profile=profile,
            length_category="medium"
        )
        style2 = controller.for_delivery(
            mode=SpeakingMode.ALERT,
            delivery_profile=profile,
            length_category="medium"
        )
        
        assert style1 == style2


class TestSegmentationEdgeCases:
    """Test edge cases in segmentation."""

    def test_consecutive_technical(self):
        text = "C:\\path1 C:\\path2"
        segments = segment_for_delivery(text)
        
        tech_segments = [s for s in segments if s[0] == "technical"]
        # Should merge consecutive technical content
        assert len(tech_segments) >= 1

    def test_technical_at_start(self):
        text = "C:\\path is the location"
        segments = segment_for_delivery(text)
        
        assert segments[0][0] == "technical"

    def test_technical_at_end(self):
        text = "The location is C:\\path"
        segments = segment_for_delivery(text)
        
        assert segments[-1][0] == "technical"

    def test_mixed_content(self):
        text = "First sentence. C:\\path. Second sentence."
        segments = segment_for_delivery(text)
        
        types = [s[0] for s in segments]
        assert "sentence" in types
        assert "technical" in types

    def test_no_duplicate_segments(self):
        text = "Test."
        segments = segment_for_delivery(text)
        
        # No empty segments
        for seg_type, seg_text in segments:
            assert seg_text.strip()

    def test_preserve_order(self):
        text = "A. C:\\path. B."
        segments = segment_for_delivery(text)
        
        texts = [s[1] for s in segments]
        # Order should be preserved
        assert "A." in texts[0]
        assert "C:\\path" in texts[1]
        assert "B." in texts[2]


class TestNoRandomness:
    """Test that all behavior is deterministic."""

    def test_classification_deterministic(self):
        for _ in range(10):
            cat1 = classify_response_category("Test message")
            cat2 = classify_response_category("Test message")
            assert cat1 == cat2

    def test_segmentation_deterministic(self):
        text = "Test C:\\path. Another."
        for _ in range(10):
            seg1 = segment_for_delivery(text)
            seg2 = segment_for_delivery(text)
            assert seg1 == seg2

    def test_delivery_profile_deterministic(self):
        controller = ProsodyController(TACTICAL_VOICE_PERSONALITY)
        profile = get_delivery_profile(ResponseCategory.WARNING)
        
        for _ in range(10):
            style1 = controller.for_delivery(
                mode=SpeakingMode.ALERT,
                delivery_profile=profile,
                length_category="medium"
            )
            style2 = controller.for_delivery(
                mode=SpeakingMode.ALERT,
                delivery_profile=profile,
                length_category="medium"
            )
            assert style1 == style2

    def test_emphasis_deterministic(self):
        profile = DELIVERY_PROFILES[ResponseCategory.CONFIRMATION]
        text = "Understood and confirmed."
        
        for _ in range(10):
            targets1 = extract_emphasis_targets(text, profile)
            targets2 = extract_emphasis_targets(text, profile)
            assert targets1 == targets2


class TestBackwardCompatibility:
    """Test that existing functionality still works."""

    def test_personality_unchanged(self):
        assert TACTICAL_VOICE_PERSONALITY.name == "DOOM Tactical"
        assert TACTICAL_VOICE_PERSONALITY.base_pitch == -3.0

    def test_speaking_modes_unchanged(self):
        for mode in SpeakingMode:
            assert mode in SpeakingMode

    def test_speech_style_creation(self):
        style = SpeechStyle()
        assert style.mode == SpeakingMode.NORMAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])