"""Qwen2.5:3B Gate verification: request proof + 5 trials"""
import asyncio
import json
import hashlib
import time

from core.config import get_settings
from core.llm_provider_ollama import OllamaProvider
from core.one_call_pipeline import one_call_generation

async def payload_proof():
    print("=== ACTUAL OLLAMA REQUEST PROOF ===")
    s = get_settings()
    prov = OllamaProvider()
    print(f"provider_name={prov.provider_name}")
    print(f"provider_model={prov._model}")
    print(f"settings ollama_model={s.ollama_model}")
    print(f"settings ollama_num_ctx={s.ollama_num_ctx}")
    print(f"settings ollama_timeout={s.ollama_timeout}")
    # Build payload as OneCall does
    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "hey"}]
    payload = prov._build_payload(messages, num_predict=400, temperature=0.7, response_mime_type="application/json")
    print(f"payload model={payload.get('model')}")
    print(f"payload think={payload.get('think')}")
    print(f"payload format={payload.get('format')}")
    print(f"payload stream={payload.get('stream')}")
    print(f"payload options={payload.get('options')}")
    # Verify invariants
    assert payload["model"] == "qwen2.5:3b", f"model mismatch {payload['model']}"
    assert payload["options"]["num_ctx"] == 8192, f"num_ctx {payload['options']['num_ctx']}"
    assert payload["options"]["num_predict"] == 400
    assert payload["options"]["temperature"] == 0.7
    assert payload["format"] == "json"
    assert payload["think"] == False
    assert payload["stream"] == False
    print("PAYLOAD PROOF PASS")

async def trials():
    print("\n=== 5 LIVE TRIALS ===")
    test_messages = ["hey", "hi", "hello", "what's up", "how was your day"]
    results = []
    for i, msg in enumerate(test_messages):
        gen_id = hashlib.md5(f"gate-{i}:{msg}".encode()).hexdigest()[:12]
        start = time.monotonic()
        try:
            res = await one_call_generation(
                user_id=1000+i,
                creator_id=1,
                user_message=msg,
                persona="You are Sunny, warm and friendly",
                profile={},
                user={"first_name":"test","funnel_stage":"new","message_count":5},
                persona_name="Sunny",
                generation_id=gen_id,
            )
            elapsed = int((time.monotonic()-start)*1000)
            # Use telemetry fields
            provider = getattr(res, "provider_name", "unknown")
            model = getattr(res, "model_name", "unknown")
            is_valid = getattr(res, "is_valid", False)
            inp = getattr(res, "input_tokens", None)
            out = getattr(res, "output_tokens", None)
            lat = getattr(res, "latency_ms", elapsed)
            err = getattr(res, "validation_error", None)
            reply_preview = (res.reply[:60].replace(chr(10)," ") if res.reply else "<empty>")
            print(f"T{i+1} msg={msg!r} gen_id={gen_id} provider={provider} model={model} is_valid={is_valid} in={inp} out={out} latency_ms={lat} reply={reply_preview!r}")
            if err:
                print(f"  validation_error={err[:120]}")
            results.append(is_valid and provider=="ollama" and model=="qwen2.5:3b")
            if not is_valid:
                print(f"  FAIL trial {i+1}")
        except Exception as e:
            print(f"T{i+1} msg={msg!r} EXCEPTION {e}")
            import traceback; traceback.print_exc()
            results.append(False)
    passed = sum(results)
    print(f"\nTRIALS SUMMARY: {passed}/5 valid provider=ollama model=qwen2.5:3b")
    if passed == 5:
        print("5/5 PASS")
    else:
        print("FAIL - not all trials passed")
    return passed == 5

async def main():
    await payload_proof()
    ok = await trials()
    return ok

if __name__ == "__main__":
    asyncio.run(main())
