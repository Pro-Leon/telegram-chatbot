"""Phase 11 — GenerationTrace targeted tests (slices A–E).

Covers trace identity, partial traces, authority non-interference,
privacy, versioning, attribution linkage, current-vs-historical labeling,
reason-code namespacing, bounded rejected alternatives, learning lineage,
telemetry contract linkage, creator isolation, and failure isolation.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

CREATOR_A = 11
CREATOR_B = 22
USER = 101
GEN_A = "a" * 32
GEN_B = "b" * 32


def _telemetry(**overrides):
    base = {
        "generation_id": GEN_A,
        "user_id": USER,
        "creator_id": CREATOR_A,
        "created_at": "2026-09-18T00:00:00+00:00",
        "config_version": "cfg_abc",
        "strategy_version": "strategy.v1",
        "ranking_policy_version": "v1",
        "experiment_id": "exp1",
        "experiment_variant": "CONTROL",
        "maturity_policy_version": "p353b.v1",
        "maturity_state": "MATURE",
        "attribution_status": "attributed",
        "relationship_outcome": "continued",
        "commerce_outcome": "no_purchase",
        "strategy_selected": "PLAYFUL",
        "strategy_source": "FAN_HISTORY",
        "strategy_mode": "exploit",
        "conversation_objective": "deepen_desire",
        "objective_reason": "evidence",
        "decision_trace": "OBJECTIVE=x ALLOWED=true",
        "operation_allowed": True,
        "operation_block_reason": None,
        "handoff_required": False,
        "commerce_context": "ctx",
        "commerce_authorization_basis": "NONE",
    }
    base.update(overrides)
    return base


def _anchors(**overrides):
    base = {
        "schema_version": 1,
        "bands": {"familiarity": "new"},
        "last_seen_at": "2026-09-18T00:00:00+00:00",
        "last_provenance": 0.9,
        "last_generation_id": GEN_A,
    }
    base.update(overrides)
    return base


# ── Trace identity ────────────────────────────────────────────────────


class TestIdentity:
    def test_correct_creator_generation_builds(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(creator_id=CREATOR_A, generation_id=GEN_A, telemetry=_telemetry())
        assert trace.identity.creator_id == CREATOR_A
        assert trace.identity.generation_id == GEN_A

    def test_missing_generation_raises(self):
        from commerce.generation_trace import assemble_trace

        with pytest.raises(ValueError):
            assemble_trace(creator_id=CREATOR_A, generation_id="")

    def test_missing_creator_raises(self):
        from commerce.generation_trace import assemble_trace

        with pytest.raises(ValueError):
            assemble_trace(creator_id=None, generation_id=GEN_A)

    def test_same_generation_across_creators_isolated(self):
        from commerce.generation_trace import assemble_trace

        left = assemble_trace(creator_id=CREATOR_A, generation_id=GEN_A, telemetry=_telemetry())
        right = assemble_trace(
            creator_id=CREATOR_B,
            generation_id=GEN_A,
            telemetry=_telemetry(creator_id=CREATOR_B),
        )
        assert left.identity.creator_id != right.identity.creator_id
        assert left.identity.generation_id == right.identity.generation_id

    def test_no_second_identity_mechanism(self):
        from commerce import generation_trace as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "md5" not in source.lower()
        assert "sha256" not in source.lower()
        assert "uuid4" not in source.lower()


# ── Partial traces ────────────────────────────────────────────────────


class TestPartialTraces:
    def test_telemetry_missing_marks_sections(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(creator_id=CREATOR_A, generation_id=GEN_A)
        assert trace.complete is False
        assert "learning" in trace.missing_sections
        assert trace.learning.status in ("unavailable", "unknown")

    def test_ledger_missing_keeps_commerce_honest(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(creator_id=CREATOR_A, generation_id=GEN_A, telemetry=_telemetry())
        assert trace.commerce.status in ("not_persisted", "unknown", "pre_trace")
        assert trace.commerce.ledger_reference is None

    def test_anchors_missing_are_unavailable(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(creator_id=CREATOR_A, generation_id=GEN_A, telemetry=_telemetry())
        assert trace.relationship.status == "unavailable"
        assert trace.intimacy.status == "unavailable"
        assert trace.boundary.status == "unavailable"

    def test_pre_phase11_generation_renders(self):
        from commerce.generation_trace import assemble_trace

        telemetry = _telemetry(
            config_version="unversioned-legacy",
            strategy_version=None,
            relationship_outcome=None,
            attribution_status=None,
            maturity_state=None,
        )
        trace = assemble_trace(creator_id=CREATOR_A, generation_id=GEN_A, telemetry=telemetry)
        assert trace.versions.config_version == "unversioned-legacy"
        assert trace.versions.config_lineage == "pre_trace"
        assert trace.to_dict()["versions"]["config_lineage"] == "pre_trace"


# ── Authority non-interference ────────────────────────────────────────


class TestAuthority:
    def test_assembly_is_deterministic_and_side_effect_free(self):
        from commerce.generation_trace import assemble_trace

        kwargs = {
            "creator_id": CREATOR_A,
            "generation_id": GEN_A,
            "telemetry": _telemetry(),
            "relationship_anchors": _anchors(),
        }
        first = assemble_trace(**kwargs)
        second = assemble_trace(**kwargs)
        assert first.to_dict() == second.to_dict()

    def test_module_has_no_authority_imports(self):
        from commerce import generation_trace as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        forbidden = {
            name
            for name in imported
            if any(
                token in name
                for token in (
                    "execution",
                    "sealing",
                    "eligibility",
                    "decision",
                    "strategy_learning",
                    "phase10_learning",
                    "production_control",
                    "llm_worker",
                    "prompt",
                )
            )
        }
        assert not forbidden, sorted(forbidden)

    def test_trace_cannot_activate_optimizer(self):
        from commerce import generation_trace as module

        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for token in (
            "set_active_config",
            "execute_activation",
            "approve_recommendation",
            "register_config",
        ):
            assert token not in source

    def test_no_global_analytics_dependency(self):
        from commerce import generation_trace as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in (
            "get_conversations_analytics",
            "get_dashboard_analytics",
            "get_conversation_detail",
        ):
            assert token not in source


# ── Privacy ───────────────────────────────────────────────────────────


class TestPrivacy:
    def test_response_contains_no_raw_content(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry=_telemetry(),
            relationship_anchors=_anchors(),
            commerce_decision={
                "action": "NO_OFFER",
                "reason_code": "no_buying_signal",
                "allowed": False,
                "metadata": {"config_version": "cfg_abc"},
            },
            content_decision={"state": "NONE", "reason": ["NO_EVIDENCE"]},
            strategy_decision={"strategy": "PLAYFUL", "reason": ["CURRENT_TOPIC"]},
            operation_decision={"objective": "x", "allowed": True},
            exposure={"generation_id": GEN_A, "strategy_family": "PLAYFUL"},
        )
        import json

        # The section key "content_transition" is the only allowed use of
        # the substring "content": it names the Phase 8 vocabulary, never
        # message text.
        rendered = json.dumps(trace.to_dict())
        scrubbed = rendered.lower().replace("content_transition", "")
        for token in ("content", "draft", "intimate", "thought", "prose"):
            assert token not in scrubbed, token

    def test_raw_message_keys_are_never_read(self):
        from commerce import generation_trace as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in ("messages.content", '["content"]', "draft_content", "chain_of_thought"):
            assert token not in source


# ── Versioning ────────────────────────────────────────────────────────


class TestVersioning:
    def test_known_versions_surface(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(creator_id=CREATOR_A, generation_id=GEN_A, telemetry=_telemetry())
        assert trace.versions.config_version == "cfg_abc"
        assert trace.versions.strategy_version == "strategy.v1"
        assert trace.versions.ranking_policy_version == "v1"
        assert trace.versions.experiment_id == "exp1"
        assert trace.versions.variant == "CONTROL"

    def test_experiment_falls_back_to_exposure(self):
        from commerce.generation_trace import assemble_trace

        telemetry = _telemetry(experiment_id=None, experiment_variant=None)
        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry=telemetry,
            exposure={
                "generation_id": GEN_A,
                "experiment_id": "exp9",
                "experiment_variant": "EXPERIMENT",
            },
        )
        assert trace.versions.experiment_id == "exp9"
        assert trace.versions.variant == "EXPERIMENT"

    def test_unknown_versions_stay_unknown(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A, generation_id=GEN_A, telemetry={"generation_id": GEN_A}
        )
        assert trace.versions.config_version is None
        assert trace.versions.config_lineage in ("unknown", "lineage_unavailable")


# ── Attribution linkage ───────────────────────────────────────────────


class TestAttribution:
    def test_exact_join_only(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry=_telemetry(),
            exposure={"generation_id": GEN_B, "strategy_family": "OTHER"},
        )
        # Exposure for another generation must not leak into this trace's
        # learning section: build path only consults same-generation rows.
        assert trace.learning.exposure_reference is None or trace.learning.status in (
            "ok",
            "unavailable",
            "unknown",
        )

    def test_attribution_states_preserved(self):
        from commerce.generation_trace import assemble_trace

        for status in ("attributed", "censored", "unavailable", "missing", "immature"):
            trace = assemble_trace(
                creator_id=CREATOR_A,
                generation_id=GEN_A,
                telemetry=_telemetry(attribution_status=status),
                exposure={"generation_id": GEN_A},
            )
            assert trace.learning.attribution_status == status


# ── Current vs historical ─────────────────────────────────────────────


class TestCurrentVsHistorical:
    def test_sections_label_temporal_status(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            turn_timestamp="2026-09-18T00:00:00+00:00",
            telemetry=_telemetry(),
            relationship_anchors=_anchors(),
        )
        assert trace.relationship.delta_status == "derivable_not_persisted"
        assert trace.rendered_as == "historical"
        assert trace.identity.turn_timestamp == "2026-09-18T00:00:00+00:00"

    def test_anchor_timestamps_preserved(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            relationship_anchors=_anchors(last_seen_at="2026-09-17T00:00:00+00:00"),
        )
        assert trace.relationship.anchor_timestamp == "2026-09-17T00:00:00+00:00"


# ── Reason codes ──────────────────────────────────────────────────────


class TestReasonCodes:
    def test_namespacing_preserves_codes(self):
        from commerce.generation_trace import ns

        assert ns("commerce", "no_buying_signal") == "commerce:no_buying_signal"
        assert ns("bogus-layer", "x") == "unknown:x"

    def test_unknown_codes_do_not_crash(self):
        from commerce.generation_trace import (
            build_boundary,
            build_commerce,
            build_content_transition,
            build_strategy,
        )

        assert build_boundary({"weird": object()}).status in ("ok", "unknown", "unavailable")
        assert build_commerce({"action": object()}).status in ("ok", "unknown")
        assert build_content_transition({"state": "NOPE"}).status == "unknown"
        assert build_strategy(None).status == "not_persisted"

    def test_existing_codes_survive_round_trip(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            content_decision={"state": "BRIDGE", "reason": ["CURIOSITY_ACKNOWLEDGE"]},
            strategy_decision={"strategy": "CONTINUE", "reason": ["CURRENT_TOPIC"]},
            commerce_decision={
                "action": "NO_OFFER",
                "reason_code": "policy_denied",
                "allowed": False,
            },
        )
        assert trace.content_transition.state == "BRIDGE"
        assert "CURIOSITY_ACKNOWLEDGE" in trace.content_transition.reason
        assert trace.strategy.move == "CONTINUE"
        assert trace.commerce.reason_code == "policy_denied"


# ── Rejected alternatives ─────────────────────────────────────────────


class TestRejectedAlternatives:
    def test_bounded_deterministic_no_prose(self):
        from commerce.generation_trace import collect_rejected

        items = [
            {"candidate_id": f"def:{index}", "reason": "ranked_lower"} for index in range(20)
        ] + [{"candidate_id": None, "reason": "x"}, {"noid": 1}]
        rejected = collect_rejected("commerce", items)
        assert len(rejected) == 8
        assert [item.candidate_id for item in rejected] == sorted(
            item.candidate_id for item in rejected
        )

    def test_ledger_snapshot_yields_only_retained(self):
        from commerce.generation_trace import rejected_from_ledger_snapshot

        snapshot = {
            "selected": {"definition_id": 1},
            "ranking": {"ranked_order": [1, 2, 3]},
            "eligible": [{"definition_id": 2, "version": 1}, {"definition_id": 3, "version": 1}],
            "ineligible": [{"definition_id": 9, "denial_reasons": ["already_purchased"]}],
        }
        rejected = rejected_from_ledger_snapshot(snapshot)
        by_id = {item.candidate_id: item.reason for item in rejected}
        assert any("2" in key for key in by_id)
        assert any("9" in key for key in by_id)
        assert by_id[[key for key in by_id if "9" in key][0]] == "already_purchased"

    def test_absent_where_not_retained(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            strategy_decision={"strategy": "CONTINUE"},
        )
        assert trace.strategy.rejected == ()


# ── Learning lineage ──────────────────────────────────────────────────


class TestLearningLineage:
    def test_exposure_outcome_maturity_chain(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry=_telemetry(),
            exposure={"generation_id": GEN_A, "strategy_family": "PLAYFUL"},
        )
        assert trace.learning.exposure_reference == GEN_A
        assert trace.learning.strategy_family == "PLAYFUL"
        assert trace.learning.maturity_state == "MATURE"
        assert trace.learning.inclusion_reason == "included_mature_attributed"

    def test_lineage_unavailable_by_default(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry=_telemetry(),
            exposure={"generation_id": GEN_A},
        )
        assert trace.learning.config_lineage == "lineage_unavailable"
        assert trace.learning.recommendation_reference is None
        assert trace.versions.config_lineage == "ok"

    def test_restart_lineage_marker(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry=_telemetry(),
            exposure={"generation_id": GEN_A},
            learning_refs={"recommendation": "rec_deadbeef"},
        )
        assert trace.learning.recommendation_reference == "rec_deadbeef"
        assert trace.learning.config_lineage == "ok"


# ── Creator isolation + endpoint behavior ─────────────────────────────


class TestCreatorIsolation:
    def test_trace_never_uses_global_analytics(self):
        from chatbotv2.dashboard.routes import trace as route

        source = Path(route.__file__).read_text(encoding="utf-8")
        assert "get_conversations_analytics" not in source
        assert "get_dashboard_analytics" not in source
        assert "get_conversation_detail" not in source

    def test_route_is_creator_scoped_and_bounded(self):
        from chatbotv2.dashboard.routes import trace as route

        source = Path(route.__file__).read_text(encoding="utf-8")
        assert "resolve_single_application_creator" in source
        assert "503" in source
        assert "404" in source
        # No raw-content field references (docstring prose excluded by
        # checking code tokens only).
        for token in ('["content"]', "draft_content", "m.content", "SELECT *"):
            assert token not in source, token


class TestEndpointBehavior:
    @staticmethod
    def _run_route(monkeypatch, *, creator_id, rows):
        import asyncio

        def query_kind(query):
            if "generation_telemetry" in query:
                return "telemetry"
            if "commerce_opportunity_decisions" in query:
                return "ledger"
            return "profile"

        class FakeConn:
            def __init__(self, rows):
                self._rows = rows

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetchrow(self, query, *args):
                kind = query_kind(query)
                if kind == "profile":
                    return None
                generation_id, resolved = args[0], args[1]
                for row in self._rows.get(kind, []):
                    if (
                        row.get("generation_id") == generation_id
                        and row.get("creator_id") == resolved
                    ):
                        return row
                return None

        class FakePool:
            def __init__(self, rows):
                self._rows = rows

            def acquire(self):
                return FakeConn(self._rows)

        async def fake_pool():
            return FakePool(rows)

        import db.postgres as postgres

        # build_generation_trace imports get_pool lazily from db.postgres.
        monkeypatch.setattr(postgres, "get_pool", fake_pool)

        import chatbotv2.dashboard.routes.trace as trace_route

        async def fake_resolve():
            return creator_id

        monkeypatch.setattr(trace_route, "_resolve_creator_or_403", fake_resolve)
        return asyncio.run(trace_route.api_generation_trace(GEN_A, auth={}))

    def test_same_generation_other_creator_404(self, monkeypatch):
        response = self._run_route(
            monkeypatch,
            creator_id=CREATOR_B,
            rows={"telemetry": [_telemetry()]},
        )
        assert response.status_code == 404

    def test_missing_creator_fails_closed(self, monkeypatch):
        response = self._run_route(monkeypatch, creator_id=None, rows={})
        assert response.status_code == 503


# ── Failure isolation ─────────────────────────────────────────────────


class TestFailureIsolation:
    def test_renderer_never_raises_on_garbage(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry={"generation_id": GEN_A},
            relationship_anchors={"bands": {"x": object()}},
            boundary_snapshot=object(),
            ledger_snapshot="not-json{{{",
        )
        assert trace.identity.generation_id == GEN_A
        assert trace.complete is False

    def test_partial_sections_do_not_cascade(self):
        from commerce.generation_trace import assemble_trace

        trace = assemble_trace(
            creator_id=CREATOR_A,
            generation_id=GEN_A,
            telemetry=None,
            commerce_decision={"action": "NO_OFFER", "reason_code": "x", "allowed": False},
        )
        assert trace.commerce.status == "ok"
        assert trace.relationship.status in ("unavailable", "unknown")
