"""Data Gatherers — Real production API adapters (Phase 71).

Implements typed, creator-isolated context gatherers that access real
production data sources.  Each source:
  - fails safely (return empty list on error, never raise)
  - respects creator isolation
  - provides explicit provenance
  - uses only SELECT queries (no writes, no side effects)
  - is standalone (does not modify existing production code)

Phase 70 fixture implementations have been replaced with real adapters.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from context_engine.budget import estimate_tokens
from context_engine.models import (
    AuthorityLevel,
    ContentTrust,
    ContextCategory,
    ContextItem,
    RetrievalScore,
)

logger = logging.getLogger("context_engine.gatherer")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class GathererConfig:
    """Configuration for data gathering."""

    creator_id: int | None = None
    user_id: int | None = None
    current_message: str = ""
    conversation_state: dict[str, Any] | None = None
    authoritative_state: Any | None = None  # AuthoritativeState when available (Phase 2 single snapshot)
    # Phase 87 instrumentation side-channel (fail-open, never affects retrieval)
    # MemorySource writes retrieval metrics here; gatherer reads it afterwards.
    _retrieval_metrics: dict[str, Any] | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class DataSource(ABC):
    """Abstract base class for context data sources.

    All sources must:
    - fail safely (return empty list on error)
    - respect creator isolation
    - provide explicit provenance
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Name of this source for provenance tracking."""
        ...

    @property
    @abstractmethod
    def category(self) -> ContextCategory:
        """Context category for this source."""
        ...

    @property
    @abstractmethod
    def authority(self) -> AuthorityLevel:
        """Authority level of data from this source."""
        ...

    @abstractmethod
    async def gather(
        self,
        config: GathererConfig,
    ) -> list[ContextItem]:
        """Gather context items from this source.

        Must return empty list on error, never raise.
        """
        ...

    def _make_item(
        self,
        content: str,
        config: GathererConfig,
        priority: int = 5,
        trust: ContentTrust = ContentTrust.AUTHORITATIVE,
        score: float = 0.7,
        metadata: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> ContextItem:
        """Helper to build a ContextItem with consistent defaults."""
        return ContextItem(
            item_id=ContextItem.generate_id(self.category, self.source_name, content),
            category=self.category,
            content=content,
            authority=self.authority,
            trust=trust,
            token_cost=estimate_tokens(content),
            retrieval_score=self._default_score(score),
            source=self.source_name,
            priority=priority,
            creator_id=config.creator_id,
            user_id=config.user_id,
            metadata=metadata or {},
            timestamp=timestamp,
        )

    def _default_score(self, final: float = 0.7) -> RetrievalScore:
        """Default retrieval score for this source."""
        return RetrievalScore(
            source_score=1.0,
            topic_overlap=0.5,
            recency_score=1.0,
            importance_score=0.8,
            state_relevance=0.6,
            authority_score=min(1.0, self.authority.value / 5.0 + 0.4),
            final_score=final,
        )


# ---------------------------------------------------------------------------
# PersonaSource — SYSTEM category, HARD_POLICY
# ---------------------------------------------------------------------------


class PersonaSource(DataSource):
    """System prompt and persona instructions from production DB.

    Authority: HARD_POLICY (level 0) — persona is authoritatively set
    by the creator/operator and must never be overridden by LLM output.
    """

    @property
    def source_name(self) -> str:
        return "persona"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.SYSTEM

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.HARD_POLICY

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        items: list[ContextItem] = []
        if config.creator_id is None:
            return items

        # Phase 2: consume authoritative snapshot when present (single acquisition)
        auth = config.authoritative_state
        if auth is not None:
            try:
                persona_text = getattr(auth, "persona", "") or ""
                if persona_text:
                    items.append(self._make_item(
                        persona_text, config, priority=10, score=0.95,
                    ))
                structured = getattr(auth, "structured_persona", None)
                if structured:
                    from memory.creator_persona import render_compact_persona_block
                    compact = render_compact_persona_block(structured)
                    if compact:
                        items.append(self._make_item(
                            compact, config, priority=9, score=0.90,
                            metadata={"format": "compact_structured"},
                        ))
                if items:
                    return items
                # fall through to DB if snapshot empty
            except Exception:
                logger.debug("PersonaSource: snapshot persona unavailable", exc_info=True)

        try:
            from db.postgres import get_user_persona

            persona_text = await get_user_persona(
                creator_id=config.creator_id,
                user_id=config.user_id,
            )
            if persona_text:
                items.append(self._make_item(
                    persona_text, config, priority=10, score=0.95,
                ))
        except Exception:
            logger.warning("PersonaSource: persona query failed", exc_info=True)

        # Structured persona (compact render)
        try:
            from memory.creator_persona import (
                get_structured_persona_async,
                render_compact_persona_block,
            )

            structured = await get_structured_persona_async(
                creator_id=config.creator_id,
            )
            if structured:
                compact = render_compact_persona_block(structured)
                if compact:
                    items.append(self._make_item(
                        compact, config, priority=9, score=0.90,
                        metadata={"format": "compact_structured"},
                    ))
        except Exception:
            logger.debug("PersonaSource: structured persona unavailable", exc_info=True)

        return items

    def _default_score(self, final: float = 0.9) -> RetrievalScore:
        return RetrievalScore(
            source_score=1.0,
            topic_overlap=0.5,
            recency_score=1.0,
            importance_score=1.0,
            state_relevance=0.5,
            authority_score=1.0,
            final_score=final,
        )


# ---------------------------------------------------------------------------
# FanStateSource — STATE category, DETERMINISTIC_DERIVATION
# ---------------------------------------------------------------------------


class FanStateSource(DataSource):
    """Deterministic fan/business state from production DB.

    Authority: DETERMINISTIC_DERIVATION (level 2) — computed from
    DB state, no LLM involved.
    """

    @property
    def source_name(self) -> str:
        return "fan_state"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.STATE

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.DETERMINISTIC_DERIVATION

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        items: list[ContextItem] = []
        if config.user_id is None:
            return items

        # Phase 2: use authoritative snapshot when available
        auth = config.authoritative_state
        if auth is not None:
            try:
                user_data = getattr(auth, "user", None)
                # P2 Fix 4A: user may be MappingProxyType after deep freeze
                if user_data is not None and hasattr(user_data, "get") and len(user_data) > 0:
                    pass
                else:
                    user_data = await self._get_user_safe(config.user_id)
                # Phase 5 single authority: reuse authoritative relationship_state if present
                rel_state = None
                try:
                    _auth_rel = getattr(auth, "relationship_state", None)
                    if isinstance(_auth_rel, str) and _auth_rel.strip():
                        rel_state = _auth_rel.strip()
                    else:
                        # Phase 2.4: single lifecycle owner (thin consumer).
                        from core.conversation_state import derive_lifecycle_state as _lifecycle_owner

                        _purchase_ctx = getattr(auth, "purchase_context", None)
                        _has_active_auth = getattr(auth, "has_active_offer", False)
                        _pc: int | None
                        _has_active: bool | None
                        if isinstance(_purchase_ctx, dict) and _purchase_ctx:
                            _pc = int(_purchase_ctx.get("total_purchases", 0))
                            _has_active = bool(_purchase_ctx.get("has_active_offer", _has_active_auth))
                            _last_purchase = _purchase_ctx.get("last_purchase_days_ago")
                            _last_msg = _purchase_ctx.get("last_message_days_ago")
                            # Fallback last_message from recent if missing
                            if _last_msg is None:
                                _recent_for_rel = getattr(auth, "recent_messages", None)
                                _recent_list = list(_recent_for_rel) if _recent_for_rel else []
                                if _recent_list:
                                    try:
                                        last_msg = _recent_list[-1]
                                        last_at = last_msg.get("created_at") or last_msg.get("timestamp")
                                        if last_at:
                                            if isinstance(last_at, str):
                                                last_at = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
                                            if hasattr(last_at, "tzinfo") and last_at.tzinfo is None:
                                                last_at = last_at.replace(tzinfo=UTC)
                                            _last_msg = (datetime.now(UTC) - last_at).total_seconds() / 86400
                                    except Exception:
                                        _last_msg = None
                            _life = _lifecycle_owner(
                                funnel_stage=user_data.get("funnel_stage", "new"),
                                purchase_count=_pc,
                                last_purchase_days_ago=_last_purchase if isinstance(_last_purchase, (int, float)) else None,
                                last_message_days_ago=_last_msg if isinstance(_last_msg, (int, float)) else None,
                                message_count=int(user_data.get("message_count", 0)),
                                has_active_offer=bool(_has_active),
                            )
                            if _life.degraded:
                                logger.warning(
                                    "FanStateSource: relationship degraded user=%s", config.user_id
                                )
                            _rel_value = _life.relationship_state
                        else:
                            # Legacy fallback: no purchase context — explicit unknown,
                            # never zeros-as-truth. Recency-only derivation would
                            # understate buyers, so omit (degraded) instead.
                            _recent_for_rel = getattr(auth, "recent_messages", None)
                            _recent_list = list(_recent_for_rel) if _recent_for_rel else []
                            last_message_days_ago = None
                            if _recent_list:
                                try:
                                    last_msg = _recent_list[-1]
                                    last_at = last_msg.get("created_at") or last_msg.get("timestamp")
                                    if last_at:
                                        if isinstance(last_at, str):
                                            last_at = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
                                        if hasattr(last_at, "tzinfo") and last_at.tzinfo is None:
                                            last_at = last_at.replace(tzinfo=UTC)
                                        last_message_days_ago = (datetime.now(UTC) - last_at).total_seconds() / 86400
                                except Exception:
                                    pass
                            _life = _lifecycle_owner(
                                funnel_stage=user_data.get("funnel_stage", "new"),
                                purchase_count=None,
                                last_purchase_days_ago=None,
                                last_message_days_ago=last_message_days_ago,
                                message_count=int(user_data.get("message_count", 0)),
                                has_active_offer=None,
                            )
                            logger.warning(
                                "FanStateSource: relationship unknown (no purchase context) user=%s",
                                config.user_id,
                            )
                            _rel_value = None
                        rel_state = _rel_value
                except Exception:
                    rel_state = None
                capability = await self._get_capability_safe()
                state_parts: list[str] = []
                state_parts.append(f"Funnel: {user_data.get('funnel_stage','new')}")
                state_parts.append(f"Messages: {user_data.get('message_count',0)}")
                state_parts.append(f"Blocked: {bool(user_data.get('is_blocked',False))}")
                state_parts.append(f"Auto-reply: {'no' if user_data.get('do_not_auto_reply') else 'yes'}")
                if rel_state:
                    state_parts.append(f"Relationship: {rel_state}")
                if capability:
                    state_parts.append(capability)
                # Append commerce context text from snapshot if present (authoritative)
                auth_commerce = getattr(auth, "commerce_context_text", "") or ""
                if auth_commerce:
                    # Prefer snapshot commerce text as authoritative; append compact
                    # Use first 200 chars to bound
                    snippet = auth_commerce.split("\n")[0][:200]
                    if snippet:
                        state_parts.append(f"Commerce: {snippet}")
                state_text = " | ".join(state_parts)
                if state_text:
                    items.append(self._make_item(
                        state_text, config, priority=9, score=0.85,
                    ))
                segments_text = await self._get_segments_safe(config)
                if segments_text:
                    items.append(self._make_item(
                        segments_text, config, priority=6, score=0.70,
                    ))
                return items
            except Exception:
                logger.debug("FanStateSource snapshot path failed, falling back", exc_info=True)

        # User profile
        user_data = await self._get_user_safe(config.user_id)
        # Relationship state
        rel_state = await self._get_relationship_safe(config)
        # Capability contract
        capability = await self._get_capability_safe()

        state_parts: list[str] = []
        state_parts.append(f"Funnel: {user_data['funnel_stage']}")
        state_parts.append(f"Messages: {user_data['message_count']}")
        state_parts.append(f"Blocked: {user_data['is_blocked']}")
        state_parts.append(f"Auto-reply: {'no' if user_data['do_not_auto_reply'] else 'yes'}")
        if rel_state:
            state_parts.append(f"Relationship: {rel_state}")
        if capability:
            state_parts.append(capability)

        state_text = " | ".join(state_parts)
        if state_text:
            items.append(self._make_item(
                state_text, config, priority=9, score=0.85,
            ))

        # Segments (informational)
        segments_text = await self._get_segments_safe(config)
        if segments_text:
            items.append(self._make_item(
                segments_text, config, priority=6, score=0.70,
            ))

        return items

    async def _get_user_safe(self, user_id: int) -> dict[str, Any]:
        try:
            from db.postgres import get_user_durable

            user, degraded, source = await get_user_durable(user_id)
            if degraded:
                logger.warning(
                    "FanStateSource: user query degraded user=%s source=%s", user_id, source
                )
            if user is None:
                return {
                    "funnel_stage": "new",
                    "is_blocked": False,
                    "do_not_auto_reply": False,
                    "message_count": 0,
                }
            return {
                "funnel_stage": user.get("funnel_stage") or "new",
                "is_blocked": bool(user.get("is_blocked", False)),
                "do_not_auto_reply": bool(user.get("do_not_auto_reply", False)),
                "message_count": int(user.get("message_count") or 0),
            }
        except Exception:
            logger.warning(
                "FanStateSource: user query degraded user=%s source=error", user_id, exc_info=True
            )
            return {
                "funnel_stage": "new",
                "is_blocked": False,
                "do_not_auto_reply": False,
                "message_count": 0,
            }

    async def _get_relationship_safe(self, config: GathererConfig) -> str | None:
        if config.creator_id is None or config.user_id is None:
            return None
        try:
            # Phase 2.4: single lifecycle owner. This source has no purchase
            # context, so purchase inputs are explicit unknown (None) — never
            # zeros-as-truth. Degraded output is omitted, not emitted.
            from core.conversation_state import derive_lifecycle_state as _lifecycle_owner

            from db.postgres import get_recent_messages

            messages = await get_recent_messages(config.user_id, limit=5, creator_id=config.creator_id)
            last_purchase_days_ago = None
            last_message_days_ago = None
            if messages:
                last_msg = messages[-1]
                if "created_at" in last_msg:
                    try:
                        last_at = last_msg["created_at"]
                        if isinstance(last_at, str):
                            last_at = datetime.fromisoformat(last_at)
                        if last_at.tzinfo is None:
                            last_at = last_at.replace(tzinfo=UTC)
                        last_message_days_ago = (datetime.now(UTC) - last_at).total_seconds() / 86400
                    except Exception:  # noqa: BLE001,S110 — fail-safe: datetime parse errors must not crash gatherer
                        pass

            user = await self._get_user_safe(config.user_id)
            _life = _lifecycle_owner(
                funnel_stage=user["funnel_stage"],
                purchase_count=None,
                last_purchase_days_ago=last_purchase_days_ago,
                last_message_days_ago=last_message_days_ago,
                message_count=user["message_count"],
                has_active_offer=None,
            )
            if _life.degraded:
                return None
            return _life.relationship_state
        except Exception:
            logger.debug("FanStateSource: relationship derivation failed", exc_info=True)
            return None

    async def _get_capability_safe(self) -> str | None:
        try:
            from core.capability_contract import derive_capability_contract
            contract = derive_capability_contract()
            return contract.render()
        except Exception:  # noqa: BLE001 — fail-safe: capability errors must not crash gatherer
            return None

    async def _get_segments_safe(self, config: GathererConfig) -> str | None:
        if config.creator_id is None or config.user_id is None:
            return None
        try:
            from db import segments as sdb
            from segments.evaluator import check_user_in_segment
            from segments.models import RuleGroup

            segments = await sdb.list_segments(config.creator_id, enabled_only=True)
            member_names: list[str] = []
            for seg in segments:
                rules_data = seg.get("rules")
                if not rules_data:
                    continue
                try:
                    rule = RuleGroup.model_validate(rules_data)
                    is_member = await check_user_in_segment(config.creator_id, rule, config.user_id)
                    if is_member:
                        member_names.append(seg["name"])
                except Exception:  # noqa: BLE001,S112 — fail-safe: per-segment eval failure is non-critical
                    continue
            if member_names:
                return f"Segments: {', '.join(member_names)}"
            return None
        except Exception:  # noqa: BLE001 — fail-safe: segment query failure must not crash gatherer
            return None


# ---------------------------------------------------------------------------
# ConversationHistorySource — CONVERSATION category, DETERMINISTIC_RULE
# ---------------------------------------------------------------------------


class ConversationHistorySource(DataSource):
    """Recent messages and conversation summary from production DB.

    Authority: DETERMINISTIC_RULE (level 1) — raw data from DB,
    no derivation, no LLM.
    """

    @property
    def source_name(self) -> str:
        return "conversation_history"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.CONVERSATION

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.DETERMINISTIC_RULE

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        items: list[ContextItem] = []
        if config.user_id is None:
            return items

        # Phase 2: prefer authoritative snapshot when available (single acquisition)
        auth = config.authoritative_state
        if auth is not None:
            try:
                recent_tuple = getattr(auth, "recent_messages", None)
                if recent_tuple is not None:
                    messages = list(recent_tuple) if isinstance(recent_tuple, (tuple, list)) else []
                else:
                    messages = await self._get_messages_safe(config)
                for i, msg in enumerate(messages):
                    direction = msg.get("direction") or "unknown"
                    # Canonical mapping: inbound→user (Fan/Player), outbound→assistant (Creator/Character)
                    if direction == "inbound":
                        role = "user"
                    elif direction == "outbound":
                        role = "assistant"
                    else:
                        role = "user"
                    content = msg.get("content") or ""
                    if not content:
                        continue
                    turn_text = content
                    recency = max(0.3, 1.0 - i * 0.1)
                    items.append(self._make_item(
                        turn_text, config,
                        priority=i,
                        score=0.65 + recency * 0.15,
                        metadata={"role": role, "direction": direction, "position": i},
                    ))
                summary = getattr(auth, "summary", None)
                if summary:
                    items.append(self._make_item(
                        f"[summary] {summary}", config,
                        priority=8, score=0.80,
                        metadata={"type": "summary", "role": "user"},
                    ))
                else:
                    # fallback to DB if snapshot summary empty but DB has one
                    s2 = await self._get_summary_safe(config)
                    if s2:
                        items.append(self._make_item(
                            f"[summary] {s2}", config,
                            priority=8, score=0.80,
                            metadata={"type": "summary", "role": "user"},
                        ))
                return items
            except Exception:
                logger.debug("ConversationHistorySource snapshot path failed, falling back", exc_info=True)

        # Recent messages
        messages = await self._get_messages_safe(config)
        for i, msg in enumerate(messages):
            direction = msg.get("direction") or "unknown"
            if direction == "inbound":
                role = "user"
            elif direction == "outbound":
                role = "assistant"
            else:
                role = "user"
            content = msg.get("content") or ""
            if not content:
                continue
            turn_text = content
            recency = max(0.3, 1.0 - i * 0.1)
            items.append(self._make_item(
                turn_text, config,
                priority=i,
                score=0.65 + recency * 0.15,
                metadata={"role": role, "direction": direction, "position": i},
            ))

        # Conversation summary
        summary = await self._get_summary_safe(config)
        if summary:
            items.append(self._make_item(
                f"[summary] {summary}", config,
                priority=8, score=0.80,
                metadata={"type": "summary", "role": "user"},
            ))

        return items

    async def _get_messages_safe(self, config: GathererConfig) -> list[dict[str, Any]]:
        try:
            from db.postgres import get_recent_messages_durable

            rows, degraded, source = await get_recent_messages_durable(
                config.user_id,
                limit=20,
                creator_id=config.creator_id,
            )
            if degraded:
                logger.warning(
                    "ConversationHistorySource: messages query degraded for %s source=%s",
                    config.user_id,
                    source,
                )
            return rows
        except Exception:
            logger.warning(
                "ConversationHistorySource: messages query degraded for %s source=error",
                config.user_id,
                exc_info=True,
            )
            return []

    async def _get_summary_safe(self, config: GathererConfig) -> str | None:
        try:
            from db.postgres import get_latest_summary_durable

            summary, degraded, source = await get_latest_summary_durable(
                config.user_id,
                creator_id=config.creator_id,
            )
            if degraded:
                logger.warning(
                    "ConversationHistorySource: summary query degraded for %s source=%s",
                    config.user_id,
                    source,
                )
            return summary
        except Exception:
            logger.debug(
                "ConversationHistorySource: summary query degraded for %s source=error",
                config.user_id,
                exc_info=True,
            )
            return None


# ---------------------------------------------------------------------------
# CommerceStateSource — COMMERCE category, DETERMINISTIC_RULE
# ---------------------------------------------------------------------------


class CommerceStateSource(DataSource):
    """Purchase history, active offers, and commerce timing from production DB.

    Authority: DETERMINISTIC_RULE (level 1) — raw DB data, no derivation.
    """

    @property
    def source_name(self) -> str:
        return "commerce_state"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.COMMERCE

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.DETERMINISTIC_RULE

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        items: list[ContextItem] = []
        if config.user_id is None or config.creator_id is None:
            return items

        # Phase 2: snapshot-aware path – reuse commerce_context_text if present
        auth = config.authoritative_state
        if auth is not None:
            try:
                commerce_text = getattr(auth, "commerce_context_text", "") or ""
                llm_ctx = getattr(auth, "llm_context", None)
                if commerce_text or llm_ctx is not None:
                    # Split commerce_text into lines for items (deterministic, bounded)
                    # llm_context holds structured purchase/active_offer data; reuse text directly
                    if commerce_text:
                        # Use first 500 chars as commerce state (already deterministic)
                        snippet = commerce_text[:500]
                        items.append(self._make_item(
                            f"Commerce: {snippet}", config, priority=8, score=0.80,
                            metadata={"type": "commerce_snapshot", "from_snapshot": True},
                        ))
                    # Still supplement timing from auth llm_context if available for higher fidelity
                    # No extra DB fetch needed – snapshot already has timing inside commerce_text
                    if items:
                        return items
            except Exception:
                logger.debug("CommerceStateSource snapshot path failed, falling back", exc_info=True)

        # Purchase history
        purchases = await self._get_purchases_safe(config)
        if purchases:
            items.append(self._make_item(
                purchases, config, priority=8, score=0.80,
                metadata={"type": "purchase_history"},
            ))

        # Active offers
        offers = await self._get_active_offers_safe(config)
        if offers:
            items.append(self._make_item(
                offers, config, priority=7, score=0.75,
                metadata={"type": "active_offers"},
            ))

        # Timing context
        timing = await self._get_timing_safe(config)
        if timing:
            items.append(self._make_item(
                timing, config, priority=6, score=0.70,
                metadata={"type": "timing_context"},
            ))

        return items

    async def _get_purchases_safe(self, config: GathererConfig) -> str | None:
        try:
            from db.postgres import get_pool

            pool = await get_pool()
            async with pool.acquire() as conn:
                count_row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS cnt FROM commerce_offers
                    WHERE creator_id = $1 AND user_id = $2
                      AND state = 'purchased' AND transaction_id IS NOT NULL
                    """,
                    config.creator_id,
                    config.user_id,
                )
                purchase_count = int(count_row["cnt"]) if count_row else 0

                if purchase_count == 0:
                    return None

                rows = await conn.fetch(
                    """
                    SELECT fp.title AS product_title,
                           co.price_minor,
                           co.currency,
                           co.purchased_at
                    FROM commerce_offers co
                    LEFT JOIN fangate_products fp
                        ON fp.creator_id = co.creator_id AND fp.id = co.product_id
                    WHERE co.creator_id = $1 AND co.user_id = $2
                      AND co.state = 'purchased' AND co.transaction_id IS NOT NULL
                    ORDER BY co.purchased_at DESC NULLS LAST, co.id DESC
                    LIMIT 5
                    """,
                    config.creator_id,
                    config.user_id,
                )
                parts = [f"Purchases: {purchase_count}"]
                for r in rows:
                    title = r["product_title"] or "Unknown"
                    price = r["price_minor"]
                    currency = r["currency"] or ""
                    price_str = f" {price} {currency}" if price is not None else ""
                    parts.append(f"- {title}{price_str}")
                return " | ".join(parts[:6])  # bound output
        except Exception:
            logger.debug(
                "CommerceStateSource: purchase query failed (creator=%s user=%s)",
                config.creator_id, config.user_id, exc_info=True,
            )
            return None

    async def _get_active_offers_safe(self, config: GathererConfig) -> str | None:
        try:
            from db.postgres import get_pool

            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT fp.title AS product_title,
                           co.price_minor,
                           co.currency,
                           co.state,
                           co.created_at
                    FROM commerce_offers co
                    LEFT JOIN fangate_products fp
                        ON fp.creator_id = co.creator_id AND fp.id = co.product_id
                    WHERE co.creator_id = $1 AND co.user_id = $2
                      AND co.state IN ('pending', 'clicked')
                    ORDER BY co.created_at DESC, co.id DESC
                    LIMIT 3
                    """,
                    config.creator_id,
                    config.user_id,
                )
                if not rows:
                    return None
                parts = ["Active offers:"]
                for r in rows:
                    title = r["product_title"] or "Unknown"
                    state = r["state"] or "unknown"
                    parts.append(f"- {title} [{state}]")
                return " | ".join(parts)
        except Exception:
            logger.debug(
                "CommerceStateSource: offer query failed (creator=%s user=%s)",
                config.creator_id, config.user_id, exc_info=True,
            )
            return None

    async def _get_timing_safe(self, config: GathererConfig) -> str | None:
        try:
            from commerce.dao import get_timing_context

            timing = await get_timing_context(config.creator_id, config.user_id)
            parts: list[str] = []
            hlo = timing.get("hours_since_last_offer")
            if hlo is not None:
                parts.append(f"Last offer: {hlo:.1f}h ago")
            hlp = timing.get("hours_since_last_purchase")
            if hlp is not None:
                parts.append(f"Last purchase: {hlp:.1f}h ago")
            roc = timing.get("recent_offer_count", 0)
            if roc:
                parts.append(f"Offers(24h): {roc}")
            rpc = timing.get("recent_purchase_count", 0)
            if rpc:
                parts.append(f"Purchases(24h): {rpc}")
            return " | ".join(parts) if parts else None
        except Exception:  # noqa: BLE001 — fail-safe: timing query failure must not crash gatherer
            return None


# ---------------------------------------------------------------------------
# MemorySource — MEMORY category, DETERMINISTIC_DERIVATION
# ---------------------------------------------------------------------------


class MemorySource(DataSource):
    """Fan knowledge from production long-term memory.

    Authority: DETERMINISTIC_DERIVATION (level 2) — knowledge extracted
    from conversation, confirmed facts.
    """

    @property
    def source_name(self) -> str:
        return "fan_memory"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.MEMORY

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.DETERMINISTIC_DERIVATION

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        items: list[ContextItem] = []
        if config.user_id is None or config.creator_id is None:
            return items

        knowledge_items = await self._get_knowledge_safe(config)
        for ki in knowledge_items:
            items.append(self._make_item(
                ki, config, priority=7, score=0.75,
            ))

        # Conversation summary (memory category overlap)
        summary = await self._get_summary_safe(config)
        if summary:
            items.append(self._make_item(
                f"[memory-summary] {summary}", config,
                priority=7, score=0.78,
                metadata={"type": "memory_summary"},
            ))

        return items

    async def _get_knowledge_safe(self, config: GathererConfig) -> list[str]:
        # Hybrid retrieval: lexical overlap (existing) + RapidFuzz + MiniLM semantic (brute-force)
        # Fail-open: any sub-retrieval failure returns empty for that branch, overall still lexical base.
        # Phase 87: instrument true counts/latencies faithfully (no false values; degraded flag on partial failure).
        # Pass 2 gating: deterministic, no LLM, no DB; simple turns skip lexical+semantic cheap.
        import time as _t_metrics
        _total_start = _t_metrics.monotonic()
        _lexical_count = 0
        _semantic_count = 0
        _lexical_ms = 0.0
        _semantic_ms = 0.0
        _embedding_ms = 0.0
        _degraded = False
        parts: list[str] = []
        seen: set[str] = set()
        # Deterministic gate (fail-open: error -> retrieve)
        _gated = True
        try:
            from context_engine.retrieval_gate import should_retrieve_knowledge
            _gated = should_retrieve_knowledge(config.current_message or "", config.conversation_state if isinstance(config.conversation_state, dict) else None)
            if not _gated:
                logger.debug("MemorySource gated skip message_len=%s gated=False (lexical+semantic skipped)", len(config.current_message or ""))
        except Exception:
            _gated = True
        try:
            from commerce.fan_knowledge import retrieve_relevant_knowledge

            # Phase 5: use the actual signature
            # (creator_id, user_id, current_topic, open_threads, limit,
            # profile) — the previous ``query=`` argument never existed and
            # made this branch raise TypeError on every turn (fail-open).
            _cs = config.conversation_state if isinstance(config.conversation_state, dict) else {}
            _topic = _cs.get("current_topic")
            _threads = _cs.get("open_threads", ()) or ()
            if not isinstance(_threads, (list, tuple)):
                _threads = ()
            knowledge = await retrieve_relevant_knowledge(
                creator_id=config.creator_id,
                user_id=config.user_id,
                current_topic=_topic if isinstance(_topic, str) else None,
                open_threads=tuple(t for t in _threads if isinstance(t, str)),
                limit=10,
            )
            for k in knowledge:
                subject = k.get("subject", "")
                value = k.get("value", "")
                status = k.get("status", "")
                confidence = k.get("confidence", 0)
                if subject and value:
                    key = f"{subject}={value}"
                    if key not in seen:
                        seen.add(key)
                        parts.append(f"{key} ({status}, conf {confidence})")
        except Exception:
            _degraded = True
            logger.debug(
                "MemorySource: knowledge query failed (creator=%s user=%s)",
                config.creator_id, config.user_id, exc_info=True,
            )

        # RapidFuzz lexical retrieval (cheap entity relevance) — gated
        _lex_start = _t_metrics.monotonic()
        if not _gated:
            _lexical_ms = 0.0
        else:
            try:
                from commerce.fan_knowledge import get_fan_knowledge
                try:
                    from rapidfuzz import process as _rf_process, fuzz as _rf_fuzz
                    _has_rf = True
                except ImportError:
                    _has_rf = False
                if _has_rf and config.current_message:
                    all_items = await get_fan_knowledge(config.creator_id, config.user_id)
                    if all_items:
                        corpus = []
                        corpus_keys: list[str] = []
                        for it in all_items:
                            subj = it.get("subject", "") if isinstance(it, dict) else getattr(it, "subject", "")
                            val = it.get("value", "") if isinstance(it, dict) else getattr(it, "value", "")
                            if subj and val:
                                txt = f"{subj}={val}"
                                corpus.append(txt)
                                corpus_keys.append(txt)
                        if corpus:
                            # Normalize query/corp for WRatio
                            q = config.current_message.strip().lower()
                            # Use process.extract with WRatio, cutoff 80, limit 5
                            rf_hits = _rf_process.extract(q, corpus, scorer=_rf_fuzz.WRatio, score_cutoff=80, limit=5)
                            _lexical_count = len(rf_hits)
                            for hit_text, score, idx in rf_hits:
                                # hit_text is corpus entry
                                if hit_text not in seen:
                                    # Find original item for status/conf if available
                                    seen.add(hit_text)
                                    # Try to find status/conf
                                    try:
                                        orig = all_items[idx] if idx < len(all_items) else {}
                                        st = orig.get("status", "") if isinstance(orig, dict) else getattr(orig, "status", "")
                                        cf = orig.get("confidence", 0) if isinstance(orig, dict) else getattr(orig, "confidence", 0)
                                        parts.append(f"{hit_text} ({st}, conf {cf}, rf {int(score)})")
                                    except Exception:
                                        parts.append(hit_text)
                _lexical_ms = (_t_metrics.monotonic() - _lex_start) * 1000
            except Exception:
                _degraded = True
                _lexical_ms = (_t_metrics.monotonic() - _lex_start) * 1000
                logger.debug("MemorySource: RapidFuzz lexical retrieval failed", exc_info=True)

        # MiniLM semantic retrieval (brute-force, small corpus) — gated + cold-model fast-fail
        _sem_start = _t_metrics.monotonic()
        _emb_start = _t_metrics.monotonic()
        if not _gated:
            _semantic_ms = 0.0
            _embedding_ms = 0.0
        else:
            # Cold model fast-fail: avoid synchronous 7s SentenceTransformer load on request path
            _cold_skip = False
            try:
                import commerce.embedding_model as _em_cold
                if getattr(_em_cold, "_model_instance", None) is None:
                    # Model not warmed — fail-open fast instead of blocking on load
                    _degraded = True
                    _semantic_ms = 0.0
                    _embedding_ms = 0.0
                    _cold_skip = True
                    logger.debug("MemorySource semantic skipped: model not warmed (cold), fail-open gated=%s", _gated)
            except Exception:
                _cold_skip = False
            if _cold_skip:
                pass
            else:
                try:
                    from commerce.fan_knowledge import get_fan_knowledge
                    from commerce.embedding_model import get_model, encode_message, encode_messages_sync
                    import math

                    query = (config.current_message or "").strip()
                    if query and len(query) >= 3:
                        all_items2 = await get_fan_knowledge(config.creator_id, config.user_id)
                        if all_items2:
                            texts: list[str] = []
                            keys2: list[str] = []
                            for it in all_items2:
                                subj = it.get("subject", "") if isinstance(it, dict) else getattr(it, "subject", "")
                                val = it.get("value", "") if isinstance(it, dict) else getattr(it, "value", "")
                                if subj and val:
                                    t = f"{subj}={val}"
                                    if t not in seen or True:  # allow semantic to add even if not seen, but dedup later
                                        texts.append(t)
                                        keys2.append(t)
                            if texts:
                                # Pass 3L: bound the brute-force corpus (deterministic
                                # first 20; fan_knowledge caps at 30 in practice).
                                if len(texts) > 20:
                                    logger.debug("truncated corpus %s→20 bound creator=%s user=%s", len(texts), config.creator_id, config.user_id)
                                    texts = texts[:20]
                                    keys2 = keys2[:20]
                                # Encode query
                                _emb_q_start = _t_metrics.monotonic()
                                q_vec = await encode_message(query)
                                _embedding_ms = (_t_metrics.monotonic() - _emb_q_start) * 1000
                                if q_vec is not None:
                                    # Pass 3: doc→vec cache + indexed path check
                                    if len(texts) > 500:
                                        logger.warning("corpus >500 consider pgvector/HNSW creator=%s user=%s size=%s (brute-force kept)", config.creator_id, config.user_id, len(texts))
                                    # Split cached vs miss
                                    from commerce.embedding_model import get_cached_doc_vec, set_cached_doc_vec
                                    c_vecs: list[list[float] | None] = [None] * len(texts)
                                    miss_texts: list[str] = []
                                    miss_indices: list[int] = []
                                    _doc_hits = 0
                                    for _idx, _txt in enumerate(texts):
                                        try:
                                            _cached = get_cached_doc_vec(config.creator_id, _txt)  # type: ignore
                                        except Exception:
                                            _cached = None
                                        if _cached is not None:
                                            c_vecs[_idx] = _cached
                                            _doc_hits += 1
                                        else:
                                            miss_texts.append(_txt)
                                            miss_indices.append(_idx)
                                    if miss_texts:
                                        _emb_c_start = _t_metrics.monotonic()
                                        try:
                                            from commerce.embedding_model import encode_messages_sync as _batch_sync
                                            _miss_vecs = _batch_sync(miss_texts)
                                        except Exception:
                                            _miss_vecs = None
                                        _embedding_ms += (_t_metrics.monotonic() - _emb_c_start) * 1000
                                        if _miss_vecs:
                                            for _vec, _orig_idx, _txt in zip(_miss_vecs, miss_indices, miss_texts):
                                                try:
                                                    set_cached_doc_vec(config.creator_id, _txt, _vec)  # type: ignore
                                                except Exception:
                                                    pass
                                                c_vecs[_orig_idx] = _vec
                                        else:
                                            # leave as None, will be filtered
                                            pass
                                        logger.debug("doc cache hits=%s misses=%s creator=%s", _doc_hits, len(miss_texts), config.creator_id)
                                    else:
                                        logger.debug("doc cache all hits=%s creator=%s", _doc_hits, config.creator_id)
                                        # no candidate encode needed, keep _embedding_ms as query only
                                    if c_vecs:
                                        # Cosine similarity (vectors are L2 normalized)
                                        # P81 hardening: collect qualifying semantic candidates, sort deterministically, enforce limit 5
                                        semantic_candidates: list[tuple[str, float]] = []
                                        for txt, c_vec in zip(keys2, c_vecs):
                                            if not c_vec or not q_vec:
                                                continue
                                            dot = sum(a * b for a, b in zip(q_vec, c_vec))
                                            if dot >= 0.30 and txt not in seen:
                                                semantic_candidates.append((txt, dot))
                                        # Deterministic ordering: highest dot first, then lexical tie-break, preserves existing scoring
                                        semantic_candidates.sort(key=lambda x: (-x[1], x[0]))
                                        _semantic_count = len(semantic_candidates[:5]) if semantic_candidates else 0
                                        # Actually record qualifying count before dedup against seen? For observability we want raw qualifying count (limit 5)
                                        # Already capped
                                        for txt, dot in semantic_candidates[:5]:
                                            if txt not in seen:
                                                seen.add(txt)
                                                parts.append(f"{txt} (semantic {dot:.2f})")
                                        _semantic_ms = (_t_metrics.monotonic() - _sem_start) * 1000
                                    else:
                                        _semantic_ms = (_t_metrics.monotonic() - _sem_start) * 1000
                                else:
                                    _semantic_ms = (_t_metrics.monotonic() - _sem_start) * 1000
                            else:
                                _semantic_ms = (_t_metrics.monotonic() - _sem_start) * 1000
                        else:
                            _semantic_ms = (_t_metrics.monotonic() - _sem_start) * 1000
                    else:
                        _semantic_ms = (_t_metrics.monotonic() - _sem_start) * 1000
                except Exception:
                    _degraded = True
                    _semantic_ms = (_t_metrics.monotonic() - _sem_start) * 1000
                    logger.debug("MemorySource: semantic retrieval failed", exc_info=True)
        # Record Phase 87 metrics side-channel (fail-open: never breaks retrieval)
        try:
            _total_ms = (_t_metrics.monotonic() - _total_start) * 1000
            # merged = len after merge before bound? Actually parts[:10] final; record merged candidate count as actual inserted count (≤10)
            _merged = len(parts[:10])
            # semantic_count may be 0 if branch failed; keep lexical_count as measured
            # Store on config side-channel for ContextGatherer aggregation
            if hasattr(config, "_retrieval_metrics") or True:
                try:
                    object.__setattr__(config, "_retrieval_metrics", {
                        "lexical_candidate_count": int(_lexical_count),
                        "semantic_candidate_count": int(_semantic_count),
                        "merged_candidate_count": int(_merged),
                        "lexical_latency_ms": float(_lexical_ms),
                        "semantic_latency_ms": float(_semantic_ms),
                        "embedding_latency_ms": float(_embedding_ms),
                        "total_retrieval_ms": float(_total_ms),
                        "lexical_threshold": 80,
                        "semantic_threshold": 0.30,
                        "lexical_limit": 5,
                        "semantic_limit": 5,
                        "merged_limit": 10,
                        "degraded": bool(_degraded),
                    })
                except Exception:
                    pass
        except Exception:
            pass

        return parts[:10]  # bound after merge

    async def _get_summary_safe(self, config: GathererConfig) -> str | None:
        try:
            from db.postgres import get_latest_summary_durable

            summary, degraded, source = await get_latest_summary_durable(
                config.user_id,
                creator_id=config.creator_id,
            )
            if degraded:
                logger.warning(
                    "MemorySource: summary query degraded for %s source=%s",
                    config.user_id,
                    source,
                )
            return summary
        except Exception:  # noqa: BLE001 — fail-safe: summary query failure must not crash gatherer
            return None


# ---------------------------------------------------------------------------
# TemporalSource — TEMPORAL category, DETERMINISTIC_DERIVATION
# ---------------------------------------------------------------------------


class TemporalSource(DataSource):
    """Temporal context: fan timezone and local time from production data.

    Authority: DETERMINISTIC_DERIVATION (level 2) — deterministic lookup,
    no LLM.
    """

    @property
    def source_name(self) -> str:
        return "temporal"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.TEMPORAL

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.DETERMINISTIC_DERIVATION

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        items: list[ContextItem] = []
        if config.user_id is None or config.creator_id is None:
            return items

        temporal = await self._get_temporal_safe(config)
        if temporal:
            items.append(self._make_item(
                temporal, config, priority=5, score=0.60,
            ))

        return items

    async def _get_temporal_safe(self, config: GathererConfig) -> str | None:
        try:
            from commerce.fan_knowledge import get_fan_knowledge
            from commerce.temporal_context import temporal_context_for_fan

            # Phase 5: ``retrieve_relevant_knowledge`` has no ``query=``
            # parameter (the previous call raised TypeError every turn).
            # Temporal derivation scans the full creator-scoped list, so
            # fetch it directly instead of relevance-filtering.
            knowledge = await get_fan_knowledge(
                config.creator_id,
                config.user_id,
            )
            temp_ctx = temporal_context_for_fan(knowledge)
            tz = temp_ctx.get("timezone", "UNKNOWN")
            local_time = temp_ctx.get("local_time")
            city = temp_ctx.get("city")
            parts: list[str] = []
            if city:
                parts.append(f"Fan city: {city}")
            if tz and tz != "UNKNOWN":
                parts.append(f"Timezone: {tz}")
            if local_time:
                parts.append(f"Local time: {local_time}")
            # Creator-local clock (deterministic, per-turn, zero new I/O):
            # computed from the already-available structured persona
            # location; omitted (never a dummy) when unavailable.
            try:
                from commerce.temporal_context import creator_time_line

                _auth = getattr(config, "authoritative_state", None)
                _structured = getattr(_auth, "structured_persona", None) if _auth is not None else None
                _location = None
                try:
                    if _structured is not None:
                        _location = _structured.get("location")
                except Exception:
                    _location = None
                _creator_line = creator_time_line(_location)
                if _creator_line:
                    parts.append(_creator_line)
            except Exception:
                logger.debug(
                    "TemporalSource: creator time unavailable (creator=%s user=%s)",
                    config.creator_id, config.user_id, exc_info=True,
                )
            return " | ".join(parts) if parts else None
        except Exception:
            logger.debug(
                "TemporalSource: temporal query failed (creator=%s user=%s)",
                config.creator_id, config.user_id, exc_info=True,
            )
            return None


# ---------------------------------------------------------------------------
# EmbeddedKnowledgeSource — EMBEDDED category, DETERMINISTIC_RULE
# ---------------------------------------------------------------------------


class EmbeddedKnowledgeSource(DataSource):
    """Self-knowledge and persona-identity rules embedded in prompt.

    Authority: DETERMINISTIC_RULE (level 1) — curated facts, no LLM.
    """

    @property
    def source_name(self) -> str:
        return "embedded_knowledge"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.EMBEDDED

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.DETERMINISTIC_RULE

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        items: list[ContextItem] = []
        if config.creator_id is None:
            return items

        # Persona self-facts
        self_facts = await self._get_self_facts_safe(config)
        if self_facts:
            items.append(self._make_item(
                self_facts, config, priority=7, score=0.72,
                metadata={"type": "self_facts"},
            ))

        # Capability contract
        capability = await self._get_capability_safe()
        if capability:
            items.append(self._make_item(
                capability, config, priority=8, score=0.78,
                metadata={"type": "capability_contract"},
            ))

        return items

    async def _get_self_facts_safe(self, config: GathererConfig) -> str | None:
        try:
            from core.persona_self import render_persona_self_block
            from memory.creator_persona import get_structured_persona_async

            structured = await get_structured_persona_async(creator_id=config.creator_id)
            persona_name = (structured or {}).get("identity", {}).get("name") or ""
            block = render_persona_self_block(persona_name or None)
            return block if block else None
        except Exception:  # noqa: BLE001 — fail-safe: persona self-facts must not crash gatherer
            return None

    async def _get_capability_safe(self) -> str | None:
        try:
            from core.capability_contract import derive_capability_contract
            contract = derive_capability_contract()
            return contract.render()
        except Exception:  # noqa: BLE001 — fail-safe: capability query must not crash gatherer
            return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class ContextGatherer:
    """Orchestrates data gathering from multiple real production sources.

    Collects context from all registered sources sequentially.
    Each source is failure-isolated: one source failing does not affect others.
    """

    sources: list[DataSource] = field(default_factory=list)
    # Phase 87 side-channel for retrieval metrics (populated from MemorySource via config)
    last_retrieval_metrics: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Register real production sources if none provided."""
        if not self.sources:
            self.sources = [
                PersonaSource(),
                FanStateSource(),
                ConversationHistorySource(),
                CommerceStateSource(),
                MemorySource(),
                TemporalSource(),
                EmbeddedKnowledgeSource(),
            ]

    async def gather_all(
        self,
        config: GathererConfig,
    ) -> list[ContextItem]:
        """Gather context from all registered sources.

        Sequential for simplicity; each source is failure-isolated.
        Pass 2: per-source asyncio.wait_for(2.0) to bound 21.4s semantic and 7s cold load.
        """
        all_items: list[ContextItem] = []
        _had_timeout = False

        for source in self.sources:
            try:
                items = await asyncio.wait_for(source.gather(config), timeout=2.0)
                all_items.extend(items)
            except asyncio.TimeoutError:
                _had_timeout = True
                logger.warning("Gatherer %s timeout 2.0s (fail-open, return [])", source.source_name)
                # Mark degraded for observability (fail-open)
                try:
                    rm = getattr(config, "_retrieval_metrics", None)
                    if isinstance(rm, dict):
                        rm["degraded"] = True
                    else:
                        # Ensure side-channel exists even if MemorySource never wrote it
                        object.__setattr__(config, "_retrieval_metrics", {
                            "lexical_candidate_count": 0,
                            "semantic_candidate_count": 0,
                            "merged_candidate_count": 0,
                            "lexical_latency_ms": 0.0,
                            "semantic_latency_ms": 0.0,
                            "embedding_latency_ms": 0.0,
                            "total_retrieval_ms": 0.0,
                            "lexical_threshold": 80,
                            "semantic_threshold": 0.30,
                            "lexical_limit": 5,
                            "semantic_limit": 5,
                            "merged_limit": 10,
                            "degraded": True,
                        })
                except Exception:
                    pass
                continue
            except Exception as e:
                # Sources must fail safely — never propagate
                logger.warning(
                    "Source %s failed: %s", source.source_name, e, exc_info=True
                )
                continue

        # Phase 87: surface MemorySource retrieval metrics if present
        try:
            rm = getattr(config, "_retrieval_metrics", None)
            if rm:
                self.last_retrieval_metrics = dict(rm)
                if _had_timeout:
                    self.last_retrieval_metrics["degraded"] = True
            elif _had_timeout:
                # No metrics but had timeout -> create degraded marker
                self.last_retrieval_metrics = {
                    "lexical_candidate_count": 0,
                    "semantic_candidate_count": 0,
                    "merged_candidate_count": 0,
                    "lexical_latency_ms": 0.0,
                    "semantic_latency_ms": 0.0,
                    "embedding_latency_ms": 0.0,
                    "total_retrieval_ms": 0.0,
                    "lexical_threshold": 80,
                    "semantic_threshold": 0.30,
                    "lexical_limit": 5,
                    "semantic_limit": 5,
                    "merged_limit": 10,
                    "degraded": True,
                }
        except Exception:
            pass

        return all_items


# ---------------------------------------------------------------------------
# Backward-compatible aliases (Phase 70 names)
# ---------------------------------------------------------------------------

SystemSource = PersonaSource
StateSource = FanStateSource
ConversationSource = ConversationHistorySource
# MemorySource is already named correctly
