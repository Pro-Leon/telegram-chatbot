"""Shadow comparison harness (Stage F1). V2-vs-legacy turns in, metrics out.

Caller: FUTURE shadow harness over live traffic (Stage F). Composes the
Phase 10 primitives (`observe_turn`, `compare_with_legacy`) with caller-
supplied legacy size metadata into per-turn records plus running metrics:
stage agreement, context-size deltas, divergence counts. Pure and
read-only — no sends, no offers, no mutations, no I/O. The harness selects
no winner; operators inspect the summary (Phase 42).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from relationship_v2.domain.shadow import ShadowDivergence, ShadowReport

logger = logging.getLogger("sunny.v2.shadow_harness")


class ComparedTurn(BaseModel):
    generation_id: str = Field(min_length=1)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    v2_stage: str = Field(min_length=1)
    legacy_stage: str | None = None
    stage_match: bool | None = None
    v2_chars: int = Field(ge=0)
    legacy_chars: int | None = Field(ge=0, default=None)
    char_delta: int | None = None
    v2_memory_lines: int = Field(ge=0)
    legacy_memory_count: int | None = Field(ge=0, default=None)
    divergence_count: int = Field(ge=0)

    model_config = {"frozen": True}


class ShadowSummary(BaseModel):
    turns: int = Field(ge=0)
    stage_comparisons: int = Field(ge=0)
    stage_matches: int = Field(ge=0)
    stage_match_rate: float | None = None
    divergence_total: int = Field(ge=0)
    avg_char_delta: float | None = None
    max_abs_char_delta: int | None = None

    model_config = {"frozen": True}


def compare_turn(
    report: ShadowReport,
    divergences: list[ShadowDivergence],
    legacy_stage: str | None = None,
    legacy_memory_count: int | None = None,
    legacy_chars: int | None = None,
) -> ComparedTurn:
    """One compared turn. Pure; caller supplies legacy-side metadata."""
    if legacy_chars is not None and legacy_chars < 0:
        raise ValueError("legacy_chars must be >= 0")
    if legacy_memory_count is not None and legacy_memory_count < 0:
        raise ValueError("legacy_memory_count must be >= 0")
    match = (report.selected_stage == legacy_stage) if legacy_stage is not None else None
    delta = (report.assembly_chars - legacy_chars) if legacy_chars is not None else None
    return ComparedTurn(
        generation_id=report.generation_id,
        creator_id=report.creator_id,
        user_id=report.user_id,
        v2_stage=report.selected_stage,
        legacy_stage=legacy_stage,
        stage_match=match,
        v2_chars=report.assembly_chars,
        legacy_chars=legacy_chars,
        char_delta=delta,
        v2_memory_lines=report.memory_lines_considered,
        legacy_memory_count=legacy_memory_count,
        divergence_count=len(divergences),
    )


class ShadowMetrics:
    """Mutable running accumulator. One instance per shadow window."""

    def __init__(self) -> None:
        self._turns = 0
        self._stage_comparisons = 0
        self._stage_matches = 0
        self._divergences = 0
        self._char_deltas: list[int] = []

    def record(self, turn: ComparedTurn) -> None:
        self._turns += 1
        self._divergences += turn.divergence_count
        if turn.stage_match is not None:
            self._stage_comparisons += 1
            if turn.stage_match:
                self._stage_matches += 1
        if turn.char_delta is not None:
            self._char_deltas.append(turn.char_delta)

    def summarize(self) -> ShadowSummary:
        rate = (
            self._stage_matches / self._stage_comparisons
            if self._stage_comparisons
            else None
        )
        return ShadowSummary(
            turns=self._turns,
            stage_comparisons=self._stage_comparisons,
            stage_matches=self._stage_matches,
            stage_match_rate=rate,
            divergence_total=self._divergences,
            avg_char_delta=(
                sum(self._char_deltas) / len(self._char_deltas)
                if self._char_deltas
                else None
            ),
            max_abs_char_delta=(
                max(abs(d) for d in self._char_deltas) if self._char_deltas else None
            ),
        )
