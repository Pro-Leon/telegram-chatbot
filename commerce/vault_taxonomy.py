"""Vault title taxonomy — deterministic, no vision (Phase 3).

Consistent human-readable titles are the LLM's semantic lens.

Canonical structure:
  [SUBJECT] — [SETTING] — [FORMAT]
  Red Lace — Bedroom — 3 Photo Set

Fields are derived from the authoritative DropFans title, not hallucinated.
If a title is opaque (IMG_4829, Campaign set), all taxonomy fields stay unknown
and the original title is preserved verbatim.

No LLM, no embeddings, no external taxonomy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SPLIT_RE = re.compile(r"\s*[—\-–]\s*")
_TOK_RE = re.compile(r"[a-z0-9]+")
_QUANT_RE = re.compile(r"(\d+)\s*(photo|video|picture|set|bundle|pack)", re.I)

@dataclass(frozen=True)
class VaultTaxonomy:
    subject: str | None    # e.g. "red lace"
    setting: str | None    # e.g. "bedroom"
    format: str | None     # e.g. "3 photo set" normalized
    media_count: int | None
    bundle_size: str | None  # "set" | "bundle" | "mega bundle" | None
    bundle_group: str       # P3.3.4 QUARANTINE: descriptive display key only
    # (normalized subject+setting). Retained for compatibility/display
    # organization. MUST NOT drive SINGLE vs BUNDLE selection, eligibility,
    # ownership, fatigue, suppression, or bundle preference. See
    # ``display_group`` alias below.

    @property
    def display_group(self) -> str:
        """Descriptive display grouping alias for ``bundle_group``.

        P3.3.4: display grouping is presentation metadata only — it confers
        no commercial eligibility, bundle relationship, fatigue, or
        suppression authority.
        """
        return self.bundle_group


def normalize_title(title: str) -> tuple[str, str, str]:
    """Best-effort split on em-dash. Returns (subject, setting, format_raw)."""
    if not title or not title.strip():
        return (None, None, None)  # type: ignore
    parts = [p.strip() for p in _SPLIT_RE.split(title.strip()) if p.strip()]
    if len(parts) >= 3:
        return (parts[0], parts[1], " — ".join(parts[2:]))
    if len(parts) == 2:
        return (parts[0], parts[1], None)  # type: ignore
    if len(parts) == 1:
        return (parts[0], None, None)  # type: ignore
    return (None, None, None)  # type: ignore

def _detect_media_count(format_raw: str | None) -> tuple[int | None, str | None]:
    if not format_raw:
        return (None, None)
    m = _QUANT_RE.search(format_raw)
    if not m:
        return (None, None)
    cnt = int(m.group(1))
    word = m.group(2).lower()
    if word in ("set",):
        bundle = "set"
    elif word in ("bundle", "pack"):
        bundle = "bundle"
    else:
        bundle = word
    # mega bundle heuristic: count >=8
    if cnt >= 8 and bundle == "bundle":
        bundle = "mega bundle"
    return (cnt, bundle)

def parse_taxonomy(title: str | None) -> VaultTaxonomy:
    if not title or not isinstance(title, str):
        return VaultTaxonomy(None, None, None, None, None, "")
    subject, setting, fmt = normalize_title(title)
    # Reject opaque titles with no taxonomy signal
    if title.strip().lower() in ("img_4829", "set 3", "hot pics", "new drop", "campaign set") or len(title.strip()) < 4:
        # still attempt, but low confidence
        pass
    cnt, bundle = _detect_media_count(fmt)
    fmt_norm = fmt.lower() if fmt else None
    group = ""
    if subject and setting:
        group = f"{subject.lower()} | {setting.lower()}"
    elif subject:
        group = subject.lower()
    elif setting:
        group = setting.lower()
    return VaultTaxonomy(
        subject=subject.lower() if subject else None,
        setting=setting.lower() if setting else None,
        format=fmt_norm,
        media_count=cnt,
        bundle_size=bundle,
        bundle_group=group,
    )

def bundle_related(a: VaultTaxonomy, b: VaultTaxonomy) -> bool:
    """Descriptive similarity only (P3.3.4 QUARANTINE).

    True if same subject+setting (i.e. same descriptive display group).
    This is NOT proof of a shared commercial relationship, bundle
    membership, family, prior purchase, or fatigue/suppression signal.
    No production selection/ranking path may treat a True result as
    authorization to bundle, suppress, prefer, or fatigue candidates.
    Retained for compatibility/display and tests only.
    """
    if not a.bundle_group or not b.bundle_group:
        return False
    return a.bundle_group == b.bundle_group

def tokens(title: str | None) -> set[str]:
    if not title:
        return set()
    return set(_TOK_RE.findall(title.lower()))
