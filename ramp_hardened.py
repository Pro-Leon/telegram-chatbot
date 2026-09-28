import asyncio
from db.postgres import init_pool, get_pool, close_pool
async def f():
    await init_pool()
    pool=await get_pool()
    async with pool.acquire() as conn:
        # Hardened window after 14:00 today
        rows=await conn.fetch("SELECT generation_id, creator_id, runtime_mode, provider_name, model_name, one_call_count, total_llm_calls, ppv_second_generation_count, legacy_generation_count, shadow_generation_count, agent_canary_generation_count, validation_outcome, authority_decision, commerce_status, delivery_status, total_e2e_latency_ms, generation_latency_ms, provider_latency_ms, retrieval_latency_ms, context_tokens, context_budget_violation_count, handoff_reason, duplicate_send_suppressed_count, inbound_redelivery_count, created_at FROM generation_telemetry WHERE created_at > '2026-09-09 14:00:00+00' AND runtime_mode='new' AND creator_id=1 ORDER BY created_at")
        print(f'hardened window new creator1 count {len(rows)}')
        for r in rows:
            print(dict(r))
        # Stats
        if rows:
            total=len(rows)
            exactly_one=sum(1 for r in rows if r['one_call_count']==1 and r['total_llm_calls']==1 and r['legacy_generation_count']==0 and r['ppv_second_generation_count']==0)
            print(f'exactly_one rate {exactly_one}/{total} = {exactly_one/total*100:.1f}%')
            success=sum(1 for r in rows if r['validation_outcome']=='success')
            print(f'success {success}/{total}')
            wrong_model=sum(1 for r in rows if r['model_name']!='qwen2.5:3b')
            print(f'wrong model {wrong_model}')
            dup=sum(r['duplicate_send_suppressed_count'] or 0 for r in rows)
            print(f'duplicate suppressed {dup}')
            # latency
            lat=[r['total_e2e_latency_ms'] for r in rows if r['total_e2e_latency_ms']]
            print(f'latencies {lat}')
            # Redis
            from db.redis import get_redis
            rds=await get_redis()
            pending_in=await rds.xlen("inbound_messages") if False else None
            # Try to get stream lengths via info
            try:
                info=await rds.execute_command("XLEN", "inbound_messages")
                print('inbound len', info)
                info2=await rds.execute_command("XLEN", "send_messages")
                print('send len', info2)
                info3=await rds.execute_command("XLEN", "dead_letter_queue")
                print('dlq len', info3)
                # pending
                pend=await rds.execute_command("XPENDING", "inbound_messages", "llm_workers")
                print('pending inbound', pend)
            except Exception as e:
                print('redis err', e)
        else:
            print('no rows in hardened window')
    await close_pool()
    from db.redis import close_redis
    try: await close_redis()
    except: pass
asyncio.run(f())
