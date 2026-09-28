"""M5 — Profile freshness / last-writer-wins tests (T1-T10 + barrier).

Hermetic: no live PostgreSQL/Redis/LLM. The real production functions run
against minimal fakes for ``db.postgres.get_pool`` so the freshness gates,
the atomic-mutation seam, and the SQL shapes are genuinely exercised.

Core invariant: for one (creator_id, user_id), an older computation must not
overwrite newer replacement state merely by committing later. Correlation
(generation/message identity) and freshness (monotonic source order) are
kept distinct: generation_id is never used for ordering.
"""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal fakes (self-contained; real writer code runs against them)
# ---------------------------------------------------------------------------


class FakeTx:
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
    def __init__(self, store: dict, locks: dict, log: list):
        self._s = store
        self._locks = locks
        self._log = log

    def _lock_for(self, uid) -> asyncio.Lock:
        return self._locks.setdefault(uid, asyncio.Lock())

    def transaction(self):
        # Row lock scope is per user row; summaries share the user lock.
        return FakeTx(self._lock_for("__tx__"), self._log)

    async def fetchrow(self, sql: str, *params):
        self._log.append(("fetchrow", sql, params))
        if "FROM user_profiles" in sql:
            facts = self._s["profiles"].get(params[0])
            return {"facts": copy.deepcopy(facts)} if facts is not None else None
        if "FROM conversation_summaries" in sql:
            rows = [r for r in self._s["summaries"] if r["user_id"] == params[0] and r["creator_id"] == params[1]]
            if not rows:
                return None
            latest = rows[-1]
            return {"id": latest["id"], "message_count_at_summary": latest["message_count_at_summary"]}
        return None

    async def execute(self, sql: str, *params):
        self._log.append(("execute", sql, params))
        if "INSERT INTO user_profiles" in sql:
            import json as _json

            self._s["profiles"][params[0]] = _json.loads(params[1]) if isinstance(params[1], str) else copy.deepcopy(params[1])
            return "INSERT 0 1"
        if "UPDATE conversation_summaries" in sql:
            for r in self._s["summaries"]:
                if r["id"] == params[0]:
                    r["summary"] = params[1]
                    r["message_count_at_summary"] = params[2]
                    return "UPDATE 1"
            return "UPDATE 0"
        if "INSERT INTO conversation_summaries" in sql:
            nid = (max([r["id"] for r in self._s["summaries"]] or [0]) or 0) + 1
            self._s["summaries"].append({
                "id": nid, "user_id": params[0], "creator_id": params[1],
                "summary": params[2], "message_count_at_summary": params[3],
            })
            return "INSERT 0 1"
        return "OK"


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self):
        self.store = {"profiles": {}, "summaries": []}
        self.locks: dict = {}
        self.log: list = []
        self._conn = FakeConn(self.store, self.locks, self.log)

    def acquire(self):
        return FakeAcquire(self._conn)


def _extract_facts(facts: dict, confidence: dict | None = None):
    return AsyncMock(return_value=(facts, confidence or {}))


# ---------------------------------------------------------------------------
# T5 core + T1 slow-old/fast-new + T2 sequential + T3 retry (flat profile)
# ---------------------------------------------------------------------------


