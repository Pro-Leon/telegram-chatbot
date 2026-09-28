"""M4 — Creator Scope Isolation tests (D1-D6 + cross-creator integration).

Hermetic: no live PostgreSQL/Redis/Telegram. All DB/Redis boundaries are
faked per test; the real application functions run against the fakes so the
creator predicates, ownership checks, and atomic-write shapes are exercised.

Invariant under test: for creator-dependent state, ``creator A + user X``
must never read, mutate, cache, lock, select, or render state belonging to
``creator B + user X`` unless the state is explicitly global by design.
"""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import json
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTx:
    """Emulates ``async with conn.transaction()`` holding a row lock."""

    def __init__(self, lock: asyncio.Lock, log: list):
        self._lock = lock
        self._log = log

    async def __aenter__(self):
        await self._lock.acquire()
        self._log.append(("BEGIN", ()))
        return self

    async def __aexit__(self, *exc):
        self._log.append(("COMMIT", ()))
        self._lock.release()
        return False


class FakeConn:
    """Minimal asyncpg-connection fake covering exactly the M4 queries."""

    def __init__(self, store: dict, lock: asyncio.Lock, log: list):
        self._s = store
        self._lock = lock
        self._log = log

    def transaction(self):
        return FakeTx(self._lock, self._log)

    # -- helpers ---------------------------------------------------------
    def _personas_for_creator(self, creator_id: int) -> list[dict]:
        rows = [p for p in self._s["personas"] if p.get("creator_id") == creator_id]
        rows.sort(key=lambda p: (not p.get("is_default"), -(p.get("updated_at") or 0)))
        return rows

    # -- asyncpg API ------------------------------------------------------
    async def fetchrow(self, sql: str, *params):
        self._log.append((sql, params))
        s = sql
        # user_profiles point read (optionally FOR UPDATE)
        if "FROM user_profiles WHERE user_id" in s and s.strip().upper().startswith("SELECT"):
            uid = params[0]
            facts = self._s["user_profiles"].get(uid)
            if facts is None:
                return None
            return {"facts": copy.deepcopy(facts)}
        # creator-scoped persona fetch (get_user_persona creator branch,
        # get_default_persona creator branch, get_creator_persona)
        if "FROM personas" in s and "JOIN" not in s and "creator_id = $1" in s:
            rows = self._personas_for_creator(int(params[0]))
            if not rows:
                return None
            return {"instructions": rows[0]["instructions"]}
        # legacy per-user persona join (users.persona_id)
        if "JOIN personas" in s:
            user = self._s["users"].get(params[0])
            if user and user.get("persona_id") is not None:
                for p in self._s["personas"]:
                    if p["id"] == user["persona_id"]:
                        return {"instructions": p["instructions"]}
            return None
        # legitimate global default (creator_id IS NULL)
        if "FROM personas" in s and "creator_id IS NULL" in s:
            for p in self._s["personas"]:
                if p.get("is_default") and p.get("creator_id") is None:
                    return {"instructions": p["instructions"]}
            return None
        # legacy arbitrary default (pre-M4 shape) — must no longer be issued
        if "FROM personas" in s and "is_default = TRUE" in s and "LIMIT 1" in s:
            raise AssertionError(f"arbitrary cross-creator persona fallback issued: {s}")
        # persona ownership probe
        if "SELECT creator_id FROM personas WHERE id = $1" in s:
            for p in self._s["personas"]:
                if p["id"] == params[0]:
                    return {"creator_id": p.get("creator_id")}
            return None
        # dashboard latest-inbound lookup (creator-scoped)
        if "FROM messages m JOIN users u" in s:
            uid, cid = params[0], params[1]
            assert "m.creator_id = $2" in s, f"latest inbound missing creator predicate: {s}"
            cands = [
                m for m in self._s["messages"]
                if m["user_id"] == uid and m["creator_id"] == cid and m["direction"] == "inbound"
            ]
            if not cands:
                return None
            cands.sort(key=lambda m: m["created_at"], reverse=True)
            m = cands[0]
            u = self._s["users"].get(uid, {})
            return {
                "user_id": uid,
                "content": m["content"],
                "telegram_message_id": m.get("telegram_message_id", 0),
                "username": u.get("username"),
                "first_name": u.get("first_name"),
            }
        # follow-up detail (optionally creator-scoped)
        if "FROM scheduled_messages sm" in s and "LEFT JOIN users" in s:
            if "AND sm.creator_id = $2" in s:
                mid, cid = params[0], params[1]
                row = self._s["scheduled"].get(mid)
                if not row or row.get("creator_id") != cid:
                    return None
            else:
                row = self._s["scheduled"].get(params[0])
                if not row:
                    return None
            u = self._s["users"].get(row["user_id"], {})
            out = dict(row)
            out["username"] = u.get("username")
            out["first_name"] = u.get("first_name")
            return out
        # cancel gate read
        if "FROM scheduled_messages WHERE id" in s:
            row = self._s["scheduled"].get(params[0])
            if not row:
                return None
            return {"status": row["status"], "creator_id": row.get("creator_id")}
        return None

    async def fetch(self, sql: str, *params):
        self._log.append((sql, params))
        s = sql
        # dashboard recent-20 (creator-scoped)
        if "FROM messages m" in s and "LEFT JOIN users" in s:
            assert "m.creator_id = $1" in s, f"recent feed missing creator predicate: {s}"
            cid = params[0]
            rows = [m for m in self._s["messages"] if m["creator_id"] == cid]
            rows.sort(key=lambda m: m["created_at"], reverse=True)
            out = []
            for m in rows[:20]:
                u = self._s["users"].get(m["user_id"], {})
                out.append({
                    "id": m["id"], "user_id": m["user_id"], "direction": m["direction"],
                    "content": m["content"], "created_at": m["created_at"],
                    "username": u.get("username"), "first_name": u.get("first_name"),
                })
            return out
        # dialog history (creator-scoped)
        if "FROM messages WHERE user_id" in s:
            assert "creator_id = $2" in s, f"dialog history missing creator predicate: {s}"
            uid, cid = params[0], params[1]
            limit = params[2] if len(params) > 2 else 50
            rows = [m for m in self._s["messages"] if m["user_id"] == uid and m["creator_id"] == cid]
            rows.sort(key=lambda m: m["created_at"], reverse=True)
            return [
                {
                    "id": m["id"], "user_id": m["user_id"], "direction": m["direction"],
                    "content": m["content"], "draft_content": m.get("draft_content"),
                    "was_edited": False, "was_auto_approved": False,
                    "confidence_score": 1.0, "sent_at": None, "created_at": m["created_at"],
                }
                for m in rows[:limit]
            ]
        return []

    async def execute(self, sql: str, *params):
        self._log.append((sql, params))
        s = sql
        if "INSERT INTO user_profiles" in s:
            self._s["user_profiles"][params[0]] = json.loads(params[1]) if isinstance(params[1], str) else copy.deepcopy(params[1])
            return "INSERT 0 1"
        if "UPDATE users SET persona_id" in s:
            self._s["users"][params[1]]["persona_id"] = params[0]
            return "UPDATE 1"
        if "UPDATE scheduled_messages" in s and "SET status = 'cancelled'" in s:
            if "AND creator_id = $2" in s:
                mid, cid = params[0], params[1]
                row = self._s["scheduled"].get(mid)
                if row and row.get("creator_id") == cid and row["status"] == "pending":
                    row["status"] = "cancelled"
                    return "UPDATE 1"
                return "UPDATE 0"
            mid = params[0]
            row = self._s["scheduled"].get(mid)
            if row and row["status"] == "pending":
                row["status"] = "cancelled"
                return "UPDATE 1"
            return "UPDATE 0"
        return "OK"


