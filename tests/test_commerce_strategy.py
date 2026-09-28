"""Phase 5.4 Chk 2 tests — deterministic commerce strategy layer.

Covers the full Checkpoint 2 contract:
- model validation (actions, pressure, booleans, extras, field types)
- action -> strategy mapping for every CommerceAction
- action-driven pressure (NONE/LOW/MODERATE only; HIGH impossible)
- product authority (never invents product data; permission flags only)
- suppression (strategy never overrides/reinterprets the deterministic
  decision; decline/opt-out/blocked/unavailable/purchased/active-offer/
  creator-not-ready paths stay suppressed)
- purity (import whitelist, no IO/marketplace/LLM code)
- determinism and secret safety

No I/O, no database, no Fangate, no event bus: pure mapping only.
"""

import ast
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from commerce.context import CommerceConversationContext
from commerce.decision import (
    CommerceDecision,
    CommerceReason,
    decide_commerce_action,
)
from commerce.eligibility import (
    OfferContext,
    ProductEligibilityState,
    UserEligibilityState,
    evaluate_ppv_eligibility,
)
from commerce.models import CommerceAction, PolicyDecision
from commerce.strategy import (
    DEFAULT_COMMUNICATION_CONSTRAINTS,
    CommerceStrategy,
    CommunicationConstraints,
    SalesPressure,
    StrategyKind,
    build_strategy,
)

pytestmark = [pytest.mark.unit]

STRATEGY_PATH = Path(__file__).parent.parent / "commerce" / "strategy.py"


def decision(action: CommerceAction, reason: str = "no_buying_signal") -> CommerceDecision:
    return CommerceDecision(
        action=action,
        reason_code=CommerceReason(reason),
        allowed=action
        in (CommerceAction.SOFT_OFFER, CommerceAction.OFFER_PPV, CommerceAction.FOLLOW_UP),
        confidence=0.5,
    )


def make_context(**overrides) -> CommerceConversationContext:
    base = {
        "user_id": 5,
        "creator_id": 1,
        "eligibility": PolicyDecision(allowed=True),
    }
    base.update(overrides)
    return CommerceConversationContext(**base)


def full_product_context(**overrides) -> CommerceConversationContext:
    base = {
        "product_identity": {"product_id": 5155, "title": "Campaign set"},
        "product_state": {
            "price_minor": 4400,
            "sales_url": "https://fangate.info/5155x",
        },
    }
    base.update(overrides)
    return make_context(**base)


def product_only_context(**overrides) -> CommerceConversationContext:
    base = {
        "product_state": {
            "price_minor": 4400,
            "sales_url": "https://fangate.info/5155x",
        }
    }
    base.update(overrides)
    return make_context(**base)


def identity_only_context(**overrides) -> CommerceConversationContext:
    base = {"product_identity": {"product_id": 5155, "title": "Campaign set"}}
    base.update(overrides)
    return make_context(**base)


def engine_denial_strategy(denial_reason: str):
    """Real eligibility-denied state, then the real engine verdict.

    Each denial reason exercises its own eligibility branch so the tests
    prove real engine output — not a hand-built decision."""
    user = UserEligibilityState()
    product = ProductEligibilityState(sales_url="https://fangate.info/5155x")
    ctx = OfferContext(creator_ready=True)
    if denial_reason == "user_blocked":
        user = UserEligibilityState(is_blocked=True)
    elif denial_reason == "user_opted_out":
        user = UserEligibilityState(do_not_auto_reply=True)
    elif denial_reason == "product_unavailable":
        product = ProductEligibilityState(
            is_accessible=False, sales_url="https://fangate.info/5155x"
        )
    elif denial_reason == "already_purchased":
        ctx = OfferContext(creator_ready=True, already_purchased=True)
    elif denial_reason == "offer_exists":
        ctx = OfferContext(creator_ready=True, has_active_offer=True)
    elif denial_reason == "creator_not_ready":
        ctx = OfferContext(creator_ready=False)
    else:
        raise AssertionError(f"unknown denial reason: {denial_reason}")

    eligible = evaluate_ppv_eligibility(user=user, product=product, ctx=ctx)
    assert eligible.denial_reason == denial_reason
    d = decide_commerce_action(
        CommerceConversationContext(
            user_id=5, creator_id=1, eligibility=eligible
        ).to_decision_context()
    )
    return build_strategy(d, full_product_context())


