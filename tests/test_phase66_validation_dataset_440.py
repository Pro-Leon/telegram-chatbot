"""Phase 66 — 440-Case Validation Dataset Tests
Tests for dataset integrity, diversity, contamination, and evaluation compatibility.
"""
import pytest


def test_440_dataset_count():
    from commerce.validation_dataset_440 import VALIDATION_440, validate_440
    assert len(VALIDATION_440) == 440
    ok, msg = validate_440()
    assert ok, msg


def test_440_intent_distribution():
    from collections import Counter
    from commerce.validation_dataset_440 import VALIDATION_440
    cnt = Counter(e["intent"] for e in VALIDATION_440)
    assert len(cnt) == 22
    for intent, c in cnt.items():
        assert c == 20, f"{intent} has {c} not 20"


def test_440_no_duplicate_texts():
    from commerce.validation_dataset_440 import VALIDATION_440
    texts = [e["text"].lower().strip() for e in VALIDATION_440]
    assert len(texts) == len(set(texts))


def test_440_unique_example_ids():
    from commerce.validation_dataset_440 import VALIDATION_440
    ids = [e["example_id"] for e in VALIDATION_440]
    assert len(ids) == len(set(ids))
    assert min(ids) == 1
    assert max(ids) == 440


def test_440_independent_from_corpus():
    from commerce.intent_corpus import INTENT_CORPUS
    from commerce.validation_dataset_440 import VALIDATION_440
    ref_texts = set(e["example_text"].lower().strip() for e in INTENT_CORPUS)
    val_texts = [e["text"].lower().strip() for e in VALIDATION_440]
    overlap = set(val_texts) & ref_texts
    assert len(overlap) == 0, f"overlap with corpus: {overlap}"


def test_440_independent_from_110_validation():
    from commerce.validation_dataset import VALIDATION_DATASET
    from commerce.validation_dataset_440 import VALIDATION_440
    ref_texts = set(e["text"].lower().strip() for e in VALIDATION_DATASET)
    val_texts = [e["text"].lower().strip() for e in VALIDATION_440]
    overlap = set(val_texts) & ref_texts
    assert len(overlap) == 0, f"overlap with 110-case: {overlap}"


def test_440_valid_intents():
    from commerce.validation_dataset_440 import VALIDATION_440
    from commerce.signals import INTENT_CATEGORIES
    for e in VALIDATION_440:
        assert e["intent"] in INTENT_CATEGORIES, f"invalid intent: {e['intent']}"


def test_440_has_required_fields():
    from commerce.validation_dataset_440 import VALIDATION_440
    for e in VALIDATION_440:
        assert "text" in e
        assert "intent" in e
        assert "notes" in e
        assert "example_id" in e
        assert "hard_negative_for" in e
        assert "context" in e
        assert "length_category" in e
        assert "style" in e


def test_440_length_category_diversity():
    from collections import Counter
    from commerce.validation_dataset_440 import VALIDATION_440
    cats = Counter(e["length_category"] for e in VALIDATION_440)
    assert "short" in cats
    assert "medium" in cats
    assert "long" in cats


def test_440_style_diversity():
    from collections import Counter
    from commerce.validation_dataset_440 import VALIDATION_440
    styles = Counter(e["style"] for e in VALIDATION_440)
    assert len(styles) >= 3, f"only {len(styles)} styles, expected diversity"


def test_440_hard_negative_distribution():
    from collections import Counter
    from commerce.validation_dataset_440 import VALIDATION_440
    negatives = Counter(e["hard_negative_for"] for e in VALIDATION_440 if e["hard_negative_for"] is not None)
    assert len(negatives) >= 15, f"only {len(negatives)} hard negative targets"


def test_440_context_examples_present():
    from commerce.validation_dataset_440 import VALIDATION_440
    with_context = [e for e in VALIDATION_440 if e["context"] is not None]
    assert len(with_context) >= 20, f"only {len(with_context)} context examples"


def test_evaluation_script_compatibility():
    import pathlib
    path = pathlib.Path("tests/evaluate_unified_intelligence.py")
    assert path.exists()
    content = path.read_text()
    assert "VALIDATION_DATASET" in content or "validation_dataset" in content.lower()
