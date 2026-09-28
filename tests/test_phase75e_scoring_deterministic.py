"""Phase 75E: Deterministic scoring replacement tests.

Tests the deterministic scoring module that replaces LLM #3.
"""

import pytest

from core.scoring_deterministic import (
    score_draft_deterministic,
    compute_safety_flags,
    validate_draft_quality,
)


class TestScoreDraftDeterministic:
    """Tests for score_draft_deterministic."""

    def test_good_quality_reply(self):
        """Good quality reply should score high."""
        score, flags = score_draft_deterministic(
            "Hey! I was just thinking about you. How's your day going? I'd love to hear about it!",
            "Hello!",
        )
        assert score >= 0.7
        assert "too_generic" not in flags

    def test_too_short_reply(self):
        """Very short reply should be flagged."""
        score, flags = score_draft_deterministic("ok", "Hello!")
        assert score <= 0.5
        assert "too_generic" in flags

    def test_formal_reply(self):
        """Formal reply should be flagged."""
        score, flags = score_draft_deterministic(
            "Dear Sir, I respectfully inform you that furthermore, therefore, consequently, moreover, I appreciate your inquiry.",
            "Hello!",
        )
        assert "too_formal" in flags

    def test_repetitive_reply(self):
        """Repetitive reply should be flagged."""
        score, flags = score_draft_deterministic(
            "hello hello hello hello hello hello hello hello hello hello hello hello",
            "Hello!",
        )
        assert "repetitive" in flags

    def test_empty_reply(self):
        """Empty reply should return low score."""
        score, flags = score_draft_deterministic("", "Hello!")
        assert score <= 0.5
        assert "too_generic" in flags

    def test_medium_length_reply(self):
        """Medium length reply should score reasonably."""
        score, flags = score_draft_deterministic(
            "Hey there! How's it going today?",
            "Hello!",
        )
        assert 0.5 <= score <= 1.0

    def test_long_reply(self):
        """Long reply should score lower."""
        score, flags = score_draft_deterministic(
            "Hey! I was just thinking about you. How's your day going? I'd love to hear about it. What have you been up to lately? I've been so busy with work but I'm glad we can chat.",
            "Hello!",
        )
        assert score >= 0.5


class TestComputeSafetyFlags:
    """Tests for compute_safety_flags."""

    def test_distress_signal(self):
        """Distress keywords should be detected."""
        flags = compute_safety_flags("I'm feeling depressed")
        assert "distress_signal" in flags

    def test_legal_mention(self):
        """Legal keywords should be detected."""
        flags = compute_safety_flags("I'll report this to the police")
        assert "legal_mention" in flags

    def test_personal_info_request(self):
        """Personal info keywords should be detected."""
        flags = compute_safety_flags("Can I have your email address?")
        assert "personal_info_request" in flags

    def test_clean_reply_no_flags(self):
        """Clean reply should have no flags."""
        flags = compute_safety_flags("Hey! How's your day going?")
        assert len(flags) == 0

    def test_price_mention_authorized(self):
        """Authorized price mention should not be flagged."""
        flags = compute_safety_flags(
            "The price is $50",
            is_authorized_commerce=True,
            authorized_price_minor=5000,
        )
        assert "price_mention" not in flags

    def test_price_mention_unauthorized(self):
        """Unauthorized price mention should be flagged."""
        flags = compute_safety_flags("The price is $50")
        assert "price_mention" in flags


class TestValidateDraftQuality:
    """Tests for validate_draft_quality."""

    def test_approved_draft(self):
        """Good draft should be approved."""
        is_approved, score, quality_flags, safety_flags = validate_draft_quality(
            "Hey! I was just thinking about you. How's your day going? I'd love to hear about it!",
            "Hello!",
        )
        assert is_approved is True
        assert score >= 0.80
        assert len(safety_flags) == 0

    def test_rejected_low_quality(self):
        """Low quality draft should be rejected."""
        is_approved, score, quality_flags, safety_flags = validate_draft_quality(
            "ok",
            "Hello!",
        )
        assert is_approved is False
        assert score <= 0.5

    def test_rejected_safety_flag(self):
        """Draft with safety flag should be rejected."""
        is_approved, score, quality_flags, safety_flags = validate_draft_quality(
            "I'm feeling depressed",
            "Hello!",
        )
        assert is_approved is False
        assert "distress_signal" in safety_flags

    def test_rejected_formal(self):
        """Formal draft should be rejected."""
        is_approved, score, quality_flags, safety_flags = validate_draft_quality(
            "Dear Sir, I respectfully inform you that furthermore, therefore, consequently, moreover.",
            "Hello!",
        )
        assert is_approved is False
        assert "too_formal" in quality_flags

    def test_rejected_generic(self):
        """Generic draft should be rejected."""
        is_approved, score, quality_flags, safety_flags = validate_draft_quality(
            "Thank you for reaching out. I appreciate your message. Let me know how I can help.",
            "Hello!",
        )
        assert is_approved is False
        assert "too_generic" in quality_flags

    def test_authorized_commerce_price(self):
        """Authorized commerce price should not be flagged."""
        is_approved, score, quality_flags, safety_flags = validate_draft_quality(
            "The price is $50 for this exclusive content",
            "How much?",
            is_authorized_commerce=True,
            authorized_price_minor=5000,
        )
        assert "price_mention" not in safety_flags
