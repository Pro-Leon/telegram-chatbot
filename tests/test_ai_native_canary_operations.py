"""Tests for AI-native canary operations and promotion gates."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.telemetry import GenerationTelemetry, TelemetryCollector, get_telemetry_collector
from agent.canary import CanaryConfig, should_use_agent, _stable_hash


class TestTelemetryCompleteness:
    """Test telemetry field completeness."""
    
    def test_provider_name_set(self):
        """Test provider_name is populated."""
        telemetry = GenerationTelemetry(provider_name="gemini", model_name="gemini-flash-latest")
        assert telemetry.provider_name == "gemini"
        assert telemetry.model_name == "gemini-flash-latest"
    
    def test_routing_decision_set(self):
        """Test routing_decision is populated."""
        for decision in ["auto_approved", "operator_queued", "commerce_response", "excluded"]:
            telemetry = GenerationTelemetry(routing_decision=decision)
            assert telemetry.routing_decision == decision
    
    def test_runtime_mode_set(self):
        """Test runtime_mode is populated."""
        for mode in ["legacy", "agent", "canary"]:
            telemetry = GenerationTelemetry(runtime_mode=mode)
            assert telemetry.runtime_mode == mode
    
    def test_tool_calls_tracked(self):
        """Test tool call tracking."""
        telemetry = GenerationTelemetry(
            tool_calls_count=3,
            tool_names=["get_relationship_state", "get_user_profile", "get_commerce_context"],
        )
        assert telemetry.tool_calls_count == 3
        assert len(telemetry.tool_names) == 3
    
    def test_provider_latency_tracked(self):
        """Test provider latency tracking."""
        telemetry = GenerationTelemetry(provider_latency_ms=1500)
        assert telemetry.provider_latency_ms == 1500


class TestCanaryRouting:
    """Test deterministic canary routing."""
    
    def test_stable_hash_deterministic(self):
        """Test hash is deterministic."""
        for user_id in [1, 2, 3, 100, 999]:
            h1 = _stable_hash(user_id)
            h2 = _stable_hash(user_id)
            assert h1 == h2
    
    def test_stable_routing(self):
        """Test routing is stable per user."""
        config = CanaryConfig(enabled=True, sample_rate=0.5)
        for user_id in [1, 2, 3, 100, 999]:
            r1 = should_use_agent(user_id, config=config)
            r2 = should_use_agent(user_id, config=config)
            assert r1 == r2
    
    def test_sample_rate_zero(self):
        """Test sample_rate=0 routes nothing."""
        config = CanaryConfig(enabled=True, sample_rate=0.0)
        for user_id in range(100):
            assert should_use_agent(user_id, config=config) is False
    
    def test_sample_rate_one(self):
        """Test sample_rate=1 routes all."""
        config = CanaryConfig(enabled=True, sample_rate=1.0)
        for user_id in range(100):
            assert should_use_agent(user_id, config=config) is True
    
    def test_creator_filter(self):
        """Test creator filtering."""
        config = CanaryConfig(enabled=True, sample_rate=1.0, creator_ids=(1, 2))
        assert should_use_agent(1, creator_id=1, config=config) is True
        assert should_use_agent(1, creator_id=3, config=config) is False


class TestTelemetryIsolation:
    """Test telemetry failure isolation."""
    
    @pytest.mark.asyncio
    async def test_telemetry_failure_does_not_affect_crm(self):
        """Test telemetry failure is isolated from CRM processing."""
        collector = get_telemetry_collector()
        telemetry = collector.start_generation(user_id=123)
        
        # Simulate DB failure
        with patch("db.postgres.insert_generation_telemetry", side_effect=Exception("DB down")):
            # Should not raise
            await collector.record(telemetry)
        
        # Verify cache is cleaned up
        assert collector.get(telemetry.generation_id) is None
    
    def test_telemetry_record_is_best_effort(self):
        """Test telemetry recording is best-effort."""
        collector = get_telemetry_collector()
        telemetry = collector.start_generation(user_id=123)
        
        # Verify telemetry exists in cache
        assert collector.get(telemetry.generation_id) is telemetry


class TestPromotionGates:
    """Test promotion gate calculations."""
    
    def test_metrics_available(self):
        """Test required metrics are available."""
        telemetry = GenerationTelemetry(
            user_id=123,
            provider_name="gemini",
            model_name="gemini-flash-latest",
            runtime_mode="legacy",
            total_e2e_latency_ms=1500,
            success=True,
            routing_decision="auto_approved",
        )
        data = telemetry.to_dict()
        
        # Verify all required fields exist
        required_fields = [
            "generation_id", "user_id", "provider_name", "model_name",
            "runtime_mode", "total_e2e_latency_ms", "success", "routing_decision",
        ]
        for field in required_fields:
            assert field in data
    
    def test_latency_metrics(self):
        """Test latency metrics are available."""
        telemetry = GenerationTelemetry(
            context_build_ms=100,
            generation_latency_ms=1000,
            scoring_latency_ms=200,
            provider_latency_ms=800,
            total_e2e_latency_ms=1500,
        )
        data = telemetry.to_dict()
        
        assert data["context_build_ms"] == 100
        assert data["generation_latency_ms"] == 1000
        assert data["scoring_latency_ms"] == 200
        assert data["provider_latency_ms"] == 800
        assert data["total_e2e_latency_ms"] == 1500


class TestAuthorityPreservation:
    """Test authority invariants are preserved."""
    
    def test_agent_cannot_invent_product(self):
        """Test agent cannot invent product IDs."""
        # Agent tools are READ_ONLY for commerce
        from agent.tools import get_tool_registry
        registry = get_tool_registry()
        
        # No tool allows product creation
        tool_names = [t.name for t in registry.list_tools()]
        assert "create_product" not in tool_names
        assert "set_price" not in tool_names
        assert "create_offer" not in tool_names
    
    def test_agent_cannot_send_telegram(self):
        """Test agent cannot send Telegram directly."""
        from agent.tools import get_tool_registry
        registry = get_tool_registry()
        
        tool_names = [t.name for t in registry.list_tools()]
        assert "send_message" not in tool_names
        assert "send_telegram" not in tool_names
    
    def test_agent_cannot_access_dropfans(self):
        """Test agent cannot access DropFans directly."""
        from agent.tools import get_tool_registry
        registry = get_tool_registry()
        
        tool_names = [t.name for t in registry.list_tools()]
        assert "dropfans" not in " ".join(tool_names).lower()
        assert "access_provider" not in tool_names
