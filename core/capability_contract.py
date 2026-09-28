"""Capability contract (C.1-F Phase 6).

Single source of truth for what Sunny can/can't do — rendered into prompt
and used by scoring. No capability is added without app support.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityContract:
    send_text: bool = True
    send_photo: bool = False
    send_video: bool = False
    send_file: bool = False
    send_tip_link: str = "governed"   # governed via suggest_tip
    send_commerce_link: str = "governed"  # via propose_product_offer
    schedule_followup: bool = True
    dropfans: bool = True
    fangate: bool = False

    def render(self) -> str:
        """Compact capability block for prompt."""
        lines = [
            f"CAPABILITIES: send_text:{'yes' if self.send_text else 'no'}",
            f"  send_photo:{'yes' if self.send_photo else 'no'} send_video:{'yes' if self.send_video else 'no'}",
            f"  send_tip:{self.send_tip_link} send_commerce_link:{self.send_commerce_link}",
            f"  schedule_followup:{'yes' if self.schedule_followup else 'no'}",
        ]
        if not self.send_photo:
            lines.append("NOTE: Do NOT promise to send a photo/video — you can't. If asked, deflect warmly: you share content via the vault when available, but don't promise a specific photo.")
        return "\n".join(lines)


def derive_capability_contract() -> CapabilityContract:
    """Derive from app configuration.

    Today: photo/video/file false for LLM; text true; vault remains
    operator-only. Tip/commerce are governed tools. Keep deterministic.
    """
    return CapabilityContract()
