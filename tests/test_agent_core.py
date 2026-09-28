"""Tests for agent core module."""

import pytest
from agent.state import AgentState, AgentIdentity, ConversationContext, MemoryContext, RelationshipContext, CommerceContext
from agent.tools import AgentTool, AgentToolRegistry, get_tool_registry
from agent.loop import AgentLoop, AgentTurn
from agent.memory import AgentMemory


class TestAgentState:
    """Test AgentState creation and immutability."""
    
    def test_create_agent_state(self):
        """Test creating AgentState with factory method."""
        state = AgentState.create(
            creator_id=1,
            user_id=2,
            current_message="Hello",
            relationship_state="ENGAGED",
            commercial_pressure="NONE",
        )
        
        assert state.identity.creator_id == 1
        assert state.identity.user_id == 2
        assert state.conversation.current_message == "Hello"
        assert state.relationship.relationship_state == "ENGAGED"
        assert state.relationship.commercial_pressure == "NONE"
    
    def test_agent_state_is_frozen(self):
        """Test that AgentState is immutable."""
        state = AgentState.create(
            creator_id=1,
            user_id=2,
            current_message="Hello",
        )
        
        with pytest.raises(AttributeError):
            state.identity = AgentIdentity(creator_id=99, user_id=99)
    
    def test_to_compact_dict(self):
        """Test conversion to compact dictionary."""
        state = AgentState.create(
            creator_id=1,
            user_id=2,
            current_message="Hello",
            relationship_state="WARM",
            has_relevant_product=True,
            available_tools=("get_relationship_state",),
        )
        
        compact = state.to_compact_dict()
        
        assert compact["user_id"] == 2
        assert compact["creator_id"] == 1
        assert compact["current_message"] == "Hello"
        assert compact["relationship_state"] == "WARM"
        assert compact["has_relevant_product"] is True
        assert "get_relationship_state" in compact["available_tools"]


class TestAgentTools:
    """Test agent tool registry and tools."""
    
    def test_tool_registry(self):
        """Test tool registration and retrieval."""
        registry = AgentToolRegistry()
        
        tool = AgentTool(
            name="test_tool",
            description="Test tool",
            parameters={"type": "object", "properties": {}},
            handler=None,
        )
        
        registry.register(tool)
        assert registry.get("test_tool") == tool
        assert len(registry.list_tools()) == 1
    
    def test_get_tool_schemas(self):
        """Test schema generation."""
        registry = AgentToolRegistry()
        
        tool = AgentTool(
            name="test_tool",
            description="Test tool",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            handler=None,
        )
        
        registry.register(tool)
        schemas = registry.get_schemas()
        
        assert len(schemas) == 1
        assert schemas[0]["name"] == "test_tool"
        assert schemas[0]["description"] == "Test tool"
    
    def test_tool_authority_check(self):
        """Test tool authority validation."""
        def require_creator(state):
            return state.identity.creator_id > 0
        
        tool = AgentTool(
            name="restricted_tool",
            description="Restricted tool",
            parameters={"type": "object", "properties": {}},
            handler=None,
            authority_check=require_creator,
        )
        
        # Valid state
        state = AgentState.create(creator_id=1, user_id=2, current_message="Hi")
        assert tool.validate_authority(state) is True
        
        # Invalid state
        state_invalid = AgentState.create(creator_id=0, user_id=2, current_message="Hi")
        assert tool.validate_authority(state_invalid) is False


class TestAgentLoop:
    """Test bounded agent loop."""
    
    def test_agent_turn_creation(self):
        """Test AgentTurn dataclass."""
        turn = AgentTurn(
            response_text="Hello!",
            tool_calls_made=2,
            tool_names=["get_relationship_state", "get_user_profile"],
            execution_time_seconds=1.5,
            terminated_reason="completed",
        )
        
        assert turn.response_text == "Hello!"
        assert turn.tool_calls_made == 2
        assert len(turn.tool_names) == 2
        assert turn.terminated_reason == "completed"


class TestAgentMemory:
    """Test agent memory integration."""
    
    def test_memory_initialization(self):
        """Test memory module initialization."""
        memory = AgentMemory()
        assert memory._initialized is False