class FakeAcquire:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


class FakePool:
    """Shares one store + one row lock across all acquisitions."""

    def __init__(self, store: dict):
        self.store = store
        self.lock = asyncio.Lock()
        self.log: list[tuple[str, tuple]] = []
        self._conn = FakeConn(store, self.lock, self.log)

    def acquire(self):
        return FakeAcquire(self._conn)


class FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.kv[key] = value
        return True

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.kv:
                del self.kv[k]
                n += 1
        return n

    async def scan_iter(self, match):
        for k in list(self.kv.keys()):
            if fnmatch.fnmatch(k, match):
                yield k


def make_store() -> dict:
    return {"user_profiles": {}, "personas": [], "users": {}, "messages": [], "scheduled": {}}


def seed_user(store: dict, uid: int = 777, **kw) -> None:
    row = {"id": uid, "username": "fan", "first_name": "Alex", "persona_id": None}
    row.update(kw)
    store["users"][uid] = row


def _ready_ctx(creator_id: int | None):
    from unittest.mock import MagicMock

    from commerce.single_creator import SingleCreatorStatus

    ctx = MagicMock()
    if creator_id is None:
        ctx.status = SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
        ctx.creator_id = None
    else:
        ctx.status = SingleCreatorStatus.READY
        ctx.creator_id = creator_id
    return ctx


# ---------------------------------------------------------------------------
# D1 — legacy prompt cannot render foreign creator facts
# ---------------------------------------------------------------------------


