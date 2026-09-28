"""Deterministic PPV offer eligibility (Phase 5.2).

Pure rules engine: verdict = f(user, product, context) -> PolicyDecision.

No DB, no HTTP, no event bus, no Fangate credentials. Callers supply every
input as plain values; this module never resolves them itself.
"""

from dataclasses import dataclass

from commerce.models import PolicyDecision


@dataclass(frozen=True)
class UserEligibilityState:
    """Fan-side inputs that gate whether an automated offer may be made."""

    is_blocked: bool = False
    do_not_auto_reply: bool = False


@dataclass(frozen=True)
class ProductEligibilityState:
    """Product-side inputs that gate whether an offer is actionable.

    ``price_minor`` is informational only: None means pay-what-you-want or an
    unknown price, which is not a denial condition.
    """

    is_accessible: bool = True
    sales_url: str | None = None
    price_minor: int | None = None


@dataclass(frozen=True)
class OfferContext:
    """State the caller already knows about the creator/fan relationship."""

    creator_ready: bool = False
    has_active_offer: bool = False
    already_purchased: bool = False
    enforce_age_verification: bool = False
    age_verified: bool = False


DEFAULT_OFFER_CONTEXT = OfferContext()


def evaluate_ppv_eligibility(
    user: UserEligibilityState,
    product: ProductEligibilityState,
    ctx: OfferContext = DEFAULT_OFFER_CONTEXT,
) -> PolicyDecision:
    """Return whether an automated PPV offer to this fan is appropriate.

    Rules run in a fixed order; the first denial wins. Denial reasons are
    stable code values (never user-facing text) so callers can map them to
    UI/audit labels without parsing prose.
    """
    if user.is_blocked:
        return PolicyDecision(allowed=False, denial_reason="user_blocked")
    if user.do_not_auto_reply:
        return PolicyDecision(allowed=False, denial_reason="user_opted_out")
    if not ctx.creator_ready:
        return PolicyDecision(allowed=False, denial_reason="creator_not_ready")
    if not product.is_accessible:
        return PolicyDecision(allowed=False, denial_reason="product_unavailable")
    if not product.sales_url:
        return PolicyDecision(allowed=False, denial_reason="product_missing_sales_url")
    if ctx.already_purchased:
        return PolicyDecision(allowed=False, denial_reason="already_purchased")
    if ctx.has_active_offer:
        return PolicyDecision(allowed=False, denial_reason="offer_exists")
    if ctx.enforce_age_verification and not ctx.age_verified:
        return PolicyDecision(allowed=False, denial_reason="age_verification_required")
    return PolicyDecision(allowed=True)