def strategy_payload(**overrides) -> dict:
    base = {
        "action": "no_offer",
        "kind": "no_offer",
        "pressure": "none",
        "allow_cta": False,
        "allow_price_reference": False,
        "allow_product_reference": False,
        "relationship_first": True,
        "follow_up_allowed": True,
        "reason": "no_buying_signal",
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════
# GROUP A — model validation
# ═══════════════════════════════════════════════════════════════════════════


class TestModelValidation:
    def test_valid_strategy_accepted(self):
        s = CommerceStrategy(**strategy_payload())
        assert s.action is CommerceAction.NO_OFFER
        assert s.kind is StrategyKind.NO_OFFER
        assert s.pressure is SalesPressure.NONE
        assert s.reason is CommerceReason.NO_BUYING_SIGNAL

    def test_pressure_defaults_to_low(self):
        s = CommerceStrategy(**{k: v for k, v in strategy_payload().items() if k != "pressure"})
        assert s.pressure is SalesPressure.LOW

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            CommerceStrategy(**strategy_payload(action="offer_ppv_v2"))
        with pytest.raises(ValidationError):
            CommerceStrategy(**strategy_payload(action="high_pressure_offer"))

    def test_invalid_pressure_rejected(self):
        for bad in ("high", "HIGH", "medium", "aggressive", ""):
            with pytest.raises(ValidationError):
                CommerceStrategy(**strategy_payload(pressure=bad))

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            CommerceStrategy(**strategy_payload(payment_url="https://x.test/1"))

    def test_invalid_boolean_types_rejected(self):
        payload = strategy_payload()
        for field in (
            "allow_cta",
            "allow_price_reference",
            "allow_product_reference",
            "relationship_first",
            "follow_up_allowed",
        ):
            for bad in ("true", "false", "yes", 1, 0, None):
                with pytest.raises(ValidationError):
                    CommerceStrategy(**{**payload, field: bad})

    def test_invalid_field_types_rejected(self):
        with pytest.raises(ValidationError):
            CommerceStrategy(**strategy_payload(reason=42))
        with pytest.raises(ValidationError):
            CommerceStrategy(**strategy_payload(kind=["no_offer"]))
        with pytest.raises(ValidationError):
            CommerceStrategy(**strategy_payload(action=None))

    def test_constraints_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            CommerceStrategy(
                **strategy_payload(communication_constraints={"allow_guilt": True, "bribe": True})
            )

    def test_constraints_booleans_are_strict(self):
        with pytest.raises(ValidationError):
            CommunicationConstraints(allow_urgency="true")
        with pytest.raises(ValidationError):
            CommunicationConstraints(allow_guilt=1)

    def test_reason_code_normalizes_from_string(self):
        s = CommerceStrategy(**strategy_payload(reason="already_purchased"))
        assert s.reason is CommerceReason.ALREADY_PURCHASED


# ═══════════════════════════════════════════════════════════════════════════
# GROUP B — action -> strategy mapping
# ═══════════════════════════════════════════════════════════════════════════


class TestMapping:
    @pytest.mark.parametrize(
        ("action", "expected_kind"),
        [
            (CommerceAction.NO_OFFER, StrategyKind.NO_OFFER),
            (CommerceAction.RELATIONSHIP_BUILDING, StrategyKind.RELATIONSHIP_BUILDING),
            (CommerceAction.SOFT_OFFER, StrategyKind.SOFT_OFFER),
            (CommerceAction.FOLLOW_UP, StrategyKind.FOLLOW_UP),
            (CommerceAction.OFFER_PPV, StrategyKind.OFFER_PPV),
            (CommerceAction.DONT_OFFER, StrategyKind.SUPPRESSED),
        ],
    )
    def test_every_action_maps_to_its_kind(self, action, expected_kind):
        s = build_strategy(decision(action), make_context())
        assert s.kind is expected_kind

    def test_strategy_action_always_matches_decision(self):
        for action in CommerceAction:
            s = build_strategy(decision(action), make_context())
            assert s.action is action

    def test_decision_is_never_reinterpreted(self):
        """Strategy is downstream: output action equals input action, always."""
        for action in CommerceAction:
            offered = build_strategy(decision(action), full_product_context())
            assert offered.action is action

    def test_unknown_action_is_hard_error(self):
        d = CommerceDecision(
            action="mystery_action",
            reason_code=CommerceReason.NO_BUYING_SIGNAL,
            allowed=False,
            confidence=0.5,
        )
        with pytest.raises(ValueError):
            build_strategy(d, make_context())

    def test_invalid_reason_code_is_hard_error(self):
        d = CommerceDecision(
            action=CommerceAction.NO_OFFER,
            reason_code="mystery_reason",
            allowed=False,
            confidence=0.5,
        )
        with pytest.raises(ValueError):
            build_strategy(d, make_context())

    def test_relationship_first_is_universal(self):
        for action in CommerceAction:
            s = build_strategy(decision(action), make_context())
            assert s.relationship_first is True

    def test_mapping_flags_for_non_sale_actions(self):
        for action in (CommerceAction.NO_OFFER, CommerceAction.RELATIONSHIP_BUILDING):
            s = build_strategy(decision(action), full_product_context())
            assert s.allow_cta is False
            assert s.allow_price_reference is False
            assert s.allow_product_reference is False

    def test_no_offer_allows_follow_up_but_dont_offer_does_not(self):
        no_offer = build_strategy(decision(CommerceAction.NO_OFFER), make_context())
        assert no_offer.follow_up_allowed is True
        dont = build_strategy(decision(CommerceAction.DONT_OFFER), make_context())
        assert dont.follow_up_allowed is False


# ═══════════════════════════════════════════════════════════════════════════
# GROUP C — pressure
# ═══════════════════════════════════════════════════════════════════════════


class TestPressure:
    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            (CommerceAction.NO_OFFER, SalesPressure.NONE),
            (CommerceAction.DONT_OFFER, SalesPressure.NONE),
            (CommerceAction.RELATIONSHIP_BUILDING, SalesPressure.LOW),
            (CommerceAction.SOFT_OFFER, SalesPressure.LOW),
            (CommerceAction.FOLLOW_UP, SalesPressure.LOW),
            (CommerceAction.OFFER_PPV, SalesPressure.MODERATE),
        ],
    )
    def test_action_driven_pressure(self, action, expected):
        s = build_strategy(decision(action), make_context())
        assert s.pressure is expected

    def test_high_pressure_does_not_exist(self):
        assert set(SalesPressure) == {
            SalesPressure.NONE,
            SalesPressure.LOW,
            SalesPressure.MODERATE,
            SalesPressure.DIRECT,
        }
        with pytest.raises(ValidationError):
            CommerceStrategy(**strategy_payload(pressure="high"))

    def test_confidence_never_drives_pressure(self):
        """A model confidence score must not influence pressure."""
        for action in (
            CommerceAction.NO_OFFER,
            CommerceAction.SOFT_OFFER,
            CommerceAction.OFFER_PPV,
            CommerceAction.DONT_OFFER,
        ):
            low = CommerceDecision(
                action=action,
                reason_code=CommerceReason.NO_BUYING_SIGNAL,
                allowed=False,
                confidence=0.05,
            )
            high = CommerceDecision(
                action=action,
                reason_code=CommerceReason.NO_BUYING_SIGNAL,
                allowed=False,
                confidence=1.0,
            )
            assert (
                build_strategy(low, make_context()).pressure
                is build_strategy(high, make_context()).pressure
            )

    def test_moderate_only_for_authorized_offer_ppv(self):
        moderate = {
            s.pressure
            for action in CommerceAction
            for s in [build_strategy(decision(action), make_context())]
            if s.pressure is SalesPressure.MODERATE
        }
        assert moderate == {SalesPressure.MODERATE}
        offering = build_strategy(decision(CommerceAction.OFFER_PPV), make_context())
        assert offering.pressure is SalesPressure.MODERATE

    def test_offers_never_wire_execution(self):
        """Strategies carry no execution/HTTP/send directions."""
        for action in (CommerceAction.OFFER_PPV, CommerceAction.SOFT_OFFER):
            s = build_strategy(decision(action), full_product_context())
            dump = s.model_dump(mode="json")
            for marker in ("url", "link", "execute", "send", "price_minor", "product_id"):
                assert marker not in dump


