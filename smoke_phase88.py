import asyncio, hashlib, time, sys
sys.path.insert(0, 'E:/chatbot')
from unittest.mock import patch

async def run_smoke():
    # Capture events
    events = []
    original_publish = None
    import core.event_bus as eb

    # monkey patch publish_event to capture while still publishing to redis (best-effort)
    orig_publish = eb.publish_event
    orig_batch = eb.publish_events_batch
    async def capture_publish(event_type, data, *, user_id=None, dialog_id=None, generation_id=None, creator_id=None, scope="global"):
        events.append((event_type, dict(data) if isinstance(data, dict) else data, generation_id, creator_id, user_id, scope))
        # also call original to publish to redis (fail-open)
        try:
            return await orig_publish(event_type, data, user_id=user_id, dialog_id=dialog_id, generation_id=generation_id, creator_id=creator_id, scope=scope)
        except Exception as e:
            print(f"publish failed {e}")
            return None
    async def capture_batch(evts):
        for ev in evts:
            events.append((ev.get("event") or ev.get("event_type"), ev.get("data"), ev.get("generation_id"), ev.get("creator_id"), ev.get("user_id")))
        try:
            return await orig_batch(evts)
        except Exception:
            return []

    # ensure module loaded for patch targets
    import workers.llm_worker as lw
    from db.postgres import init_pool, close_pool, get_pool

    await init_pool()
    # ensure consumer group exists (for ack test)
    try:
        from db.redis import ensure_consumer_group
        await ensure_consumer_group()
    except Exception as e:
        print("ensure_consumer_group failed", e)

    user_id = 888888001
    username = "smoketest"
    first_name = "Smoke"
    persona = "You are Sunny Skye, warm conversational."
    user_message = "hey there, how's your day going? love your vibe"
    telegram_message_id = int(time.time()) % 1000000  # unique per run to avoid ON CONFLICT
    generation_id = hashlib.md5(f"{user_id}:{user_message}:{telegram_message_id}".encode()).hexdigest()
    print(f"SMOKE generation_id={generation_id} user={user_id} tg={telegram_message_id}")

    # Patch publish to capture (core.event_bus is the only import site; phase87_events imports inside function)
    with patch("core.event_bus.publish_event", side_effect=capture_publish), \
         patch("core.event_bus.publish_events_batch", side_effect=capture_batch):
        # Also need to patch where phase87_events imports publish_event - it imports inside function, so patching core.event_bus is enough
        start = time.monotonic()
        try:
            await lw.process_message(user_id=user_id, user_message=user_message, telegram_message_id=telegram_message_id, username=username, first_name=first_name, persona=persona, generation_id=generation_id)
            elapsed = time.monotonic() - start
            print(f"process_message completed in {elapsed:.1f}s")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"process_message failed: {e}")
            elapsed = time.monotonic() - start

    # Allow telemetry record background task to complete
    await asyncio.sleep(1.5)

    # Query telemetry
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT generation_id, creator_id, user_id, runtime_mode, provider_name, model_name, one_call_count, total_llm_calls, ppv_second_generation_count, legacy_generation_count, shadow_generation_count, agent_canary_generation_count, input_token_count, output_token_count, generation_latency_ms, provider_latency_ms, retrieval_latency_ms, lexical_candidate_count, semantic_candidate_count, merged_candidate_count, lexical_threshold, semantic_threshold, retrieval_degraded, ranking_latency_ms, conflict_dropped_count, lexical_dedup_removed_count, total_deduplication_count, context_tokens, context_category_tokens, context_degradation_level, context_truncation_count, context_budget_limit, context_header_reserve, context_effective_budget, context_budget_violation_count, validation_outcome, authority_decision, commerce_status, delivery_status, handoff_reason, duplicate_send_suppressed_count, inbound_redelivery_count, total_e2e_latency_ms, success, routing_decision FROM generation_telemetry WHERE generation_id=$1 AND user_id=$2 ORDER BY created_at DESC LIMIT 1", generation_id, user_id)
            if row:
                print("\n=== TELEMETRY ROW ===")
                for k, v in dict(row).items():
                    print(f"{k}: {v}")
                telemetry = dict(row)
            else:
                print("NO TELEMETRY ROW FOUND for generation_id", generation_id)
                # try without creator filter
                rows = await conn.fetch("SELECT generation_id, runtime_mode, one_call_count, total_llm_calls FROM generation_telemetry WHERE user_id=$1 ORDER BY created_at DESC LIMIT 5", user_id)
                print("recent rows for user", [dict(r) for r in rows])
                telemetry = None
            # Check events
            print(f"\n=== EVENTS captured {len(events)} ===")
            for ev in events:
                if len(ev) == 6:
                    ev_type, data, gid, cid, uid, scope = ev
                else:
                    ev_type, data, gid, cid, uid = ev[:5]
                    scope = None
                print(f"  {ev_type}: gid={str(gid)[:8] if gid else None} cid={cid} uid={uid} keys={list(data.keys()) if isinstance(data, dict) else type(data)}")
                if isinstance(data, dict) and len(str(data))<500:
                    print(f"    data={data}")

            # Verify hard conditions
            print("\n=== VERIFICATION ===")
            if telemetry:
                checks = []
                def chk(name, actual, expected):
                    ok = actual == expected
                    checks.append(ok)
                    print(f"{name}: observed={actual} expected={expected} {'PASS' if ok else 'FAIL'}")
                    return ok
                chk("one_call_count", telemetry.get("one_call_count"), 1)
                chk("total_llm_calls", telemetry.get("total_llm_calls"), 1)
                chk("ppv_second_generation_count", telemetry.get("ppv_second_generation_count"), 0)
                chk("legacy_generation_count", telemetry.get("legacy_generation_count"), 0)
                chk("runtime_mode", telemetry.get("routing_decision") and "one_call" in str(telemetry.get("routing_decision")) or telemetry.get("runtime_mode")=="new", True)  # loose
                print(f"runtime_mode actual={telemetry.get('runtime_mode')} routing={telemetry.get('routing_decision')}")
                chk("provider_name ollama", telemetry.get("provider_name"), "ollama")
                # model may be qwen2.5:3b per env, not qwen3:4b
                print(f"model_name={telemetry.get('model_name')} (expected qwen2.5:3b or qwen3:4b per env)")
                chk("lexical_threshold", telemetry.get("lexical_threshold"), 80)
                chk("semantic_threshold", float(telemetry.get("semantic_threshold") or 0), 0.30)
                chk("context_budget_limit", telemetry.get("context_budget_limit"), 2600)
                chk("context_header_reserve", telemetry.get("context_header_reserve"), 60)
                chk("context_effective_budget", telemetry.get("context_effective_budget"), 2540)
                # Check budget not exceeded
                ctx_tokens = telemetry.get("context_tokens")
                print(f"context_tokens={ctx_tokens} <=2600 ? {ctx_tokens is None or ctx_tokens <=2600}")
                # Check generation_id correlation in events
                gids = set(ev[2] for ev in events if len(ev)>=3 and ev[2])
                print(f"event generation_ids distinct {gids} all equal generation_id? {gids=={generation_id} if gids else 'no events'}")
                # Check STATE_READY etc
                ev_types = [e[0] for e in events]
                for required in ["state_ready","retrieval_ready","context_ready","one_call_start","one_call_success","authority_decision"]:
                    print(f"event {required}: {'FOUND' if required in ev_types else 'MISSING'}")
                # Check no legacy calls
                # we can't directly check generate_draft not called without mock, but we can infer via telemetry legacy count 0
                # Check delivery
                print(f"delivery_status={telemetry.get('delivery_status')} authority_decision={telemetry.get('authority_decision')} validation_outcome={telemetry.get('validation_outcome')}")
                print("\nVERIFICATION DONE")
            else:
                print("no telemetry to verify")
        # also show queue health
        try:
            from db.redis import get_inbound_pending_count, get_send_pending_count, get_dlq_age_seconds
            print("\n=== QUEUE HEALTH ===")
            print("inbound_pending", await get_inbound_pending_count())
            print("send_pending", await get_send_pending_count())
            # dlq count
            from db.redis import get_redis
            r = await get_redis()
            info = await r.xinfo_stream("dead_letter_queue") if True else {}
            print("dlq info", info)
        except Exception as e:
            print("queue health failed", e)
            import traceback; traceback.print_exc()
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await close_pool()
        try:
            from db.redis import close_redis
            await close_redis()
        except: pass

asyncio.run(run_smoke())
