"""Stage F2 tests: legacy facts gated to historical-only or rejected.

Pure validator, no infra. Proves: ordinary facts migrate as history with
legacy confidence, creator/commerce/intimate/oversized/malformed reject,
batches count correctly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from relationship_v2.services.migration_validator import (
    LEGACY_IMPORTED_CONFIDENCE,
    LegacyFact,
    MigrationDisposition,
    validate_batch,
    validate_legacy_fact,
)


def _fact(key: str, value: str, **kw) -> LegacyFact:
    base = {"source_table": "user_profiles", "key": key, "value": value}
    base.update(kw)
    return LegacyFact(**base)


def test_ordinary_fact_migrates_historical() -> None:
    d = validate_legacy_fact(_fact("occupation", "architect", category="WORK"))
    assert d.disposition == MigrationDisposition.MIGRATE_HISTORICAL
    assert d.mapped_confidence == LEGACY_IMPORTED_CONFIDENCE
    assert d.mapped_category == "work"


def test_spec_example_architect() -> None:
    d = validate_legacy_fact(_fact("occupation", "photographer"))
    assert d.disposition == MigrationDisposition.MIGRATE_HISTORICAL
    assert d.reason == "legacy_imported_as_history"


def test_creator_identity_rejected() -> None:
    d = validate_legacy_fact(_fact("creator_name", "Sunny"))
    assert d.disposition == MigrationDisposition.REJECT


def test_commerce_rejected() -> None:
    for key in ("price", "purchase_status", "ownership", "offer"):
        d = validate_legacy_fact(_fact(key, "something"))
        assert d.disposition == MigrationDisposition.REJECT, key


def test_intimate_verbatim_rejected() -> None:
    d = validate_legacy_fact(_fact("note", "he said he was horny"))
    assert d.disposition == MigrationDisposition.REJECT
    assert d.reason == "intimate_verbatim_never_migrated"


def test_oversized_rejected() -> None:
    d = validate_legacy_fact(_fact("note", "x" * 513))
    assert d.disposition == MigrationDisposition.REJECT


def test_malformed_model_rejected() -> None:
    with pytest.raises(ValidationError):
        LegacyFact(source_table="t", key="", value="v")


def test_batch_counts() -> None:
    rep = validate_batch(
        [
            _fact("occupation", "architect"),
            _fact("price", "$20"),
            _fact("hobby", "football"),
        ]
    )
    assert rep.accepted == 2 and rep.rejected == 1
    assert len(rep.decisions) == 3


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "services"
        / "migration_validator.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
    assert "relationship_v2.persistence.repository" not in mods
