"""Phase 1.1 output-rails unit tests (mocked outbound/embed only, no I/O)."""

from core.output_rails import check


def test_echo_verbatim():
    verdict, flags, cap = check("Your conversational response to the fan")
    assert verdict == "review"
    assert "prompt_echo" in flags
    assert cap == 0.29


def test_echo_punctuated_cased():
    verdict, flags, cap = check("  YOUR CONVERSATIONAL RESPONSE TO THE FAN. ")
    assert verdict == "review"
    assert "prompt_echo" in flags
    assert cap == 0.29


def test_echo_fuzzy_paraphrase():
    # Dropped word: exact-only misses, fuzzy/substring must catch.
    verdict, flags, cap = check("Your conversational reply to the fan")
    assert verdict == "review"
    assert "prompt_echo" in flags
    assert cap == 0.29


def test_echo_embedded_substring():
    verdict, flags, cap = check("Sure! Your conversational response to the fan, enjoy!")
    assert verdict == "review"
    assert "prompt_echo" in flags
    assert cap == 0.29


def test_repeat_identical_last3():
    verdict, flags, cap = check(
        "Hey Luna, how's it going?",
        recent_outbound=["see you soon!", "Hey Luna, how's it going?", "miss you"],
    )
    assert verdict == "review"
    assert "repeat" in flags
    assert cap == 0.5


def test_repeat_near_paraphrase_with_embed():
    # Fake embed: reply [1,0]; close outbound cosine 0.9; far outbound cosine 0.
    def fake_embed(texts):
        vecs = {"Hey Luna, how is it going?": [0.9, 0.4359], "good morning": [0.0, 1.0]}
        return [[1.0, 0.0]] + [vecs[t] for t in texts[1:]]

    verdict, flags, cap = check(
        "Hey Luna, how's it going?",
        recent_outbound=["good morning", "Hey Luna, how is it going?"],
        embed=fake_embed,
    )
    assert verdict == "review"
    assert "repeat" in flags
    assert cap == 0.5


def test_repeat_embed_none_lexical_only():
    verdict, flags, cap = check(
        "Hey Luna, how is it going?",
        recent_outbound=["Hey Luna, how's it going?"],
        embed=None,
    )
    assert verdict == "clean"
    assert flags == []
    assert cap is None


def test_repeat_embed_failure_fail_open():
    def bad_embed(texts):
        raise RuntimeError("embedding down")

    verdict, flags, cap = check(
        "Something completely different here",
        recent_outbound=["unrelated prior outbound message"],
        embed=bad_embed,
    )
    assert verdict == "clean"
    assert flags == []
    assert cap is None


def test_fan_leak_flagged():
    verdict, flags, cap = check("Hey fan, great to see you!")
    assert verdict == "review"
    assert "fan_word" in flags
    assert cap == 0.5


def test_fan_narrative_clean():
    # Third-party mention is not direct address; echo verdict stays clean too.
    verdict, flags, cap = check("A fan once asked me that, funny story")
    assert "prompt_echo" not in flags
    assert verdict == "clean"
    assert flags == []
    assert cap is None


def test_speaker_prefix_leading():
    verdict, flags, cap = check(
        "Sunny Skye: Hey there!", character_name="Sunny Skye", player_name="Alex"
    )
    assert verdict == "review"
    assert "speaker_prefix" in flags
    assert cap == 0.5


def test_speaker_prefix_mid_text():
    verdict, flags, cap = check(
        "Glad you asked. CHARACTER: here is the plan.",
        character_name="Sunny Skye",
        player_name="Alex",
    )
    assert verdict == "review"
    assert "speaker_prefix" in flags
    assert cap == 0.5


def test_speaker_comma_not_prefix():
    verdict, flags, cap = check(
        "Sunny, that sounds amazing!", character_name="Sunny Skye", player_name="Alex"
    )
    assert verdict == "clean"
    assert flags == []
    assert cap is None


def test_markup_flagged():
    verdict, flags, cap = check("[PLAYER MESSAGE] hello there friend")
    assert verdict == "review"
    assert "markup_echo" in flags
    assert cap == 0.5


def test_internal_id_flagged():
    verdict, flags, cap = check("See this item id:12345 today")
    assert verdict == "review"
    assert "markup_echo" in flags
    assert cap == 0.5


def test_clean_reply():
    verdict, flags, cap = check(
        "Hey Alex! Workouts are the best, how was dance class today?",
        character_name="Sunny Skye",
        player_name="Alex",
        recent_outbound=["see you at eight"],
    )
    assert verdict == "clean"
    assert flags == []
    assert cap is None


def test_fail_open_inputs():
    assert check(None) == ("clean", [], None)
    assert check("") == ("clean", [], None)
    assert check(123) == ("clean", [], None)


def test_multi_flag_min_cap():
    verdict, flags, cap = check("Your conversational response to the fan. Hey fan!")
    assert verdict == "review"
    assert "prompt_echo" in flags
    assert "fan_word" in flags
    assert cap == 0.29