A_B_FACTS = {
    "name": "Alex",
    "interests": ["hiking"],
    "fan_knowledge_by_creator": {
        "1": [{"subject": "city", "value": "Berlin", "status": "CURRENT"}],
        "2": [{"subject": "city", "value": "Paris", "status": "CURRENT"}],
    },
    "commercial_preferences_by_creator": {
        "1": {"red lace": {"count": 2}},
        "2": {"blue silk": {"count": 5}},
    },
    "long_term_memory_by_creator": {
        "2": [{"subject": "anniversary", "value": "June", "memory_type": "fact"}],
    },
    "strategy_evidence_by_creator": {"2": {"TEASE": {"attempt_count": 9}}},
    "strategy_generation_seen_by_creator": {"2": ["gen-b"]},
    "strategy_exposures_by_creator": {"2": []},
    "handoff_by_creator": {"2": {"active": True}},
    "funnel_journey_by_creator": {"2": []},
}


class TestD1LegacyPromptIsolation:
    def test_format_profile_skips_all_namespaced_containers(self):
        from memory.context import format_profile

        out = format_profile(dict(A_B_FACTS))
        assert "Alex" in out and "hiking" in out
        for leak in ("Berlin", "Paris", "blue silk", "anniversary", "TEASE", "gen-b", "by_creator"):
            assert leak not in out, f"foreign/namespaced fact leaked: {leak}"

    def test_legacy_system_prompt_isolation(self):
        from memory.context import build_qwen3_system_prompt

        user = {"first_name": "Alex", "funnel_stage": "new"}
        prompt = build_qwen3_system_prompt("You are Ava", user, dict(A_B_FACTS))
        assert "Alex" in prompt
        for leak in ("Paris", "blue silk", "anniversary", "TEASE", "gen-b"):
            assert leak not in prompt, f"creator B fact in legacy prompt: {leak}"

    def test_new_path_state_context_allow_list(self):
        from memory.context import build_qwen3_state_context

        profile = {"age": "30", "location": "Berlin", "occupation": "nurse",
                   "interests": ["hiking"], **{k: v for k, v in A_B_FACTS.items() if k.endswith("_by_creator")}}
        ctx = build_qwen3_state_context(
            user={"first_name": "Alex", "funnel_stage": "new"}, profile=profile
        )
        assert "hiking" in ctx and "Berlin" in ctx
        for leak in ("Paris", "blue silk", "anniversary", "TEASE", "gen-b"):
            assert leak not in ctx, f"creator B fact in new-path state: {leak}"

    @pytest.mark.asyncio
    async def test_build_qwen3_context_end_to_end_isolation(self):
        """Full context for creator 1: A visible, B absent (legacy + new blocks)."""
        import memory.context as ctx_mod

        fk_items = {
            1: [{"subject": "interest", "value": "hiking", "status": "CURRENT"},
                {"subject": "city", "value": "Berlin", "status": "CURRENT"}],
            2: [{"subject": "interest", "value": "opera", "status": "CURRENT"},
                {"subject": "city", "value": "Paris", "status": "CURRENT"}],
        }

        async def _fk(creator_id, user_id, profile=None):
            return list(fk_items.get(int(creator_id), []))

        async def _rel(creator_id, user_id, current_topic=None, open_threads=(), limit=5, profile=None):
            cid = int(creator_id)
            if cid == 1:
                return [{"subject": "city", "value": "Berlin", "memory_type": "fact",
                         "confidence": 1.0, "temporal_type": "PERMANENT", "status": "CURRENT"}]
            return [{"subject": "city", "value": "Paris", "memory_type": "fact",
                      "confidence": 1.0, "temporal_type": "PERMANENT", "status": "CURRENT"}]

        async def _mem(*a, **k):
            return []

        async def _llm_ctx(*a, **k):
            return object()

        with (
            patch.object(ctx_mod, "get_user", new=AsyncMock(return_value={"first_name": "Alex", "funnel_stage": "new"})),
            patch.object(ctx_mod, "get_user_profile", new=AsyncMock(return_value=copy.deepcopy(A_B_FACTS))),
            patch.object(ctx_mod, "get_recent_messages", new=AsyncMock(return_value=[])),
            patch.object(ctx_mod, "get_latest_summary_with_age", new=AsyncMock(return_value=(None, None))),
            patch("commerce.fan_knowledge.get_fan_knowledge", new=AsyncMock(side_effect=_fk)),
            patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new=AsyncMock(side_effect=_rel)),
            patch("commerce.long_term_memory.retrieve_relevant_memories", new=AsyncMock(side_effect=_mem)),
            patch("memory.context_assembler.build_llm_context", new=AsyncMock(side_effect=_llm_ctx)),
            patch("memory.context_assembler.render_context", return_value=""),
            patch("memory.creator_persona.render_compact_persona_block", return_value=""),
            patch("commerce.product_selection.list_valid_products", new=AsyncMock(return_value=[])),
            patch("commerce.temporal_context.temporal_context_for_fan", return_value={"timezone": "UNKNOWN"}),
        ):
            messages = await ctx_mod.build_qwen3_context(
                777, "hey", "You are Ava",
                creator_id=1,
                structured_persona_snapshot={"identity": {"name": "Ava"}},
            )
        blob = "\n".join(m.get("content", "") for m in messages)
        assert "Berlin" in blob and "hiking" in blob  # creator A slice visible
        assert "Paris" not in blob and "opera" not in blob and "blue silk" not in blob


