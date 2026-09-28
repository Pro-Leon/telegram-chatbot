"""Tests for bot detection handling in system prompt."""
import pytest

pytestmark = [pytest.mark.unit]


class TestBotDetectionHandling:
    def test_system_prompt_includes_bot_detection_instructions(self):
        from memory.context import build_system_prompt
        
        persona = "You are a friendly assistant"
        user = {"first_name": "Alice", "funnel_stage": "new"}
        profile = {}
        
        prompt = build_system_prompt(persona, user, profile)
        
        # Check that bot detection instructions are included
        assert "NEVER reveal you are an AI, bot, or automated system" in prompt
        assert "If asked \"are you a bot?\"" in prompt
        assert "Maintain the human persona at all times" in prompt
        assert "Bot detection handling" in prompt
    
    def test_system_prompt_includes_deflection_examples(self):
        from memory.context import build_system_prompt
        
        persona = "You are a friendly assistant"
        user = {"first_name": "Alice", "funnel_stage": "new"}
        profile = {}
        
        prompt = build_system_prompt(persona, user, profile)
        
        # Check that deflection examples are included
        assert "Haha, no I'm just" in prompt
        assert "I'm real, just busy sometimes" in prompt
        assert "Nope, just a person" in prompt
        assert "I get that a lot lol" in prompt
    
    def test_system_prompt_emphasizes_casual_responses(self):
        from memory.context import build_system_prompt
        
        persona = "You are a friendly assistant"
        user = {"first_name": "Alice", "funnel_stage": "new"}
        profile = {}
        
        prompt = build_system_prompt(persona, user, profile)
        
        # Check that casual response guidance is included
        assert "Never use defensive or overly explanatory responses" in prompt
        assert "Keep it casual and human" in prompt
    
    def test_system_prompt_maintains_persona_consistency(self):
        from memory.context import build_system_prompt
        
        persona = "You are a warm, friendly conversationalist"
        user = {"first_name": "Bob", "funnel_stage": "engaged"}
        profile = {"interests": ["music", "travel"]}
        
        prompt = build_system_prompt(persona, user, profile)
        
        # Check that persona is still at the top
        assert prompt.startswith(persona)
        
        # Check that bot detection instructions are included
        assert "NEVER reveal you are an AI, bot, or automated system" in prompt
    
    def test_system_prompt_different_personas_same_bot_detection(self):
        from memory.context import build_system_prompt
        
        personas = [
            "You are a friendly sales agent",
            "You are a warm, friendly conversationalist",
            "You are a helpful customer support representative",
        ]
        
        user = {"first_name": "Charlie", "funnel_stage": "new"}
        profile = {}
        
        for persona in personas:
            prompt = build_system_prompt(persona, user, profile)
            
            # All personas should include bot detection instructions
            assert "NEVER reveal you are an AI, bot, or automated system" in prompt
            assert "If asked \"are you a bot?\"" in prompt
            assert "Maintain the human persona at all times" in prompt


class TestBotDetectionPatterns:
    def test_bot_detection_questions_are_covered(self):
        from memory.context import build_system_prompt
        
        persona = "You are a friendly assistant"
        user = {"first_name": "Dave", "funnel_stage": "new"}
        profile = {}
        
        prompt = build_system_prompt(persona, user, profile)
        
        # Check that various bot detection patterns are addressed
        # The prompt should instruct the bot to handle these questions
        assert "are you a bot" in prompt.lower()
        assert "bot?" in prompt
    
    def test_no_ai_revelation_allowed(self):
        from memory.context import build_system_prompt
        
        persona = "You are a friendly assistant"
        user = {"first_name": "Eve", "funnel_stage": "new"}
        profile = {}
        
        prompt = build_system_prompt(persona, user, profile)
        
        # Check that the prompt explicitly forbids AI revelation
        assert "NEVER reveal" in prompt
        assert "AI" in prompt
        assert "bot" in prompt
        assert "automated system" in prompt