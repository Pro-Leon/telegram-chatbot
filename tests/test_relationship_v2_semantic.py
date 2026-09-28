"""Stage F5 tests: fusion math, rerank budgets, summary distillation.

No live pgvector/embedding model: fusion/rerank/summary are pure; SQL and
DDL shapes verified statically. Live semantic-quality verification belongs
to staging (documented open item).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from relationship_v2.domain.memory import (
    MemoryEpisode,
    MemoryEpisodeType,
    MemoryFact,
    MemoryFactStatus,
    MemoryImportance,
)
from relationship_v2.services.semantic_retrieval import (
    embed_query,
    fuse_scores,
    rerank,
)
from relationship_v2.services.summary import SUMMARY_BUDGET_CHARS, build_summary


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _fact(key="job", value="photographer", importance=MemoryImportance.HIGH):
    now = datetime.now(UTC)
    return MemoryFact(
        id=uuid4(), creator_id=1, user_id=2, relationship_id=uuid4(),
        category="occupation", memory_key=key, value=value,
        status=MemoryFactStatus.CURRENT, importance=importance,
        confidence=0.9, effective_from=now, provenance="test",
        created_at=now, updated_at=now,
    )


def _episode(summary="soccer game Saturday", salience=0.7):
    return MemoryEpisode(
        id=uuid4(), creator_id=1, user_id=2, relationship_id=uuid4(),
        episode_type=MemoryEpisodeType.IMPORTANT_EVENT, summary=summary,
        salience=salience, provenance="test", created_at=datetime.now(UTC),
    )


def test_fuse_weights() -> None:
    assert fuse_scores(1.0, 1.0) == 1.0
    assert fuse_scores(1.0, 0.0, alpha=1.0) == 1.0
    assert fuse_scores(1.0, 0.0, alpha=0.0) == 0.0
    assert fuse_scores(0.5, 0.5) == 0.5
    with pytest.raises(ValueError):
        fuse_scores(-0.1, 0.5)
    with pytest.raises(ValueError):
        fuse_scores(0.5, 1.5)
    with pytest.raises(ValueError):
        rerank({"a": 1.0}, {"a": 0.5}, top_k=0)


def test_rerank_semantic_rescues_lexical_miss() -> None:
    ranked = rerank({"a": 0.9}, {"b": 0.95}, alpha=0.5, top_k=2)
    assert [h.ref_id for h in ranked] == ["b", "a"]
    assert ranked[0].fused == pytest.approx(0.475)


def test_rerank_budget_and_determinism() -> None:
    lex = {f"f{i}": 0.5 for i in range(6)}
    first = rerank(lex, {}, top_k=3)
    second = rerank(lex, {}, top_k=3)
    assert len(first) == 3
    assert [h.ref_id for h in first] == [h.ref_id for h in second]


def test_embed_query_requires_port() -> None:
    async def _fake(text: str) -> list[float]:
        return [0.1, 0.2]

    assert _run(embed_query("hello", _fake)) == [0.1, 0.2]
    with pytest.raises(ValueError):
        _run(embed_query("hello", None))
    with pytest.raises(ValueError):
        _run(embed_query("   ", _fake))


def test_summary_bounded_with_ids() -> None:
    facts = [_fact(), _fact(key="hobby", value="football")]
    episodes = [_episode()]
    s = build_summary(facts, episodes, ["known 120d"], provenance="test")
    assert s.total_chars <= SUMMARY_BUDGET_CHARS
    assert len(s.fact_ids) == 2 and len(s.episode_ids) == 1
    assert any("job=photographer" in line for line in s.lines)


def test_summary_regenerable_and_truncates() -> None:
    facts = [_fact(value="x" * 100) for _ in range(20)]
    a = build_summary(facts, [], provenance="t")
    b = build_summary(facts, [], provenance="t")
    assert a.lines == b.lines and a.total_chars == b.total_chars
    assert a.truncated is True


def test_summary_provenance_required() -> None:
    with pytest.raises(ValueError):
        build_summary([], [], provenance="")


def test_embedding_ddl_and_repo_shape() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    mig = (
        root / "relationship_v2" / "persistence" / "migrations"
        / "007_fact_embeddings.sql"
    ).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS embedding vector(1536)" in mig
    assert "idx_v2_facts_embedding" in mig
    mig8 = (
        root / "relationship_v2" / "persistence" / "migrations"
        / "008_fact_embeddings_384.sql"
    ).read_text(encoding="utf-8")
    assert "TYPE vector(384)" in mig8
    schema = (root / "relationship_v2" / "persistence" / "schema.sql").read_text(
        encoding="utf-8"
    )
    assert "embedding vector(384)" in schema
    repo = (root / "relationship_v2" / "persistence" / "repository.py").read_text(
        encoding="utf-8"
    )
    assert "def set_fact_embedding" in repo
    assert "def search_facts_semantic" in repo
    assert "embedding <=> $3::vector" in repo
    assert "AND status = 'current' AND embedding IS NOT NULL" in repo


def test_vector_literal_binding() -> None:
    import pytest

    from relationship_v2.persistence.repository import _to_vector_literal

    assert _to_vector_literal([1.0, 2.5]) == "[1.0,2.5]"
    with pytest.raises(ValueError):
        _to_vector_literal([])


def test_no_provider_imports() -> None:
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in (
        "relationship_v2/services/semantic_retrieval.py",
        "relationship_v2/services/summary.py",
    ):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        assert not any(
            m == "commerce" or m.startswith("commerce.") for m in mods
        )
        assert "relationship_v2.persistence.repository" not in mods
