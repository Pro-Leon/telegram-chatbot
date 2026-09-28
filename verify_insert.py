import asyncio, hashlib
import sys
sys.path.insert(0, 'E:/chatbot')
from db.postgres import insert_generation_telemetry, get_pool, init_pool, close_pool

async def test_insert():
    try:
        await init_pool()
        gid = hashlib.md5(b'test:hello:1').hexdigest()
        data = {
            'generation_id': gid,
            'user_id': 99999,
            'creator_id': 123,
            'runtime_mode': 'new',
            'provider_name': 'ollama',
            'model_name': 'qwen3:4b',
            'context_build_ms': 10,
            'generation_latency_ms': 100,
            'scoring_latency_ms': 5,
            'scoring_score': 0.9,
            'scoring_flags': [],
            'tool_calls_count': 0,
            'tool_names': [],
            'total_e2e_latency_ms': 200,
            'routing_decision': 'auto_approved',
            'success': True,
            'failure_type': None,
            'provider_latency_ms': 90,
            'provider_error': None,
            'input_token_count': 10,
            'output_token_count': 20,
            'worker_id': 'test_worker',
            'one_call_count': 1,
            'total_llm_calls': 1,
            'ppv_second_generation_count': 0,
            'legacy_generation_count': 0,
            'shadow_generation_count': 0,
            'agent_canary_generation_count': 0,
            'retrieval_latency_ms': 15,
            'lexical_candidate_count': 2,
            'semantic_candidate_count': 2,
            'merged_candidate_count': 4,
            'embedding_latency_ms': 8,
            'lexical_latency_ms': 3,
            'ranking_latency_ms': 5,
            'conflict_dropped_count': 1,
            'lexical_dedup_removed_count': 1,
            'total_deduplication_count': 2,
            'context_tokens': 120,
            'context_category_tokens': {'memory': 30},
            'context_degradation_level': 0,
            'context_truncation_count': 0,
            'context_budget_limit': 2600,
            'context_header_reserve': 60,
            'context_effective_budget': 2540,
            'context_budget_violation_count': 0,
            'ppv_provider_name': None,
            'ppv_model_name': None,
            'ppv_input_tokens': None,
            'ppv_output_tokens': None,
            'ppv_latency_ms': None,
            'validation_outcome': 'success',
            'authority_decision': 'NO_COMMERCE',
            'commerce_status': 'NO_COMMERCE',
            'handoff_reason': None,
            'delivery_status': 'sent',
            'duplicate_send_suppressed_count': 0,
            'already_executed_count': 0,
            'inbound_redelivery_count': 0,
            'lexical_threshold': 80,
            'semantic_threshold': 0.30,
            'retrieval_degraded': False,
        }
        ok = await insert_generation_telemetry(data)
        print('insert ok', ok)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT generation_id, one_call_count, total_llm_calls, ppv_second_generation_count, provider_name, model_name, retrieval_latency_ms, context_tokens, validation_outcome FROM generation_telemetry WHERE generation_id=$1", gid)
            print('row', dict(row) if row else None)
        await close_pool()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print('failed', e)

asyncio.run(test_insert())
