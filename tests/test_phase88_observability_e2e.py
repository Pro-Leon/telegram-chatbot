"""Phase 88 observability E2E (light) using ExitStack to avoid block limit."""
import hashlib, pytest, asyncio, contextlib
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.unit]

def _gid(uid, msg, tg):
    return hashlib.md5(f"{uid}:{msg}:{tg}".encode()).hexdigest()

@pytest.mark.asyncio
async def test_normal_canonical_observability():
    from core.one_call import OneCallResult
    from commerce.signals import CommerceSignals
    from commerce.single_creator import SingleCreatorStatus
    generation_id = _gid(12345, "hello world test", 999)
    creator_id = 42
    mock_one = OneCallResult(reply="hey gorgeous, how are you?", signals=CommerceSignals.low_information(), confidence=0.9, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.9, quality_flags=[], safety_flags=[], provider_name="ollama", model_name="qwen2.5:3b", input_tokens=10, output_tokens=20, latency_ms=100)
    mock_observation = MagicMock(enabled=True, failed=False, candidate_count=6, selected_count=4, dropped_count=2, token_count=120, char_count=480, gather_ms=15.0, total_ms=30.0, score_ms=5.0, dedup_ms=2.0, budget_ms=1.0, render_ms=1.0, rendered_text="RETRIEVED: test", pipeline_result=MagicMock(candidate_count=6, selected_count=4, total_tokens=120, category_tokens={"memory": 30}, degradation_level=0, violations=[], conflict_dropped_count=1, lexical_dedup_removed_count=1, truncation_count=0, retrieval_metrics={"lexical_candidate_count": 2, "semantic_candidate_count": 2, "merged_candidate_count": 4, "lexical_latency_ms": 3.0, "semantic_latency_ms": 10.0, "embedding_latency_ms": 8.0, "total_retrieval_ms": 15.0, "lexical_threshold": 80, "semantic_threshold": 0.30, "degraded": False}, score_ms=5.0, dedup_ms=2.0, budget_ms=1.0, render_ms=1.0), conflict_dropped=1, lexical_dedup_removed=1, truncation_count=0, budget_violations=0, degradation_level=0, category_tokens={"memory": 30}, retrieval_metrics={"lexical_candidate_count": 2, "semantic_candidate_count": 2, "merged_candidate_count": 4, "lexical_latency_ms": 3.0, "semantic_latency_ms": 10.0, "embedding_latency_ms": 8.0, "total_retrieval_ms": 15.0, "lexical_threshold": 80, "semantic_threshold": 0.30, "degraded": False})
    published=[]
    async def fake_publish(event_type, data, *, user_id=None, dialog_id=None, generation_id=None, creator_id=None, scope="global", **kw):
        published.append((event_type, data, generation_id, creator_id, user_id)); return "evt-1"
    async def fake_batch(events):
        for ev in events:
            published.append((ev.get("event") or ev.get("event_type"), ev.get("data"), ev.get("generation_id"), ev.get("creator_id") or ev.get("user_id"), ev.get("user_id")))
        return [f"evt-{i}" for i in range(len(events))]
    telemetry_captured={}
    async def fake_insert(data):
        telemetry_captured.update(data); return True
    enqueue_captured={}
    async def fake_enqueue(data, dedup_id=None, generation_id=None, creator_id=None):
        enqueue_captured.update(data); enqueue_captured["generation_id_arg"]=generation_id; enqueue_captured["creator_id_arg"]=creator_id; enqueue_captured["dedup_id"]=dedup_id; return "send-1"
    mock_creator=MagicMock(); mock_creator.status=SingleCreatorStatus.READY; mock_creator.creator_id=creator_id
    mock_auth=MagicMock(); mock_auth.recent_messages=({"direction":"inbound","content":"hi"},); mock_auth.persona="You are Sunny"; mock_auth.persona_name="Sunny"; mock_auth.user={"first_name":"there","funnel_stage":"new","message_count":5}; mock_auth.profile={}; mock_auth.conversation_state={"current_topic":None,"open_threads":()}; mock_auth.commerce_context_text=""; mock_auth.metadata={"acquisition_ms":12}; mock_auth.summary=None
    import workers.llm_worker  # ensure module loaded for patch target
    stack=contextlib.ExitStack()
    stack.enter_context(patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True))
    stack.enter_context(patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.upsert_user", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False))
    stack.enter_context(patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[{"role":"system","content":"sys"}]))
    stack.enter_context(patch("context_engine.authoritative_assembly.assemble_authoritative_context", new_callable=AsyncMock, return_value=mock_auth))
    stack.enter_context(patch("context_engine.worker_integration.observe_context_engine", new_callable=AsyncMock, return_value=mock_observation))
    stack.enter_context(patch("core.event_bus.publish_event", side_effect=fake_publish))
    stack.enter_context(patch("core.event_bus.publish_events_batch", side_effect=fake_batch))
    stack.enter_context(patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True))
    stack.enter_context(patch("workers.llm_worker.enqueue_send", side_effect=fake_enqueue))
    stack.enter_context(patch("workers.llm_worker.post_process", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=100))
    stack.enter_context(patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=None))
    stack.enter_context(patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one))
    stack.enter_context(patch("db.postgres.insert_generation_telemetry", side_effect=fake_insert))
    stack.enter_context(patch("commerce.single_creator.resolve_single_application_creator", new_callable=AsyncMock, return_value=mock_creator))
    stack.enter_context(patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock, return_value={"identity":{"name":"Sunny"}}))
    stack.enter_context(patch("workers.llm_worker._settings", MagicMock(llm_path="new", context_engine_enabled=True, context_engine_observational=True, context_engine_sample_rate=1.0, user_lock_ttl=60, auto_approve_threshold=0.80, llm_provider="ollama", ollama_model="qwen2.5:3b")))
    stack.enter_context(patch("core.config.get_settings", return_value=MagicMock(llm_path="new", context_engine_enabled=True, context_engine_observational=True, context_engine_sample_rate=1.0, llm_provider="ollama", ollama_model="qwen2.5:3b")))
    try:
        from workers.llm_worker import process_message
        await process_message(user_id=12345, user_message="hello world test", telegram_message_id=999, username="u", first_name="f", persona="You are Sunny", generation_id=generation_id)
        await asyncio.sleep(0.05)
        assert generation_id == _gid(12345, "hello world test", 999)
        gids=[g for _,_,g,_,_ in published if g is not None]
        assert all(g==generation_id for g in gids)
        state_events=[e for e in published if e[0]=="state_ready"]
        assert len(state_events)==1
        assert state_events[0][3]==creator_id
        assert state_events[0][2]==generation_id
        assert state_events[0][1]["runtime_mode"]=="new"
        retr=[e for e in published if e[0]=="retrieval_ready"]
        assert len(retr)==1 and retr[0][1]["lexical_candidate_count"]==2 and retr[0][1]["semantic_candidate_count"]==2
        ctx=[e for e in published if e[0]=="context_ready"]
        assert len(ctx)==1 and ctx[0][1]["candidate_count"]==6 and ctx[0][1]["budget_limit"]==2600
        starts=[e for e in published if e[0]=="one_call_start" and e[1].get("generation_kind")=="one_call"]
        assert len(starts)==1 and starts[0][1]["provider_name"]=="ollama"
        assert telemetry_captured.get("provider_name")=="ollama"
        assert telemetry_captured.get("one_call_count")==1
        assert telemetry_captured.get("total_llm_calls")==1
        assert telemetry_captured.get("ppv_second_generation_count")==0
        auth=[e for e in published if e[0]=="authority_decision"]
        assert len(auth)==1
        assert enqueue_captured.get("generation_id")==generation_id or enqueue_captured.get("generation_id_arg")==generation_id
        ppv=[e for e in published if e[0]=="one_call_start" and e[1].get("generation_kind")=="ppv_second_generation"]
        assert len(ppv)==0
    finally:
        stack.close()

