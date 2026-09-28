"""Tests for AI-native canary routing."""

import pytest
from unittest.mock import MagicMock, patch
from agent.canary import CanaryConfig, should_use_agent, get_canary_info, _stable_hash


class TestStableHash:
    """Test deterministic hashing."""
    
    def test_same_user_same_hash(self):
        """Same user_id always produces same hash."""
        hash1 = _stable_hash(123)
        hash2 = _stable_hash(123)
        assert hash1 == hash2
    
    def test_different_user_different_hash(self):
        """Different user_ids produce different hashes."""
        hash1 = _stable_hash(123)
        hash2 = _stable_hash(456)
        assert hash1 != hash2
    
    def test_hash_in_range(self):
        """Hash is in [0.0, 1.0) range."""
        for user_id in range(100):
            h = _stable_hash(user_id)
            assert 0.0 <= h < 1.0
    
    def test_hash_is_deterministic(self):
        """Hash is deterministic across calls."""
        hashes = [_stable_hash(42) for _ in range(10)]
        assert len(set(hashes)) == 1


class TestCanaryConfig:
    """Test canary configuration."""
    
    def test_default_config(self):
        """Test default configuration."""
        config = CanaryConfig()
        assert config.enabled is False
        assert config.sample_rate == 0.0
        assert config.creator_ids == ()
    
    def test_config_parsing(self):
        """Test creator ID parsing logic."""
        # Test parsing comma-separated IDs
        creator_ids_str = "1,2,3"
        creator_ids = tuple(
            int(x.strip()) 
            for x in creator_ids_str.split(",") 
            if x.strip()
        )
        assert creator_ids == (1, 2, 3)
    
    def test_config_empty_parsing(self):
        """Test empty creator ID parsing."""
        creator_ids_str = ""
        creator_ids = tuple(
            int(x.strip()) 
            for x in creator_ids_str.split(",") 
            if x.strip()
        )
        assert creator_ids == ()


class TestShouldUseAgent:
    """Test canary routing decision."""
    
    def test_canary_disabled(self):
        """When canary disabled, always legacy."""
        config = CanaryConfig(enabled=False, sample_rate=0.1)
        assert should_use_agent(123, config=config) is False
    
    def test_sample_rate_zero(self):
        """When sample rate 0, always legacy."""
        config = CanaryConfig(enabled=True, sample_rate=0.0)
        assert should_use_agent(123, config=config) is False
    
    def test_sample_rate_one(self):
        """When sample rate 1.0, always agent."""
        config = CanaryConfig(enabled=True, sample_rate=1.0)
        assert should_use_agent(123, config=config) is True
    
    def test_stable_assignment(self):
        """Same user always gets same decision."""
        config = CanaryConfig(enabled=True, sample_rate=0.5)
        results = [should_use_agent(123, config=config) for _ in range(10)]
        assert len(set(results)) == 1
    
    def test_creator_filter(self):
        """Creator filter works correctly."""
        config = CanaryConfig(enabled=True, sample_rate=1.0, creator_ids=(1, 2))
        assert should_use_agent(123, creator_id=1, config=config) is True
        assert should_use_agent(123, creator_id=3, config=config) is False
    
    def test_percentage_sampling(self):
        """Sample rate gives approximate percentage."""
        config = CanaryConfig(enabled=True, sample_rate=0.1)
        # With 1000 users, approximately 10% should be selected
        selected = sum(
            1 for uid in range(1000) 
            if should_use_agent(uid, config=config)
        )
        # Allow 5-15% range
        assert 50 <= selected <= 150


class TestGetCanaryInfo:
    """Test canary info for observability."""
    
    def test_canary_info_structure(self):
        """Test canary info has required fields."""
        info = get_canary_info(123)
        
        assert "canary_enabled" in info
        assert "canary_sample_rate" in info
        assert "canary_creator_filter" in info
        assert "user_hash" in info
        assert "use_agent" in info
    
    def test_canary_info_types(self):
        """Test canary info values are correct types."""
        info = get_canary_info(123)
        
        assert isinstance(info["canary_enabled"], bool)
        assert isinstance(info["canary_sample_rate"], (int, float))
        assert isinstance(info["canary_creator_filter"], bool)
        assert isinstance(info["user_hash"], float)
        assert isinstance(info["use_agent"], bool)
    
    def test_canary_info_hash_range(self):
        """Test user hash is in valid range."""
        info = get_canary_info(123)
        assert 0.0 <= info["user_hash"] < 1.0
