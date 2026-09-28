"""Phase 75D: Commerce signal integration tests.

Tests the commerce context builder for the one-call prompt.
"""

import pytest

from core.commerce_prompt import (
    build_commerce_signal_hints,
    _extract_key_commerce_facts,
    build_commerce_signal_schema,
    COMMERCE_SIGNAL_INSTRUCTIONS,
)


class TestBuildCommerceSignalHints:
    """Tests for build_commerce_signal_hints."""

    def test_empty_commerce_text(self):
        """Empty commerce text with no other hints should return relevant product default."""
        hints = build_commerce_signal_hints("")
        # has_relevant_product defaults to True
        assert "RELEVANT PRODUCT: yes" in hints

    def test_no_hints(self):
        """Empty commerce text with all defaults should return minimal hints."""
        hints = build_commerce_signal_hints(
            commerce_text="",
            has_relevant_product=False,
        )
        assert hints == ""

    def test_basic_hints(self):
        """Basic hints should include relationship state."""
        hints = build_commerce_signal_hints(
            commerce_text="",
            relationship_state="warm",
        )
        assert "RELATIONSHIP: warm" in hints

    def test_commerce_facts_extracted(self):
        """Key facts should be extracted from commerce text."""
        commerce_text = "Purchase history: 2 purchases\nOffer: active pending\nRevenue: $50"
        hints = build_commerce_signal_hints(commerce_text)
        assert "COMMERCE FACTS:" in hints

    def test_active_offer(self):
        """Active offer should be included."""
        hints = build_commerce_signal_hints(
            commerce_text="",
            has_active_offer=True,
        )
        assert "ACTIVE OFFER: yes" in hints

    def test_recent_counts(self):
        """Recent offer/purchase counts should be included."""
        hints = build_commerce_signal_hints(
            commerce_text="",
            recent_offer_count=3,
            recent_purchase_count=2,
        )
        assert "RECENT OFFERS: 3" in hints
        assert "RECENT PURCHASES: 2" in hints

    def test_relevant_product(self):
        """Relevant product should be included."""
        hints = build_commerce_signal_hints(
            commerce_text="",
            has_relevant_product=True,
        )
        assert "RELEVANT PRODUCT: yes" in hints


class TestExtractKeyCommerceFacts:
    """Tests for _extract_key_commerce_facts."""

    def test_empty_text(self):
        """Empty text should return empty list."""
        facts = _extract_key_commerce_facts("")
        assert facts == []

    def test_no_facts(self):
        """Text without key facts should return empty list."""
        text = "This is a general message without commerce keywords."
        facts = _extract_key_commerce_facts(text)
        assert facts == []

    def test_purchase_fact(self):
        """Purchase-related facts should be extracted."""
        text = "Purchase history: 2 purchases\nGeneral message here."
        facts = _extract_key_commerce_facts(text)
        assert any("purchase" in f.lower() for f in facts)

    def test_offer_fact(self):
        """Offer-related facts should be extracted."""
        text = "Active offer pending\nAnother message."
        facts = _extract_key_commerce_facts(text)
        assert any("offer" in f.lower() for f in facts)

    def test_formatting_excluded(self):
        """Formatting lines should be excluded."""
        text = "[HEADER]\n─ separator\nReal fact: purchase pending"
        facts = _extract_key_commerce_facts(text)
        assert not any("[" in f for f in facts)
        assert not any("─" in f for f in facts)


class TestBuildCommerceSignalSchema:
    """Tests for build_commerce_signal_schema."""

    def test_schema_exists(self):
        """Schema should be defined."""
        schema = build_commerce_signal_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0

    def test_schema_has_required_fields(self):
        """Schema should have all required fields."""
        schema = build_commerce_signal_schema()
        required_fields = [
            "purchase_intent",
            "content_interest",
            "relationship_engagement",
            "price_interest",
            "explicit_purchase_request",
            "explicit_content_request",
            "requested_price",
            "declined_recent_offer",
            "asks_for_free_content",
            "negative_sentiment",
            "confidence",
            "evidence",
            "model_uncertainty",
            "primary_intent",
            "intent_tags",
            "negative_intent_tags",
            "fan_asks_question",
        ]
        for field in required_fields:
            assert field in schema, f"Missing field: {field}"

    def test_primary_intent_values(self):
        """Primary intent should list valid values."""
        schema = build_commerce_signal_schema()
        assert "casual_chat" in schema["primary_intent"]
        assert "greeting" in schema["primary_intent"]
        assert "purchase_intent" in schema["primary_intent"]


class TestCommerceSignalInstructions:
    """Tests for COMMERCE_SIGNAL_INSTRUCTIONS."""

    def test_instructions_exist(self):
        """Instructions should be defined."""
        assert len(COMMERCE_SIGNAL_INSTRUCTIONS) > 0

    def test_instructions_mention_key_fields(self):
        """Instructions should mention key fields."""
        assert "purchase_intent" in COMMERCE_SIGNAL_INSTRUCTIONS
        assert "content_interest" in COMMERCE_SIGNAL_INSTRUCTIONS
        assert "evidence" in COMMERCE_SIGNAL_INSTRUCTIONS

    def test_instructions_warn_about_payment_data(self):
        """Instructions should warn about payment data."""
        assert "payment data" in COMMERCE_SIGNAL_INSTRUCTIONS.lower()