@pytest.mark.asyncio
async def test_ppv_second_generation():
    from core.one_call import OneCallResult
    from commerce.signals import CommerceSignals
    from commerce.selection import CommerceSelectionResult, CommerceSelectionStatus, CommerceSelectionReason
    from commerce.execution import ExecutionStatus
    generation_id=_gid(2,"send it",2)
    creator_id=5
    mock_one=OneCallResult(reply="here is your offer", signals=CommerceSignals.low_information(), confidence=0.9, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.9, provider_name="ollama", model_name="qwen2.5:3b", input_tokens=5, output_tokens=10)
    mock_one.signals.purchase_intent=0.9
    mock_selection=CommerceSelectionResult(status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE, reason=CommerceSelectionReason.COMMERCE_COMPLETED, commerce_response_text="PPV offer: $30", execution_status=ExecutionStatus.EXECUTED, offer_active=True)
    published=[]
    async def fake_publish(event_type, data, *, user_id=None, dialog_id=None, generation_id=None, creator_id=None, scope="global", **kw):
        published.append((event_type, data, generation_id, creator_id)); return "evt"
    async def fake_batch(evts):
        for ev in evts:
            published.append((ev.get("event") or ev.get("event_type"), ev.get("data"), ev.get("generation_id"), ev.get("creator_id"))); return []
    telemetry={}
    async def fake_insert(data): telemetry.update(data); return True
    enqueue={}
    async def fake_enqueue(data, dedup_id=None, generation_id=None, creator_id=None):
        enqueue.update(data); enqueue["gid_arg"]=generation_id; return "sid"
    mock_obs=MagicMock(enabled=True, failed=False, candidate_count=5, selected_count=3, dropped_count=2, token_count=100, char_count=400, gather_ms=10, total_ms=20, score_ms=5, dedup_ms=1, budget_ms=1, render_ms=1, rendered_text="ctx", pipeline_result=MagicMock(candidate_count=5, selected_count=3, total_tokens=100, category_tokens={}, degradation_level=0, violations=[], conflict_dropped_count=0, lexical_dedup_removed_count=0, truncation_count=0, retrieval_metrics={"lexical_candidate_count":1,"semantic_candidate_count":1,"merged_candidate_count":2,"lexical_latency_ms":2,"semantic_latency_ms":5,"embedding_latency_ms":4,"total_retrieval_ms":10,"lexical_threshold":80,"semantic_threshold":0.30,"degraded":False}, score_ms=5, dedup_ms=1, budget_ms=1, render_ms=1), conflict_dropped=0, lexical_dedup_removed=0, truncation_count=0, budget_violations=0, degradation_level=0, category_tokens={}, retrieval_metrics={"lexical_candidate_count":1,"semantic_candidate_count":1,"merged_candidate_count":2,"lexical_latency_ms":2,"semantic_latency_ms":5,"embedding_latency_ms":4,"total_retrieval_ms":10,"lexical_threshold":80,"semantic_threshold":0.30,"degraded":False})
    mock_auth=MagicMock(); mock_auth.recent_messages=({"direction":"inbound","content":"send it"},); mock_auth.persona="p"; mock_auth.persona_name=None; mock_auth.user={"first_name":"x","funnel_stage":"new","message_count":10}; mock_auth.profile={}; mock_auth.conversation_state={"current_topic":None}; mock_auth.commerce_context_text=""; mock_auth.metadata={"acquisition_ms":5}; mock_auth.summary=None
    from commerce.single_creator import SingleCreatorStatus
    mock_creator_res=MagicMock(); mock_creator_res.status=SingleCreatorStatus.READY; mock_creator_res.creator_id=creator_id
    import workers.llm_worker
    stack=contextlib.ExitStack()
    stack.enter_context(patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True))
    stack.enter_context(patch("workers.llm_worker.upsert_user", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False))
    stack.enter_context(patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]))
    stack.enter_context(patch("context_engine.authoritative_assembly.assemble_authoritative_context", new_callable=AsyncMock, return_value=mock_auth))
    stack.enter_context(patch("context_engine.worker_integration.observe_context_engine", new_callable=AsyncMock, return_value=mock_obs))
    stack.enter_context(patch("core.event_bus.publish_event", side_effect=fake_publish))
    stack.enter_context(patch("core.event_bus.publish_events_batch", side_effect=fake_batch))
    stack.enter_context(patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True))
    stack.enter_context(patch("workers.llm_worker.enqueue_send", side_effect=fake_enqueue))
    stack.enter_context(patch("workers.llm_worker.post_process", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=1))
    stack.enter_context(patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=mock_selection))
    stack.enter_context(patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one))
    stack.enter_context(patch("db.postgres.insert_generation_telemetry", side_effect=fake_insert))
    stack.enter_context(patch("commerce.single_creator.resolve_single_application_creator", new_callable=AsyncMock, return_value=mock_creator_res))
    stack.enter_context(patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock, return_value=None))
    stack.enter_context(patch("workers.llm_worker._settings", MagicMock(llm_path="new", context_engine_enabled=True, context_engine_observational=True, context_engine_sample_rate=1.0, user_lock_ttl=60, auto_approve_threshold=0.80, llm_provider="ollama", ollama_model="qwen2.5:3b")))
    stack.enter_context(patch("core.config.get_settings", return_value=MagicMock(llm_path="new", context_engine_enabled=True, context_engine_observational=True, context_engine_sample_rate=1.0, llm_provider="ollama", ollama_model="qwen2.5:3b")))
    try:
        from workers.llm_worker import process_message
        await process_message(user_id=2, user_message="send it", telegram_message_id=2, username="u", first_name="f", persona="p", generation_id=generation_id)
        await asyncio.sleep(0.05)
        assert telemetry.get("one_call_count")==1
        assert telemetry.get("ppv_second_generation_count")==1
        assert telemetry.get("total_llm_calls")==2
        ppv_starts=[e for e in published if e[0]=="one_call_start" and e[1].get("generation_kind")=="ppv_second_generation"]
        assert len(ppv_starts)==1
    finally:
        stack.close()