# ---------------------------------------------------------------------------
# D2 — resolver contract
# ---------------------------------------------------------------------------


class TestD2ResolverContract:
    @pytest.mark.asyncio
    async def test_zero_integrations_unavailable(self):
        from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator

        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[])):
            res = await resolve_single_application_creator()
        assert res.status == SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
        assert res.creator_id is None

    @pytest.mark.asyncio
    async def test_db_failure_unavailable(self):
        from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator

        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(side_effect=RuntimeError("down"))):
            res = await resolve_single_application_creator()
        assert res.status == SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE
        assert res.creator_id is None

    @pytest.mark.asyncio
    async def test_single_active_ready_deterministic(self):
        from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator

        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[7])):
            first = await resolve_single_application_creator()
            second = await resolve_single_application_creator()
        assert first.status == SingleCreatorStatus.READY and first.creator_id == 7
        assert second.creator_id == first.creator_id

    @pytest.mark.asyncio
    async def test_two_active_ambiguous_never_picks(self):
        from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator

        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[3, 9])):
            res = await resolve_single_application_creator()
        assert res.status == SingleCreatorStatus.AMBIGUOUS_CREATOR_CONTEXT
        assert res.creator_id is None  # never silently the lowest id

    @pytest.mark.asyncio
    async def test_dead_integrations_excluded(self):
        """Only status='active' rows count: the list fn is the status filter."""
        import inspect

        import db.dropfans as ddb
        from commerce.single_creator import SingleCreatorStatus, resolve_single_application_creator

        src = inspect.getsource(ddb.list_active_dropfans_creator_ids)
        assert "status = 'active'" in src
        # 1 dead + 1 active surfaces as exactly one active -> READY
        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[5])):
            res = await resolve_single_application_creator()
        assert (res.status, res.creator_id) == (SingleCreatorStatus.READY, 5)
        # 2 dead -> empty -> UNAVAILABLE
        with patch("db.dropfans.list_active_dropfans_creator_ids", new=AsyncMock(return_value=[])):
            res = await resolve_single_application_creator()
        assert res.status == SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE

    def test_resolver_does_not_use_unfiltered_lookup(self):
        import inspect

        import commerce.single_creator as sc

        assert "get_any_creator_id_with_dropfans" not in inspect.getsource(sc)


# ---------------------------------------------------------------------------
# D3 — dashboard creator predicates
# ---------------------------------------------------------------------------


def _seed_messages(store: dict) -> None:
    from datetime import datetime, timezone

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store["messages"].extend([
        {"id": 1, "user_id": 777, "creator_id": 1, "direction": "inbound",
         "content": "hello A", "telegram_message_id": 11, "created_at": t0},
        {"id": 2, "user_id": 777, "creator_id": 2, "direction": "inbound",
         "content": "hello B", "telegram_message_id": 12, "created_at": t0},
    ])


