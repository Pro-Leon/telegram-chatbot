"""Conversational commerce state bridge (Phase 6/7)."""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger("commerce.conversational")


async def build_conversational_commerce_state(
    *,
    user_id: int,
    creator_id: int | None,
    context: list[dict[str, Any]],
    conversation_state: Any | None = None,
    selection: Any | None = None,
    signals: Any | None = None,
    current_topic: str | None = None,
    open_threads: tuple[str, ...] = (),
    profile: dict | None = None,
    generation_id: str | None = None,
) -> dict[str, Any] | None:
    try:
        from commerce.desire import derive_desire_stage
        from commerce.objective import derive_commercial_objective
        from commerce.offer_readiness import evaluate_offer_readiness
        from commerce.sales_window import derive_sales_window
        from commerce.temperature import derive_commercial_temperature
        from commerce.dao import get_timing_context, get_behavioral_feedback_context
        from commerce.relationship import derive_relationship_state
        from db.postgres import get_user as _get_user

        _signals = signals
        if _signals is None:
            try:
                from commerce.deepseek import extract_commerce_signals

                _signals = await extract_commerce_signals(context)
            except Exception:
                _signals = None
        _timing = {
            "hours_since_last_offer": None,
            "hours_since_last_purchase": None,
            "recent_offer_count": 0,
            "recent_sales_attempt_count": 0,
            "recent_purchase_count": 0,
        }
        _behavioral = {
            "consecutive_rejections": 0,
            "total_purchases": 0,
            "aftercare_status": "none",
            "hours_since_last_tip": None,
            "tip_suggestions_sent": 0,
            "tip_suggestions_ignored": 0,
        }
        _has_active_offer = False
        _has_purchased = False
        _is_on_cooldown = False
        _aftercare_status = "none"
        if creator_id is not None:
            try:
                _timing = await get_timing_context(creator_id, user_id)
            except Exception:
                pass
            try:
                _behavioral = await get_behavioral_feedback_context(creator_id, user_id)
                _aftercare_status = _behavioral.get("aftercare_status", "none")
            except Exception:
                pass
            try:
                from db.postgres import get_pool

                _pool = await get_pool()
                async with _pool.acquire() as _conn:
                    _row = await _conn.fetchrow(
                        "SELECT 1 FROM commerce_offers WHERE creator_id=$1 AND user_id=$2 AND state IN ('pending','clicked') LIMIT 1",
                        creator_id,
                        user_id,
                    )
                    _has_active_offer = _row is not None
                    _row2 = await _conn.fetchrow(
                        "SELECT 1 FROM commerce_offers WHERE creator_id=$1 AND user_id=$2 AND state='purchased' LIMIT 1",
                        creator_id,
                        user_id,
                    )
                    _has_purchased = _row2 is not None
            except Exception:
                pass
            try:
                _hours_offer = _timing.get("hours_since_last_offer")
                _hours_purchase = _timing.get("hours_since_last_purchase")
                _consecutive = _behavioral.get("consecutive_rejections", 0)
                if (
                    (_hours_purchase is not None and _hours_purchase < 6)
                    or (_hours_offer is not None and _hours_offer < 24)
                    or _consecutive >= 3
                ):
                    _is_on_cooldown = True
                if _behavioral.get("consecutive_rejections", 0) >= 3:
                    _is_on_cooldown = True
            except Exception:
                pass
        _relationship_state = "cold"
        _relationship_score: float | None = None
        try:
            _user_row = await _get_user(user_id)
            _funnel = _user_row.get("funnel_stage") if _user_row else "new"
            _purchase_count = _behavioral.get("total_purchases", 0)
            _last_purchase_days = None
            if _timing.get("hours_since_last_purchase") is not None:
                _last_purchase_days = _timing["hours_since_last_purchase"] / 24.0
            _last_msg_days = None
            if _timing.get("hours_since_last_offer") is not None:
                _last_msg_days = _timing["hours_since_last_offer"] / 24.0
            _rel_obj = derive_relationship_state(
                funnel_stage=_funnel,
                purchase_count=_purchase_count,
                last_purchase_days_ago=_last_purchase_days,
                last_message_days_ago=_last_msg_days,
                message_count=_user_row.get("message_count", 0) if _user_row else 0,
                has_active_offer=_has_active_offer,
            )
            _relationship_state = _rel_obj.value
            _rel_score_map = {
                "cold": 0.2,
                "new": 0.3,
                "engaged": 0.5,
                "warm": 0.65,
                "buying_signal": 0.75,
                "purchased": 0.6,
                "repeat_buyer": 0.7,
                "vip": 0.8,
                "cooling_down": 0.3,
                "do_not_push": 0.1,
                "operator_required": 0.1,
            }
            _relationship_score = _rel_score_map.get(_relationship_state, 0.35)
        except Exception:
            _relationship_state = "warm"
            _relationship_score = 0.5
        _primary_intent = None
        _intent_tags: list[str] = []
        _purchase_intent = 0.0
        _price_interest = 0.0
        _explicit_content = False
        _explicit_purchase = False
        _asks_free = False
        _fan_asks_q = False
        _content_interest = 0.0
        if _signals is not None:
            try:
                _primary_intent = _signals.primary_intent
                _intent_tags = list(_signals.intent_tags or [])
                _purchase_intent = float(_signals.purchase_intent or 0.0)
                _price_interest = float(_signals.price_interest or 0.0)
                _explicit_content = bool(_signals.explicit_content_request)
                _explicit_purchase = bool(_signals.explicit_purchase_request)
                _asks_free = bool(_signals.asks_for_free_content)
                _fan_asks_q = bool(_signals.fan_asks_question)
                _content_interest = float(_signals.content_interest or 0.0)
            except Exception:
                pass
        if current_topic is None and conversation_state is not None:
            current_topic = getattr(conversation_state, "current_topic", None)
        if not open_threads and conversation_state is not None:
            open_threads = tuple(getattr(conversation_state, "open_threads", ()) or ())
        desire = derive_desire_stage(
            relationship_state=_relationship_state,
            primary_intent=_primary_intent,
            intent_tags=_intent_tags,
            purchase_intent=_purchase_intent,
            price_interest=_price_interest,
            explicit_content_request=_explicit_content,
            explicit_purchase_request=_explicit_purchase,
            asked_for_free_content=_asks_free,
            has_active_offer=_has_active_offer,
            aftercare_status=_aftercare_status,
            has_purchased=_has_purchased,
            hours_since_last_offer=_timing.get("hours_since_last_offer"),
            hours_since_last_purchase=_timing.get("hours_since_last_purchase"),
            consecutive_rejections=_behavioral.get("consecutive_rejections", 0),
            fan_asks_question=_fan_asks_q,
            commercial_paused=_behavioral.get("consecutive_rejections", 0) >= 3,
            current_topic=current_topic,
            open_threads=open_threads,
        )
        # Phase 10 Fix #2: Wire desire decay (topic change + time)
        try:
            from commerce.desire import decay_desire, DesireStage, DesireState

            _topic_changed = False
            if conversation_state is not None:
                _recent = tuple(getattr(conversation_state, "recent_topics", ()) or ())
                if _recent and current_topic and current_topic not in _recent:
                    _topic_changed = True
                elif _recent and current_topic and _recent[0] != current_topic:
                    _topic_changed = True
            _hours_for_decay = _timing.get("hours_since_last_offer") or 0
            _decayed_conf = decay_desire(desire.confidence, float(_hours_for_decay), _topic_changed)
            if _decayed_conf < 0.40 and desire.stage.value in (
                "offer_ready",
                "qualification",
                "desire",
            ):
                _order = [
                    "relationship",
                    "curiosity",
                    "interest",
                    "desire",
                    "qualification",
                    "offer_ready",
                    "purchase",
                    "aftercare",
                    "repeat",
                ]
                try:
                    idx = _order.index(desire.stage.value)
                    if idx > 0:
                        new_stage = DesireStage(_order[idx - 1])
                        desire = DesireState(
                            stage=new_stage,
                            confidence=_decayed_conf,
                            evidence=desire.evidence,
                            last_transition_at=desire.last_transition_at,
                        )
                except Exception:
                    pass
        except Exception:
            pass
        # Phase 10 Fix #3: Aftercare completion (pending -> completed after meaningful interaction)
        try:
            if _aftercare_status in ("pending", "sent") and creator_id is not None:
                # If fan has sent a message after purchase (we are in inbound context), consider aftercare completed
                # Check if last inbound is recent and non-empty (length>5) and hours_since_purchase >1
                _hours_purchase = _timing.get("hours_since_last_purchase")
                if _hours_purchase is not None and _hours_purchase > 1:
                    from commerce.dao import mark_aftercare_completed

                    # Best-effort, no await outside? We are async, so await
                    try:
                        await mark_aftercare_completed(creator_id, user_id)
                        _aftercare_status = "completed"
                    except Exception:
                        pass
        except Exception:
            pass
        temp = derive_commercial_temperature(
            relationship_score=_relationship_score,
            desire_stage=desire.stage.value,
            purchase_intent=_purchase_intent,
            content_interest=_content_interest,
            recent_offer_count=_timing.get("recent_offer_count", 0),
            recent_sales_attempts=_timing.get("recent_sales_attempt_count", 0),
            consecutive_rejections=_behavioral.get("consecutive_rejections", 0),
            hours_since_last_offer=_timing.get("hours_since_last_offer"),
            hours_since_last_purchase=_timing.get("hours_since_last_purchase"),
            aftercare_status=_aftercare_status,
            commercial_paused=_behavioral.get("consecutive_rejections", 0) >= 3,
        )
        _has_relevant_product = True
        _not_purchased = not _has_purchased
        try:
            from commerce.product_selection import list_valid_products
            from commerce.dao import get_recent_offered_product_ids, get_recent_offered_groups
            from commerce.content_matching import rank_products_by_relevance
            from db.postgres import get_commercial_preferences

            if creator_id is not None:
                _valid = await list_valid_products(creator_id)
                if _valid:
                    # Use creator-scoped preferences for relevance check
                    try:
                        _cre_prefs = await get_commercial_preferences(
                            creator_id, user_id, profile=profile
                        )
                        # Convert commercial preferences dict to list of keys for ranking
                        _pref_list = list(_cre_prefs.keys())[:5] if _cre_prefs else []
                    except Exception:
                        _pref_list = []
                    # Check recent offered for per-product recency only.
                    # P3.3.4 QUARANTINE: ``_recent_groups`` (title-inferred
                    # display groups) is fetched for signature compatibility
                    # but has NO commercial authority — ranking ignores it.
                    # Only actual per-product ``_recent_ids`` may penalize.
                    try:
                        _recent_ids = await get_recent_offered_product_ids(
                            creator_id, user_id, hours=24
                        )
                        _recent_groups = await get_recent_offered_groups(
                            creator_id, user_id, hours=24
                        )
                    except Exception:
                        _recent_ids, _recent_groups = set(), set()
                    _purchased_ids = set()
                    try:
                        from commerce.product_selection import _get_purchased_product_ids

                        _purchased_ids = await _get_purchased_product_ids(creator_id, user_id)
                    except Exception:
                        pass
                    ranked = rank_products_by_relevance(
                        _valid,
                        current_topic,
                        open_threads,
                        _pref_list,
                        _purchased_ids,
                        _recent_ids,
                        _recent_groups,
                        creator_id=creator_id,
                    )
                    _has_relevant_product = (
                        len([r for r, _ in ranked if r[1] >= 0.15]) > 0 if ranked else False
                    )
                    if not _has_relevant_product:
                        # Fallback: check if any valid unpurchased exists (even low relevance) for has_relevant check
                        _has_relevant_product = len(_valid) > len(_purchased_ids)
                else:
                    _has_relevant_product = False
            else:
                _has_relevant_product = False
        except Exception:
            pass
        _is_aftercare = _aftercare_status in ("pending", "sent")
        # Phase 101: warming → readiness → window (acyclic: readiness gates window)
        warming = None
        phase101_readiness = None
        try:
            from commerce.warming import derive_warming_state
            from commerce.readiness import evaluate_readiness as evaluate_phase101_readiness

            warming = derive_warming_state(
                creator_id=creator_id,
                relationship_state=_relationship_state,
                desire_stage=desire.stage.value,
                purchase_intent=_purchase_intent,
                recent_offer_count=_timing.get("recent_offer_count", 0),
                consecutive_rejections=_behavioral.get("consecutive_rejections", 0),
                hours_since_last_offer=_timing.get("hours_since_last_offer"),
                hours_since_last_purchase=_timing.get("hours_since_last_purchase"),
                aftercare_status=_aftercare_status,
                commercial_paused=_behavioral.get("consecutive_rejections", 0) >= 3,
                has_active_offer=_has_active_offer,
            )
            phase101_readiness = evaluate_phase101_readiness(
                warming=warming,
                desire_stage=desire.stage.value,
                temperature=temp.level,
                purchase_intent=_purchase_intent,
                has_active_offer=_has_active_offer,
                aftercare_active=_is_aftercare,
                is_on_cooldown=_is_on_cooldown,
                has_relevant_product=_has_relevant_product,
                not_purchased=_not_purchased,
                creator_id=creator_id,
                desire_evidence=desire.evidence,
            )
        except Exception:
            pass
        # Existing offer readiness (kept for backward compat, but window now gated by Phase 101 readiness when available)
        readiness = evaluate_offer_readiness(
            desire.stage.value,
            temp.level,
            purchase_intent=_purchase_intent,
            has_active_offer=_has_active_offer,
            is_on_cooldown=_is_on_cooldown,
            aftercare_active=_is_aftercare,
            has_relevant_product=_has_relevant_product,
            not_purchased=_not_purchased,
            desire_evidence=desire.evidence,
        )
        # Window consumes Phase 101 readiness (readiness gates window, not vice versa)
        # Fail-closed: if Phase 101 unavailable (exception → None), window must not open
        _window_offer_readiness = (
            phase101_readiness.offer_readiness
            if phase101_readiness and phase101_readiness.offer_readiness
            else OfferReadiness.NOT_READY.value
        )
        window = derive_sales_window(
            desire.stage.value,
            temp.level,
            _window_offer_readiness,
            aftercare_active=_is_aftercare,
            is_on_cooldown=_is_on_cooldown,
        )
        objective = derive_commercial_objective(selection, relationship_state=_relationship_state)
        # Phase 16: Conversation Intelligence Orchestrator
        try:
            from commerce.conversation_intelligence import derive_conversation_objective

            # Check for open loops via long-term memory
            _has_open_loop = False
            _open_loop_importance = 0.0
            try:
                from commerce.long_term_memory import retrieve_relevant_memories

                _loops = await retrieve_relevant_memories(
                    creator_id,
                    user_id,
                    current_topic=current_topic,
                    open_threads=open_threads,
                    limit=5,
                )
                for m in _loops or []:
                    if m.get("memory_type") == "open_loop" and not m.get("is_expired", False):
                        _has_open_loop = True
                        _open_loop_importance = max(_open_loop_importance, m.get("importance", 0.5))
            except Exception:
                pass
            _has_objection = _behavioral.get("consecutive_rejections", 0) > 0 or is_on_cooldown
            _next_action, _candidates = derive_conversation_objective(
                desire=desire.stage.value,
                temperature=temp.level,
                sales_window=window,
                offer_readiness=readiness.value,
                has_active_offer=_has_active_offer,
                aftercare_status=_aftercare_status,
                is_on_cooldown=_is_on_cooldown,
                has_relevant_product=_has_relevant_product,
                explicit_purchase_request=_explicit_purchase,
                explicit_content_request=_explicit_content,
                has_open_loop=_has_open_loop,
                open_loop_importance=_open_loop_importance,
                has_objection=_has_objection,
                is_blocked=False,
                desire_evidence=desire.evidence,
            )
        except Exception:
            _next_action = None
            _candidates = []
        # G6 evidence-ledger emission only (log; fail-open; no behavior change).
        # All values already fetched above; no new I/O, no new state, no DB write.
        # generation_id is caller-supplied (optional, None for legacy callers).
        try:
            logger.info(
                "G6 commercial history user=%s creator=%s generation_id=%s "
                "total_purchases=%s consecutive_rejections=%s aftercare_status=%s "
                "hours_since_offer=%s hours_since_purchase=%s recent_offers=%s "
                "recent_attempts=%s recent_purchases=%s has_active_offer=%s "
                "relationship_state=%s relationship_score=%s",
                user_id,
                creator_id,
                generation_id,
                _behavioral.get("total_purchases", 0),
                _behavioral.get("consecutive_rejections", 0),
                _aftercare_status,
                _timing.get("hours_since_last_offer"),
                _timing.get("hours_since_last_purchase"),
                _timing.get("recent_offer_count", 0),
                _timing.get("recent_sales_attempt_count", 0),
                _timing.get("recent_purchase_count", 0),
                _has_active_offer,
                _relationship_state,
                _relationship_score,
            )
        except Exception:
            pass
        return {
            "desire": desire,
            "temp": temp,
            "readiness": readiness,
            "window": window,
            "objective": objective,
            "next_best_action": _next_action,
            "candidates": _candidates,
            "relationship_state": _relationship_state,
            "signals": _signals,
            "warming": warming,
            "phase101_readiness": phase101_readiness,
        }
    except Exception as e:
        logger.debug("build_conversational_commerce_state failed", exc_info=True)
        try:
            from commerce.objective import derive_commercial_objective

            objective = derive_commercial_objective(selection)
            return {"objective": objective}
        except Exception:
            return None