@pytest.mark.asyncio
async def test_one_call_failure_no_legacy():
    from core.one_call import OneCallResult
    from commerce.signals import CommerceSignals
    generation_id=_gid(99,"hi",99)
    bad=OneCallResult(reply="", signals=CommerceSignals.low_information(), confidence=0.0, needs_handoff=True, is_valid=False, validation_error="Schema validation failed: extra fields", quality_score=0.0, provider_name="ollama", model_name="qwen2.5:3b")
    published=[]
    async def fake_publish(event_type, data, *, user_id=None, dialog_id=None, generation_id=None, creator_id=None, scope="global", **kw):
        published.append((event_type, data, generation_id)); return "evt"
    async def fake_batch(evts):
        for ev in evts:
            published.append((ev.get("event") or ev.get("event_type"), ev.get("data"), ev.get("generation_id"))); return []
    telemetry={}
    async def fake_insert(data): telemetry.update(data); return True
    mock_obs=MagicMock(enabled=True, failed=False, candidate_count=3, selected_count=2, dropped_count=1, token_count=50, char_count=200, gather_ms=5, total_ms=10, score_ms=2, dedup_ms=1, budget_ms=0, render_ms=0, rendered_text="ctx", pipeline_result=MagicMock(candidate_count=3, selected_count=2, total_tokens=50, category_tokens={}, degradation_level=0, violations=[], conflict_dropped_count=0, lexical_dedup_removed_count=0, truncation_count=0, retrieval_metrics={"lexical_candidate_count":0,"semantic_candidate_count":0,"merged_candidate_count":0,"lexical_latency_ms":1,"semantic_latency_ms":1,"embedding_latency_ms":1,"total_retrieval_ms":5,"lexical_threshold":80,"semantic_threshold":0.30,"degraded":False}, score_ms=2, dedup_ms=1, budget_ms=0, render_ms=0), conflict_dropped=0, lexical_dedup_removed=0, truncation_count=0, budget_violations=0, degradation_level=0, category_tokens={}, retrieval_metrics={"lexical_candidate_count":0,"semantic_candidate_count":0,"merged_candidate_count":0,"lexical_latency_ms":1,"semantic_latency_ms":1,"embedding_latency_ms":1,"total_retrieval_ms":5,"lexical_threshold":80,"semantic_threshold":0.30,"degraded":False})
    mock_auth=MagicMock(); mock_auth.recent_messages=(); mock_auth.persona="p"; mock_auth.persona_name=None; mock_auth.user={"first_name":"x","funnel_stage":"new","message_count":1}; mock_auth.profile={}; mock_auth.conversation_state=None; mock_auth.commerce_context_text=""; mock_auth.metadata={"acquisition_ms":5}; mock_auth.summary=None
    import workers.llm_worker
    stack=contextlib.ExitStack()
    mock_legacy=AsyncMock(); mock_score=AsyncMock(return_value=(0.9,[]))
    mock_extract=AsyncMock()
    stack.enter_context(patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True))
    stack.enter_context(patch("workers.llm_worker.upsert_user", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False))
    stack.enter_context(patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]))
    stack.enter_context(patch("context_engine.authoritative_assembly.assemble_authoritative_context", new_callable=AsyncMock, return_value=mock_auth))
    stack.enter_context(patch("context_engine.worker_integration.observe_context_engine", new_callable=AsyncMock, return_value=mock_obs))
    stack.enter_context(patch("core.event_bus.publish_event", side_effect=fake_publish))
    stack.enter_context(patch("core.event_bus.publish_events_batch", side_effect=fake_batch))
    stack.enter_context(patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True))
    stack.enter_context(patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.post_process", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=5))
    stack.enter_context(patch("workers.llm_worker.generate_draft", mock_legacy))
    stack.enter_context(patch("core.scoring.score_draft", mock_score))
    stack.enter_context(patch("commerce.deepseek.extract_commerce_signals", mock_extract))
    stack.enter_context(patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock))
    stack.enter_context(patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=bad))
    stack.enter_context(patch("db.postgres.insert_generation_telemetry", side_effect=fake_insert))
    stack.enter_context(patch("commerce.single_creator.resolve_single_application_creator", new_callable=AsyncMock, return_value=MagicMock(status=MagicMock(READY=True), creator_id=1)))
    stack.enter_context(patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock, return_value=None))
    stack.enter_context(patch("workers.llm_worker._settings", MagicMock(llm_path="new", context_engine_enabled=True, context_engine_observational=True, context_engine_sample_rate=1.0, user_lock_ttl=60, auto_approve_threshold=0.80, llm_provider="ollama", ollama_model="qwen2.5:3b")))
    stack.enter_context(patch("core.config.get_settings", return_value=MagicMock(llm_path="new", context_engine_enabled=True, context_engine_observational=True, context_engine_sample_rate=1.0, llm_provider="ollama", ollama_model="qwen2.5:3b")))
    try:
        from workers.llm_worker import process_message
        await process_message(user_id=99, user_message="hi", telegram_message_id=99, username="u", first_name="f", persona="p", generation_id=generation_id)
        await asyncio.sleep(0.05)
        assert mock_legacy.call_count==0
        assert mock_score.call_count==0
        handoffs=[e for e in published if e[0]=="handoff"]
        assert len(handoffs)>=1
        fails=[e for e in published if e[0]=="one_call_failed"]
        assert len(fails)==1
        assert telemetry.get("one_call_count")==1
    finally:
        stack.close()

