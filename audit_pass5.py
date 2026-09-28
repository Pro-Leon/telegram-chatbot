import asyncio, time, sys
sys.path.insert(0, "E:/chatbot")
from context_engine.models import AuthoritativeState
from context_engine.worker_integration import observe_context_engine
from core.context_compact import build_one_call_from_snapshot
from commerce.relationship import derive_relationship_state
from datetime import datetime, timezone

async def main():
    # Step 1: Show authoritative derivation args
    print("=== Step1 Authoritative assembly ===")
    # The assembly does NOT derive relationship_state; it only fetches user. The derivation is in FanStateSource with purchase_count 0.
    # We simulate what authoritative would have if it did derive:
    for funnel, pc, mc in [("warming",0,27), ("warming",3,27), ("engaged",0,27), ("new",0,27)]:
        rel = derive_relationship_state(funnel_stage=funnel, purchase_count=pc, last_purchase_days_ago=None, last_message_days_ago=0.1, message_count=mc, has_active_offer=False)
        print(f"funnel={funnel} pc={pc} mc={mc} last_msg 0.1d -> Relationship={rel.value}  [commerce/relationship.py:107] args: funnel_stage={funnel}, purchase_count={pc}, message_count={mc}")

    # Step 4: Build synthetic AuthoritativeStates with contradictory funnel vs relationship
    print("\n=== Step4 Synthetic contradiction ===")
    for funnel, expected in [("warming","cold?"), ("engaged","warm?"), ("new","new")]:
        # Create recent 27 msgs mock (just count)
        recent = tuple({"direction":"inbound","content":f"msg {i}","created_at": datetime.now(timezone.utc).isoformat()} for i in range(27))
        # Use Message count 27
        auth = AuthoritativeState(
            creator_id=1, user_id=8151382101, generation_id="pass5-audit", current_message="hey there",
            timestamp=time.time(), user={"first_name":"Mason","funnel_stage":funnel,"message_count":27}, profile={},
            recent_messages=recent, summary="test", persona="You are Sunny Skye", structured_persona=None, persona_name="Sunny Skye",
            conversation_state=None, conversation_state_dict={}, commerce_context_text="", llm_context=None
        )
        from core.conversation_contract import derive_participants, derive_contract
        participants = derive_participants(auth)
        contract = derive_contract(auth, participants)
        object.__setattr__(auth, "participants", participants)
        object.__setattr__(auth, "conversation_contract", contract)
        # What FanStateSource will derive with purchase_count 0?
        # It uses _derive_rel with funnel_stage, purchase_count 0, last_message_days_ago from recent[-1]
        # Let's simulate:
        rel_fan = derive_relationship_state(funnel_stage=funnel, purchase_count=0, last_purchase_days_ago=None, last_message_days_ago=0.01, message_count=27, has_active_offer=False)
        print(f"\nSynthetic funnel={funnel} -> FanStateSource Relationship: {rel_fan.value} [context_engine/gatherer.py:298 purchase_count=0]")
        print(f" Authoritative user.funnel_stage={auth.user.get('funnel_stage')} [authoritative_assembly.py:82-85 get_user]")
        # Workers carrier: relationship_context_text is not directly relationship_state but via relationship_context; check workers/llm_worker.py:2019
        # For this audit, we check what observe_context_engine yields
        obs = await observe_context_engine(user_id=8151382101, creator_id=1, user_message="hey there", generation_id="pass5-audit", enabled=True, authoritative_state=auth, conversation_state={})
        pipeline = obs.pipeline_result
        # Find STATE block content
        state_items = [i for i in pipeline.snapshot.items if i.category.value=="state"]
        for it in state_items:
            print(f"  STATE block: {it.content[:300]!r} tokens={it.token_cost} cat={it.category}")
            if "Relationship:" in it.content:
                print(f"    -> Extracted Relationship line in STATE")
        # Carrier via deterministic enrichment: workers/llm_worker:2015 relationship_context_text is from relationship_context, not from FanStateSource. But we can check build_one_call output
        final = build_one_call_from_snapshot(authoritative_state=auth, pipeline_result=pipeline)
        # Find RELATIONSHIP line in final
        for idx,m in enumerate(final):
            c=m.get("content","")
            if "Relationship:" in c or "RELATIONSHIP" in c:
                print(f"  final[{idx}] {m['role']} contains Relationship: {c[:400].replace(chr(10),' | ')}")
            if "Funnel:" in c:
                print(f"  final[{idx}] funnel: {c[:400].replace(chr(10),' | ')}")
        # Also check what purchase_count 3 would give
        rel_pc3 = derive_relationship_state(funnel_stage=funnel, purchase_count=3, last_purchase_days_ago=None, last_message_days_ago=0.01, message_count=27, has_active_offer=False)
        print(f"  If purchase_count=3 -> Relationship would be {rel_pc3.value} [real commerce_offers count] vs FanState 0->{rel_fan.value} CONTRADICTION")

    # Check workers/llm_worker carriers: we can simulate building conversational commerce state
    print("\n=== Deterministic carriers vs FanState ===")
    # The carriers are relationship_context_text etc., but they are derived from relationship_state which is currently from FanState 0, not from real purchase count

    # Check scorer/dedup
    print("\n=== Conflict resolution ===")
    # Does scorer keep both cold and engaged? No, it's same category STATE with different content; dedup won't dedup because fact identity is different
    # But both would be in different messages: STATE block vs carrier block
    # Category tokens
    obs2 = await observe_context_engine(user_id=8151382101, creator_id=1, user_message="hey there", generation_id="pass5-audit2", enabled=True, authoritative_state=auth, conversation_state={})
    print(f"category_tokens {obs2.pipeline_result.category_tokens} STATE vs carriers in final: check final blocks")

asyncio.run(main())
