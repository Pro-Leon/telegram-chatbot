import asyncio, hashlib, time, random
from workers.llm_worker import process_message
from db.postgres import init_pool, close_pool, get_pool
from db.redis import close_redis

MSGS=["hey","hi","hello","what's up","how was your day","thanks so much","haha","hey beautiful","how are you?","what's new","love your content","you're amazing","good morning","good night","see you later","miss you","hey there","quick question","are you free?","let's chat"]

async def one_turn(idx):
    user_id=888888100 + idx
    msg=random.choice(MSGS) + f" #{idx}"
    tg=int(time.time()*1000)%1000000 + idx
    gid=hashlib.md5(f"{user_id}:{msg}:{tg}".encode()).hexdigest()
    persona="You are Sunny Skye, warm conversational."
    start=time.monotonic()
    try:
        await process_message(user_id=user_id, user_message=msg, telegram_message_id=tg, username=f"test{idx}", first_name="Test", persona=persona, generation_id=gid)
        elapsed=time.monotonic()-start
        print(f"[{idx+1:02d}/30] gid={gid[:8]} user={user_id} msg={msg!r:20s} done {elapsed:.1f}s")
        return True
    except Exception as e:
        print(f"[{idx+1:02d}] FAIL {e}")
        import traceback; traceback.print_exc()
        return False

async def main():
    await init_pool()
    # ensure we are in new runtime
    from core.config import get_settings
    get_settings.cache_clear()
    s=get_settings()
    print(f"START extended generation llm_path={s.llm_path} model={s.ollama_model} provider={s.llm_provider} time={time.strftime('%Y-%m-%d %H:%M:%S')}")
    # sequential to avoid overloading Ollama CPU (previously 40-50s per generation)
    success=0
    for i in range(30):
        ok=await one_turn(i)
        if ok:
            success+=1
        # small pause between
        await asyncio.sleep(2)
        # check telemetry count every 5
        if (i+1)%5==0:
            pool=await get_pool()
            async with pool.acquire() as conn:
                cnt=await conn.fetchval("SELECT count(*) FROM generation_telemetry WHERE runtime_mode='new' AND creator_id=1 AND created_at > '2026-09-09 14:00:00+00'")
                print(f" -- telemetry new creator1 since 14:00: {cnt}")
    print(f"COMPLETE {success}/30")
    await close_pool()
    try: await close_redis()
    except: pass

asyncio.run(main())
