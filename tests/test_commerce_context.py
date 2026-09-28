"""Phase 5.4 Chk 1 tests — CommerceConversationContext boundary.

Covers the full Checkpoint 1 contract:
- construction/defaults and strict identity validation
- conversation surface (canonical roles, verbatim preservation, no trimming)
- product split (identity vs authoritative commerce attributes)
- authority (conversation text never overrides structured state)
- numeric/bool strictness (no NaN/Inf/negative, no string->bool coercion)
- security (no credential fields, extras rejected, ordinary text preserved)
- purity (import whitelist, no IO code) and determinism

No I/O, no database, no Fangate client, no event bus: everything here is
pure validation and projection.
"""

import ast
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from commerce.context import (
    PRODUCT_TITLE_MAX,
    CommerceConversationContext,
    CommerceConversationMessage,
    ProductCommerceState,
    ProductIdentity,
)
from commerce.decision import CommerceDecisionContext
from commerce.models import OFFER_STATES, OfferState, PolicyDecision

pytestmark = [pytest.mark.unit]

CONTEXT_PATH = Path(__file__).parent.parent / "commerce" / "context.py"


def eligibility(**overrides) -> PolicyDecision:
    base = {"allowed": True, "denial_reason": ""}
    base.update(overrides)
    return PolicyDecision(**base)


def make_context(**overrides) -> CommerceConversationContext:
    base = {"user_id": 5, "creator_id": 1, "eligibility": eligibility()}
    base.update(overrides)
    return CommerceConversationContext(**base)


def make_product_state(**overrides) -> ProductCommerceState:
    base = {
        "price_minor": None,
        "sales_url": None,
        "is_accessible": True,
        "age_verification_required": False,
        "age_verified": False,
    }
    base.update(overrides)
    return ProductCommerceState(**base)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — construction and defaults
# ═══════════════════════════════════════════════════════════════════════════


class TestConstruction:
    def test_minimal_context_uses_neutral_defaults(self):
        """Only user_id, creator_id, eligibility are required."""
        ctx = make_context()
        assert ctx.messages == []
        assert ctx.product_identity is None
        assert ctx.product_state is None
        assert ctx.relationship_score is None
        assert ctx.buying_intent_score is None
        assert ctx.messages_since_last_offer == 0
        assert ctx.messages_since_last_purchase == 0
        assert ctx.hours_since_last_offer is None
        assert ctx.hours_since_last_purchase is None
        assert ctx.recent_offer_count == 0
        assert ctx.recent_purchase_count == 0
        assert ctx.recent_sales_attempt_count == 0
        assert ctx.previous_offer_status is None
        assert ctx.has_active_offer is False
        assert ctx.has_relevant_product is True
        assert ctx.user_requested_content is False
        assert ctx.user_asked_about_price is False
        assert ctx.user_asked_to_buy is False
        assert ctx.creator_sales_enabled is True

    def test_missing_user_id_is_hard_error(self):
        with pytest.raises(ValidationError):
            CommerceConversationContext(creator_id=1, eligibility=eligibility())

    def test_missing_creator_id_is_hard_error(self):
        with pytest.raises(ValidationError):
            CommerceConversationContext(user_id=5, eligibility=eligibility())

    def test_missing_eligibility_is_hard_error(self):
        with pytest.raises(ValidationError):
            CommerceConversationContext(user_id=5, creator_id=1)

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(ValidationError):
            make_context(anything_else=True)

    def test_messages_default_is_not_shared_between_instances(self):
        """default_factory: mutating one context never leaks into another."""
        a = make_context()
        b = make_context()
        a.messages.append(CommerceConversationMessage(role="user", content="hello"))
        assert b.messages == []


class TestIdentityStrictness:
    def test_ids_are_preserved_verbatim(self):
        ctx = make_context(user_id=987654321, creator_id=42)
        assert ctx.user_id == 987654321
        assert ctx.creator_id == 42

    def test_zero_user_id_rejected(self):
        with pytest.raises(ValidationError):
            make_context(user_id=0)

    def test_negative_creator_id_rejected(self):
        with pytest.raises(ValidationError):
            make_context(creator_id=-3)

    def test_float_user_id_rejected(self):
        """No float->int coercion on identity fields."""
        with pytest.raises(ValidationError):
            make_context(user_id=7.0)

    def test_str_user_id_rejected(self):
        with pytest.raises(ValidationError):
            make_context(user_id="5")

    def test_bool_user_id_rejected(self):
        with pytest.raises(ValidationError):
            make_context(user_id=True)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — conversation surface
