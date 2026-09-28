"""MiniLM embedder tests: dims contract + fail-closed inputs.

Model load itself is staging-proven (live recall PASS on real PG); unit
tests avoid reloading weights and cover guards + contract only.
"""

from __future__ import annotations

import pytest

from relationship_v2.integration.minilm import EXPECTED_DIMS, MODEL_NAME


def test_contract_constants() -> None:
    assert MODEL_NAME == "all-MiniLM-L6-v2"
    assert EXPECTED_DIMS == 384


def test_embed_texts_rejects_empty() -> None:
    import asyncio

    from relationship_v2.integration.minilm import embed_texts

    with pytest.raises(ValueError):
        asyncio.run(embed_texts([]))
    with pytest.raises(ValueError):
        asyncio.run(embed_texts(["   "]))


def test_no_forbidden_imports() -> None:
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "relationship_v2"
        / "integration"
        / "minilm.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert not any(m == "commerce" or m.startswith("commerce.") for m in mods)
