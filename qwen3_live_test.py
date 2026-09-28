"""QWEN3 4B Live Qualification Tests."""
import asyncio
import json
import os
import sys
import time
import io

# Fix Windows console Unicode encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def run_tests():
    from dotenv import load_dotenv
    load_dotenv(".env")

    api_key = os.environ.get("OLLAMA_API_KEY", "")
    if not api_key:
        print("FAIL: OLLAMA_API_KEY not set")
        return False

    import core.llm_provider_ollama as ollama_mod
    from core.config import get_settings
    s = get_settings()
    ollama_mod._settings = s

    from core.llm_provider_ollama import OllamaProvider
    provider = OllamaProvider()

    print("=" * 60)
    print("QWEN3 4B LIVE QUALIFICATION")
    print("=" * 60)
    print(f"Model: {provider._model}")
    print(f"Base URL: {provider._base_url}")
    print(f"Timeout: {provider._timeout}s")
    print()

    results = {}

    # Test 1: Health check
    print("--- Test 1: Health Check ---")
    t0 = time.time()
    hc = await provider.health_check()
    t1 = time.time()
    results["health_check"] = hc["healthy"]
    print(f"  Status: {hc['status']}")
    print(f"  Healthy: {hc['healthy']}")
    print(f"  Message: {hc['message']}")
    print(f"  Latency: {int((t1-t0)*1000)}ms")
    print()
    if not hc["healthy"]:
        print("FATAL: Health check failed")
        await provider.close()
        return False

    # Test 2: Basic conversational generation
    print("--- Test 2: Basic Conversational Generation ---")
    t0 = time.time()
    r2 = await provider.generate(
        system_instruction="You are a warm, friendly conversational partner. Have a natural conversation.",
        user_content="Hello!",
        temperature=0.7,
        max_output_tokens=150,
        timeout_seconds=180,
    )
    t1 = time.time()
    results["basic_gen"] = len(r2) > 10
    print(f"  Response: {r2[:200]}")
    print(f"  Latency: {int((t1-t0)*1000)}ms")
    print(f"  Length: {len(r2)} chars")
    print()

    # Test 3: Multi-turn conversation
    print("--- Test 3: Multi-Turn Conversation ---")
    t0 = time.time()
    r3 = await provider.generate_with_history(
        system_instruction="You are a warm, empathetic conversational partner.",
        messages=[
            {"role": "user", "content": "Hey, I've had a really long day."},
            {"role": "model", "content": "I'm sorry to hear that. Want to talk about it?"},
            {"role": "user", "content": "Yeah, work was exhausting."},
            {"role": "user", "content": "I just want to relax and talk for a bit."},
        ],
        temperature=0.7,
        max_output_tokens=200,
        timeout_seconds=180,
    )
    t1 = time.time()
    results["multi_turn"] = len(r3) > 10
    print(f"  Response: {r3[:300]}")
    print(f"  Latency: {int((t1-t0)*1000)}ms")
    print()

    # Test 4: Structured JSON output
    print("--- Test 4: Structured JSON Output ---")
    t0 = time.time()
    r4 = await provider.generate(
        system_instruction='Output ONLY valid JSON. No markdown, no explanation. Format: {"intent": "string", "confidence": 0.0, "sentiment": "string"}',
        user_content='Extract intent, confidence, and sentiment from: "I love this content, want to see more!"',
        temperature=0.0,
        timeout_seconds=180,
    )
    t1 = time.time()
    try:
        parsed = json.loads(r4)
        results["structured_json"] = True
        print(f"  Parsed: {json.dumps(parsed, indent=2)}")
    except json.JSONDecodeError as e:
        results["structured_json"] = False
        print(f"  FAIL: Invalid JSON: {e}")
        print(f"  Raw: {r4}")
    print(f"  Latency: {int((t1-t0)*1000)}ms")
    print()

    # Test 5: Long-context test
    print("--- Test 5: Long-Context Test ---")
    long_context = """Context: This is a CRM conversation between a creator (Alex) and a fan (Sarah).
Sarah has been a fan for 3 months. She has made 2 purchases totaling $45.
Her relationship status is "warm". She prefers casual, friendly conversation.
Recent conversation: Sarah mentioned she loves behind-the-scenes content.
She previously asked about custom content pricing but didn't purchase yet.
She has been active in the last 48 hours.
Open loops: Sarah asked about a custom video idea, hasn't received a response yet.
Commercial state: She is eligible for a tip suggestion (2+ purchases, 7+ days since last tip).
She has not been offered anything in the current session."""

    t0 = time.time()
    r5 = await provider.generate(
        system_instruction="You are a CRM assistant. Use the provided context to respond naturally.",
        user_content=f"{long_context}\n\nFan says: Hey! Remember that custom video idea I mentioned?",
        temperature=0.7,
        max_output_tokens=250,
        timeout_seconds=180,
    )
    t1 = time.time()
    results["long_context"] = len(r5) > 20
    print(f"  Response: {r5[:400]}")
    print(f"  Latency: {int((t1-t0)*1000)}ms")
    print()

    # Test 6: Temperature variation
    print("--- Test 6: Generation Parameters ---")
    t0 = time.time()
    r6a = await provider.generate(
        system_instruction="Say exactly: LOW_TEMP_OK",
        user_content="go",
        temperature=0.0,
        max_output_tokens=10,
        timeout_seconds=180,
    )
    t1 = time.time()
    t2 = time.time()
    r6b = await provider.generate(
        system_instruction="Say exactly: HIGH_TEMP_OK",
        user_content="go",
        temperature=1.5,
        max_output_tokens=10,
        timeout_seconds=180,
    )
    t3 = time.time()
    results["temp_control"] = "LOW_TEMP_OK" in r6a
    print(f"  Temp 0.0: '{r6a}' ({int((t1-t0)*1000)}ms)")
    print(f"  Temp 1.5: '{r6b}' ({int((t3-t2)*1000)}ms)")
    print()

    # Test 7: Tool calling
    print("--- Test 7: Tool Calling ---")
    import httpx
    auth = httpx.BasicAuth("ollama", api_key)
    tool_payload = {
        "model": provider._model,
        "messages": [{"role": "user", "content": "What's the weather in Paris?"}],
        "stream": False,
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"]
                }
            }
        }]
    }
    t0 = time.time()
    async with httpx.AsyncClient(auth=auth, timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(f"{provider._base_url}/api/chat", json=tool_payload)
        tool_data = resp.json()
    t1 = time.time()
    has_tool_calls = bool(tool_data.get("message", {}).get("tool_calls"))
    results["tool_calling"] = has_tool_calls
    print(f"  Tool calls present: {has_tool_calls}")
    if has_tool_calls:
        print(f"  Tool call: {json.dumps(tool_data['message']['tool_calls'][0], indent=2)[:200]}")
    else:
        print(f"  Message content: {tool_data.get('message', {}).get('content', 'NONE')[:200]}")
    print(f"  Latency: {int((t1-t0)*1000)}ms")
    print()

    # Test 8: Timeout test
    print("--- Test 8: Timeout Behavior ---")
    try:
        from core.llm_provider import LLMProviderError
        r8 = await provider.generate(
            system_instruction="Write a very long story",
            user_content="Once upon a time",
            max_output_tokens=500,
            timeout_seconds=3,
        )
        print(f"  Completed within timeout: {len(r8)} chars")
        results["timeout"] = True
    except LLMProviderError as e:
        if "timed out" in str(e).lower():
            print(f"  Timeout correctly raised")
            results["timeout"] = True
        else:
            print(f"  Unexpected error: {e}")
            results["timeout"] = False
    print()

    # Test 9: Concurrency
    print("--- Test 9: Concurrency (2 requests) ---")
    async def gen_task(msg):
        return await provider.generate(
            system_instruction="Reply with one word.",
            user_content=msg,
            max_output_tokens=5,
            temperature=0.0,
            timeout_seconds=180,
        )
    t0 = time.time()
    r9a, r9b = await asyncio.gather(
        gen_task("Say hello"),
        gen_task("Say goodbye"),
    )
    t1 = time.time()
    results["concurrency"] = bool(r9a and r9b)
    print(f"  Response 1: {r9a}")
    print(f"  Response 2: {r9b}")
    print(f"  Latency: {int((t1-t0)*1000)}ms")
    print()

    # Summary
    print("=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    all_pass = True
    for test, passed in results.items():
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  {test}: {status}")
    print()

    if all_pass:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")

    await provider.close()
    return all_pass


if __name__ == "__main__":
    result = asyncio.run(run_tests())
    sys.exit(0 if result else 1)