# ═══════════════════════════════════════════════════════════════════════════


class TestConversationSurface:
    def test_user_and_assistant_messages_preserved_verbatim(self):
        ctx = make_context(
            messages=[
                {"role": "user", "content": "hey"},
                {"role": "assistant", "content": "hey there!"},
                {"role": "user", "content": "how much?"},
            ]
        )
        assert [m.role for m in ctx.messages] == ["user", "assistant", "user"]
        assert [m.content for m in ctx.messages] == ["hey", "hey there!", "how much?"]

    def test_system_role_accepted(self):
        """system is a canonical memory.context.build_context role."""
        ctx = make_context(messages=[{"role": "system", "content": "context"}])
        assert ctx.messages[0].role == "system"

    def test_unknown_role_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages=[{"role": "fan", "content": "hi"}])

    def test_model_role_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages=[{"role": "model", "content": "hi"}])

    def test_none_content_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages=[{"role": "user", "content": None}])

    def test_numeric_content_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages=[{"role": "user", "content": 42}])

    def test_dict_content_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages=[{"role": "user", "content": {"text": "hi"}}])

    def test_extra_message_field_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages=[{"role": "user", "content": "hi", "timestamp": 1}])

    def test_non_dict_message_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages=["just some text"])

    def test_empty_content_accepted_deterministically(self):
        """Empty content is not malformed: preserved as-is, no synthesis."""
        ctx = make_context(messages=[{"role": "user", "content": ""}])
        assert ctx.messages[0].content == ""

    def test_long_content_preserved_without_trimming(self):
        """Context does not re-trim: memory layer already bounded messages."""
        long_text = "x" * 5000
        ctx = make_context(messages=[{"role": "user", "content": long_text}])
        assert ctx.messages[0].content == long_text


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — product identity
# ═══════════════════════════════════════════════════════════════════════════


class TestProductIdentity:
    def test_product_identity_absent_by_default(self):
        assert make_context().product_identity is None

    def test_product_id_positive_required(self):
        with pytest.raises(ValidationError):
            make_context(product_identity={"product_id": 0, "title": "Campaign set"})

    def test_product_id_rejects_str_bool_float(self):
        for bad in ("5155", True, 5155.0):
            with pytest.raises(ValidationError):
                make_context(product_identity={"product_id": bad})

    def test_availability_is_strict_bool(self):
        for bad in ("yes", "true", 1):
            with pytest.raises(ValidationError):
                make_context(product_identity={"product_id": 5155, "available": bad})

    def test_title_capped_at_constant(self):
        with pytest.raises(ValidationError):
            make_context(
                product_identity={
                    "product_id": 5155,
                    "title": "t" * (PRODUCT_TITLE_MAX + 1),
                }
            )
        ok = make_context(product_identity={"product_id": 5155, "title": "t" * PRODUCT_TITLE_MAX})
        assert ok.product_identity.title == "t" * PRODUCT_TITLE_MAX

    def test_extra_product_identity_field_rejected(self):
        with pytest.raises(ValidationError):
            make_context(product_identity={"product_id": 5155, "price_minor": 4400})


# ═══════════════════════════════════════════════════════════════════════════
# GROUP D — product commerce state (authoritative attributes)
# ═══════════════════════════════════════════════════════════════════════════


