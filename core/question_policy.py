"""Question-budget policy (C.1-F Phase 4).

Tracks last assistant questions, enforces bounded question use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_QUESTIONS_PER_3_TURNS = 1
MAX_CONSECUTIVE_QUESTIONS = 1


@dataclass(frozen=True)
class QuestionBudget:
    allowed: bool
    reason: str  # human-readable wire for context
    consecutive: int
    last_question: str | None


def evaluate_question_budget(
    last_question: str | None,
    answered: bool,
    consecutive_questions: int,
    proposed_mode: str,
    questions_in_last_3: int | None = None,
) -> QuestionBudget:
    """Bound question asking.

    Rules:
    - If last Sunny question was unanswered, do not ask again.
    - If already at consecutive limit, block.
    - If 1+ questions in last 3 assistant turns, block (D-02: MAX_QUESTIONS_PER_3_TURNS).
    - EXPLORE/CLARIFY modes are the only modes that may legitimately ask.
    - Otherwise disallow; planner should pick REACT/SHARE/TEASE/CALLBACK.
    """
    if proposed_mode in ("explore", "clarify"):
        # explore legitimacy depends on recent count
        if last_question and not answered:
            return QuestionBudget(False, "last question unanswered", consecutive_questions, last_question)
        if consecutive_questions >= MAX_CONSECUTIVE_QUESTIONS:
            return QuestionBudget(False, "consecutive question limit", consecutive_questions, last_question)
        if questions_in_last_3 is not None and questions_in_last_3 >= MAX_QUESTIONS_PER_3_TURNS:
            return QuestionBudget(False, "questions per 3 turns limit", consecutive_questions, last_question)
        return QuestionBudget(True, "explore allowed", consecutive_questions, last_question)

    # non-explore modes should not ask
    # allow answer/clarify to contain a question if fan asked, otherwise block
    return QuestionBudget(False, f"mode {proposed_mode} should not ask", consecutive_questions, last_question)


def count_questions(text: str) -> int:
    return text.count("?")
