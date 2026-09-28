import asyncio
from db.postgres import init_pool, get_pool, close_pool
async def f():
    await init_pool()
    pool=await get_pool()
    async with pool.acquire() as conn:
        rows=await conn.fetch("SELECT generation_id, creator_id, runtime_mode, provider_name, model_name, created_at FROM generation_telemetry ORDER BY created_at DESC LIMIT 20")
        for r in rows:
            print(f"{r['generation_id'][:12]} creator={r['creator_id']} mode={r['runtime_mode']} provider={r['provider_name']} model={r['model_name']} created={r['created_at']}")
        # Check that qwen3 row is old
        # Get min created_at for new qwen3
        r=await conn.fetchrow("SELECT generation_id, created_at FROM generation_telemetry WHERE model_name='qwen3:4b' AND runtime_mode='new'")
        print('qwen3 new row', dict(r) if r else None)
        # Count recent window last 24h
        cnt=await conn.fetchval("SELECT count(*) FROM generation_telemetry WHERE created_at > now() - interval '24 hours' AND runtime_mode='new'")
        print('last 24h new count', cnt)
        rows2=await conn.fetch("SELECT generation_id, model_name, validation_outcome FROM generation_telemetry WHERE created_at > now() - interval '24 hours' AND runtime_mode='new'")
        print('last 24h new rows', [dict(x) for x in rows2])
        # Legacy last 24h
        cnt2=await conn.fetchval("SELECT count(*) FROM generation_telemetry WHERE created_at > now() - interval '24 hours' AND runtime_mode='legacy'")
        print('last 24h legacy count', cnt2)
    await close_pool()
    from db.redis import close_redis
    try: await close_redis()
    except: pass
asyncio.run(f())