class TestProductCommerceState:
    def test_neutral_defaults(self):
        s = make_product_state()
        assert s.price_minor is None
        assert s.sales_url is None
        assert s.is_accessible is True
        assert s.age_verification_required is False
        assert s.age_verified is False

    def test_positive_price_minor_accepted(self):
        s = make_product_state(price_minor=4400)
        assert s.price_minor == 4400

    def test_zero_price_minor_accepted(self):
        s = make_product_state(price_minor=0)
        assert s.price_minor == 0

    def test_negative_price_minor_rejected(self):
        with pytest.raises(ValidationError):
            make_product_state(price_minor=-1)

    def test_price_minor_rejects_str_bool_float(self):
        for bad in ("4400", True, 44.0):
            with pytest.raises(ValidationError):
                make_product_state(price_minor=bad)

    def test_price_minor_none_allowed(self):
        """None keeps pay-what-you-want semantics (not a denial condition)."""
        assert make_product_state(price_minor=None).price_minor is None

    def test_https_sales_url_accepted(self):
        s = make_product_state(sales_url="https://fangate.info/5155x")
        assert s.sales_url == "https://fangate.info/5155x"

    def test_http_sales_url_accepted(self):
        s = make_product_state(sales_url="http://example.com/ppv")
        assert s.sales_url == "http://example.com/ppv"

    def test_sales_url_rejects_bad_urls(self):
        for bad in (
            "not a url",
            "ftp://files.example.com/x",
            "javascript:alert(1)",
            "https://",
            "https:///missing-host",
            "",
        ):
            with pytest.raises(ValidationError):
                make_product_state(sales_url=bad)

    def test_sales_url_none_allowed(self):
        assert make_product_state(sales_url=None).sales_url is None

    def test_age_flags_reject_truthy_strings(self):
        """Even the string 'false' must not silently become False/True."""
        with pytest.raises(ValidationError):
            make_product_state(age_verification_required="false")
        with pytest.raises(ValidationError):
            make_product_state(age_verified="true")

    def test_extra_product_state_field_rejected(self):
        with pytest.raises(ValidationError):
            make_product_state(conversation_price_hint=5)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP E — authority: conversation text never overrides structured state
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthority:
    def test_already_bought_text_never_reverses_eligibility(self):
        ctx = make_context(
            eligibility=eligibility(allowed=False, denial_reason="already_purchased"),
            messages=[{"role": "user", "content": "I bought this, thanks!"}],
        )
        d = ctx.to_decision_context()
        assert d.eligibility.allowed is False
        assert d.eligibility.denial_reason == "already_purchased"

    def test_price_text_never_creates_product_price(self):
        ctx = make_context(
            messages=[
                {"role": "user", "content": "send me that for $5"},
                {"role": "user", "content": "I'll pay 100 dollars"},
            ]
        )
        assert ctx.product_state is None
        assert ctx.product_identity is None

    def test_i_paid_text_preserves_offer_state(self):
        ctx = make_context(
            has_active_offer=True,
            previous_offer_status=OfferState.PENDING.value,
            messages=[{"role": "user", "content": "I paid already"}],
        )
        d = ctx.to_decision_context()
        assert d.has_active_offer is True
        assert d.previous_offer_status == "pending"

    def test_conversation_text_never_sets_intent_flags(self):
        ctx = make_context(messages=[{"role": "user", "content": "buy this now, seriously"}])
        assert ctx.user_asked_to_buy is False
        assert ctx.user_requested_content is False
        assert ctx.user_asked_about_price is False
        d = ctx.to_decision_context()
        assert d.user_asked_to_buy is False

    def test_structured_intent_survives_contradictory_text(self):
        ctx = make_context(
            user_asked_to_buy=True,
            messages=[{"role": "user", "content": "never mind"}],
        )
        assert ctx.to_decision_context().user_asked_to_buy is True

    def test_eligibility_allowed_survives_purchase_claim(self):
        ctx = make_context(
            eligibility=eligibility(allowed=True),
            messages=[{"role": "user", "content": "I already bought this"}],
        )
        d = ctx.to_decision_context()
        assert d.eligibility.allowed is True
        assert d.eligibility.denial_reason == ""

    def test_product_identity_never_conversation_derived(self):
        ctx = make_context(
            messages=[{"role": "user", "content": "this is my product id 5155 https://x"}]
        )
        assert ctx.product_identity is None
        assert ctx.product_state is None

    def test_to_decision_context_projects_every_field(self):
        ctx = make_context(
            eligibility=eligibility(allowed=False, denial_reason="age_verification_required"),
            relationship_score=0.9,
            buying_intent_score=0.2,
            messages_since_last_offer=7,
            messages_since_last_purchase=3,
            hours_since_last_offer=30.0,
            hours_since_last_purchase=120.5,
            recent_offer_count=1,
            recent_purchase_count=0,
            recent_sales_attempt_count=2,
            previous_offer_status="declined",
            has_active_offer=False,
            has_relevant_product=True,
            user_requested_content=True,
            user_asked_about_price=True,
            user_asked_to_buy=False,
            creator_sales_enabled=True,
        )
        expected = CommerceDecisionContext(
            user_id=5,
            creator_id=1,
            eligibility=ctx.eligibility,
            relationship_score=0.9,
            buying_intent_score=0.2,
            messages_since_last_offer=7,
            messages_since_last_purchase=3,
            hours_since_last_offer=30.0,
            hours_since_last_purchase=120.5,
            recent_offer_count=1,
            recent_purchase_count=0,
            recent_sales_attempt_count=2,
            previous_offer_status="declined",
            has_active_offer=False,
            has_relevant_product=True,
            user_requested_content=True,
            user_asked_about_price=True,
            user_asked_to_buy=False,
            creator_sales_enabled=True,
        )
        assert ctx.to_decision_context() == expected