class TestFlatProfileFreshness:
    @pytest.mark.asyncio
    async def test_t5_same_scalar_late_commit_loses(self):
        """m1 occupation=student commits after m2 occupation=engineer: engineer wins."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        pool = FakePool()
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "engineer"})),
        ):
            await extract_and_update_profile(777, [{"direction": "inbound", "content": "b"}], source_order=7, creator_id=1)
        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "student"})),
        ):
            await extract_and_update_profile(777, [{"direction": "inbound", "content": "a"}], source_order=5, creator_id=1)
            final = await get_user_profile(777)
        assert final["occupation"] == "engineer"
        assert final["_profile_source_by_creator"] == {"1": 7}

    @pytest.mark.asyncio
    async def test_t1_t2_old_new_converge_regardless_of_commit_order(self):
        """Slow-old/fast-new AND sequential: final replacement state is identical."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        async def _run(order: str) -> dict:
            pool = FakePool()
            calls = [
                ({"occupation": "student", "name": "Al"}, 5),
                ({"occupation": "engineer", "name": "Alicia"}, 7),
            ]
            if order == "new_first":
                calls.reverse()
            with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
                for facts, src in calls:
                    with patch("memory.profile.extract_profile_facts", _extract_facts(facts)):
                        await extract_and_update_profile(
                            777, [{"direction": "inbound", "content": "x"}],
                            source_order=src, creator_id=1,
                        )
                return await get_user_profile(777)

        slow_first = await _run("old_first")
        new_first = await _run("new_first")
        assert slow_first["occupation"] == new_first["occupation"] == "engineer"
        assert slow_first["name"] == new_first["name"] == "Alicia"
        assert new_first["_profile_source_by_creator"] == {"1": 7}

    @pytest.mark.asyncio
    async def test_t1_concurrent_overlap_converges(self):
        """True asyncio overlap via gather: either serialization converges.

        Barrier pattern (M5-N): the old extraction is held at a barrier until
        the new extraction completes, forcing old-read/new-commit/new-read...
        interleavings deterministically. The barrier sits in the extraction
        phase (outside any DB lock, exactly like production LLM latency), so
        no deadlock is possible; the freshness decision happens at commit.
        """
        import memory.profile as profile_mod
        from db.postgres import get_user_profile

        pool = FakePool()
        release_old = asyncio.Event()

        async def _extract(text):
            if "OLD-MARKER" in text:
                await release_old.wait()
                return {"occupation": "student"}, {}
            release_old.set()
            return {"occupation": "engineer"}, {}

        async def _old():
            return await profile_mod.extract_and_update_profile(
                777, [{"direction": "inbound", "content": "OLD-MARKER a"}],
                source_order=5, creator_id=1)

        async def _new():
            return await profile_mod.extract_and_update_profile(
                777, [{"direction": "inbound", "content": "b"}],
                source_order=7, creator_id=1)

        with (
            patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)),
            patch.object(profile_mod, "extract_profile_facts", side_effect=_extract),
        ):
            await asyncio.gather(_old(), _new())
            final = await get_user_profile(777)
        assert final["occupation"] == "engineer"
        assert final["_profile_source_by_creator"] == {"1": 7}

    @pytest.mark.asyncio
    async def test_t3_old_retry_cannot_revert(self):
        """m1 original, m2, m1 retry with original source: engineer survives."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "student"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "a"}], source_order=5, creator_id=1)
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "engineer"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "b"}], source_order=7, creator_id=1)
            # retry of the m1 computation, same original source position
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "student"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "a"}], source_order=5, creator_id=1)
            final = await get_user_profile(777)
        assert final["occupation"] == "engineer"
        assert final["_profile_source_by_creator"] == {"1": 7}

    @pytest.mark.asyncio
    async def test_t4_namespaces_and_foreign_state_survive(self):
        """Stale flat-profile write preserves additive data + other namespaces."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile, update_commercial_preferences

        pool = FakePool()
        pool.store["profiles"][777] = {
            "fan_knowledge_by_creator": {"2": [{"subject": "city", "value": "Paris"}]},
        }
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await update_commercial_preferences(1, 777, {"red": {"count": 1}})
            with patch(
                "memory.profile.extract_profile_facts",
                _extract_facts({"interests": ["football"]},
                               {"interests": "explicit"}),
            ):
                await extract_and_update_profile(
                    777, [{"direction": "inbound", "content": "x"}],
                    source_order=3, creator_id=1)
            final = await get_user_profile(777)
        assert final["commercial_preferences_by_creator"] == {"1": {"red": {"count": 1}}}
        assert final["fan_knowledge_by_creator"] == {"2": [{"subject": "city", "value": "Paris"}]}
        assert "football" in final["interests"]

    @pytest.mark.asyncio
    async def test_t6_creator_isolation(self):
        """High-waters are per-creator; A never blocks B and vice versa."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "engineer"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "b"}], source_order=7, creator_id=1)
            # creator B with an older absolute order still applies (own boundary)
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "artist"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "c"}], source_order=3, creator_id=2)
            # creator B stale relative to its own boundary is rejected
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "student"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "a"}], source_order=1, creator_id=2)
            final = await get_user_profile(777)
        # flat scalars are global (pre-existing semantics); last allowed apply wins,
        # but each creator's high-water advanced independently
        assert final["_profile_source_by_creator"] == {"1": 7, "2": 3}
        assert final["occupation"] == "artist"

    @pytest.mark.asyncio
    async def test_t7_retry_retains_original_position(self):
        """Replaying the original old source keeps position 5, never advances hw."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "engineer"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "b"}], source_order=7, creator_id=1)
            # replay of the m1 computation with its ORIGINAL source identity
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "student"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "a"}], source_order=5, creator_id=1)
            final = await get_user_profile(777)
        assert final["occupation"] == "engineer"
        assert final["_profile_source_by_creator"] == {"1": 7}

    @pytest.mark.asyncio
    async def test_t10_additive_from_stale_source_applies(self):
        """Stale source: new list items + new dict keys apply; scalars/dicts kept."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        pool = FakePool()
        pool.store["profiles"][777] = {
            "occupation": "engineer",
            "interests": ["tennis"],
            "important_dates": {"birthday": "March 6"},
            "_profile_source_by_creator": {"1": 7},
        }
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            with patch(
                "memory.profile.extract_profile_facts",
                _extract_facts(
                    {"occupation": "student",
                     "interests": ["football"],
                     "important_dates": {"birthday": "March 5", "anniversary": "June 1"}},
                    {"occupation": "inferred", "interests": "explicit",
                     "important_dates": "explicit"},
                ),
            ):
                await extract_and_update_profile(
                    777, [{"direction": "inbound", "content": "old"}],
                    source_order=5, creator_id=1)
            final = await get_user_profile(777)
        assert final["occupation"] == "engineer"  # replacement refused
        assert final["important_dates"]["birthday"] == "March 6"  # existing kept
        assert final["important_dates"]["anniversary"] == "June 1"  # new key added
        assert "football" in final["interests"] and "tennis" in final["interests"]
        assert final["_profile_source_by_creator"] == {"1": 7}  # hw untouched
        assert final["_confidence"]["interests"] == "explicit"
        assert "occupation" not in final["_confidence"]

    @pytest.mark.asyncio
    async def test_legacy_path_without_source_unchanged(self):
        """No source metadata: legacy full merge, no high-water written."""
        from memory.profile import extract_and_update_profile
        from db.postgres import get_user_profile

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "student"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "a"}])
            final = await get_user_profile(777)
        assert final["occupation"] == "student"
        assert "_profile_source_by_creator" not in final


# ---------------------------------------------------------------------------
# Merge unit semantics
# ---------------------------------------------------------------------------


class TestMergeSemantics:
    def test_additive_only_never_replaces(self):
        from memory.profile import merge_profiles

        existing = {"occupation": "engineer", "interests": ["tennis"],
                    "important_dates": {"birthday": "March 6"}}
        out = merge_profiles(
            existing,
            {"occupation": "student", "interests": ["football"],
             "important_dates": {"birthday": "March 5", "anniversary": "June 1"}},
            {"occupation": "x", "interests": "y", "important_dates": "z"},
            additive_only=True,
        )
        assert out["occupation"] == "engineer"
        assert out["important_dates"] == {"birthday": "March 6", "anniversary": "June 1"}
        assert out["interests"] == ["tennis", "football"]
        assert out["_confidence"] == {"interests": "y", "important_dates": "z"}

    def test_full_merge_replaces(self):
        from memory.profile import merge_profiles

        out = merge_profiles(
            {"occupation": "student", "important_dates": {"birthday": "March 5"}},
            {"occupation": "engineer", "important_dates": {"birthday": "March 6"}},
        )
        assert out["occupation"] == "engineer"
        assert out["important_dates"] == {"birthday": "March 6"}


# ---------------------------------------------------------------------------
# post_process source capture (M5-B)
# ---------------------------------------------------------------------------


class TestPostProcessSourceCapture:
    @pytest.mark.asyncio
    async def test_source_order_and_count_propagated(self):
        from workers.llm_worker import post_process

        rows = [
            {"id": 10 + i, "direction": "inbound", "content": f"m{i}",
             "created_at": None}
            for i in range(15)
        ]
        captured: dict = {}

        async def _fake_extract(user_id, recent, source_order=None, creator_id=None):
            captured["source_order"] = source_order
            captured["creator_id"] = creator_id
            captured["n"] = len(recent)

        async def _fake_summarize(user_id, message_count, creator_id=None, source_count=None):
            captured["source_count"] = source_count
            captured["message_count"] = message_count

        with (
            patch("workers.llm_worker.get_recent_messages", new=AsyncMock(return_value=rows)),
            patch("db.postgres.get_user", new=AsyncMock(return_value={"message_count": 42})),
            patch("workers.llm_worker.extract_and_update_profile", side_effect=_fake_extract),
            patch("workers.llm_worker.maybe_summarize", side_effect=_fake_summarize),
        ):
            await post_process(777, creator_id=1)
        # extraction slice is the newest 10 of 15 -> max id 24
        assert captured["source_order"] == 24
        assert captured["creator_id"] == 1
        assert captured["n"] == 15
        assert captured["source_count"] == 42
        assert captured["message_count"] == 42

    @pytest.mark.asyncio
    async def test_rows_without_ids_yield_legacy_none(self):
        from workers.llm_worker import post_process

        captured: dict = {}

        async def _fake_extract(user_id, recent, source_order=None, creator_id=None):
            captured["source_order"] = source_order

        async def _fake_summarize(user_id, message_count, creator_id=None, source_count=None):
            captured["source_count"] = source_count

        with (
            patch("workers.llm_worker.get_recent_messages",
                  new=AsyncMock(return_value=[{"direction": "inbound", "content": "Hi"}])),
            patch("db.postgres.get_user", new=AsyncMock(return_value={"message_count": 2})),
            patch("workers.llm_worker.extract_and_update_profile", side_effect=_fake_extract),
            patch("workers.llm_worker.maybe_summarize", side_effect=_fake_summarize),
        ):
            await post_process(777, creator_id=1)
        assert captured["source_order"] is None
        assert captured["source_count"] == 2


# ---------------------------------------------------------------------------
# T8/T9 summary LWW
# ---------------------------------------------------------------------------


class TestSummaryFreshness:
    @pytest.mark.asyncio
    async def test_t8_old_summary_cannot_overwrite_newer(self):
        from db.postgres import save_summary

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await save_summary(777, "new era summary", 40, creator_id=1, source_count=40)
            await save_summary(777, "stale old summary", 20, creator_id=1, source_count=20)
            rows = [r for r in pool.store["summaries"] if r["creator_id"] == 1]
        assert len(rows) == 1
        assert rows[0]["summary"] == "new era summary"
        assert rows[0]["message_count_at_summary"] == 40

    @pytest.mark.asyncio
    async def test_t8_tie_and_legacy_apply(self):
        from db.postgres import save_summary

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await save_summary(777, "s40", 40, creator_id=1, source_count=40)
            await save_summary(777, "s40b", 40, creator_id=1, source_count=40)
            rows = [r for r in pool.store["summaries"] if r["creator_id"] == 1]
            assert rows[0]["summary"] == "s40b"  # same-source rewrite allowed
            await save_summary(777, "legacy", 99, creator_id=1)
            rows = [r for r in pool.store["summaries"] if r["creator_id"] == 1]
            assert rows[0]["summary"] == "legacy"  # no source: legacy path

    @pytest.mark.asyncio
    async def test_t8_null_stored_count_allows_first_write(self):
        from db.postgres import save_summary

        pool = FakePool()
        pool.store["summaries"].append({
            "id": 1, "user_id": 777, "creator_id": 1,
            "summary": "legacy row", "message_count_at_summary": None,
        })
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await save_summary(777, "fresh", 20, creator_id=1, source_count=20)
            rows = [r for r in pool.store["summaries"] if r["creator_id"] == 1]
        assert len(rows) == 1 and rows[0]["summary"] == "fresh"

    @pytest.mark.asyncio
    async def test_t8_creators_independent(self):
        from db.postgres import save_summary

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await save_summary(777, "A40", 40, creator_id=1, source_count=40)
            await save_summary(777, "B20", 20, creator_id=2, source_count=20)
            a = [r for r in pool.store["summaries"] if r["creator_id"] == 1]
            b = [r for r in pool.store["summaries"] if r["creator_id"] == 2]
        assert a[0]["summary"] == "A40" and b[0]["summary"] == "B20"

    @pytest.mark.asyncio
    async def test_t9_summary_decision_atomic(self):
        """SELECT ... FOR UPDATE and the conditional UPDATE share one transaction."""
        from db.postgres import save_summary

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            await save_summary(777, "s40", 40, creator_id=1, source_count=40)
            await save_summary(777, "stale", 20, creator_id=1, source_count=20)
        kinds = [entry[0] for entry in pool.log]
        assert "BEGIN" in kinds and "COMMIT" in kinds
        for_update = [e for e in pool.log if e[0] == "fetchrow" and "FOR UPDATE" in e[1]]
        assert len(for_update) == 2, "both attempts read under row lock"
        # first save INSERTed; the stale attempt performed no write at all
        inserts = [e for e in pool.log if e[0] == "execute" and "INSERT INTO conversation_summaries" in e[1]]
        updates = [e for e in pool.log if e[0] == "execute" and "UPDATE conversation_summaries" in e[1]]
        assert len(inserts) == 1 and len(updates) == 0
        rows = [r for r in pool.store["summaries"] if r["creator_id"] == 1]
        assert rows[0]["summary"] == "s40"

    @pytest.mark.asyncio
    async def test_t9_profile_decision_atomic(self):
        """Freshness compare + mutation + high-water write occur in one transaction."""
        from memory.profile import extract_and_update_profile

        pool = FakePool()
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "engineer"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "b"}], source_order=7, creator_id=1)
            with patch("memory.profile.extract_profile_facts", _extract_facts({"occupation": "student"})):
                await extract_and_update_profile(777, [{"direction": "inbound", "content": "a"}], source_order=5, creator_id=1)
        begins = sum(1 for e in pool.log if e[0] == "BEGIN")
        assert begins == 2
        for_update = [e for e in pool.log if e[0] == "fetchrow" and "FOR UPDATE" in e[1]]
        assert len(for_update) == 2
        # both writers persisted (stale one additively), replacement intact
        from db.postgres import get_user_profile
        with patch("db.postgres.get_pool", new=AsyncMock(return_value=pool)):
            final = await get_user_profile(777)
        assert final["occupation"] == "engineer"