# ═══════════════════════════════════════════════════════════════════════════
# GROUP D — product authority
# ═══════════════════════════════════════════════════════════════════════════


class TestProductAuthority:
    def test_full_product_unlocks_price_and_product_reference(self):
        s = build_strategy(decision(CommerceAction.SOFT_OFFER), full_product_context())
        assert s.allow_price_reference is True
        assert s.allow_product_reference is True

    def test_missing_product_blocks_all_reference(self):
        s = build_strategy(decision(CommerceAction.OFFER_PPV), make_context())
        assert s.allow_price_reference is False
        assert s.allow_product_reference is False

    def test_identity_without_price_blocks_price(self):
        s = build_strategy(decision(CommerceAction.SOFT_OFFER), identity_only_context())
        assert s.allow_product_reference is True
        assert s.allow_price_reference is False

    def test_state_without_identity_blocks_product_reference(self):
        s = build_strategy(decision(CommerceAction.SOFT_OFFER), product_only_context())
        assert s.allow_price_reference is True
        assert s.allow_product_reference is False

    def test_unavailable_product_blocks_references(self):
        s = build_strategy(
            decision(CommerceAction.OFFER_PPV),
            full_product_context(
                product_identity={"product_id": 5155, "title": "Set", "available": False}
            ),
        )
        assert s.allow_product_reference is False

    def test_inaccessible_product_blocks_price(self):
        s = build_strategy(
            decision(CommerceAction.OFFER_PPV),
            product_only_context(
                product_state={
                    "price_minor": 4400,
                    "sales_url": "https://x.test/1",
                    "is_accessible": False,
                }
            ),
        )
        assert s.allow_price_reference is False

    def test_missing_sales_url_blocks_price(self):
        s = build_strategy(
            decision(CommerceAction.SOFT_OFFER),
            product_only_context(product_state={"price_minor": 4400, "sales_url": None}),
        )
        assert s.allow_price_reference is False

    def test_pay_what_you_want_blocks_price(self):
        s = build_strategy(
            decision(CommerceAction.SOFT_OFFER),
            product_only_context(
                product_state={"price_minor": None, "sales_url": "https://x.test/1"}
            ),
        )
        assert s.allow_price_reference is False

    def test_strategy_never_carries_product_values(self):
        """Even with a full product, the strategy emits permissions only."""
        s = build_strategy(decision(CommerceAction.OFFER_PPV), full_product_context())
        dump = s.model_dump(mode="json")
        assert "product_id" not in dump
        assert "price" not in dump
        assert "price_minor" not in dump
        assert "currency" not in dump
        assert "sales_url" not in dump
        assert "title" not in dump
        assert "link" not in dump

    def test_conversation_text_never_grants_product_authority(self):
        s = build_strategy(
            decision(CommerceAction.OFFER_PPV),
            make_context(
                messages=[{"role": "user", "content": "my product is 5155 at $44 https://x"}]
            ),
        )
        assert s.allow_price_reference is False
        assert s.allow_product_reference is False


