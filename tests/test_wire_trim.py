"""Wire-budget fail-soft trim tests (deterministic, no DB, no LLM).

Pins the Luna incident contract: a wire overrun sheds oldest history,
never system blocks or the current turn, instead of failing the whole
turn into an empty draft.
"""

from core.context_compact import (
    trim_wire_to_budget,
    validate_one_call_context,
)


def _pad(n_chars: int) -> str:
    return "w " * (n_chars // 2)


def _over_budget_messages(n_conv: int = 8, pad: int = 900):
    messages = [
        {"role": "system", "content": "PERSONA: sunny " + _pad(400)},
        {"role": "system", "content": "BOUNDARY: none active"},
    ]
    for i in range(n_conv):
        messages.append({"role": "user", "content": f"old turn {i} " + _pad(pad)})
        messages.append({"role": "assistant", "content": f"old reply {i} " + _pad(pad)})
    messages.append({"role": "user", "content": "CURRENT hello luna here"})
    return messages


def test_overrun_trims_to_fit():
    messages = _over_budget_messages()
    ok, err = validate_one_call_context(messages)
    assert not ok and "Wire prompt too large" in err
    trimmed = trim_wire_to_budget(messages, "hello luna here")
    ok2, err2 = validate_one_call_context(trimmed)
    assert ok2, err2
    assert len(trimmed) < len(messages)


def test_system_blocks_and_current_turn_survive():
    messages = _over_budget_messages()
    system_before = [m["content"] for m in messages if m["role"] == "system"]
    trimmed = trim_wire_to_budget(messages, "hello luna here")
    system_after = [m["content"] for m in trimmed if m["role"] == "system"]
    assert system_after == system_before
    assert any("hello luna here" in m.get("content", "") for m in trimmed)


def test_minimum_recent_turns_kept():
    messages = _over_budget_messages(n_conv=8, pad=900)
    trimmed = trim_wire_to_budget(messages, "hello luna here", min_conv_turns=2)
    conv = [m for m in trimmed if m.get("role") != "system"]
    # Carrier is the newest turn, so it sits inside the recent window.
    assert len(conv) >= 2
    assert any("hello luna here" in m.get("content", "") for m in conv)


def test_fitting_input_byte_identical():
    messages = [{"role": "system", "content": "hi"}, {"role": "user", "content": "hey"}]
    assert trim_wire_to_budget(messages, "hey") == messages


def test_system_only_overrun_returned_unchanged():
    messages = [{"role": "system", "content": "big " + _pad(9000)}]
    assert trim_wire_to_budget(messages, "hey") == messages


def test_odd_input_never_raises():
    for bad in (None, [], "nope", [{"role": "user"}], [{"content": "x"}]):
        assert trim_wire_to_budget(bad, "hey") == bad
    assert trim_wire_to_budget([{"role": "user", "content": "hi"}], None) == [
        {"role": "user", "content": "hi"}
    ]
