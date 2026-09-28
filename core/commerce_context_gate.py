"""Pass 7: deterministic commerce-context gate (read-only audit → implementation).

Decides whether OneCall commerce hints are worth their wire cost for a turn.
Pure, deterministic, no LLM, no DB, no clock, no randomness.

Commerce authority remains DB-driven (canonical decision + DAO timing +
``commerce/state.py``); hints only improve advisory signal quality for
product/price/purchase turns. Normal chat must not pay the hint cost.
"""

from __future__ import annotations


_COMMERCE_SUBSTRINGS = ("price", "product", "buy", "purchase")

# Pass 7L: commercial desire stages (actual DesireStage values in
# commerce/desire.py). Warmth/curiosity/interest alone never activate.
_COMMERCIAL_DESIRE_MARKERS = ("purchase", "offer_ready", "desire", "qualif")
# Pass 7L: commercial objectives (actual derive_commercial_objective values
# in commerce/objective.py). relationship/explore/no_sale/aftercare stay out.
_COMMERCIAL_OBJECTIVE_MARKERS = ("purchase", "upsell", "offer", "recommend", "qualif", "desire")


def _enrichment_text(value) -> str:
    """Coerce a desire/window/objective enrichment value to lower text.

    Accepts plain strings, str-subclasses (SalesWindow), and DesireState-like
    objects (``.stage`` / ``.stage.value``). Anything else → "".
    """
    try:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip().lower()
        stage = getattr(value, "stage", None)
        if stage is not None:
            inner = getattr(stage, "value", stage)
            if isinstance(inner, str):
                return inner.strip().lower()
            return str(inner).strip().lower()
        if isinstance(value, (dict, list, tuple)):
            return ""
        return str(value).strip().lower()
    except Exception:
        return ""


def commerce_context_required(
    message: str,
    relationship_state: str | None = None,
    has_active_offer: bool = False,
    has_relevant_product: bool = False,
    desire: str | None = None,
    window: str | None = None,
    objective: str | None = None,
) -> bool:
    """Return True iff commerce hints are warranted for this turn.

    Args:
        message: Raw fan message (authoritative text source).
        relationship_state: Current relationship state (accepted, unused —
            warmth alone never activates commerce context by construction).
        has_active_offer: Pending/clicked offer exists (needs context).
        has_relevant_product: Relevant product exists (accepted, unused —
            inventory alone never activates context; requires textual cue
            or active offer).
        desire: Deterministic desire stage (DesireStage value or DesireState;
            commercial stages desire/qualification/offer_ready/purchase
            activate). None = unknown (no signal).
        window: Deterministic sales window (SalesWindow value; "open"
            activates). None = unknown.
        objective: Deterministic commercial objective
            (derive_commercial_objective value; present_offer/recommend/
            qualify/build_desire and purchase/upsell/offer markers activate).

    True conditions (any):
    - deterministic explicit purchase request
    - deterministic price inquiry
    - deterministic photo request (text only, signals=None)
    - "price"/"product"/"buy"/"purchase" substring (lower)
    - has_active_offer True
    - commercial desire / open window / commercial objective (Pass 7L)

    Fail-closed: unexpected input → False.
    """
    try:
        if has_active_offer is True:
            return True
        # Pass 7L: deterministic enrichment from the commerce state derivation
        # (commerce/conversational.py). Pure string matching, no DB/LLM.
        try:
            _des = _enrichment_text(desire)
            if _des and any(k in _des for k in _COMMERCIAL_DESIRE_MARKERS):
                return True
            _win = _enrichment_text(window)
            if _win == "open":
                return True
            _obj = _enrichment_text(objective)
            if _obj and any(k in _obj for k in _COMMERCIAL_OBJECTIVE_MARKERS):
                return True
        except Exception:
            pass
        if not isinstance(message, str) or not message.strip():
            return False
        lowered = message.strip().lower()
        if any(s in lowered for s in _COMMERCE_SUBSTRINGS):
            return True
        try:
            from commerce.purchase_intent import (
                is_explicit_purchase_request as _is_buy,
            )
            from commerce.purchase_intent import is_price_inquiry as _is_price

            if bool(_is_buy(message)):
                return True
            if bool(_is_price(message)):
                return True
        except Exception:
            pass
        try:
            from commerce.free_photo_routing import is_photo_request as _is_photo

            if bool(_is_photo(message, None)):
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False
