"""Tests for AI-native runtime integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agent.runtime import (
    RuntimeConfig,
    RuntimeResult,
    get_runtime_config,
    build_agent_state,
    run_agent_runtime,
    run_legacy_runtime,
)
from agent.state import AgentState


class TestRuntimeConfig:
    """Test runtime configuration."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = RuntimeConfig()
        assert config.max_tool_calls == 5
        assert config.max_response_tokens == 120
        assert config.max_response_time_seconds == 30.0
        assert config.enabled is False
    
    def test_config_from_settings(self):
        """Test configuration loading from settings."""
        with patch("core.config.Settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_max_tool_calls=10,
                agent_max_response_tokens=200,
                agent_max_runtime_seconds=60.0,
                ai_runtime_mode="agent",
            )
            config = get_runtime_config()
            assert config.max_tool_calls == 10
            assert config.max_response_tokens == 200
            assert config.max_response_time_seconds == 60.0
            assert config.enabled is True
    
    def test_config_legacy_mode(self):
        """Test configuration in legacy mode."""
        with patch("core.config.Settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                agent_max_tool_calls=5,
                agent_max_response_tokens=120,
                agent_max_runtime_seconds=30.0,
                ai_runtime_mode="legacy",
            )
            config = get_runtime_config()
            assert config.enabled is False


class TestBuildAgentState:
    """Test AgentState building from CRM context."""
    
    @pytest.mark.asyncio
    async def test_build_agent_state(self):
        """Test building AgentState from CRM context."""
        state = await build_agent_state(
            user_id=1,
            creator_id=2,
            user_message="Hello",
            conversation_history=[],
            context={},
            relationship_state="ENGAGED",
            commercial_pressure="NONE",
        )
        
        assert state.identity.user_id == 1
        assert state.identity.creator_id == 2
        assert state.conversation.current_message == "Hello"
        assert state.relationship.relationship_state == "ENGAGED"
        assert state.relationship.commercial_pressure == "NONE"
    
    @pytest.mark.asyncio
    async def test_build_agent_state_with_profile(self):
        """Test building AgentState with profile data."""
        profile = {"name": "Test User", "age": 25}
        state = await build_agent_state(
            user_id=1,
            creator_id=2,
            user_message="Hello",
            conversation_history=[],
            context={},
            profile=profile,
        )
        
        assert state.memory.profile == profile
    
    @pytest.mark.asyncio
    async def test_build_agent_state_with_commerce(self):
        """Test building AgentState with commerce context."""
        products = [{"id": 1, "name": "Product 1"}]
        offers = [{"id": 1, "status": "pending"}]
        purchases = [{"id": 1, "product": "Product 1"}]
        
        state = await build_agent_state(
            user_id=1,
            creator_id=2,
            user_message="Hello",
            conversation_history=[],
            context={},
            eligible_products=tuple(products),
            active_offers=tuple(offers),
            purchase_history=tuple(purchases),
            has_relevant_product=True,
        )
        
        assert len(state.commerce.eligible_products) == 1
        assert len(state.commerce.active_offers) == 1
        assert len(state.commerce.purchase_history) == 1
        assert state.commerce.has_relevant_product is True


class TestRunAgentRuntime:
    """Test agent runtime execution."""
    
    @pytest.mark.asyncio
    async def test_agent_runtime_success(self):
        """Test successful agent runtime execution."""
        state = AgentState.create(
            creator_id=1,
            user_id=2,
            current_message="Hello",
        )
        
        mock_provider = MagicMock()
        mock_provider.supports_tool_calling.return_value = False
        mock_provider.generate = AsyncMock(return_value={"text": "Hi there!"})
        
        result = await run_agent_runtime(state, mock_provider)
        
        assert result.success is True
        assert result.response_text == "Hi there!"
        assert result.runtime_mode == "agent"
        assert result.terminated_reason == "completed"
    
    @pytest.mark.asyncio
    async def test_agent_runtime_error_handling(self):
        """Test agent runtime handles provider errors gracefully."""
        state = AgentState.create(
            creator_id=1,
            user_id=2,
            current_message="Hello",
        )
        
        mock_provider = MagicMock()
        mock_provider.supports_tool_calling.return_value = False
        mock_provider.generate = AsyncMock(side_effect=Exception("Provider error"))
        
        result = await run_agent_runtime(state, mock_provider)
        
        # Agent catches errors gracefully and returns user-friendly message
        assert result.success is True
        assert result.runtime_mode == "agent"
        assert result.terminated_reason == "completed"
        assert "error" in result.response_text.lower()


class TestRunLegacyRuntime:
    """Test legacy runtime execution."""
    
    @pytest.mark.asyncio
    async def test_legacy_runtime_success(self):
        """Test successful legacy runtime execution."""
        context = [{"role": "user", "content": "Hello"}]
        
        mock_provider = MagicMock()
        mock_provider.generate_with_history = AsyncMock(return_value="Hi there!")
        
        result = await run_legacy_runtime(
            context=context,
            user_message="Hello",
            provider=mock_provider,
        )
        
        assert result.success is True
        assert result.response_text == "Hi there!"
        assert result.runtime_mode == "legacy"
        assert result.terminated_reason == "completed"
    
    @pytest.mark.asyncio
    async def test_legacy_runtime_error(self):
        """Test legacy runtime error handling."""
        context = [{"role": "user", "content": "Hello"}]
        
        mock_provider = MagicMock()
        mock_provider.generate_with_history = AsyncMock(side_effect=Exception("Provider error"))
        
        result = await run_legacy_runtime(
            context=context,
            user_message="Hello",
            provider=mock_provider,
        )
        
        assert result.success is False
        assert result.runtime_mode == "legacy"
        assert result.terminated_reason == "error"


class TestRuntimeResult:
    """Test RuntimeResult dataclass."""
    
    def test_runtime_result_creation(self):
        """Test RuntimeResult creation."""
        result = RuntimeResult(
            response_text="Hello!",
            tool_calls_made=2,
            tool_names=["get_relationship_state", "get_user_profile"],
            execution_time_seconds=1.5,
            terminated_reason="completed",
            runtime_mode="agent",
            success=True,
        )
        
        assert result.response_text == "Hello!"
        assert result.tool_calls_made == 2
        assert len(result.tool_names) == 2
        assert result.runtime_mode == "agent"
        assert result.success is True
        assert result.error is None
    
    def test_runtime_result_with_error(self):
        """Test RuntimeResult with error."""
        result = RuntimeResult(
            response_text="",
            tool_calls_made=0,
            tool_names=[],
            execution_time_seconds=0.5,
            terminated_reason="error",
            runtime_mode="agent",
            success=False,
            error="Provider timeout",
        )
        
        assert result.success is False
        assert result.error == "Provider timeout"
