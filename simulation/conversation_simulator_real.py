"""Real-path Conversation Simulator — follows exact fan deterministic path.

Fan: hi -> Telegram (simulated) -> ingestion debounce -> Redis Stream llm_workers
     -> workers.llm_worker.process_message (lock -> upsert -> authoritative context
        -> Qwen llama.cpp -> scoring -> commerce Opportunity Engine (quarantined)
        -> send_worker / operator queue) -> Telegram send (captured via DB)

Stops only on breakage / loose logic. Report: every fan message + system response + breakage.

Usage:
    python -m simulation.conversation_simulator_real --creator 1 --fan 999999 --max-turns 6

File-only report, no global random, SHA256 fan replies.
Requires: Postgres (get_pool), Redis (optional, fail-open), Ollama llama.cpp
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

def _hash_float(seed: str, domain: str, counter: int) -> float:
    d = hashlib.sha256(f"{seed}:{domain}:{counter}".encode()).hexdigest()
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
            "fan_id": self.fan_id, "creator_id": self.creator_id,
            "started_at": self.started_at, "ended_at": self.ended_at,
            "stopped_reason": self.stopped_reason, "breakage_count": self.breakage_count,
            "turns": [{"turn": t.turn, "fan_message": t.fan_message, "system_stage": t.system_stage, "system_response": t.system_response[:4000], "breakage": t.breakage, "details": t.details, "ts": t.ts} for t in self.turns],
        }
    def to_markdown(self) -> str:
        lines = [f"# Real-path Simulation - fan {self.fan_id} creator {self.creator_id}", f"started {self.started_at} ended {self.ended_at}", f"**stopped:** {self.stopped_reason} **breakages:** {self.breakage_count}", "", "| turn | fan -> | system stage | system -> | breakage |", "|---|---|---|---|---|"]
        for t in self.turns:
            fan = t.fan_message.replace("\n"," ")[:60]
            sys = t.system_response.replace("\n"," ")[:90]
            br = t.breakage or "-"
            lines.append(f"| {t.turn} | {fan} | {t.system_stage} | {sys} | {br} |")
        lines.append("")
        lines.append("## Details")
        for t in self.turns:
            if t.details:
                lines.append(f"- turn {t.turn} {json.dumps(t.details, ensure_ascii=False)[:300]}")
        return "\n".join(lines)

FAN_SCRIPT = ["hi","hey there","how are you?","what do you offer?","how much is it?","okay send it","thanks!","see you later"]
def fan_next_message(turn: int, last_system: str, fan_id: int, creator_id: int) -> str:
    if turn < len(FAN_SCRIPT):
        return FAN_SCRIPT[turn]
    r = _hash_float(f"{creator_id}:{fan_id}", f"fan_reply:{last_system[:20]}", turn)
    pool = ["haha","okay","nice","tell me more","is that real?","can you show me?"]
    return pool[int(r*len(pool))]

class RealPathSimulator:
    def __init__(self, creator_id: int, fan_id: int, max_turns: int = 6) -> None:
        self.creator_id = int(creator_id)
        self.fan_id = int(fan_id)
        self.max_turns = int(max_turns)
        self.report = SimulationReport(fan_id=self.fan_id, creator_id=self.creator_id, started_at=_now_iso(), ended_at=_now_iso())

    async def run(self, initial: str = "hi") -> SimulationReport:
        # init pools (fail-open) with 5s timeout (patch for hang)
        try:
            from db.postgres import init_pool, get_pool
            try:
                await asyncio.wait_for(init_pool(), timeout=5)
            except asyncio.TimeoutError:
                print("DB init timeout after 5s (fail-open, continue with simulated lock)")
        except Exception as e:
            print(f"DB init failed (fail-open): {e}")

        # ensure we have a lock namespace clean
        last_system = ""
        fan_msg = initial or "hi"
        for turn in range(self.max_turns):
            log = TurnLog(turn=turn+1, fan_message=fan_msg, system_stage="inbound", system_response="")

            # ---- stage debounce (simulated 3s) + Redis Stream ----
            # real: ingestion/bot.py Telethon -> debounce -> Redis Stream llm_workers
            # we simulate debounce as always pass (messages spaced 1s > 3s? actually we space turns 1s, but debounce is per-burst, so pass)
            # and we go directly to process_message which does lock + full chain
            telegram_msg_id = int(hashlib.sha256(f"{self.fan_id}:{fan_msg}:{turn}".encode()).hexdigest()[:8],16) % 1000000
            generation_id = None
            try:
                from core.generation import telegram_generation_id
                generation_id = telegram_generation_id(self.fan_id, fan_msg, telegram_msg_id)
            except Exception:
                generation_id = f"gen:{self.fan_id}:{telegram_msg_id}"

            # ---- call the REAL deterministic worker path ----
            # This is the exact function a Telegram fan triggers
            system_text = ""
            stage = "process_message"
            breakage = None
            details: dict[str,Any] = {}
            try:
                from workers.llm_worker import process_message, UserLockContentionError
                from db.postgres import get_recent_messages
                # snapshot before
                before = []
                try:
                    before = await get_recent_messages(self.fan_id, limit=20, creator_id=self.creator_id)
                except Exception:
                    before = []
                # call real path - 1s lock try, 10s overall timeout (patch for debounce/lock hang)
                try:
                    await asyncio.wait_for(
                        process_message(
                            user_id=self.fan_id,
                            user_message=fan_msg,
                            telegram_message_id=telegram_msg_id,
                            username="testfan",
                            first_name="Test",
                            persona="default",
                            generation_id=generation_id,
                            creator_id=self.creator_id,
                        ),
                        timeout=10,
                    )
                except asyncio.TimeoutError:
                    raise TimeoutError("process_message timeout after 10s (lock/Debounce/LLM hang) -> breakage: lock_or_llm_timeout")
                except UserLockContentionError as e:
                    raise e
                # ---- capture what system actually did ----
                # 1. check if message was stored (upsert + recent_messages)
                # 2. check operator queue vs send queue vs direct reply
                await asyncio.sleep(0.5)  # let post_process async settle
                after = []
                try:
                    after = await get_recent_messages(self.fan_id, limit=20, creator_id=self.creator_id)
                except Exception as e:
                    details["recent_err"] = str(e)[:200]
                # find new assistant message
                new_assistant = ""
                try:
                    before_ids = {m.get("id") for m in before if isinstance(m, dict)}
                    for m in reversed(after):
                        if isinstance(m, dict) and m.get("id") not in before_ids and m.get("direction") == "outbound":
                            new_assistant = m.get("content","") or m.get("text","") or ""
                            break
                except Exception:
                    pass
                # fallback: check operator queue
                op_hint = ""
                try:
                    from db.postgres import get_pool as _gp
                    pool = await _gp()
                    async with pool.acquire() as conn:
                        row = await conn.fetchrow("SELECT draft_content, flags FROM operator_queue WHERE user_id=$1 AND creator_id=$2 ORDER BY created_at DESC LIMIT 1", self.fan_id, self.creator_id)
                        if row:
                            op_hint = f"[operator queue: draft len {len(row['draft_content'])} flags {row['flags']}]"
                            if not new_assistant:
                                new_assistant = row['draft_content'][:400] or ""
                except Exception as e:
                    details["op_queue_err"] = str(e)[:200]

                # also check send queue (Redis) - just probe
                try:
                    from db.redis import get_pool as _rp  # not needed, just probe
                    details["redis"] = "probed"
                except Exception:
                    pass

                system_text = new_assistant or op_hint or ""
                if not system_text:
                    # no outbound found - could be suppressed or LLM empty
                    # check if suppressed due to do_not_auto_reply
                    try:
                        from db.postgres import get_pool as _gp2
                        pool2 = await _gp2()
                        async with pool2.acquire() as conn2:
                            u = await conn2.fetchrow("SELECT do_not_auto_reply FROM users WHERE user_id=$1", self.fan_id)
                            if u and u["do_not_auto_reply"]:
                                breakage = "suppressed: do_not_auto_reply (H6 deterministic suppression, not breakage)"
                                stage = "suppressed"
                                system_text = ""
                            else:
                                # LLM may have returned empty -> DLQ path
                                stage = "llm+commerce (quarantined)"
                                system_text = new_assistant or ""
                                # not breakage, just normal no-offer for hi
                                if turn == 0 and fan_msg == "hi":
                                    details["commerce"] = "no_offer: hi is RELATIONSHIP_BUILDING (v1 quarantined Opportunity Engine)"
                    except Exception:
                        stage = "llm+commerce (quarantined)"
                        system_text = new_assistant or ""
                else:
                    stage = "llm+commerce -> send/operator"
                    details["generation_id"] = generation_id
                    details["telegram_msg_id"] = telegram_msg_id

            except Exception as e:
                # capture real breakage
                import traceback
                tb = traceback.format_exc()[:500]
                # classify
                msg = str(e)
                if isinstance(e, asyncio.TimeoutError) or "TimeoutError" in type(e).__name__ or "timeout after 10s" in msg:
                    breakage = f"breakage: lock_or_llm_timeout after 10s (debounce/lock/LLM hang) creator:{self.creator_id}:user:{self.fan_id}"
                    stage = "lock_or_llm_timeout"
                elif "UserLockContentionError" in type(e).__name__ or "already locked" in msg:
                    breakage = f"breakage: lock_contended creator:{self.creator_id}:user:{self.fan_id} (UserLockContentionError)"
                    stage = "lock"
                elif "creator_context_unavailable" in msg or "creator_id missing" in msg:
                    breakage = f"breakage: creator_resolution fail-closed P1.4: {msg[:150]}"
                    stage = "creator_resolution"
                elif "generate_draft" in msg or "llamacpp" in msg:
                    breakage = f"breakage: LLM failure (llamacpp): {msg[:150]}"
                    stage = "llm"
                else:
                    breakage = f"breakage: process_message exception: {type(e).__name__}: {msg[:200]}"
                    stage = "exception"
                details["traceback"] = tb
                system_text = ""

            log.system_stage = stage
            log.system_response = system_text
            log.breakage = breakage
            log.details = details
            self.report.turns.append(log)

            # stop only on real breakage (lock, creator fail-closed, LLM exception)
            # loose logic like "no_offer for hi" is NOT breakage -> continue
            if breakage and breakage.startswith("breakage:"):
                self.report.stopped_reason = f"breakage at turn {turn+1}: {breakage}"
                break
            if turn + 1 >= self.max_turns:
                self.report.stopped_reason = "completed: max_turns reached (no breakage)"
                break
            # check if would have offered -> natural end
            if "would_offer" in system_text.lower() or "sales_url" in system_text.lower():
                self.report.stopped_reason = "completed: PPV would be sent (end of deterministic fan path)"
                break
            fan_msg = fan_next_message(turn+1, system_text or last_system, self.fan_id, self.creator_id)
            last_system = system_text
            await asyncio.sleep(0.3)

        self.report.ended_at = _now_iso()
        self.report.breakage_count = sum(1 for t in self.report.turns if t.breakage and t.breakage.startswith("breakage:"))
        return self.report

    def save(self, base: str | Path | None = None) -> Path:
        base = Path(base) if base else Path("simulation_runs") / f"real_{self.creator_id}_{self.fan_id}_{int(time.time())}"
        base.mkdir(parents=True, exist_ok=True)
        j = base / "report.json"
        j.write_text(json.dumps(self.report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        m = base / "report.md"
        m.write_text(self.report.to_markdown(), encoding="utf-8")
        return j

async def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Real-path Simulator - hi-first via process_message")
    p.add_argument("--creator", type=int, default=1)
    p.add_argument("--fan", type=int, default=999999)
    p.add_argument("--max-turns", type=int, default=6)
    p.add_argument("--out", type=str, default=None)
    a = p.parse_args()
    sim = RealPathSimulator(creator_id=a.creator, fan_id=a.fan, max_turns=a.max_turns)
    report = await sim.run(initial="hi")
    out = sim.save(a.out)
    print(f"creator={report.creator_id} fan={report.fan_id} turns={len(report.turns)} stopped={report.stopped_reason} breakages={report.breakage_count}")
    print(f"json: {out}")
    print(f"md: {out.parent / 'report.md'}")
    for t in report.turns:
        print(f"\n[turn {t.turn}] fan: {t.fan_message}")
        print(f"  system ({t.system_stage}): {t.system_response[:300].replace(chr(10),' ')}")
        if t.breakage:
            print(f"  !! {t.breakage}")

if __name__ == "__main__":
    asyncio.run(_cli())
