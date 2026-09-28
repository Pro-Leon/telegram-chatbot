"""Context Engine Integration Harness (Phase 72).

Production-shaped integration that exercises the complete
gather → score → dedup → budget → assemble → render pipeline
against real application state.

This harness is OBSERVATIONAL ONLY:
- Does NOT replace the existing LLM pipeline
- Does NOT become authoritative
- Does NOT change production responses
- Does NOT activate shadow mode

The correct result is:
- production system unchanged
- Context Engine proven end-to-end in isolation
- production-shaped data verified
- future integration boundary clearly defined
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from context_engine.assembler import ContextAssembler
from context_engine.dedup import ContextDeduplicator
from context_engine.gatherer import (
    ContextGatherer,
    DataSource,
    GathererConfig,
)
from context_engine.models import (
    ContextCategory,
    ContextItem,
    ContextSnapshot,
)
from context_engine.renderer import CompactRenderer, RenderedContext
from context_engine.scorer import ContextScorer

logger = logging.getLogger("context_engine.integration")


# ---------------------------------------------------------------------------
# Request Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextRequest:
    """Production-shaped request for Context Engine integration.

    Makes identity explicit. Contains no secrets.
    """

    creator_id: int | None
    user_id: int
    current_message: str
    timestamp: float = field(default_factory=time.time)
    persona_id: int | None = None
    conversation_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    conversation_state: dict[str, Any] | None = None
    authoritative_state: Any | None = None  # Phase 2: single snapshot (AuthoritativeState)

    def to_gatherer_config(self) -> GathererConfig:
        """Convert to GathererConfig for the gatherer orchestrator."""
        return GathererConfig(
            creator_id=self.creator_id,
            user_id=self.user_id,
            current_message=self.current_message,
            conversation_state=self.conversation_state,
            authoritative_state=self.authoritative_state,
        )


# ---------------------------------------------------------------------------
# Pipeline Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPipelineResult:
    """Complete result from the Context Engine pipeline."""

    request: ContextRequest
    snapshot: ContextSnapshot
    rendered: RenderedContext
    messages: list[dict[str, str]]
    candidate_count: int
    selected_count: int
    deduplication_count: int
    total_tokens: int
    category_tokens: dict[ContextCategory, int]
    degradation_level: int
    assembly_time_ms: float
    gather_time_ms: float
    total_time_ms: float
    items_by_category: dict[ContextCategory, list[ContextItem]]
    authority_summary: dict[str, int]
    violations: list[str]
    # Phase 87 detailed observability
    conflict_dropped_count: int = 0
    lexical_dedup_removed_count: int = 0
    truncation_count: int = 0
    retrieval_metrics: dict[str, Any] | None = None  # lexical/semantic split
    score_ms: float = 0.0
    dedup_ms: float = 0.0
    budget_ms: float = 0.0
    render_ms: float = 0.0


# ---------------------------------------------------------------------------
# Integration Harness
# ---------------------------------------------------------------------------


class ContextEngineIntegration:
    """Production-shaped Context Engine integration harness.

    Orchestrates the complete pipeline:
        ContextRequest
            ↓
        Data Gatherers (Phase 71 real APIs)
            ↓
        ContextItems
            ↓
        ContextScorer
            ↓
        ContextDeduplicator
            ↓
        TokenBudgetManager
            ↓
        ContextSnapshot
            ↓
        CompactRenderer
            ↓
        ContextPipelineResult

    Observational only — does not modify production behavior.
    """

    def __init__(
        self,
        sources: list[DataSource] | None = None,
        scorer: ContextScorer | None = None,
        deduplicator: ContextDeduplicator | None = None,
        assembler: ContextAssembler | None = None,
        renderer: CompactRenderer | None = None,
    ):
        self.gatherer = ContextGatherer(sources=sources or [])
        self.scorer = scorer or ContextScorer()
        self.deduplicator = deduplicator or ContextDeduplicator()
        self.assembler = assembler or ContextAssembler(
            scorer=self.scorer,
            deduplicator=self.deduplicator,
        )
        self.renderer = renderer or CompactRenderer()

    async def process(self, request: ContextRequest) -> ContextPipelineResult:
        """Execute the complete Context Engine pipeline.

        Args:
            request: Production-shaped context request

        Returns:
            ContextPipelineResult with full pipeline output
        """
        total_start = time.monotonic()

        # Step 1: Gather
        gather_start = time.monotonic()
        config = request.to_gatherer_config()
        candidates = await self.gatherer.gather_all(config)
        gather_time_ms = (time.monotonic() - gather_start) * 1000

        # Step 2-6: Score → Dedup → Budget → Assemble → Snapshot
        assembly_start = time.monotonic()
        snapshot = self.assembler.assemble(
            candidates,
            query=request.current_message,
            conversation_state=request.conversation_state,
        )
        assembly_time_ms = (time.monotonic() - assembly_start) * 1000

        # Step 7: Render (measured separately for observability)
        render_start = time.monotonic()
        rendered = self.renderer.render(snapshot)
        messages = self.renderer.render_to_messages(snapshot)
        render_ms = (time.monotonic() - render_start) * 1000

        total_time_ms = (time.monotonic() - total_start) * 1000

        # Phase 87: extract detailed metrics from snapshot metadata and gatherer
        _conflict_dropped = int(snapshot.metadata.get("conflict_dropped", 0)) if isinstance(snapshot.metadata, dict) else 0
        _lexical_dedup = int(snapshot.metadata.get("lexical_dedup_removed", 0)) if isinstance(snapshot.metadata, dict) else int(snapshot.deduplication_count) - _conflict_dropped
        _truncation = int(snapshot.metadata.get("truncation_count", 0)) if isinstance(snapshot.metadata, dict) else 0
        _score_ms = float(snapshot.metadata.get("score_ms", 0)) if isinstance(snapshot.metadata, dict) else 0.0
        _dedup_ms = float(snapshot.metadata.get("dedup_ms", 0)) if isinstance(snapshot.metadata, dict) else 0.0
        _budget_ms = float(snapshot.metadata.get("budget_ms", 0)) if isinstance(snapshot.metadata, dict) else 0.0
        _retrieval_metrics = getattr(self.gatherer, "last_retrieval_metrics", None)

        # Build items-by-category index
        items_by_category: dict[ContextCategory, list[ContextItem]] = {}
        for item in snapshot.items:
            items_by_category.setdefault(item.category, []).append(item)

        # Authority summary
        authority_summary: dict[str, int] = {}
        for item in snapshot.items:
            key = item.authority.name
            authority_summary[key] = authority_summary.get(key, 0) + 1

        # Validate
        violations = self.assembler.validate_assembly(snapshot)

        return ContextPipelineResult(
            request=request,
            snapshot=snapshot,
            rendered=rendered,
            messages=messages,
            candidate_count=snapshot.candidate_count,
            selected_count=snapshot.selected_count,
            deduplication_count=snapshot.deduplication_count,
            total_tokens=snapshot.total_tokens,
            category_tokens=dict(snapshot.category_tokens),
            degradation_level=snapshot.degradation_level,
            assembly_time_ms=assembly_time_ms,
            gather_time_ms=gather_time_ms,
            total_time_ms=total_time_ms,
            items_by_category=items_by_category,
            authority_summary=authority_summary,
            violations=violations,
            conflict_dropped_count=_conflict_dropped,
            lexical_dedup_removed_count=max(0, _lexical_dedup),
            truncation_count=_truncation,
            retrieval_metrics=_retrieval_metrics,
            score_ms=_score_ms,
            dedup_ms=_dedup_ms,
            budget_ms=_budget_ms,
            render_ms=render_ms,
        )

    def validate_request(self, request: ContextRequest) -> list[str]:
        """Validate a request without executing the pipeline.

        Returns list of validation violations (empty if valid).
        """
        violations: list[str] = []
        if request.user_id <= 0:
            violations.append(f"Invalid user_id: {request.user_id}")
        if not request.current_message and not request.metadata.get("allow_empty"):
            violations.append("Empty current_message")
        if request.creator_id is not None and request.creator_id <= 0:
            violations.append(f"Invalid creator_id: {request.creator_id}")
        return violations