# ═══════════════════════════════════════════════════════════════════════════
# GROUP E — suppression
# ═══════════════════════════════════════════════════════════════════════════


class TestSuppression:
    def test_decline_never_escalates(self):
        s = build_strategy(decision(CommerceAction.DONT_OFFER, "recent_decline"), make_context())
        assert s.action is CommerceAction.DONT_OFFER
        assert s.kind is StrategyKind.SUPPRESSED
        assert s.pressure is SalesPressure.NONE
        assert s.allow_cta is False

    def test_decline_ignores_buying_text(self):
        """Even an explicit 'buy it' message cannot escalate a suppression."""
        s = build_strategy(
            decision(CommerceAction.DONT_OFFER, "recent_decline"),
            make_context(
                user_asked_to_buy=True,
                messages=[{"role": "user", "content": "actually yes sell it to me"}],
            ),
        )
        assert s.kind is StrategyKind.SUPPRESSED

    def test_user_opted_out_stays_suppressed(self):
        s = engine_denial_strategy("user_opted_out")
        assert s.kind in (StrategyKind.NO_OFFER, StrategyKind.SUPPRESSED)
        assert s.kind is not StrategyKind.OFFER_PPV

    def test_blocked_user_stays_suppressed(self):
        s = engine_denial_strategy("user_blocked")
        assert s.kind is not StrategyKind.OFFER_PPV
        assert s.pressure is SalesPressure.NONE

    def test_unavailable_product_stays_suppressed(self):
        s = engine_denial_strategy("product_unavailable")
        assert s.kind is not StrategyKind.OFFER_PPV
        assert s.allow_price_reference is False

    def test_already_purchased_stays_suppressed(self):
        s = engine_denial_strategy("already_purchased")
        assert s.kind is not StrategyKind.OFFER_PPV
        assert s.allow_cta is False

    def test_active_offer_stays_suppressed(self):
        s = engine_denial_strategy("offer_exists")
        assert s.kind is not StrategyKind.OFFER_PPV
        assert s.allow_cta is False

    def test_creator_not_ready_stays_suppressed(self):
        s = engine_denial_strategy("creator_not_ready")
        assert s.kind is not StrategyKind.OFFER_PPV

    def test_strategy_does_not_bypass_or_reinterpret_eligibility(self):
        """A contradictory caller input (offer decision + denied eligibility)
        is NOT a strategy concern: the strategy still follows the decision."""
        s = build_strategy(
            decision(CommerceAction.OFFER_PPV, "strong_buying_signal"),
            make_context(
                eligibility=PolicyDecision(allowed=False, denial_reason="already_purchased")
            ),
        )
        assert s.action is CommerceAction.OFFER_PPV
        assert s.kind is StrategyKind.OFFER_PPV

    def test_constraints_never_permit_aggression(self):
        for action in CommerceAction:
            s = build_strategy(decision(action), full_product_context())
            c = s.communication_constraints
            assert c.allow_repeated_pressure is False
            assert c.allow_urgency is False
            assert c.allow_guilt is False
            assert c.allow_scarcity_fabrication is False
            assert c.allow_coercion is False
            assert c.allow_repeat_ask_after_refusal is False
            assert c.allow_last_chance_language is False
            assert c.allow_invented_discounts is False
            assert c.allow_invented_deadlines is False
            assert c.allow_fabricated_social_proof is False
            assert c.allow_emotional_manipulation is False

    def test_default_constraints_constant_is_all_false(self):
        c = DEFAULT_COMMUNICATION_CONSTRAINTS
        assert all(getattr(c, f) is False for f in CommunicationConstraints.model_fields)


