"""Sales window — deterministic NO_WINDOW / BUILDING / OPEN / COOLDOWN / AFTERCARE.

Thin composition of desire + temperature + offer_readiness + cooldown + aftercare.
Deterministic, no LLM, no clock beyond hours thresholds already derived.

This is the sales-window scalar the LLM needs to lead. It is NOT the
commerce decision. The LLM may see OPEN but still not sell until
offer_readiness READY. Aftercare/cooldown suppress OPEN.

Provider-independent. Written once per turn.
"""

from __future__ import annotations


class SalesWindow(str):
    NO_WINDOW = "no_window"
    BUILDING = "building"
    OPEN = "open"
    COOLDOWN = "cooldown"
    AFTERCARE = "aftercare"


def derive_sales_window(
    desire_stage: str,
    temperature: str,  # cold/warm/hot
    offer_readiness: str,  # not_ready/build_desire/test_interest/ready
    *,
    aftercare_active: bool = False,
    is_on_cooldown: bool = False,
) -> str:
    if aftercare_active:
        return SalesWindow.AFTERCARE
    if is_on_cooldown:
        return SalesWindow.COOLDOWN
    # Offer ready + hot/warm → open
    if offer_readiness == "ready" and temperature in ("warm", "hot"):
        return SalesWindow.OPEN
    # Desire building stages
    if desire_stage in ("desire", "qualification", "interest", "curiosity"):
        return SalesWindow.BUILDING
    if desire_stage in ("relationship",):
        return SalesWindow.NO_WINDOW
    # Fallback on temperature
    if temperature == "hot" and offer_readiness in ("test_interest", "ready"):
        return SalesWindow.OPEN
    if temperature == "warm":
        return SalesWindow.BUILDING
    return SalesWindow.NO_WINDOW