# ═══════════════════════════════════════════════════════════════════════════
# GROUP F — numeric and boolean strictness
# ═══════════════════════════════════════════════════════════════════════════


class TestNumericStrictness:
    def test_nan_score_rejected(self):
        with pytest.raises(ValidationError):
            make_context(relationship_score=float("nan"))

    def test_infinity_hours_rejected(self):
        with pytest.raises(ValidationError):
            make_context(hours_since_last_offer=float("inf"))

    def test_negative_hours_rejected(self):
        with pytest.raises(ValidationError):
            make_context(hours_since_last_purchase=-0.5)

    def test_score_above_one_rejected(self):
        with pytest.raises(ValidationError):
            make_context(buying_intent_score=1.5)

    def test_score_rejects_bool(self):
        with pytest.raises(ValidationError):
            make_context(relationship_score=True)

    def test_score_rejects_string(self):
        with pytest.raises(ValidationError):
            make_context(relationship_score="0.9")

    def test_counts_reject_bool_str_float(self):
        for bad in (True, "2", 2.0):
            with pytest.raises(ValidationError):
                make_context(recent_offer_count=bad)

    def test_negative_count_rejected(self):
        with pytest.raises(ValidationError):
            make_context(messages_since_last_offer=-1)

    def test_hours_reject_bool_str(self):
        with pytest.raises(ValidationError):
            make_context(hours_since_last_offer=True)
        with pytest.raises(ValidationError):
            make_context(hours_since_last_offer="24")

    def test_hours_none_allowed(self):
        assert make_context(hours_since_last_offer=None).hours_since_last_offer is None

    def test_previous_offer_status_accepts_closed_set(self):
        for status in OFFER_STATES:
            ctx = make_context(previous_offer_status=status)
            assert ctx.previous_offer_status == status
        ctx = make_context(previous_offer_status=OfferState.DECLINED)
        assert ctx.previous_offer_status == "declined"

    def test_previous_offer_status_rejects_unknown_and_wrong_type(self):
        with pytest.raises(ValidationError):
            make_context(previous_offer_status="bogus_status")
        with pytest.raises(ValidationError):
            make_context(previous_offer_status=True)

    def test_strict_bool_flags_reject_strings(self):
        for flag in ("has_active_offer", "user_asked_to_buy", "creator_sales_enabled"):
            with pytest.raises(ValidationError):
                make_context(**{flag: "true"})
            with pytest.raises(ValidationError):
                make_context(**{flag: "false"})
            with pytest.raises(ValidationError):
                make_context(**{flag: 1})