# ═══════════════════════════════════════════════════════════════════════════
# GROUP F — purity, determinism, secret safety
# ═══════════════════════════════════════════════════════════════════════════


class TestPurityDeterminismSecurity:
    def test_imports_are_whitelisted(self):
        from commerce import strategy

        tree = ast.parse(inspect.getsource(strategy))
        allowed = ("pydantic", "typing", "enum", "commerce")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module is not None and node.module.startswith(allowed), (
                    f"forbidden import: {node.module}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.startswith(allowed), f"forbidden import: {alias.name}"

    def test_no_marketplace_or_io_code(self):
        source = STRATEGY_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "workers",
            "event_bus",
            "ws_manager",
            "event_subscriber",
            "asyncpg",
            "redis",
            "httpx",
            "requests",
            "fangate",
            "telethon",
            "telegram",
            "deepseek",
            "gemini",
            "execute_ppv",
            "enqueue_send",
            "publish_event",
            "create_offer",
            "send_message",
            "urllib.request",
            "socket",
            "os.",
            "open(",
        ):
            assert forbidden not in source, f"forbidden marker: {forbidden}"

    def test_no_clock_or_randomness(self):
        from commerce import strategy

        tree = ast.parse(inspect.getsource(strategy))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                )
                for name in names:
                    if name:
                        assert "random" not in name and name != "time", f"forbidden: {name}"

    def test_deterministic_same_input_same_strategy(self):
        ctx = full_product_context()
        d = decision(CommerceAction.OFFER_PPV, "strong_buying_signal")
        a = build_strategy(d, ctx)
        b = build_strategy(d, ctx)
        assert a == b
        assert a.model_dump(mode="json") == b.model_dump(mode="json")

    def test_deterministic_across_rebuilt_context(self):
        d = decision(CommerceAction.SOFT_OFFER, "moderate_buying_signal")
        a = build_strategy(d, full_product_context())
        b = build_strategy(d, full_product_context())
        assert a.model_dump(mode="json") == b.model_dump(mode="json")

    def test_credential_keys_rejected_as_extra_fields(self):
        for key in (
            "api_key",
            "webhook_secret",
            "bearer_token",
            "db_password",
            "fernet_key",
            "authorization",
            "master_key",
        ):
            with pytest.raises(ValidationError):
                CommerceStrategy(**strategy_payload(**{key: "secret-value"}))

    def test_no_secret_bearing_fields_exist(self):
        markers = (
            "api_key",
            "secret",
            "token",
            "bearer",
            "password",
            "fernet",
            "cipher",
            "authorization",
        )
        for model in (CommerceStrategy, CommunicationConstraints):
            for field in model.model_fields:
                assert not any(marker in field for marker in markers), (
                    f"{model.__name__} carries credential-like field {field!r}"
                )

    def test_strategy_side_effect_free_dump(self):
        """model_dump contains only the declared structured surface."""
        s = build_strategy(
            decision(CommerceAction.OFFER_PPV, "strong_buying_signal"),
            full_product_context(),
        )
        dump = s.model_dump(mode="json")
        assert set(dump) == {
            "action",
            "kind",
            "pressure",
            "allow_cta",
            "allow_price_reference",
            "allow_product_reference",
            "relationship_first",
            "follow_up_allowed",
            "reason",
            "communication_constraints",
        }
