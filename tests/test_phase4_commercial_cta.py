"""Phase 4 — unauthorized soft-sell CTA flag.

(a) Gap table: each previously-clean pattern fires.
(b) Authorized must-pass: is_authorized short-circuits; sealed /
    commerce-draft / free-photo paths never reach the check as
    conversational drafts (proven by wiring, asserted via validator).
(c) FP must-pass: legitimate non-commercial uses stay clean.
(d) Validator purity: odd input never raises, always no-hit.
"""

import pytest

pytestmark = [pytest.mark.unit]


class TestGapTable:
    @pytest.mark.parametrize(
        "reply,pattern",
        [
            ("Want to see my exclusive pics tonight?", "want_to_see"),
            ("I can send you something special", "send_you_something"),
            ("Unlock my private set for you", "unlock_content"),
            ("Check out my new set", "check_out_content"),
            ("I have exclusive content if interested", "exclusive_content"),
            ("My exclusive pics are ready", "exclusive_content"),
            ("Made something special for you", "special_for_you"),
            ("Offer was created for you", "offer_created"),
            ("Grab it here tonight", "offer_created"),
            ("Limited offer, discount today", "promo_language"),
            ("Only today, special price", "promo_language"),
            ("That bundle is 20 dollars", "bare_amount"),
            ("Buy it here now", "offer_created"),
            ("Payment link is ready", "offer_created"),
        ],
    )
    def test_gap_pattern_fires(self, reply, pattern):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, matched = detect_unauthorized_commercial_cta(reply)
        assert hit is True
        assert matched == pattern

    @pytest.mark.parametrize(
        "reply",
        [
            "Subscribe for more updates",
            "PPV drop is live now",
            "You can purchase the bundle",
            "Price is 20 dollars tonight",
        ],
    )
    def test_covered_still_fire(self, reply):
        from core.scoring_deterministic import compute_safety_flags

        assert "price_mention" in compute_safety_flags(reply)

    def test_photo_promise_still_fires(self):
        from core.scoring_deterministic import compute_safety_flags

        assert "photo_promise" in compute_safety_flags("I can send you a pic tonight")


class TestAuthorizedMustPass:
    @pytest.mark.parametrize(
        "reply",
        [
            "Want to see my exclusive pics tonight?",
            "Unlock my private set for you",
            "Offer was created for you",
            "That bundle is 20 dollars",
            "1 exclusive item for $20.00",
        ],
    )
    def test_authorized_short_circuits(self, reply):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        assert detect_unauthorized_commercial_cta(reply, is_authorized=True) == (False, None)

    def test_flag_name_distinct_from_ledger_key(self):
        from commerce.commercial_cta import CTA_FLAG

        assert CTA_FLAG == "unauthorized_commercial_cta"
        assert CTA_FLAG != "unauthorized_price_attempt"

    def test_wiring_exempts_authorized_paths(self):
        import pathlib

        src = pathlib.Path("workers/llm_worker.py").read_text(encoding="utf-8")
        # Commerce-draft exemption
        assert "CommerceSelectionStatus.USE_COMMERCE_RESPONSE" in src
        # Sealed-PPV exemption (path itself returns before the choke)
        assert "_sealed_ppv_handled" in src
        # Free-photo exemption via reservation
        assert "_free_photo_result" in src and "reservation_id" in src
        # Sealed path untouched (no CTA reference inside the sealed block)
        sealed = src.split("if _sealed_ppv_handled:")[1].split("elif not auto_reply_on:")[0]
        assert "unauthorized_commercial_cta" not in sealed.lower()
        assert "detect_unauthorized_commercial_cta" not in sealed


class TestFalsePositives:
    @pytest.mark.parametrize(
        "reply",
        [
            "Did you catch that exclusive interview last night?",
            "Unlock your potential today, you have got this",
            "So glad you are enjoying it, let me know if you need anything",
            "What did you get up to today?",
            "That sounds like a great Saturday plan",
        ],
    )
    def test_legitimate_use_clean(self, reply):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        assert detect_unauthorized_commercial_cta(reply) == (False, None)


class TestValidatorPurity:
    @pytest.mark.parametrize("reply", ["", "   ", None, 123, ["x"], {"a": 1}, "ok"])
    def test_odd_input_no_hit_never_raises(self, reply):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        assert detect_unauthorized_commercial_cta(reply) == (False, None)

    def test_only_re_typing_imports(self):
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path("commerce/commercial_cta.py").read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or "").split(".")[0])
        assert imports <= {"re", "typing", "__future__"}