# ═══════════════════════════════════════════════════════════════════════════
# GROUP G — security boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestSecurityBoundary:
    def test_eligibility_must_be_policy_decision_instance(self):
        """Serialized text can never claim eligibility for itself."""
        with pytest.raises(ValidationError):
            make_context(eligibility={"allowed": True, "denial_reason": ""})

    def test_eligibility_allowed_must_be_real_bool(self):
        with pytest.raises(ValidationError):
            make_context(eligibility=PolicyDecision(allowed="yes"))
        with pytest.raises(ValidationError):
            make_context(eligibility=PolicyDecision(allowed=1))

    def test_eligibility_denial_reason_must_be_str(self):
        with pytest.raises(ValidationError):
            make_context(eligibility=PolicyDecision(allowed=False, denial_reason=None))

    def test_credential_keys_rejected_as_extra_fields(self):
        for key in (
            "api_key",
            "api_key_name",
            "webhook_secret",
            "encrypted_api_key",
            "encrypted_webhook_secret",
            "bearer_token",
            "authorization",
            "db_password",
            "fernet_key",
            "master_key",
        ):
            with pytest.raises(ValidationError):
                make_context(**{key: "gAAAAA-fake-ciphertext"})

    def test_ordinary_secret_like_text_is_preserved_not_scrubbed(self):
        """No generic scrubbing: ordinary conversation content stays intact."""
        text = (
            "my api key is sk-abcd1234, card 4242 4242 4242 4242, "
            "password hunter2 — did you get the payment?"
        )
        ctx = make_context(messages=[{"role": "user", "content": text}])
        assert ctx.messages[0].content == text

    def test_no_structured_credential_fields_exist(self):
        models = (
            CommerceConversationContext,
            CommerceConversationMessage,
            ProductIdentity,
            ProductCommerceState,
        )
        credential_markers = (
            "api_key",
            "secret",
            "token",
            "bearer",
            "password",
            "fernet",
            "cipher",
            "authorization",
            "credential",
        )
        for model in models:
            for field in model.model_fields:
                lowered = field.lower()
                assert not any(marker in lowered for marker in credential_markers), (
                    f"{model.__name__} carries credential-like field {field!r}"
                )


# ═══════════════════════════════════════════════════════════════════════════
# GROUP H — purity and determinism
# ═══════════════════════════════════════════════════════════════════════════


class TestPurityAndDeterminism:
    def test_module_imports_only_stdlib_and_domain_types(self):
        """context.py must not touch IO, worker, bus, db, ws, or model code."""
        from commerce import context

        tree = ast.parse(inspect.getsource(context))
        allowed_modules = ("pydantic", "typing", "urllib", "commerce", "math")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module is not None and node.module.startswith(allowed_modules), (
                    f"forbidden import: {node.module}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.startswith(allowed_modules), f"forbidden import: {alias.name}"

    def test_module_has_no_io_or_execution_code(self):
        source = CONTEXT_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "enqueue_send",
            "publish_event",
            "execute_ppv",
            "create_offer",
            "event_bus",
            "ws_manager",
            "event_subscriber",
            "asyncpg",
            "redis",
            "httpx",
            "requests",
            "urllib.request",
            "socket",
            "os.",
            "open(",
        ):
            assert forbidden not in source, f"forbidden marker: {forbidden}"

    def test_module_never_reads_the_clock(self):
        from commerce import context

        tree = ast.parse(inspect.getsource(context))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                )
                for name in names:
                    if name:
                        assert "random" not in name and name != "time", f"forbidden: {name}"

    def test_deterministic_reconstruction(self):
        kwargs = {
            "user_id": 5,
            "creator_id": 1,
            "eligibility": eligibility(allowed=False, denial_reason="offer_exists"),
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            "product_identity": {"product_id": 5155, "title": "Campaign set"},
            "product_state": {"price_minor": 4400, "sales_url": "https://x.test/1"},
            "relationship_score": 0.7,
            "buying_intent_score": 0.6,
            "messages_since_last_offer": 4,
            "messages_since_last_purchase": 1,
            "hours_since_last_offer": 10.0,
            "hours_since_last_purchase": None,
            "recent_offer_count": 1,
            "recent_purchase_count": 0,
            "recent_sales_attempt_count": 1,
            "previous_offer_status": "clicked",
            "has_active_offer": True,
            "has_relevant_product": True,
            "user_requested_content": True,
            "user_asked_about_price": False,
            "user_asked_to_buy": True,
            "creator_sales_enabled": True,
        }
        a = CommerceConversationContext(**kwargs)
        b = CommerceConversationContext(**kwargs)
        assert a.model_dump(mode="json") == b.model_dump(mode="json")
        assert a.to_decision_context() == b.to_decision_context()
        assert a.to_decision_context() == a.to_decision_context()