@pytest.mark.asyncio
async def test_runtime_mode_separation():
    from core.one_call import OneCallResult
    from commerce.signals import CommerceSignals
    mock_one=OneCallResult(reply="hi", signals=CommerceSignals.low_information(), confidence=0.9, needs_handoff=False, is_valid=True, validation_error=None, quality_score=0.9, provider_name="ollama", model_name="qwen2.5:3b")
    for mode, expected in [("new","new"), ("legacy","legacy")]:
        telemetry={}
        async def fake_insert(data): telemetry.update(data); return True
        mock_auth=MagicMock(); mock_auth.recent_messages=(); mock_auth.persona="p"; mock_auth.persona_name=None; mock_auth.user={"first_name":"x","funnel_stage":"new","message_count":1}; mock_auth.profile={}; mock_auth.conversation_state=None; mock_auth.commerce_context_text=""; mock_auth.metadata={"acquisition_ms":1}; mock_auth.summary=None
        mock_obs=MagicMock(enabled=False, failed=True)
        import workers.llm_worker
        stack=contextlib.ExitStack()
        stack.enter_context(patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True))
        stack.enter_context(patch("workers.llm_worker.upsert_user", new_callable=AsyncMock))
        stack.enter_context(patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False))
        stack.enter_context(patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[{"role":"system","content":"x"}]))
        stack.enter_context(patch("context_engine.authoritative_assembly.assemble_authoritative_context", new_callable=AsyncMock, return_value=mock_auth if mode=="new" else None))
        stack.enter_context(patch("context_engine.worker_integration.observe_context_engine", new_callable=AsyncMock, return_value=mock_obs))
        stack.enter_context(patch("core.event_bus.publish_event", new_callable=AsyncMock))
        stack.enter_context(patch("core.event_bus.publish_events_batch", new_callable=AsyncMock))
        stack.enter_context(patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True))
        stack.enter_context(patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock))
        stack.enter_context(patch("workers.llm_worker.post_process", new_callable=AsyncMock))
        stack.enter_context(patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=1))
        stack.enter_context(patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=None))
        stack.enter_context(patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_one))
        stack.enter_context(patch("commerce.deepseek.extract_commerce_signals", new_callable=AsyncMock, return_value=CommerceSignals.low_information()))
        stack.enter_context(patch("core.scoring.score_draft", new_callable=AsyncMock, return_value=(0.9, [])))
        stack.enter_context(patch("workers.llm_worker.generate_draft", new_callable=AsyncMock, return_value="hi"))
        stack.enter_context(patch("db.postgres.insert_generation_telemetry", side_effect=fake_insert))
        stack.enter_context(patch("commerce.single_creator.resolve_single_application_creator", new_callable=AsyncMock, return_value=MagicMock(status=MagicMock(READY=True), creator_id=1)))
        stack.enter_context(patch("memory.creator_persona.get_structured_persona_async", new_callable=AsyncMock, return_value=None))
        stack.enter_context(patch("workers.llm_worker._settings", MagicMock(llm_path=mode, context_engine_enabled=False, context_engine_observational=False, context_engine_sample_rate=0.0, user_lock_ttl=60, auto_approve_threshold=0.80, llm_provider="ollama", ollama_model="qwen2.5:3b")))
        stack.enter_context(patch("core.config.get_settings", return_value=MagicMock(llm_path=mode, context_engine_enabled=False, context_engine_observational=False, context_engine_sample_rate=0.0, llm_provider="ollama", ollama_model="qwen2.5:3b")))
        try:
            from workers.llm_worker import process_message
            gid=_gid(10,"msg",10)
            await process_message(user_id=10, user_message="msg", telegram_message_id=10, username="u", first_name="f", persona="p", generation_id=gid)
            await asyncio.sleep(0.02)
            assert telemetry.get("runtime_mode")==expected
        finally:
            stack.close()

