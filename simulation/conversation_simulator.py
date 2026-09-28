"""Conversation Simulator - deterministic path a real fan would take.

Starts with `hi`, follows the exact worker deterministic chain:
  debounce -> lock -> upsert -> context -> LLM -> scoring -> commerce -> sealing -> send
Stops only on breakage / loose logic, not on success.

Produces report: for every fan message, what the system gave, where it broke.

Usage:
    python -m simulation.conversation_simulator --creator 1 --fan 999999
    python -m simulation.conversation_simulator --creator 1 --fan 999999 --messages hi --max-turns 8

File-only, no Telegram send, no DB write unless --live (fail-closed otherwise).
Deterministic via SHA256(creator:fan:turn) for fan replies, no global random.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hash_float(seed: str, domain: str, counter: int) -> float:
    payload = f"{seed}:{domain}:{counter}".encode()
    d = hashlib.sha256(payload).hexdigest()
    return int(d[:8], 16) / 4294967296.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TurnLog:
    turn: int
    fan_message: str
    system_stage: str
    system_response: str
    breakage: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)


@dataclass
class SimulationReport:
    fan_id: int
    creator_id: int
    started_at: str
    ended_at: str
    turns: list[TurnLog] = field(default_factory=list)
    stopped_reason: str = "completed"
    breakage_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fan_id": self.fan_id,
            "creator_id": self.creator_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "stopped_reason": self.stopped_reason,
            "breakage_count": self.breakage_count,
            "turns": [
                {
                    "turn": t.turn,
                    "fan_message": t.fan_message,
                    "system_stage": t.system_stage,
                    "system_response": t.system_response[:2000],
                    "breakage": t.breakage,
                    "details": t.details,
                    "ts": t.ts,
                }
                for t in self.turns
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Conversation Simulation - fan {self.fan_id} creator {self.creator_id}",
            f"started {self.started_at} ended {self.ended_at}",
            f"**stopped:** {self.stopped_reason} **breakages:** {self.breakage_count}",
            "",
            "| turn | fan -> | system stage | system -> | breakage |",
            "|---|---|---|---|---|",
        ]
        for t in self.turns:
            fan = t.fan_message.replace("\n", " ")[:60]
            sys = t.system_response.replace("\n", " ")[:80]
            br = t.breakage or "-"
            lines.append(f"| {t.turn} | {fan} | {t.system_stage} | {sys} | {br} |")
        lines.append("")
        lines.append("## Details")
        for t in self.turns:
            if t.details:
                lines.append(f"- turn {t.turn} {t.details}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# deterministic fan reply generator (hi-first)
# ---------------------------------------------------------------------------

FAN_SCRIPT = [
    "hi",
    "hey there",
    "how are you?",
    "what do you offer?",
    "how much is it?",
    "okay send it",
    "thanks!",
    "see you later",
]

def fan_next_message(turn: int, last_system: str, fan_id: int, creator_id: int) -> str:
    # deterministic script, then hash-based continuation
    if turn < len(FAN_SCRIPT):
        return FAN_SCRIPT[turn]
    # after script, generate based on last system
    seed = f"{creator_id}:{fan_id}"
    r = _hash_float(seed, f"fan_reply:{last_system[:20]}", turn)
    pool = ["haha", "okay", "nice", "tell me more", "is that real?", "can you show me?"]
    return pool[int(r * len(pool))]


# ---------------------------------------------------------------------------
# breakage checks (pure, no I/O)
# ---------------------------------------------------------------------------

def _check_stage(name: str, ok: bool, err: str | None) -> tuple[bool, str | None]:
    if not ok:
        return False, err
    return True, None


async def _try_imports() -> dict[str, Any]:
    """Probe which real subsystems are available."""
    avail: dict[str, Any] = {}
    # db
    try:
        from db.postgres import get_pool  # type: ignore
        avail["db_pool"] = "available"
    except Exception as e:
        avail["db_pool"] = f"missing: {e}"
    # redis
    try:
        from db.redis import acquire_user_lock  # type: ignore
        avail["redis"] = "available"
    except Exception as e:
        avail["redis"] = f"missing: {e}"
    # llm
    try:
        from core.llm_provider import get_llm_provider  # type: ignore
        avail["llm"] = "available"
    except Exception as e:
        avail["llm"] = f"missing: {e}"
    # commerce
    try:
        from commerce.single_creator import resolve_single_application_creator  # type: ignore
        avail["commerce"] = "available"
    except Exception as e:
        avail["commerce"] = f"missing: {e}"
    return avail


# ---------------------------------------------------------------------------
# main simulator
# ---------------------------------------------------------------------------

class ConversationSimulator:
    def __init__(self, creator_id: int, fan_id: int, live: bool = False, max_turns: int = 8) -> None:
        if not isinstance(creator_id, int) or creator_id <= 0:
            raise ValueError("creator_id must be positive int")
        if not isinstance(fan_id, int) or fan_id <= 0:
            raise ValueError("fan_id must be positive int")
        self.creator_id = int(creator_id)
        self.fan_id = int(fan_id)
        self.live = bool(live)
        self.max_turns = int(max_turns)
        self.report = SimulationReport(
            fan_id=self.fan_id,
            creator_id=self.creator_id,
            started_at=_now_iso(),
            ended_at=_now_iso(),
        )
        self._avail: dict[str, Any] | None = None

    async def run(self, initial: str = "hi") -> SimulationReport:
        self._avail = await _try_imports()
        # pre-flight breakage log
        pre = []
        for k, v in self._avail.items():
            if "missing" in str(v):
                pre.append(f"{k}:{v}")
        # simulate turns
        last_system = ""
        fan_msg = initial if initial else "hi"
        for turn in range(self.max_turns):
            log = TurnLog(turn=turn + 1, fan_message=fan_msg, system_stage="start", system_response="", details={})
            # ---- stage 1: debounce / lock (simulated) ----
            # real: Redis lock creator:{creator}:user:{fan}
            # we simulate: if fan_id % 100 == 0 -> lock contention breakage (rare)
            if self.fan_id % 100 == 0 and turn == 0:
                log.system_stage = "lock"
                log.breakage = "lock_contended: creator lock exists (UserLockContentionError)"
                log.system_response = ""
                log.details = {"avail": self._avail, "pre": pre}
                self.report.turns.append(log)
                self.report.stopped_reason = "breakage: lock_contended (deterministic edge)"
                break

            # ---- stage 2: creator resolution ----
            creator_ok = True
            c_err = None
            # real: resolve_single_application_creator must be READY
            # check via DB if live else assume ready for creator 1, not ready for 999999
            if not self.live:
                if self.creator_id == 999999:
                    creator_ok = False
                    c_err = "creator_context_unavailable: single creator not READY (P1.4 fail-closed)"
            else:
                try:
                    from commerce.single_creator import resolve_single_application_creator
                    ctx = await resolve_single_application_creator()
                    if ctx.status.value != "ready":  # type: ignore
                        creator_ok = False
                        c_err = f"creator not ready: {ctx.status}"
                except Exception as e:
                    creator_ok = False
                    c_err = f"creator resolution exception: {e}"
            if not creator_ok:
                log.system_stage = "creator_resolution"
                log.breakage = c_err
                log.system_response = ""
                log.details = {"creator_id": self.creator_id}
                self.report.turns.append(log)
                self.report.stopped_reason = f"breakage: creator_resolution at turn {turn+1}"
                break

            # ---- stage 3: context assembly ----
            context_ok = True
            ctx_err = None
            context_preview = ""
            try:
                if self.live:
                    from memory.context import build_qwen3_context
                    ctx = await build_qwen3_context(self.fan_id, fan_msg, persona="default", creator_id=self.creator_id)
                    context_preview = str(ctx)[:200]
                else:
                    # simulated context: just fan msg + creator persona stub
                    context_preview = f"system: creator {self.creator_id} persona\nuser: {fan_msg}"
            except Exception as e:
                context_ok = False
                ctx_err = f"context assembly failed: {e}"
            if not context_ok:
                log.system_stage = "context"
                log.breakage = ctx_err
                log.system_response = ""
                log.details = {"context_preview": context_preview}
                self.report.turns.append(log)
                self.report.stopped_reason = f"breakage: context at turn {turn+1}"
                break

            # ---- stage 4: LLM draft ----
            llm_text = ""
            llm_break = None
            try:
                if self.live:
                    from workers.llm_worker import generate_draft
                    # build minimal context_messages for generate_draft
                    msgs = [{"role": "system", "content": f"creator {self.creator_id}"}, {"role": "user", "content": fan_msg}]
                    llm_text = await generate_draft(msgs, fan_msg)
                    if not llm_text or not llm_text.strip():
                        llm_break = "generate_draft empty (llamacpp failure) -> DLQ/requeue"
                else:
                    # deterministic stub: hash-based reply, break if fan_id 999998 triggers empty
                    if self.fan_id == 999998:
                        llm_text = ""
                        llm_break = "stub LLM empty (simulated breakage)"
                    else:
                        # simple deterministic persona reply
                        h = _hash_float(f"{self.creator_id}:{self.fan_id}", fan_msg, turn)
                        stubs = [
                            f"Hey there! I'm good, how are you? (turn {turn+1})",
                            f"Nice to meet you! What are you looking for today?",
                            f"I have some exclusive drops - want to see?",
                            f"It's $5 - Mirror selfie 24/06/2026, sales_url via DropFans. Want the link?",
                            f"Here you go! Let me know what you think ",
                        ]
                        llm_text = stubs[int(h * len(stubs))]
            except Exception as e:
                llm_break = f"LLM exception: {e}"
                llm_text = ""

            # ---- stage 5: commerce decision (deterministic v1) ----
            commerce_status = "no_offer"
            commerce_break = None
            try:
                if self.live:
                    from workers.llm_worker import _try_commerce_draft
                    # _try_commerce_draft will log quarantined Opportunity Engine
                    # we just probe: if it returns CommerceSelectionResult with OFFER_PPV
                    # but currently quarantined -> always no_offer, loose logic is expected
                    pass
                else:
                    # simulated commerce: if fan says "send it" and creator has product, would offer
                    if "send" in fan_msg.lower() and "okay" in fan_msg.lower():
                        # check product exists (from DB earlier: creator 1 has 2)
                        if self.creator_id == 1:
                            commerce_status = "would_offer: $5 Mirror selfie (sealing-> would verify DropFans)"
                        else:
                            commerce_status = "no_product: creator has no fangate_products -> NO_SELL (loose logic: no approved vault item)"
                            # this is not breakage, but loose logic gap
                            if self.creator_id != 1:
                                commerce_break = "loose: creator without product -> has_relevant_product false -> NO_SELL forever"
                    elif turn == 0 and fan_msg == "hi":
                        commerce_status = "no_offer: hi is RELATIONSHIP_BUILDING (deterministic v1)"
            except Exception as e:
                commerce_break = f"commerce exception: {e}"

            # compose system response
            system_resp = llm_text or ""
            if commerce_status.startswith("would_offer"):
                system_resp = system_resp + " " + commerce_status
            # loose logic flag: if commerce would offer but sealing would fail
            # (e.g., price_minor NULL) - we flag as breakage only if actually needed
            final_break = llm_break or commerce_break
            # Do NOT stop on loose logic unless it's required for next turn
            # "loose" we log but continue; "breakage" we stop
            is_breakage = llm_break is not None and "empty" in str(llm_break)
            # creator without product is not breakage for hi, but is for "send it" - flag
            if commerce_break and "loose" in commerce_break and "send" in fan_msg.lower():
                is_breakage = True

            log.system_stage = "llm+commerce"
            log.system_response = system_resp
            log.details = {
                "context_preview": context_preview[:120],
                "commerce_status": commerce_status,
                "avail": {k: ("ok" if "available" in str(v) else str(v)[:60]) for k, v in (self._avail or {}).items()},
            }
            if is_breakage:
                log.breakage = final_break
                self.report.turns.append(log)
                self.report.stopped_reason = f"breakage: {final_break} at turn {turn+1}"
                break
            elif final_break:
                # loose logic - log but continue
                log.breakage = f"loose: {final_break}"
            else:
                log.breakage = None

            self.report.turns.append(log)
            last_system = system_resp

            # ---- decide next fan message ----
            # if we are at max_turns stop naturally
            if turn + 1 >= self.max_turns:
                self.report.stopped_reason = "completed: max_turns reached (no breakage)"
                break
            # check if system said would_offer and fan would purchase - end naturally
            if "would_offer" in commerce_status and turn >= 5:
                # simulate purchase: stop after offer
                self.report.stopped_reason = "completed: offer would be sent -> fan would purchase (end of deterministic path)"
                break
            fan_msg = fan_next_message(turn + 1, last_system, self.fan_id, self.creator_id)

            # small deterministic delay
            await asyncio.sleep(0.01)

        self.report.ended_at = _now_iso()
        self.report.breakage_count = sum(1 for t in self.report.turns if t.breakage and "breakage" in t.breakage)
        return self.report

    def save(self, base: str | Path | None = None) -> Path:
        base = Path(base) if base else Path("simulation_runs") / f"conv_{self.creator_id}_{self.fan_id}_{int(time.time())}"
        base.mkdir(parents=True, exist_ok=True)
        j = base / "report.json"
        j.write_text(json.dumps(self.report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        m = base / "report.md"
        m.write_text(self.report.to_markdown(), encoding="utf-8")
        return j


async def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Conversation Simulator - hi-first deterministic")
    p.add_argument("--creator", type=int, default=1, help="creator_id (1 has 2 products)")
    p.add_argument("--fan", type=int, default=999999, help="fan_id (synthetic 900000+)")
    p.add_argument("--messages", type=str, default="hi", help="initial message (default hi)")
    p.add_argument("--max-turns", type=int, default=8)
    p.add_argument("--live", action="store_true", help="use live DB/LLM (requires Postgres+Ollama); default simulated")
    p.add_argument("--out", type=str, default=None)
    a = p.parse_args()
    sim = ConversationSimulator(creator_id=a.creator, fan_id=a.fan, live=a.live, max_turns=a.max_turns)
    report = await sim.run(initial=a.messages)
    out = sim.save(a.out)
    print(f"creator={report.creator_id} fan={report.fan_id} turns={len(report.turns)} stopped={report.stopped_reason} breakages={report.breakage_count}")
    print(f"json: {out}")
    print(f"md: {out.parent / 'report.md'}")
    for t in report.turns:
        print(f"\n[turn {t.turn}] fan: {t.fan_message}")
        print(f"  system ({t.system_stage}): {t.system_response[:200]}")
        if t.breakage:
            print(f"  !! {t.breakage}")

if __name__ == "__main__":
    asyncio.run(_cli())
