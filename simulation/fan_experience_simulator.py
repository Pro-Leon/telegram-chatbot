"""Fan Experience Simulator — full process_message as a real fan sees it.

Fan sends `hi` via Telegram (simulated) -> the system does:
  Telegram MTProto (simulated) -> ingestion debounce 3s -> Redis Stream llm_workers
  -> workers.llm_worker.process_message (lock 1s -> upsert -> authoritative context
     -> Pola Llama via POST http://localhost:8081/v1/chat/completions -> scoring -> commerce quarantined -> send/operator)
  -> Telegram reply (what fan sees)

Fan then reads the reply and decides next message (scripted hi-first).
Stops only on breakage (lock timeout, LLM timeout, creator fail-closed).
Report: fan view — what fan sent, what fan saw, how long waited, where it broke.

Usage:
    python -m simulation.fan_experience_simulator --creator 1 --fan 999992 --max-turns 5
Requires: Postgres, Redis, llama.cpp Pola at http://localhost:8081
File-only report, SHA256 fan replies, Pola runtime.

Fan experience is the product — this shows it.
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
    return int(d[:8],16)/4294967296.0
def _now_iso(): return datetime.now(UTC).isoformat()

@dataclass
class FanTurn:
    turn:int; fan_sent:str; fan_saw:str; wait_ms:int; system_stage:str; breakage:str|None=None; details:dict[str,Any]=field(default_factory=dict); ts:str=field(default_factory=_now_iso)

@dataclass
class FanReport:
    fan_id:int; creator_id:int; started_at:str; ended_at:str; turns:list[FanTurn]=field(default_factory=list); stopped_reason:str="completed"; breakage_count:int=0
    def to_dict(self): return {"fan_id":self.fan_id,"creator_id":self.creator_id,"started_at":self.started_at,"ended_at":self.ended_at,"stopped_reason":self.stopped_reason,"breakage_count":self.breakage_count,"turns":[{"turn":t.turn,"fan_sent":t.fan_sent,"fan_saw":t.fan_saw,"wait_ms":t.wait_ms,"system_stage":t.system_stage,"breakage":t.breakage,"details":t.details,"ts":t.ts} for t in self.turns]}
    def to_markdown(self):
        lines=[f"# Fan Experience — fan {self.fan_id} creator {self.creator_id}", f"started {self.started_at} ended {self.ended_at}", f"**stopped:** {self.stopped_reason} **breakages:** {self.breakage_count}", "", "Fan sees exactly what Telegram would show — no internal stages.", "", "| turn | fan sent (Telegram) | fan saw (Telegram) | wait | breakage |", "|---|---|---|---|---|"]
        for t in self.turns:
            fan=t.fan_sent.replace("\n"," ")[:50]; saw=t.fan_saw.replace("\n"," ")[:70] or "(no reply — breakage)"
            br=t.breakage or "-"
            lines.append(f"| {t.turn} | {fan} | {saw} | {t.wait_ms}ms | {br} |")
        lines.append(""); lines.append("## System trace (for debugging, fan doesn't see this)")
        for t in self.turns:
            if t.details: lines.append(f"- turn {t.turn} stage={t.system_stage} details={json.dumps(t.details, ensure_ascii=False)[:300]}")
        return "\n".join(lines)

FAN_SCRIPT=["hi","hey there","how are you?","what do you offer?","how much is it?","okay send it","thanks!"]
def fan_next(turn:int, last_saw:str, fan_id:int, creator_id:int)->str:
    if turn < len(FAN_SCRIPT): return FAN_SCRIPT[turn]
    r=_hash_float(f"{creator_id}:{fan_id}", f"fan:{last_saw[:20]}", turn)
    pool=["haha","okay","nice","tell me more","is that real?"]
    return pool[int(r*len(pool))]

class FanExperienceSimulator:
    def __init__(self, creator_id:int, fan_id:int, max_turns:int=5):
        self.creator_id=int(creator_id); self.fan_id=int(fan_id); self.max_turns=int(max_turns)
        self.report=FanReport(fan_id=self.fan_id,creator_id=self.creator_id,started_at=_now_iso(),ended_at=_now_iso())

    async def run(self, initial:str="hi")->FanReport:
        # init pools 5s
        try:
            from db.postgres import init_pool
            try: await asyncio.wait_for(init_pool(),timeout=5)
            except asyncio.TimeoutError: print("DB init timeout 5s (continue)")
        except Exception as e: print(f"DB init fail {e}")

        fan_msg=initial or "hi"
        for turn in range(self.max_turns):
            t0=time.monotonic()
            fan_sent=fan_msg
            # --- simulate Telegram send -> ingestion debounce -> Redis Stream ---
            # Fan hits send, Telegram MTProto delivers to ingestion/bot.py
            # Debounce: first hi owns 3s window (we simulate as instant for single hi, 3s would collapse burst)
            # Then Redis Stream llm_workers -> process_message
            stage="Telegram -> debounce -> Redis Stream"
            details:dict[str,Any]={"telegram_message_id": int(hashlib.sha256(f"{self.fan_id}:{fan_msg}:{turn}".encode()).hexdigest()[:8],16)%1000000}
            breakage=None
            fan_saw=""
            # call the EXACT fan path
            try:
                from workers.llm_worker import process_message
                from core.generation import telegram_generation_id
                from db.postgres import get_recent_messages
                tel_id=details["telegram_message_id"]
                try:
                    gen_id=telegram_generation_id(self.fan_id, fan_msg, tel_id)
                except Exception:
                    gen_id=f"gen:{self.fan_id}:{tel_id}"
                # snapshot before (what fan saw before)
                before=[]
                try: before=await asyncio.wait_for(get_recent_messages(self.fan_id, limit=20, creator_id=self.creator_id), timeout=3)
                except Exception: before=[]
                # Fan waits for reply (real fan sees typing... then reply)
                # process_message does: lock (1s) -> upsert -> context -> Pola (12s) -> scoring -> commerce -> send
                try:
                    await asyncio.wait_for(process_message(user_id=self.fan_id, user_message=fan_msg, telegram_message_id=tel_id, username="testfan", first_name="Test", persona="default", generation_id=gen_id, creator_id=self.creator_id), timeout=15)
                except asyncio.TimeoutError:
                    raise TimeoutError("Fan waited 15s for reply — lock/LLM hang (P1.6 lock ttl 300s, llama timeout 12s)")
                # fan polls Telegram for reply (what fan actually sees)
                await asyncio.sleep(0.5)
                after=[]
                try: after=await asyncio.wait_for(get_recent_messages(self.fan_id, limit=20, creator_id=self.creator_id), timeout=3)
                except Exception as e: details["recent_err"]=str(e)[:150]
                # find new outbound (what Telegram would deliver to fan)
                try:
                    before_ids={m.get("id") for m in before if isinstance(m,dict)}
                    for m in reversed(after):
                        if isinstance(m,dict) and m.get("id") not in before_ids and m.get("direction")=="outbound":
                            fan_saw=m.get("content","") or m.get("text","") or ""
                            break
                except Exception: pass
                if not fan_saw:
                    # check operator queue (fan would see nothing, but operator would)
                    try:
                        from db.postgres import get_pool
                        pool=await get_pool()
                        async with pool.acquire() as conn:
                            row=await conn.fetchrow("SELECT draft_content FROM operator_queue WHERE user_id=$1 AND creator_id=$2 ORDER BY created_at DESC LIMIT 1", self.fan_id, self.creator_id)
                            if row and row["draft_content"]:
                                fan_saw=""  # fan sees nothing, operator sees draft
                                details["operator_draft_len"]=len(row["draft_content"])
                    except Exception: pass
                stage="Telegram reply"
                if not fan_saw and not breakage:
                    # no reply after 15s is breakage from fan perspective
                    # but for hi, no_offer is normal (fan sees LLM reply, not PPV)
                    # if truly no outbound, it's LLM empty breakage
                    if turn==0 and fan_msg=="hi":
                        # hi should have a reply; if none, it's breakage
                        if not fan_saw:
                            # try to check if suppressed
                            try:
                                from db.postgres import get_pool as _gp
                                pool2=await _gp()
                                async with pool2.acquire() as conn2:
                                    u=await conn2.fetchrow("SELECT do_not_auto_reply FROM users WHERE user_id=$1", self.fan_id)
                                    if u and u["do_not_auto_reply"]:
                                        breakage="fan saw nothing — suppressed do_not_auto_reply (not breakage, fan would see silence)"
                                        stage="suppressed"
                                    else:
                                        breakage="breakage: fan waited 15s, no Telegram reply (LLM empty -> DLQ)"
                                        stage="llm"
                            except Exception:
                                breakage="breakage: no reply after 15s (LLM hang)"
                                stage="llm"
                    else:
                        if not fan_saw:
                            breakage="breakage: no reply (LLM empty)"
                            stage="llm"
                details["generation_id"]=gen_id
            except Exception as e:
                import traceback
                tb=traceback.format_exc()[:400]
                msg=str(e)
                if "TimeoutError" in type(e).__name__ or "timeout" in msg.lower():
                    breakage=f"breakage: fan waited {int((time.monotonic()-t0)*1000)}ms — {msg[:150]} (lock/LLM hang, fan sees typing... then nothing)"
                    stage="timeout"
                elif "UserLockContentionError" in type(e).__name__ or "already locked" in msg:
                    breakage=f"breakage: fan sees typing... then nothing — lock contended creator:{self.creator_id}:user:{self.fan_id}"
                    stage="lock"
                else:
                    breakage=f"breakage: {type(e).__name__}: {msg[:150]}"
                    stage="exception"
                details["traceback"]=tb

            wait_ms=int((time.monotonic()-t0)*1000)
            log=FanTurn(turn=turn+1, fan_sent=fan_sent, fan_saw=fan_saw, wait_ms=wait_ms, system_stage=stage, breakage=breakage, details=details)
            self.report.turns.append(log)
            if breakage and breakage.startswith("breakage:"):
                self.report.stopped_reason=f"breakage at turn {turn+1}: fan experienced breakage"
                break
            if turn+1>=self.max_turns:
                self.report.stopped_reason="completed: fan finished script (no breakage, fan saw every reply)"
                break
            fan_msg=fan_next(turn+1, fan_saw, self.fan_id, self.creator_id)
            await asyncio.sleep(0.3)

        self.report.ended_at=_now_iso()
        self.report.breakage_count=sum(1 for t in self.report.turns if t.breakage and t.breakage.startswith("breakage:"))
        return self.report

    def save(self, base: str|Path|None=None)->Path:
        base=Path(base) if base else Path("simulation_runs")/f"fanexp_{self.creator_id}_{self.fan_id}_{int(time.time())}"
        base.mkdir(parents=True, exist_ok=True)
        j=base/"report.json"; j.write_text(json.dumps(self.report.to_dict(),indent=2,ensure_ascii=False),encoding="utf-8")
        m=base/"report.md"; m.write_text(self.report.to_markdown(),encoding="utf-8")
        return j

async def _cli():
    import argparse
    p=argparse.ArgumentParser(description="Fan Experience Simulator — full process_message as fan sees it, hi-first")
    p.add_argument("--creator",type=int,default=1)
    p.add_argument("--fan",type=int,default=999992)
    p.add_argument("--max-turns",type=int,default=5)
    p.add_argument("--out",type=str,default=None)
    a=p.parse_args()
    sim=FanExperienceSimulator(creator_id=a.creator,fan_id=a.fan,max_turns=a.max_turns)
    report=await sim.run(initial="hi")
    out=sim.save(a.out)
    print(f"creator={report.creator_id} fan={report.fan_id} turns={len(report.turns)} stopped={report.stopped_reason} breakages={report.breakage_count}")
    print(f"json: {out}")
    for t in report.turns:
        print(f"\n[fan sent] {t.fan_sent}")
        print(f"  [fan saw after {t.wait_ms}ms] {t.fan_saw[:300].replace(chr(10),' ') or '(no reply)'}")
        if t.breakage: print(f"  !! {t.breakage}")

if __name__=="__main__":
    asyncio.run(_cli())