class TestD3DashboardPredicates:
    @pytest.mark.asyncio
    async def test_latest_inbound_scoped(self):
        from fastapi import HTTPException

        import chatbotv2.dashboard.routes.messages as routes

        store = make_store()
        seed_user(store)
        pool = FakePool(store)
        pool.store["messages"].append(
            {"id": 1, "user_id": 777, "creator_id": 1, "direction": "inbound",
             "content": "hello A", "telegram_message_id": 11,
             "created_at": __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)}
        )
        with (
            patch.object(routes, "get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "get_cached_user_persona", new=AsyncMock(return_value=None)),
            patch.object(routes, "get_user_persona", new=AsyncMock(return_value="persona")),
            patch("db.redis.cache_user_persona", new=AsyncMock()),
            patch("db.redis.get_redis", new=AsyncMock(return_value=FakeRedis())),
            patch.object(routes, "get_cached_default_persona", new=AsyncMock(return_value="d")),
            patch.object(routes, "cache_default_persona", new=AsyncMock()),
            patch.object(routes, "enqueue_inbound", new=AsyncMock(return_value="1-1")) as mock_enq,
        ):
            resp = await routes.api_dialog_ai_reply(777, auth={})
            assert resp.status_code == 200
            payload = mock_enq.await_args[0][0]
            assert payload["content"] == "hello A" and payload["creator_id"] == "1"
        # creator B session sees no inbound for the same user -> fail closed
        with (
            patch.object(routes, "get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(2))),
        ):
            with pytest.raises(HTTPException) as ei:
                await routes.api_dialog_ai_reply(777, auth={})
            assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_recent_messages_scoped(self):
        import chatbotv2.dashboard.routes.messages as routes
        from fastapi.responses import JSONResponse

        store = make_store()
        seed_user(store)
        _seed_messages(store)
        pool = FakePool(store)
        with (
            patch.object(routes, "get_pool", new=AsyncMock(return_value=pool)),
            patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(1))),
        ):
            resp = await routes.api_recent_messages(auth={})
            assert isinstance(resp, JSONResponse)
            import json as _json

            body = _json.loads(resp.body.decode())
            assert {m["content"] for m in body["messages"]} == {"hello A"}
        # missing creator -> fail closed (empty, never cross-creator)
        with patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(None))):
            resp = await routes.api_recent_messages(auth={})
            import json as _json

            assert _json.loads(resp.body.decode()) == {"messages": []}

    @pytest.mark.asyncio
    async def test_dialog_history_scoped(self):
        from fastapi import HTTPException

        import chatbotv2.dashboard.routes.dialogs as routes

        store = make_store()
        seed_user(store)
        _seed_messages(store)
        pool = FakePool(store)
        for cid, want in ((1, {"hello A"}), (2, {"hello B"})):
            with (
                patch.object(routes, "get_pool", new=AsyncMock(return_value=pool)),
                patch.object(routes, "resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(cid))),
            ):
                resp = await routes.api_dialog_history(777, limit=50, auth={})
                import json as _json

                body = _json.loads(resp.body.decode())
                assert {m["content"] for m in body} == want
        with patch.object(routes, "resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(None))):
            with pytest.raises(HTTPException):
                await routes.api_dialog_history(777, limit=50, auth={})

    @pytest.mark.asyncio
    async def test_followup_detail_and_cancel_scoped(self):
        import chatbotv2.dashboard.routes.followups as routes

        from datetime import datetime, timezone

        store = make_store()
        seed_user(store)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store["scheduled"][10] = {"id": 10, "user_id": 777, "creator_id": 1, "content": "A-nudge",
                                  "status": "pending", "execute_at": now, "created_at": now, "updated_at": now}
        store["scheduled"][11] = {"id": 11, "user_id": 777, "creator_id": 2, "content": "B-nudge",
                                  "status": "pending", "execute_at": now, "created_at": now, "updated_at": now}
        pool = FakePool(store)
        import json as _json

        async def _noop(*a, **k):
            return None

        # detail: own creator visible, foreign creator -> 404
        # NOTE: followups top-imports the resolver, so patch the route binding
        # (patching the source module would leave the real resolver wired and
        # could reach a live dev database).
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch.object(routes, "resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(1))),
            patch.object(routes, "_require_creator", new=AsyncMock(side_effect=_noop)),
        ):
            resp = await routes.api_followups_detail(10, auth={})
            assert resp.status_code == 200
            assert _json.loads(resp.body.decode())["message"]["content"] == "A-nudge"
            resp = await routes.api_followups_detail(11, auth={})
            assert resp.status_code == 404
        # cancel: foreign row can never be mutated
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch.object(routes, "resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(2))),
            patch.object(routes, "_require_creator", new=AsyncMock(side_effect=_noop)),
        ):
            resp = await routes.api_followups_cancel(10, auth={})
            assert resp.status_code == 409
            assert store["scheduled"][10]["status"] == "pending"
            resp = await routes.api_followups_cancel(11, auth={})
            assert resp.status_code == 200
            assert store["scheduled"][11]["status"] == "cancelled"
            assert store["scheduled"][10]["status"] == "pending"


# ---------------------------------------------------------------------------
# D4 — persona assignment ownership + invalidation scope
# ---------------------------------------------------------------------------


class TestD4PersonaAssignmentInvalidation:
    def _persona_store(self) -> dict:
        store = make_store()
        seed_user(store)
        store["personas"] = [
            {"id": 100, "name": "Ava", "instructions": "SUNNY", "is_default": True, "creator_id": 1, "updated_at": 3},
            {"id": 200, "name": "Mia", "instructions": "MIA", "is_default": True, "creator_id": 2, "updated_at": 3},
        ]
        return store

    @pytest.mark.asyncio
    async def test_assign_own_persona_succeeds_foreign_rejected(self):
        from fastapi import HTTPException

        import chatbotv2.dashboard.routes.personas as routes

        store = self._persona_store()
        pool = FakePool(store)
        redis = FakeRedis()
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("db.redis.get_redis", new=AsyncMock(return_value=redis)),
            patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(1))),
        ):
            resp = await routes.api_set_user_persona(777, persona_id=100, auth={})
            import json as _json

            assert _json.loads(resp.body.decode()) == {"ok": True}
            assert store["users"][777]["persona_id"] == 100
            with pytest.raises(HTTPException) as ei:
                await routes.api_set_user_persona(777, persona_id=200, auth={})
            assert ei.value.status_code == 403
            assert store["users"][777]["persona_id"] == 100  # unchanged
        # missing creator fails closed
        with patch("commerce.single_creator.resolve_single_application_creator", new=AsyncMock(return_value=_ready_ctx(None))):
            with pytest.raises(HTTPException) as ei:
                await routes.api_set_user_persona(777, persona_id=100, auth={})
            assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalidation_is_creator_scoped(self):
        from db.redis import invalidate_persona_cache

        redis = FakeRedis()
        redis.kv.update({
            "persona:1:777": "a", "persona:2:777": "b",
            "persona:creator:1": "ca", "persona:creator:2": "cb",
            "persona:creator:1:default": "da", "persona:creator:2:default": "db",
            "persona:default": "global",
        })
        with patch("db.redis.get_redis", new=AsyncMock(return_value=redis)):
            await invalidate_persona_cache(user_id=777, creator_id=1)
        assert "persona:1:777" not in redis.kv
        assert redis.kv["persona:2:777"] == "b"  # creator B untouched
        assert "persona:creator:2" in redis.kv and "persona:creator:2:default" in redis.kv
        with patch("db.redis.get_redis", new=AsyncMock(return_value=redis)):
            await invalidate_persona_cache(creator_id=2)
        assert "persona:2:777" not in redis.kv
        assert "persona:creator:2" not in redis.kv
        assert redis.kv.get("persona:default") == "global"  # global preserved

    @pytest.mark.asyncio
    async def test_creator_less_user_invalidation_keeps_scoped_keys(self):
        """M4 D4B: the legacy user-only call must not wipe creator caches."""
        from db.redis import invalidate_persona_cache

        redis = FakeRedis()
        redis.kv.update({"persona:777": "legacy", "persona:1:777": "a", "persona:2:777": "b"})
        with patch("db.redis.get_redis", new=AsyncMock(return_value=redis)):
            await invalidate_persona_cache(user_id=777)
        assert "persona:777" not in redis.kv
        assert redis.kv["persona:1:777"] == "a"
        assert redis.kv["persona:2:777"] == "b"


