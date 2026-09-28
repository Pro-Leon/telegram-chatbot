import asyncio
from db.postgres import init_pool, get_pool, close_pool
async def f():
    await init_pool()
    pool=await get_pool()
    async with pool.acquire() as conn:
        cols=await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='generation_telemetry' AND column_name IN ('generation_id','creator_id','runtime_mode','provider_name','model_name','one_call_count','total_llm_calls','ppv_second_generation_count','legacy_generation_count','shadow_generation_count','agent_canary_generation_count','validation_outcome','authority_decision','commerce_status','delivery_status')")
        print('required columns present', len(cols), '/15')
        for c in cols:
            print(' ', c['column_name'])
        total=await conn.fetchval('SELECT count(*) FROM generation_telemetry')
        print('total telemetry rows', total)
        rows=await conn.fetch('SELECT runtime_mode, count(*) as cnt FROM generation_telemetry GROUP BY runtime_mode')
        print('by runtime_mode', [dict(r) for r in rows])
        rows2=await conn.fetch("SELECT provider_name, model_name, count(*) as cnt FROM generation_telemetry WHERE runtime_mode='new' GROUP BY provider_name, model_name")
        print('by provider/model for new', [dict(r) for r in rows2])
        rows3=await conn.fetch('SELECT one_call_count, total_llm_calls, legacy_generation_count, ppv_second_generation_count, count(*) as cnt FROM generation_telemetry WHERE runtime_mode=\'new\' GROUP BY one_call_count, total_llm_calls, legacy_generation_count, ppv_second_generation_count ORDER BY cnt DESC')
        print('accounting new', [dict(r) for r in rows3])
        rows4=await conn.fetch("SELECT validation_outcome, count(*) as cnt FROM generation_telemetry WHERE runtime_mode='new' GROUP BY validation_outcome")
        print('validation new', [dict(r) for r in rows4])
        rows5=await conn.fetch('SELECT delivery_status, count(*) as cnt FROM generation_telemetry GROUP BY delivery_status')
        print('delivery', [dict(r) for r in rows5])
        bad=await conn.fetchval("SELECT count(*) FROM generation_telemetry WHERE runtime_mode='new' AND model_name='qwen3:4b'")
        print('wrong model qwen3:4b in new', bad)
        rows6=await conn.fetch('SELECT generation_id, creator_id, runtime_mode, provider_name, model_name, one_call_count, total_llm_calls, ppv_second_generation_count, legacy_generation_count, validation_outcome, delivery_status, total_e2e_latency_ms, generation_latency_ms, provider_latency_ms, retrieval_latency_ms FROM generation_telemetry ORDER BY created_at DESC LIMIT 10')
        print('recent 10')
        for r in rows6:
            print(dict(r))
        # latency stats for new
        rows7=await conn.fetch("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY total_e2e_latency_ms) as p50, percentile_cont(0.95) WITHIN GROUP (ORDER BY total_e2e_latency_ms) as p95, percentile_cont(0.99) WITHIN GROUP (ORDER BY total_e2e_latency_ms) as p99 FROM generation_telemetry WHERE runtime_mode='new' AND total_e2e_latency_ms IS NOT NULL")
        print('latency p50/p95/p99 new', [dict(r) for r in rows7])
        rows8=await conn.fetch("SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY generation_latency_ms) as p95_onecall, percentile_cont(0.95) WITHIN GROUP (ORDER BY retrieval_latency_ms) as p95_retrieval FROM generation_telemetry WHERE runtime_mode='new'")
        print('onecall/retrieval p95', [dict(r) for r in rows8])
        # context
        rows9=await conn.fetch("SELECT avg(context_tokens) as avg_tokens, max(context_tokens) as max_tokens, count(*) filter (where context_budget_violation_count>0) as violations, count(*) filter (where retrieval_degraded) as degraded, avg(lexical_candidate_count) as avg_lex, avg(semantic_candidate_count) as avg_sem, avg(merged_candidate_count) as avg_merged FROM generation_telemetry WHERE runtime_mode='new'")
        print('context stats', [dict(r) for r in rows9])
        # authority
        rows10=await conn.fetch("SELECT authority_decision, count(*) as cnt FROM generation_telemetry WHERE runtime_mode='new' GROUP BY authority_decision")
        print('authority new', [dict(r) for r in rows10])
        # commerce
        rows11=await conn.fetch("SELECT commerce_status, count(*) as cnt FROM generation_telemetry GROUP BY commerce_status")
        print('commerce', [dict(r) for r in rows11])
        # handoff
        rows12=await conn.fetch("SELECT handoff_reason, count(*) as cnt FROM generation_telemetry GROUP BY handoff_reason")
        print('handoff', [dict(r) for r in rows12])
        # shadow/agent
        rows13=await conn.fetch("SELECT sum(shadow_generation_count) as shadow, sum(agent_canary_generation_count) as agent FROM generation_telemetry WHERE runtime_mode='new'")
        print('shadow/agent sum', [dict(r) for r in rows13])
        # creator isolation check: count per creator
        rows14=await conn.fetch("SELECT creator_id, runtime_mode, count(*) as cnt FROM generation_telemetry GROUP BY creator_id, runtime_mode ORDER BY creator_id")
        print('per creator', [dict(r) for r in rows14])
        # Redis health
        try:
            from db.redis import get_redis
            r=await get_redis()
            info=await r.xinfo_groups("inbound_messages") if False else None
            print('redis check done')
        except Exception as e:
            print('redis err', e)
    await close_pool()
    from db.redis import close_redis
    try: await close_redis()
    except: pass
import asyncio
asyncio.run(f())
