"""Real-path Simulator v2 — step-by-step real fan path with 1s lock, no hang.

Fan hi -> debounce -> Redis Stream -> lock (1s try) -> upsert -> context -> LLM (10s) -> scoring -> commerce -> send
Report every stage, stop only on breakage.
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
class TurnLog:
    turn:int; fan_message:str; system_stage:str; system_response:str; breakage:str|None=None; details:dict[str,Any]=field(default_factory=dict); ts:str=field(default_factory=_now_iso)
@dataclass
class SimulationReport:
    fan_id:int; creator_id:int; started_at:str; ended_at:str; turns:list[TurnLog]=field(default_factory=list); stopped_reason:str="completed"; breakage_count:int=0
    def to_dict(self): return {"fan_id":self.fan_id,"creator_id":self.creator_id,"started_at":self.started_at,"ended_at":self.ended_at,"stopped_reason":self.stopped_reason,"breakage_count":self.breakage_count,"turns":[{"turn":t.turn,"fan_message":t.fan_message,"system_stage":t.system_stage,"system_response":t.system_response[:4000],"breakage":t.breakage,"details":t.details,"ts":t.ts} for t in self.turns]}
    def to_markdown(self):
        lines=[f"# Real-path v2 - fan {self.fan_id} creator {self.creator_id}",f"started {self.started_at} ended {self.ended_at}",f"**stopped:** {self.stopped_reason} **breakages:** {self.breakage_count}","", "| turn | fan -> | system stage | system -> | breakage |","|---|---|---|---|---|"]
        for t in self.turns:
            lines.append(f"| {t.turn} | {t.fan_message[:60].replace(chr(10),' ')} | {t.system_stage} | {t.system_response[:90].replace(chr(10),' ')} | {t.breakage or '-'} |")
        lines.append(""); lines.append("## Details")
        for t in self.turns:
            if t.details: lines.append(f"- turn {t.turn} {json.dumps(t.details, ensure_ascii=False)[:400]}")
        return "\n".join(lines)

FAN_SCRIPT=["hi","hey there","how are you?","what do you offer?","how much is it?","okay send it","thanks!","see you later"]
def fan_next_message(turn:int,last_system:str,fan_id:int,creator_id:int)->str:
    if turn < len(FAN_SCRIPT): return FAN_SCRIPT[turn]
    r=_hash_float(f"{creator_id}:{fan_id}",f"fan_reply:{last_system[:20]}",turn)
    pool=["haha","okay","nice","tell me more","is that real?","can you show me?"]
    return pool[int(r*len(pool))]

class RealPathSimulatorV2:
    def __init__(self,creator_id:int,fan_id:int,max_turns:int=6):
        self.creator_id=int(creator_id); self.fan_id=int(fan_id); self.max_turns=int(max_turns)
        self.report=SimulationReport(fan_id=self.fan_id,creator_id=self.creator_id,started_at=_now_iso(),ended_at=_now_iso())

    async def run(self,initial:str="hi")->SimulationReport:
        try:
            from db.postgres import init_pool
            try: await asyncio.wait_for(init_pool(),timeout=5)
            except asyncio.TimeoutError: print("DB init timeout 5s (continue)")
        except Exception as e: print(f"DB init fail {e}")

        fan_msg=initial or "hi"
        last_system=""
        for turn in range(self.max_turns):
            log=TurnLog(turn=turn+1,fan_message=fan_msg,system_stage="inbound",system_response="")
            details:dict[str,Any]={}
            stage="inbound"
            breakage=None
            system_text=""

            # --- lock 1s try (real path: Redis lock creator:{creator}:user:{fan}) ---
            locked=False
            try:
                from db.redis import acquire_user_lock, release_user_lock
                try:
                    locked=await asyncio.wait_for(acquire_user_lock(self.fan_id, ttl=10, creator_id=self.creator_id), timeout=2)
                except asyncio.TimeoutError:
                    breakage="breakage: lock timeout after 2s (Redis hang)"; stage="lock"
                if not locked and not breakage:
                    breakage=f"breakage: lock_contended creator:{self.creator_id}:user:{self.fan_id} (UserLockContentionError)"; stage="lock"
                else:
                    details["lock"]="acquired" if locked else "failed"
            except Exception as e:
                breakage=f"breakage: lock exception {type(e).__name__}:{str(e)[:150]}"; stage="lock"
            if breakage:
                log.system_stage=stage; log.breakage=breakage; log.details=details; self.report.turns.append(log); self.report.stopped_reason=f"breakage at turn {turn+1}: {breakage}"; break

            try:
                # --- upsert ---
                try:
                    from db.postgres import upsert_user
                    await asyncio.wait_for(upsert_user(self.fan_id, "testfan", "Test"), timeout=3)
                    details["upsert"]="ok"
                    stage="upsert"
                except asyncio.TimeoutError:
                    breakage="breakage: upsert timeout 3s"; stage="upsert"
                    raise RuntimeError(breakage)
                except Exception as e:
                    breakage=f"breakage: upsert {e}"; stage="upsert"
                    raise RuntimeError(breakage)

                # --- creator resolution ---
                try:
                    from commerce.single_creator import resolve_single_application_creator
                    ctx=await asyncio.wait_for(resolve_single_application_creator(), timeout=3)
                    if ctx.status.value !="ready":
                        breakage=f"breakage: creator not ready {ctx.status}"; stage="creator_resolution"
                        raise RuntimeError(breakage)
                    details["creator"]="ready"
                    stage="creator_resolution"
                except asyncio.TimeoutError:
                    breakage="breakage: creator timeout 3s"; stage="creator_resolution"
                    raise RuntimeError(breakage)
                except Exception as e:
                    if "breakage" not in str(e): breakage=f"breakage: creator {e}"
                    stage="creator_resolution"
                    raise RuntimeError(breakage or str(e))

                # --- context ---
                try:
                    from memory.context import build_qwen3_context
                    ctx_msgs=await asyncio.wait_for(build_qwen3_context(self.fan_id, fan_msg, persona="default", creator_id=self.creator_id), timeout=5)
                    details["context_len"]=len(str(ctx_msgs))
                    stage="context"
                except asyncio.TimeoutError:
                    breakage="breakage: context timeout 5s"; stage="context"
                    raise RuntimeError(breakage)
                except Exception as e:
                    breakage=f"breakage: context {e}"; stage="context"
                    raise RuntimeError(breakage)

                # --- LLM draft (real llama.cpp) ---
                try:
                    from workers.llm_worker import generate_draft
                    # build minimal context_messages for generate_draft (it expects list[dict] with system/user)
                    msgs=[{"role":"system","content":f"creator {self.creator_id}"},{"role":"user","content":fan_msg}]
                    # reuse ctx_msgs if available (first 3)
                    if isinstance(ctx_msgs, list) and ctx_msgs:
                        msgs=ctx_msgs[:6]  # real context
                    llm_text=await asyncio.wait_for(generate_draft(msgs, fan_msg), timeout=12)
                    if not llm_text or not llm_text.strip():
                        breakage="breakage: LLM empty (llamacpp failure) -> DLQ"; stage="llm"
                        raise RuntimeError(breakage)
                    system_text=llm_text
                    details["llm_len"]=len(llm_text)
                    stage="llm"
                except asyncio.TimeoutError:
                    breakage="breakage: LLM timeout 12s (llamacpp hang)"; stage="llm"
                    raise RuntimeError(breakage)
                except Exception as e:
                    if "breakage" not in str(e):
                        breakage=f"breakage: LLM {type(e).__name__}:{str(e)[:150]}"
                    stage="llm"
                    raise RuntimeError(breakage or str(e))

                # --- scoring (deterministic) ---
                try:
                    from core.scoring import score_draft
                    score=score_draft(system_text, fan_msg)  # pure, no hang
                    details["score"]=str(score)[:100]
                except Exception:
                    pass

                # --- commerce (quarantined Opportunity Engine) ---
                try:
                    from commerce.opportunity_engine import evaluate_opportunity
                    from core.conversation_state import derive_conversation_state
                    # derive state from recent messages
                    try:
                        from db.postgres import get_recent_messages
                        recent=await asyncio.wait_for(get_recent_messages(self.fan_id, limit=10, creator_id=self.creator_id), timeout=3)
                        cs=derive_conversation_state(recent) if recent else None
                    except Exception:
                        cs=None
                    # evaluate (should be no_offer for hi)
                    try:
                        opp=await asyncio.wait_for(evaluate_opportunity(self.creator_id, self.fan_id, cs, datetime.now(UTC)), timeout=3)
                        if getattr(opp,"has_opportunity",False):
                            details["opportunity"]="has_opportunity (quarantined, no PPV)"
                        else:
                            details["opportunity"]="no_offer (RELATIONSHIP_BUILDING v1)"
                    except asyncio.TimeoutError:
                        details["opportunity"]="timeout"
                    stage="commerce (quarantined)"
                except Exception as e:
                    details["commerce_err"]=str(e)[:150]
                    stage="commerce (quarantined)"

                # success: system_text is the reply
                log.system_stage=stage
                log.system_response=system_text
                log.details=details
                # not breakage
                self.report.turns.append(log)
                last_system=system_text
                if turn+1>=self.max_turns:
                    self.report.stopped_reason="completed: max_turns reached (no breakage)"
                    break
                fan_msg=fan_next_message(turn+1, last_system, self.fan_id, self.creator_id)

            except RuntimeError as e:
                # breakage path
                log.system_stage=stage
                log.system_response=system_text
                log.breakage=str(e)
                log.details=details
                self.report.turns.append(log)
                self.report.stopped_reason=f"breakage at turn {turn+1}: {e}"
                break
            except Exception as e:
                log.system_stage=stage
                log.breakage=f"breakage: unexpected {type(e).__name__}:{e}"
                log.details=details
                self.report.turns.append(log)
                self.report.stopped_reason=f"breakage at turn {turn+1}: {e}"
                break
            finally:
                # release lock
                try:
                    from db.redis import release_user_lock
                    await asyncio.wait_for(release_user_lock(self.fan_id, creator_id=self.creator_id), timeout=2)
                except Exception:
                    pass
            await asyncio.sleep(0.2)

        self.report.ended_at=_now_iso()
        self.report.breakage_count=sum(1 for t in self.report.turns if t.breakage and t.breakage.startswith("breakage:"))
        return self.report

    def save(self, base: str|Path|None=None)->Path:
        base=Path(base) if base else Path("simulation_runs")/f"realv2_{self.creator_id}_{self.fan_id}_{int(time.time())}"
        base.mkdir(parents=True,exist_ok=True)
        j=base/"report.json"; j.write_text(json.dumps(self.report.to_dict(),indent=2,ensure_ascii=False),encoding="utf-8")
        m=base/"report.md"; m.write_text(self.report.to_markdown(),encoding="utf-8")
        return j

async def _cli():
    import argparse
    p=argparse.ArgumentParser(description="Real-path v2 - hi-first step-by-step")
    p.add_argument("--creator",type=int,default=1)
    p.add_argument("--fan",type=int,default=999999)
    p.add_argument("--max-turns",type=int,default=3)
    p.add_argument("--out",type=str,default=None)
    a=p.parse_args()
    sim=RealPathSimulatorV2(creator_id=a.creator,fan_id=a.fan,max_turns=a.max_turns)
    report=await sim.run(initial="hi")
    out=sim.save(a.out)
    print(f"creator={report.creator_id} fan={report.fan_id} turns={len(report.turns)} stopped={report.stopped_reason} breakages={report.breakage_count}")
    print(f"json: {out}")
    for t in report.turns:
        print(f"\n[turn {t.turn}] fan: {t.fan_message}")
        print(f"  system ({t.system_stage}): {t.system_response[:300].replace(chr(10),' ')}")
        if t.breakage: print(f"  !! {t.breakage}")

if __name__=="__main__":
    asyncio.run(_cli())