# ---------------------------------------------------------------------------
# D5 — persona fallback isolation
# ---------------------------------------------------------------------------


class TestD5PersonaFallback:
    def _store(self, with_global: bool = True) -> dict:
        store = make_store()
        seed_user(store)
        store["personas"] = [
            {"id": 100, "name": "Ava", "instructions": "SUNNY", "is_default": True, "creator_id": 1, "updated_at": 3},
            {"id": 200, "name": "Mia", "instructions": "MIA", "is_default": True, "creator_id": 2, "updated_at": 4},
        ]
        if with_global:
            store["personas"].append(
                {"id": 300, "name": "Base", "instructions": "GLOBAL", "is_default": True, "creator_id": None, "updated_at": 1}
            )
        return store

    @pytest.mark.asyncio
    async def test_creator_default_never_foreign(self):
        from db.postgres import get_default_persona, get_user_persona

        pool = FakePool(self._store())
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            assert await get_default_persona(creator_id=1) == "SUNNY"
            assert await get_default_persona(creator_id=2) == "MIA"
            assert await get_user_persona(777, creator_id=1) == "SUNNY"
            assert await get_user_persona(777, creator_id=2) == "MIA"
            # creator without personas -> legitimate global default, never another creator
            assert await get_default_persona(creator_id=9) == "GLOBAL"

    @pytest.mark.asyncio
    async def test_no_global_means_none_not_foreign(self):
        from db.postgres import get_default_persona, get_user_persona

        pool = FakePool(self._store(with_global=False))
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            assert await get_default_persona(creator_id=9) is None
            # legacy creator-less path: per-user unset + no global -> None (never arbitrary)
            assert await get_user_persona(777, creator_id=None) is None


# ---------------------------------------------------------------------------
# D6 — concurrent whole-facts writes preserve all creator namespaces
# ---------------------------------------------------------------------------