@pytest.mark.asyncio
async def test_retrieval_truthful_and_budget():
    from context_engine.gatherer import GathererConfig, MemorySource
    stack=contextlib.ExitStack()
    # keep inside function to avoid block limit
    with patch("commerce.fan_knowledge.retrieve_relevant_knowledge", new_callable=AsyncMock, return_value=[]), \
         patch("commerce.fan_knowledge.get_fan_knowledge", new_callable=AsyncMock, return_value=[{"subject":"city","value":"Nairobi","status":"CURRENT","confidence":0.9},{"subject":"hobby","value":"gaming","status":"CURRENT","confidence":0.8}]), \
         patch("commerce.embedding_model.encode_message", new_callable=AsyncMock, return_value=[0.5]*384), \
         patch("commerce.embedding_model.encode_messages_sync", return_value=[[0.5]*384, [0.5]*384]):
        src=MemorySource()
        cfg=GathererConfig(creator_id=1, user_id=1, current_message="Nairobi")
        items=await src.gather(cfg)
        rm=getattr(cfg,"_retrieval_metrics",None)
        assert rm is not None
        assert rm["lexical_threshold"]==80
        assert rm["semantic_threshold"]==0.30
        assert rm["merged_candidate_count"]<=10
    from context_engine.models import TOTAL_CONTEXT_BUDGET
    from context_engine.budget import HEADER_RESERVE_TOKENS, EFFECTIVE_TOTAL_BUDGET
    assert TOTAL_CONTEXT_BUDGET==2600
    assert HEADER_RESERVE_TOKENS==60
    assert EFFECTIVE_TOTAL_BUDGET==2540
    from context_engine.assembler import ContextAssembler
    from context_engine.models import ContextCategory, AuthorityLevel, ContextItem, RetrievalScore
    from context_engine.budget import estimate_tokens
    assembler=ContextAssembler()
    candidates=[]
    for i in range(5):
        candidates.append(ContextItem(item_id=f"id{i}", category=ContextCategory.MEMORY, content="x"*2000, authority=AuthorityLevel.DETERMINISTIC_DERIVATION, trust="authoritative", token_cost=estimate_tokens("x"*2000), retrieval_score=RetrievalScore(1,0.5,1,0.8,0.6,0.8,0.7), source="fan_memory", priority=5, creator_id=1, user_id=1, metadata={}))
    snapshot=assembler.assemble(candidates, query="test")
    assert snapshot.total_tokens<=2600
    assert isinstance(snapshot.metadata.get("truncation_count"), int)
