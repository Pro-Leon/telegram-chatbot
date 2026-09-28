"""SimulationWorld — deterministic synthetic world container (Phase 2).

World = run + clock + identity + small in-memory population (creator/fan/content/opportunity).
No DB writes. No Telegram/Dropfans. Deterministic per run seed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from simulation.clock import SimulationClock
from simulation.data_origin import SYNTHETIC_GENERATION_PREFIX, SYNTHETIC_MARKER
from simulation.identity import SimulationIdentity
from simulation.run import SimulationRun


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty string")
    return value.strip()


@dataclass(frozen=True)
class SyntheticCreator:
    creator_id: int
    simulation_id: str
    data_origin: str = "simulation"
    synthetic_marker: str = SYNTHETIC_MARKER

    def __post_init__(self) -> None:
        if (
            not isinstance(self.creator_id, int)
            or isinstance(self.creator_id, bool)
            or self.creator_id <= 0
        ):
            raise ValueError("creator_id must be positive int")
        _require_text("simulation_id", self.simulation_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "creator_id": self.creator_id,
            "simulation_id": self.simulation_id,
            "data_origin": self.data_origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyntheticCreator:
        return cls(
            creator_id=int(data["creator_id"]),
            simulation_id=str(data["simulation_id"]),
            data_origin=str(data.get("data_origin", "simulation")),
        )


@dataclass(frozen=True)
class SyntheticFan:
    fan_id: int
    creator_id: int
    simulation_id: str
    data_origin: str = "simulation"
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if not isinstance(self.fan_id, int) or isinstance(self.fan_id, bool) or self.fan_id <= 0:
            raise ValueError("fan_id must be positive int")
        if (
            not isinstance(self.creator_id, int)
            or isinstance(self.creator_id, bool)
            or self.creator_id <= 0
        ):
            raise ValueError("creator_id must be positive int")
        _require_text("simulation_id", self.simulation_id)

    @property
    def user_id(self) -> int:
        return self.fan_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "creator_id": self.creator_id,
            "simulation_id": self.simulation_id,
            "data_origin": self.data_origin,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyntheticFan:
        # support legacy key user_id
        fid = data.get("fan_id", data.get("user_id"))
        return cls(
            fan_id=int(fid),
            creator_id=int(data["creator_id"]),
            simulation_id=str(data["simulation_id"]),
        )


@dataclass(frozen=True)
class SyntheticContent:
    content_id: str
    creator_id: int
    simulation_id: str
    offer_type: str
    price_minor: int
    currency: str
    vault_ids: tuple[str, ...]
    mapped_drop_ids: tuple[str, ...]
    data_origin: str = "simulation"

    def __post_init__(self) -> None:
        _require_text("content_id", self.content_id)
        if (
            not isinstance(self.creator_id, int)
            or isinstance(self.creator_id, bool)
            or self.creator_id <= 0
        ):
            raise ValueError("creator_id must be positive int")
        _require_text("simulation_id", self.simulation_id)
        _require_text("offer_type", self.offer_type)
        if (
            not isinstance(self.price_minor, int)
            or isinstance(self.price_minor, bool)
            or self.price_minor < 0
        ):
            raise ValueError("price_minor must be int >=0")
        if not all(isinstance(v, str) and v.strip() for v in self.vault_ids):
            raise ValueError("vault_ids must be non-empty strings")
        if not all(isinstance(d, str) and d.strip() for d in self.mapped_drop_ids):
            raise ValueError("mapped_drop_ids must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "creator_id": self.creator_id,
            "simulation_id": self.simulation_id,
            "offer_type": self.offer_type,
            "price_minor": self.price_minor,
            "currency": self.currency,
            "vault_ids": list(self.vault_ids),
            "mapped_drop_ids": list(self.mapped_drop_ids),
            "data_origin": self.data_origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyntheticContent:
        return cls(
            content_id=str(data["content_id"]),
            creator_id=int(data["creator_id"]),
            simulation_id=str(data["simulation_id"]),
            offer_type=str(data["offer_type"]),
            price_minor=int(data["price_minor"]),
            currency=str(data.get("currency", "USD")),
            vault_ids=tuple(data.get("vault_ids", [])),
            mapped_drop_ids=tuple(data.get("mapped_drop_ids", [])),
        )


@dataclass(frozen=True)
class SyntheticConversationContext:
    lifecycle: str | None = "established"
    current_topic: str | None = None
    recent_topics: tuple[str, ...] = ()
    open_threads: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "lifecycle": self.lifecycle,
            "current_topic": self.current_topic,
            "recent_topics": list(self.recent_topics),
            "open_threads": list(self.open_threads),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyntheticConversationContext:
        return cls(
            lifecycle=data.get("lifecycle"),
            current_topic=data.get("current_topic"),
            recent_topics=tuple(data.get("recent_topics") or []),
            open_threads=tuple(data.get("open_threads") or []),
        )


@dataclass(frozen=True)
class SyntheticOpportunity:
    opportunity_id: int
    creator_id: int
    fan_id: int
    content_id: str
    evaluated_at: datetime
    generation_id: str
    decision_snapshot: dict[str, Any]
    data_origin: str = "simulation"
    policy_version: str | None = "v1"
    selected_definition_id: int | None = None
    selected_version: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.opportunity_id, int)
            or isinstance(self.opportunity_id, bool)
            or self.opportunity_id <= 0
        ):
            raise ValueError("opportunity_id must be positive int")
        if (
            not isinstance(self.creator_id, int)
            or isinstance(self.creator_id, bool)
            or self.creator_id <= 0
        ):
            raise ValueError("creator_id must be positive int")
        if not isinstance(self.fan_id, int) or isinstance(self.fan_id, bool) or self.fan_id <= 0:
            raise ValueError("fan_id must be positive int")
        _require_text("content_id", self.content_id)
        if not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware datetime")
        _require_text("generation_id", self.generation_id)
        if not self.generation_id.startswith(SYNTHETIC_GENERATION_PREFIX):
            raise ValueError(f"generation_id must start with {SYNTHETIC_GENERATION_PREFIX}")
        if not isinstance(self.decision_snapshot, dict):
            raise ValueError("decision_snapshot must be dict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "creator_id": self.creator_id,
            "fan_id": self.fan_id,
            "content_id": self.content_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "generation_id": self.generation_id,
            "decision_snapshot": self.decision_snapshot,
            "data_origin": self.data_origin,
            "policy_version": self.policy_version,
            "selected_definition_id": self.selected_definition_id,
            "selected_version": self.selected_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyntheticOpportunity:
        evaluated = data.get("evaluated_at")
        if isinstance(evaluated, str):
            evaluated = datetime.fromisoformat(evaluated.replace("Z", "+00:00"))
        if evaluated is None or not isinstance(evaluated, datetime) or evaluated.tzinfo is None:
            raise ValueError("invalid evaluated_at in SyntheticOpportunity dict")
        return cls(
            opportunity_id=int(data["opportunity_id"]),
            creator_id=int(data["creator_id"]),
            fan_id=int(data["fan_id"] or data.get("user_id")),
            content_id=str(data["content_id"]),
            evaluated_at=evaluated,
            generation_id=str(data["generation_id"]),
            decision_snapshot=dict(data["decision_snapshot"]),
            data_origin=str(data.get("data_origin", "simulation")),
            policy_version=data.get("policy_version"),
            selected_definition_id=data.get("selected_definition_id"),
            selected_version=data.get("selected_version"),
        )


class SimulationWorld:
    """Deterministic world container.

    Example:
        world = SimulationWorld(run)
        creator = world.create_creator()
        fan = world.create_fan(creator)
        content = world.create_content(creator)
        opp = world.create_opportunity(creator, fan, content)
    """

    def __init__(self, run: SimulationRun) -> None:
        if not isinstance(run, SimulationRun):
            raise ValueError("run must be SimulationRun")
        self.run = run
        self.clock = SimulationClock(run.simulated_start)
        # Identity bound to run seed
        self.identity = SimulationIdentity.create(
            simulation_id=run.simulation_id, seed=run.seed, creator_counter=0
        )
        self._creators: list[SyntheticCreator] = []
        self._fans: list[SyntheticFan] = []
        self._contents: list[SyntheticContent] = []
        self._opportunities: list[SyntheticOpportunity] = []
        # Counters for deterministic per-domain hashing
        self._fan_counter = 0
        self._content_counter = 0
        self._opportunity_counter = 0
        self._creator_counter = 1  # 0 used for world.identity creator
        # Ensure primary creator exists deterministically?
        # World starts empty; create_creator() yields same as identity synthetic_creator but next.

    @property
    def creators(self) -> tuple[SyntheticCreator, ...]:
        return tuple(self._creators)

    @property
    def fans(self) -> tuple[SyntheticFan, ...]:
        return tuple(self._fans)

    @property
    def contents(self) -> tuple[SyntheticContent, ...]:
        return tuple(self._contents)

    @property
    def opportunities(self) -> tuple[SyntheticOpportunity, ...]:
        return tuple(self._opportunities)

    def create_creator(self) -> SyntheticCreator:
        """Deterministic creator creation."""
        # Use identity hash to derive creator_id
        cid = (
            self.identity.synthetic_creator_id
            if not self._creators
            else self._derive_creator_id(self._creator_counter)
        )
        self._creator_counter += 1
        creator = SyntheticCreator(creator_id=int(cid), simulation_id=self.run.simulation_id)
        self._creators.append(creator)
        return creator

    def _derive_creator_id(self, counter: int) -> int:
        payload = f"{self.run.seed}:{self.run.simulation_id}:creator:{counter}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        mod = 100_000
        rng = int(digest[:8], 16) % mod
        return 900_000 + rng

    def create_fan(self, creator: SyntheticCreator) -> SyntheticFan:
        if not isinstance(creator, SyntheticCreator):
            raise ValueError("creator must be SyntheticCreator")
        self._fan_counter += 1
        fan_id = self.identity.synthetic_fan_id(self._fan_counter)
        # Ensure fan's creator is parent
        fan = SyntheticFan(
            fan_id=int(fan_id),
            creator_id=int(creator.creator_id),
            simulation_id=self.run.simulation_id,
        )
        self._fans.append(fan)
        return fan

    def create_content(
        self,
        creator: SyntheticCreator,
        *,
        offer_type: str = "SMALL_BUNDLE",
        price_minor: int = 1999,
        currency: str = "USD",
        vault_ids: tuple[str, ...] | list[str] | None = None,
        mapped_drop_ids: tuple[str, ...] | list[str] | None = None,
    ) -> SyntheticContent:
        if not isinstance(creator, SyntheticCreator):
            raise ValueError("creator must be SyntheticCreator")
        self._content_counter += 1
        # deterministic content id via identity vault
        vault = (
            tuple(vault_ids)
            if vault_ids is not None
            else (f"V{self._content_counter}", f"V{self._content_counter + 10}")
        )
        # normalize vault uniqueness
        vault = tuple(v.strip() for v in vault if isinstance(v, str) and v.strip())
        if not vault:
            vault = (self.identity.synthetic_vault_id(self._content_counter),)
        mapped = (
            tuple(mapped_drop_ids)
            if mapped_drop_ids is not None
            else (f"drop_{self._content_counter}",)
        )
        # synthetic content id maps to definition stable key
        content_id = self.identity.synthetic_vault_id(self._content_counter)
        # Use stable but distinct per creator/content
        content = SyntheticContent(
            content_id=content_id,
            creator_id=int(creator.creator_id),
            simulation_id=self.run.simulation_id,
            offer_type=offer_type,
            price_minor=int(price_minor),
            currency=currency,
            vault_ids=tuple(vault),
            mapped_drop_ids=tuple(mapped),
        )
        self._contents.append(content)
        return content

    def create_opportunity(
        self,
        creator: SyntheticCreator,
        fan: SyntheticFan,
        content: SyntheticContent,
        *,
        conversation: SyntheticConversationContext | None = None,
        evaluated_at: datetime | None = None,
        history_state: Any | None = None,
        conversation_state: Any | None = None,
    ) -> SyntheticOpportunity:
        """Create synthetic opportunity with deterministic snapshot.

        Validates creator isolation: fan.creator_id == creator.creator_id and content.creator_id == creator.creator_id
        history_state / conversation_state are optional observable wiring (Phase 7.1) — when provided they populate
        fan/history/conversation counts deterministically; when None zero defaults kept for backward compat.
        """
        if not isinstance(creator, SyntheticCreator):
            raise ValueError("creator must be SyntheticCreator")
        if not isinstance(fan, SyntheticFan):
            raise ValueError("fan must be SyntheticFan")
        if not isinstance(content, SyntheticContent):
            raise ValueError("content must be SyntheticContent")
        if int(fan.creator_id) != int(creator.creator_id):
            raise ValueError("Fan cannot belong to different creator (creator isolation)")
        if int(content.creator_id) != int(creator.creator_id):
            raise ValueError("Content cannot belong to different creator")
        self._opportunity_counter += 1
        opp_id = self.identity.synthetic_opportunity_id(self._opportunity_counter)
        gen = self.identity.synthetic_generation_id(int(opp_id))
        ts = evaluated_at or self.clock.current_time()
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware datetime")
        # Build decision_snapshot via builder (import lazily to avoid cycle)
        from simulation.snapshot import build_decision_snapshot

        snapshot = build_decision_snapshot(
            creator=creator,
            fan=fan,
            content=content,
            conversation=conversation or SyntheticConversationContext(),
            evaluated_at=ts,
            history_state=history_state,
            conversation_state=conversation_state,
        )
        # Derive selected identity for ledger convenience
        selected = snapshot.get("selected") or {}
        opp = SyntheticOpportunity(
            opportunity_id=int(opp_id),
            creator_id=int(creator.creator_id),
            fan_id=int(fan.fan_id),
            content_id=str(content.content_id),
            evaluated_at=ts,
            generation_id=gen,
            decision_snapshot=snapshot,
            policy_version=snapshot.get("ranking", {}).get("policy_version")
            if isinstance(snapshot.get("ranking"), dict)
            else "v1",
            selected_definition_id=int(selected.get("definition_id"))
            if isinstance(selected.get("definition_id"), int)
            else None,
            selected_version=int(selected.get("version"))
            if isinstance(selected.get("version"), int)
            else None,
        )
        self._opportunities.append(opp)
        return opp

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "clock": self.clock.to_dict(),
            "creators": [c.to_dict() for c in self._creators],
            "fans": [f.to_dict() for f in self._fans],
            "contents": [c.to_dict() for c in self._contents],
            "opportunities": [o.to_dict() for o in self._opportunities],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationWorld:
        run = SimulationRun.from_dict(data["run"])
        world = cls(run)
        # Restore clock
        from simulation.clock import SimulationClock as SC

        world.clock = SC.from_dict(data["clock"])
        # Restore collections without re-deriving
        world._creators = [SyntheticCreator.from_dict(d) for d in data.get("creators", [])]
        world._fans = [SyntheticFan.from_dict(d) for d in data.get("fans", [])]
        world._contents = [SyntheticContent.from_dict(d) for d in data.get("contents", [])]
        world._opportunities = [
            SyntheticOpportunity.from_dict(d) for d in data.get("opportunities", [])
        ]
        # Counters set to len to keep determinism for next creates (but restored world not strictly deterministic to re-create same IDs as original run's next counter — ok)
        world._fan_counter = len(world._fans)
        world._content_counter = len(world._contents)
        world._opportunity_counter = len(world._opportunities)
        world._creator_counter = len(world._creators) + 1
        return world