class TestD6ProfileFactsLostUpdate:
    @pytest.mark.asyncio
    async def test_naive_read_modify_write_loses_namespace(self):
        """Documents the D6 race: stale whole-JSONB overwrite drops a namespace."""
        from db.postgres import get_user_profile, update_user_profile

        pool = FakePool(make_store())
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            snap_a = await get_user_profile(777)
            snap_b = await get_user_profile(777)
            snap_a["commercial_preferences_by_creator"] = {"1": {"red": {}}}
            await update_user_profile(777, snap_a)
            snap_b["fan_knowledge_by_creator"] = {"2": [{"subject": "city", "value": "Paris"}]}
            await update_user_profile(777, snap_b)
            final = await get_user_profile(777)
        assert "commercial_preferences_by_creator" not in final  # A lost -> race proven

    @pytest.mark.asyncio
    async def test_concurrent_namespaced_writes_both_survive(self):
        import commerce.long_term_memory as ltm
        from db.postgres import get_user_profile, update_commercial_preferences

        pool = FakePool(make_store())
        item_b = {
            "memory_id": "2:777:city:Paris", "subject": "city", "value": "Paris",
            "memory_type": "fact", "confidence": 1.0, "source": "explicit",
            "first_seen": "2026-01-01", "last_seen": "2026-01-01",
            "observation_count": 1, "status": "OPEN",
        }
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await asyncio.gather(
                update_commercial_preferences(1, 777, {"red lace": {"count": 1}}),
                ltm.add_memory_item(2, 777, item_b),
            )
            final = await get_user_profile(777)
        assert final["commercial_preferences_by_creator"]["1"] == {"red lace": {"count": 1}}
        assert final["long_term_memory_by_creator"]["2"][0]["value"] == "Paris"
        for_update = [sql for sql, _ in pool.log if "FOR UPDATE" in sql]
        assert for_update, "expected SELECT ... FOR UPDATE row locking"

    @pytest.mark.asyncio
    async def test_overlapping_different_keys_preserved(self):
        """A-prefs + B-prefs + flat profile merge: everything survives."""
        from memory.profile import merge_profiles
        from db.postgres import (
            get_user_profile,
            mutate_user_profile_atomically,
            update_commercial_preferences,
        )

        pool = FakePool(make_store())
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await asyncio.gather(
                update_commercial_preferences(1, 777, {"red lace": {"count": 1}}),
                update_commercial_preferences(2, 777, {"blue silk": {"count": 2}}),
            )

            def _merge(facts):
                merged = merge_profiles(dict(facts), {"name": "Alex"}, {"name": "explicit"})
                facts.clear()
                facts.update(merged)
                return True

            assert await mutate_user_profile_atomically(777, _merge) is True
            final = await get_user_profile(777)
        assert final["commercial_preferences_by_creator"]["1"] == {"red lace": {"count": 1}}
        assert final["commercial_preferences_by_creator"]["2"] == {"blue silk": {"count": 2}}
        assert final["name"] == "Alex"

    @pytest.mark.asyncio
    async def test_mutator_touches_only_own_namespace(self):
        from db.postgres import get_user_profile, mutate_user_profile_atomically

        store = make_store()
        store["user_profiles"][777] = {
            "commercial_preferences_by_creator": {"2": {"blue silk": {}}},
            "fan_knowledge_by_creator": {"2": [{"subject": "city", "value": "Paris"}]},
        }
        pool = FakePool(store)
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            def _only_own(facts):
                facts.setdefault("commercial_preferences_by_creator", {})["1"] = {"red": {}}
                return True

            ok = await mutate_user_profile_atomically(777, _only_own)
            assert ok is True
            final = await get_user_profile(777)
        assert final["commercial_preferences_by_creator"]["1"] == {"red": {}}
        assert final["commercial_preferences_by_creator"]["2"] == {"blue silk": {}}
        assert final["fan_knowledge_by_creator"]["2"] == [{"subject": "city", "value": "Paris"}]

    @pytest.mark.asyncio
    async def test_profile_extract_preserves_foreign_namespace(self):
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        store = make_store()
        store["user_profiles"][777] = {
            "fan_knowledge_by_creator": {"2": [{"subject": "city", "value": "Paris"}]}
        }
        pool = FakePool(store)
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("memory.profile.extract_profile_facts",
                  new=AsyncMock(return_value=({"name": "Alex"}, {"name": "explicit"}))),
        ):
            await extract_and_update_profile(777, [{"direction": "inbound", "content": "hi"}])
            final = await get_user_profile(777)
        assert final["name"] == "Alex"
        assert final["fan_knowledge_by_creator"]["2"] == [{"subject": "city", "value": "Paris"}]


