"""Tests for AI-native canary phase 2 - observability and quality gates."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.telemetry import GenerationTelemetry, TelemetryCollector, get_telemetry_collector


class TestGenerationTelemetry:
    """Test generation telemetry dataclass."""
    
    def test_creation(self):
        """Test telemetry creation with defaults."""
        telemetry = GenerationTelemetry()
        assert telemetry.generation_id is not None
        assert telemetry.user_id == 0
        assert telemetry.runtime_mode == "legacy"
        assert telemetry.success is True
        assert telemetry.started_at > 0
    
    def test_complete_success(self):
        """Test marking telemetry as complete."""
        telemetry = GenerationTelemetry()
        telemetry.complete(success=True)
        assert telemetry.success is True
        assert telemetry.completed_at is not None
        assert telemetry.total_e2e_latency_ms >= 0
    
    def test_complete_failure(self):
        """Test marking telemetry as failed."""
        telemetry = GenerationTelemetry()
        telemetry.complete(success=False, failure_type="timeout")
        assert telemetry.success is False
        assert telemetry.failure_type == "timeout"
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        telemetry = GenerationTelemetry(
            user_id=123,
            creator_id=456,
            runtime_mode="agent",
            provider_name="gemini",
            model_name="gemini-flash-latest",
        )
        telemetry.complete(success=True)
        
        data = telemetry.to_dict()
        assert data["user_id"] == 123
        assert data["creator_id"] == 456
        assert data["runtime_mode"] == "agent"
        assert data["provider_name"] == "gemini"
        assert data["model_name"] == "gemini-flash-latest"
        assert data["success"] is True


class TestTelemetryCollector:
    """Test telemetry collector."""
    
    def test_singleton(self):
        """Test get_telemetry_collector returns same instance."""
        collector1 = get_telemetry_collector()
        collector2 = get_telemetry_collector()
        assert collector1 is collector2
    
    def test_start_generation(self):
        """Test starting a new generation."""
        collector = get_telemetry_collector()
        telemetry = collector.start_generation(
            user_id=123,
            creator_id=456,
            runtime_mode="canary",
        )
        assert telemetry.user_id == 123
        assert telemetry.creator_id == 456
        assert telemetry.runtime_mode == "canary"
    
    def test_get_telemetry(self):
        """Test retrieving telemetry by generation_id."""
        collector = get_telemetry_collector()
        telemetry = collector.start_generation(user_id=123)
        retrieved = collector.get(telemetry.generation_id)
        assert retrieved is telemetry
    
    def test_get_nonexistent(self):
        """Test retrieving non-existent telemetry."""
        collector = get_telemetry_collector()
        retrieved = collector.get("nonexistent-id")
        assert retrieved is None


class TestTelemetryFields:
    """Test telemetry field population."""
    
    def test_provider_timing(self):
        """Test provider timing fields."""
        telemetry = GenerationTelemetry(
            provider_name="gemini",
            model_name="gemini-flash-latest",
            provider_latency_ms=1500,
        )
        assert telemetry.provider_latency_ms == 1500
    
    def test_scoring_fields(self):
        """Test scoring fields."""
        telemetry = GenerationTelemetry(
            scoring_score=0.85,
            scoring_flags=["price_mention"],
            scoring_latency_ms=500,
        )
        assert telemetry.scoring_score == 0.85
        assert "price_mention" in telemetry.scoring_flags
        assert telemetry.scoring_latency_ms == 500
    
    def test_tool_fields(self):
        """Test tool call fields."""
        telemetry = GenerationTelemetry(
            tool_calls_count=3,
            tool_names=["get_relationship_state", "get_user_profile", "get_commerce_context"],
        )
        assert telemetry.tool_calls_count == 3
        assert len(telemetry.tool_names) == 3
    
    def test_routing_decision(self):
        """Test routing decision field."""
        telemetry = GenerationTelemetry(routing_decision="auto_approved")
        assert telemetry.routing_decision == "auto_approved"
    
    def test_token_counts(self):
        """Test token count fields."""
        telemetry = GenerationTelemetry(
            input_token_count=500,
            output_token_count=150,
        )
        assert telemetry.input_token_count == 500
        assert telemetry.output_token_count == 150


class TestTelemetrySecurity:
    """Test telemetry security (no secrets)."""
    
    def test_no_api_keys_in_telemetry(self):
        """Test that API keys are not stored in telemetry."""
        telemetry = GenerationTelemetry()
        data = telemetry.to_dict()
        
        # Check no sensitive fields
        sensitive_fields = ["api_key", "secret", "password", "token", "credential"]
        for field in sensitive_fields:
            assert field not in data
    
    def test_no_raw_conversation_in_telemetry(self):
        """Test that raw conversation is not stored in telemetry."""
        telemetry = GenerationTelemetry()
        data = telemetry.to_dict()
        
        # Check no conversation fields
        conversation_fields = ["message_content", "conversation_history", "system_prompt"]
        for field in conversation_fields:
            assert field not in data