class TestD6MigratedWriters:
    """All D6-migrated whole-facts writers persist via the row lock and keep
    foreign creator namespaces intact."""

    @pytest.mark.asyncio
    async def test_strategy_handoff_exposure_journey_writers(self):
        from commerce.strategy_learning import get_strategy_evidence, update_strategy_evidence
        from commerce.conversation_operations import get_handoff, make_handoff, set_handoff, clear_handoff
        from commerce.adaptive_optimization import make_exposure, persist_exposure
        from commerce.revenue_intelligence import (
            FunnelTransition, get_funnel_journey, record_funnel_transition,
        )
        from db.postgres import get_user_profile

        store = make_store()
        store["user_profiles"][777] = {
            "fan_knowledge_by_creator": {"2": [{"subject": "city", "value": "Paris"}]}
        }
        pool = FakePool(store)
        now = "2026-01-01T00:00:00+00:00"
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await update_strategy_evidence(1, 777, "WARM_OPEN", "POSITIVE_ENGAGEMENT")
            ev = await get_strategy_evidence(1, 777)
            assert ev["WARM_OPEN"].attempt_count == 1
            assert await get_strategy_evidence(2, 777) == {}

            assert await set_handoff(1, 777, make_handoff("operator busy")) is True
            assert (await get_handoff(1, 777)).active is True
            assert await get_handoff(2, 777) is None
            assert await clear_handoff(1, 777) is True
            assert await get_handoff(1, 777) is None

            exp = make_exposure(creator_id=1, user_id=777, generation_id="g1",
                                strategy_family="tease")
            assert await persist_exposure(exp) is True

            t = FunnelTransition(from_state="NEW", to_state="ENGAGED", creator_id=1,
                                 user_id=777, timestamp=now, generation_id="genA")
            assert await record_funnel_transition(t) is True
            assert len(await get_funnel_journey(1, 777)) == 1
            assert await get_funnel_journey(2, 777) == []

            final = await get_user_profile(777)
        # foreign namespace survived every migrated write
        assert final["fan_knowledge_by_creator"]["2"] == [{"subject": "city", "value": "Paris"}]
        assert "1" in final["strategy_evidence_by_creator"]
        assert "1" in final["strategy_exposures_by_creator"]
        assert "1" in final["funnel_journey_by_creator"]
        for_update = [sql for sql, _ in pool.log if "FOR UPDATE" in sql]
        assert len(for_update) >= 4


# ---------------------------------------------------------------------------
# Cross-creator integration: same user under two creators stays isolated
# ---------------------------------------------------------------------------


class TestCrossCreatorIntegration:
    @pytest.mark.asyncio
    async def test_same_user_two_creators_no_crossover(self):
        """One scenario across prompt / persona / messages / cache / resolver."""
        import memory.context as ctx_mod
        import chatbotv2.dashboard.routes.dialogs as dialogs
        from db.postgres import get_user_persona
        from db.redis import invalidate_persona_cache

        store = make_store()
        seed_user(store)
        store["personas"] = [
            {"id": 100, "name": "Ava", "instructions": "SUNNY", "is_default": True, "creator_id": 1, "updated_at": 3},
            {"id": 200, "name": "Mia", "instructions": "MIA", "is_default": True, "creator_id": 2, "updated_at": 3},
        ]
        _seed_messages(store)
        store["user_profiles"][777] = copy.deepcopy(A_B_FACTS)
        pool = FakePool(store)
        redis = FakeRedis()
        redis.kv.update({"persona:1:777": "SUNNY", "persona:2:777": "MIA"})

        # prompt for creator 1: B facts absent
        prompt = ctx_mod.build_qwen3_system_prompt(
            "You are Ava", {"first_name": "Alex", "funnel_stage": "new"},
            copy.deepcopy(A_B_FACTS),
        )
        assert "Paris" not in prompt and "MIA" not in prompt

        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("db.redis.get_redis", new=AsyncMock(return_value=redis)),
        ):
            # persona lookups are creator-pure
            assert await get_user_persona(777, creator_id=1) == "SUNNY"
            assert await get_user_persona(777, creator_id=2) == "MIA"
            # creator 1 invalidation keeps creator 2 cache
            await invalidate_persona_cache(user_id=777, creator_id=1)
            assert "persona:1:777" not in redis.kv
            assert redis.kv["persona:2:777"] == "MIA"

        # dialog history per creator
        import json as _json

        for cid, want, ban in ((1, "hello A", "hello B"), (2, "hello B", "hello A")):
            with (
                patch.object(dialogs, "get_pool", new=AsyncMock(return_value=pool)),
                patch.object(dialogs, "resolve_single_application_creator",
                             new=AsyncMock(return_value=_ready_ctx(cid))),
            ):
                resp = await dialogs.api_dialog_history(777, limit=50, auth={})
                body = _json.loads(resp.body.decode())
                texts = [m["content"] for m in body]
                assert texts == [want], f"creator {cid} saw {texts}"
                assert ban not in texts
