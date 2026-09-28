"""Qwen3 A/B Fast Benchmark — 10 key scenarios, 1 rep, think=true vs think=false.

Runs in ~20-30 min. Gives definitive data for the A/B verdict.
"""
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field

import httpx

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://ollama.brestalogistics.co.ke"
MODEL = "qwen3:4b"
ENDPOINT = "/api/chat"
NUM_PREDICT = 300
NUM_PREDICT_RETRY = 500

def _load_auth():
    import os
    from pathlib import Path
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    return (
        os.environ.get("OLLAMA_USERNAME", "ollama"),
        os.environ.get("OLLAMA_API_KEY", ""),
    )

USERNAME, API_KEY = _load_auth()

QWEN3_PARAMS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0,
    "presence_penalty": 1.5,
}

PERSONA = """You are Sarah, a warm and genuine content creator. Your personality is cheerful, approachable, and authentic. You care about your fans and enjoy connecting with them."""

CRM_SYSTEM_PROMPT = f"""{PERSONA}

Fan: Alex
PROFILE: 25, New York, Student, Photography, Hiking
RELATIONSHIP: warm
Stage: Warming up. Build rapport.

Rules:
- 2-4 sentences, match their energy
- Reference their history naturally
- Never reveal AI/bot/automated — you are human
- If asked "are you a bot?", deflect casually
- Vary sentence structure, avoid repetition
- No filler words (haha, lol, yeah yeah)
- No generic affirmations without substance"""

# 10 key scenarios: rapport, emotional, commercial, rejection, tips, ambiguous
SCENARIOS = [
    {"id": 1, "category": "rapport", "name": "simple_greeting", "message": "hey"},
    {"id": 2, "category": "emotional", "name": "fan_misses_creator", "message": "i really missed you today"},
    {"id": 3, "category": "emotional", "name": "fan_compliment", "message": "you're honestly one of the most beautiful people i've ever seen"},
    {"id": 4, "category": "commercial", "name": "explicit_buying_intent", "message": "i want to buy your content, how do i do that?"},
    {"id": 5, "category": "commercial", "name": "asking_price", "message": "how much does it cost?"},
    {"id": 6, "category": "rejection", "name": "stop_asking", "message": "please stop asking me to buy things"},
    {"id": 7, "category": "rejection", "name": "asks_if_bot", "message": "are you a bot? be honest with me"},
    {"id": 8, "category": "tips", "name": "tip_opportunity", "message": "you deserve a tip for being so sweet to me"},
    {"id": 9, "category": "ambiguous", "name": "short_message", "message": "k"},
    {"id": 10, "category": "emotional", "name": "vulnerable", "message": "i've been having a really hard time lately and talking to you helps"},
]

THINKING_LEAK_PATTERNS = [
    "let me think", "i need to", "first,", "okay,",
    "hmm,", "wait,", "so the user", "the user said",
    "i should", "my response", "as sarah", "we are sarah",
    "the fan", "the fan said", "thinking about",
    "i notice", "my approach", "first thought",
    "okay, user", "hmm, the", "wait, the",
    "i need to respond", "i'll respond", "my reply",
    "let me consider", "considering that",
]


def detect_leakage(text: str) -> list[str]:
    if not text:
        return []
    lower = text.lower()
    return [p for p in THINKING_LEAK_PATTERNS if p in lower]


@dataclass
class Result:
    scenario_id: int
    category: str
    name: str
    mode: str
    num_predict: int
    latency: float = 0.0
    status_code: int = 0
    content: str = ""
    thinking: str = ""
    content_tokens: int = 0
    thinking_tokens: int = 0
    total_eval: int = 0
    empty_content: bool = False
    timeout: bool = False
    error: str = ""
    leakage: list[str] = field(default_factory=list)
    leakage_count: int = 0
    retried: bool = False


async def run_single(client: httpx.AsyncClient, scenario: dict, mode: str) -> Result:
    think_flag = mode == "think_true"
    messages = [
        {"role": "system", "content": CRM_SYSTEM_PROMPT},
        {"role": "user", "content": scenario["message"]},
    ]

    result = Result(
        scenario_id=scenario["id"],
        category=scenario["category"],
        name=scenario["name"],
        mode=mode,
        num_predict=NUM_PREDICT,
    )

    # First attempt
    t0 = time.perf_counter()
    try:
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "think": think_flag,
            "options": {"num_predict": NUM_PREDICT, **QWEN3_PARAMS},
        }
        resp = await client.post(ENDPOINT, json=payload, timeout=180.0)
        first_lat = time.perf_counter() - t0
        result.latency = round(first_lat, 2)
        result.status_code = resp.status_code
        data = resp.json()
        msg = data.get("message", {})
        result.content = msg.get("content", "")
        result.thinking = msg.get("thinking", "")
        result.total_eval = data.get("eval_count", 0)
        result.content_tokens = len(result.content) // 4 if result.content else 0
        result.thinking_tokens = len(result.thinking) // 4 if result.thinking else 0
        result.empty_content = not bool(result.content.strip())
    except httpx.TimeoutException:
        result.latency = round(time.perf_counter() - t0, 2)
        result.timeout = True
        result.error = "timeout"
        return result
    except Exception as e:
        result.latency = round(time.perf_counter() - t0, 2)
        result.error = str(e)[:200]
        return result

    # Retry at 500 if empty (think=true only)
    if result.empty_content and think_flag and NUM_PREDICT < NUM_PREDICT_RETRY:
        result.retried = True
        sys.stdout.write(f" [retry@{NUM_PREDICT_RETRY}]")
        sys.stdout.flush()
        t1 = time.perf_counter()
        try:
            payload["options"]["num_predict"] = NUM_PREDICT_RETRY
            resp = await client.post(ENDPOINT, json=payload, timeout=180.0)
            retry_lat = time.perf_counter() - t1
            result.latency = round(first_lat + retry_lat, 2)
            result.status_code = resp.status_code
            result.num_predict = NUM_PREDICT_RETRY
            data = resp.json()
            msg = data.get("message", {})
            result.content = msg.get("content", "")
            result.thinking = msg.get("thinking", "")
            result.total_eval = data.get("eval_count", 0)
            result.content_tokens = len(result.content) // 4 if result.content else 0
            result.thinking_tokens = len(result.thinking) // 4 if result.thinking else 0
            result.empty_content = not bool(result.content.strip())
        except httpx.TimeoutException:
            result.latency = round(time.perf_counter() - t0, 2)
            result.timeout = True
            result.error = "timeout"
        except Exception as e:
            result.error = str(e)[:200]

    result.leakage = detect_leakage(result.content)
    result.leakage_count = len(result.leakage)
    return result


async def main():
    print("=" * 80)
    print("QWEN3 A/B FAST BENCHMARK (10 scenarios, think=true vs think=false)")
    print("=" * 80)
    print(f"Endpoint: {BASE_URL}{ENDPOINT}")
    print(f"Model: {MODEL}")
    print(f"num_predict: {NUM_PREDICT} (retry@{NUM_PREDICT_RETRY} on empty)")
    print()

    auth = httpx.BasicAuth(USERNAME, API_KEY)
    async with httpx.AsyncClient(auth=auth, base_url=BASE_URL) as client:
        # Verify connectivity
        print("Verifying connectivity...", end=" ")
        try:
            r = await client.get("/api/tags", timeout=15.0)
            models = r.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            if any(MODEL in n for n in model_names):
                print(f"OK (model={MODEL} available)")
            else:
                print(f"WARNING: model not found in {model_names}")
        except Exception as e:
            print(f"FAIL: {e}")
            return

        all_results = []

        # ── THINK=TRUE ──
        print(f"\n{'='*80}")
        print("PHASE: think=true (with retry@500)")
        print(f"{'='*80}")
        think_true_results = []
        for scenario in SCENARIOS:
            label = f"  [{scenario['name']}]"
            sys.stdout.write(f"{label}...")
            sys.stdout.flush()
            result = await run_single(client, scenario, "think_true")
            think_true_results.append(result)
            all_results.append(result)
            status = "OK" if not result.empty_content and not result.error else "FAIL"
            retry_tag = f" RE={result.num_predict}" if result.retried else ""
            leak_tag = f" LEAK={result.leakage_count}" if result.leakage_count else ""
            print(f" lat={result.latency:.1f}s eval={result.total_eval} "
                  f"content={'EMPTY' if result.empty_content else result.content_tokens} "
                  f"[{status}]{retry_tag}{leak_tag}")

        # ── THINK=FALSE ──
        print(f"\n{'='*80}")
        print("PHASE: think=false (no retry)")
        print(f"{'='*80}")
        think_false_results = []
        for scenario in SCENARIOS:
            label = f"  [{scenario['name']}]"
            sys.stdout.write(f"{label}...")
            sys.stdout.flush()
            result = await run_single(client, scenario, "think_false")
            think_false_results.append(result)
            all_results.append(result)
            status = "OK" if not result.empty_content and not result.error else "FAIL"
            leak_tag = f" LEAK={result.leakage_count}" if result.leakage_count else ""
            print(f" lat={result.latency:.1f}s eval={result.total_eval} "
                  f"content={'EMPTY' if result.empty_content else result.content_tokens} "
                  f"[{status}]{leak_tag}")

        # ── SUMMARY ──
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"{'='*80}")

        def summarize(results, label):
            total = len(results)
            ok = sum(1 for r in results if not r.empty_content and not r.error)
            empty = sum(1 for r in results if r.empty_content)
            errors = sum(1 for r in results if r.error)
            retried = sum(1 for r in results if r.retried)
            retry_succeeded = sum(1 for r in results if r.retried and not r.empty_content)
            lats = [r.latency for r in results if r.latency > 0]
            leaks = sum(1 for r in results if r.leakage_count > 0)
            avg_lat = sum(lats) / len(lats) if lats else 0
            p50 = sorted(lats)[len(lats)//2] if lats else 0
            p95 = sorted(lats)[int(len(lats)*0.95)] if lats else 0
            avg_eval = sum(r.total_eval for r in results) / total if total else 0
            avg_content_tok = sum(r.content_tokens for r in results) / total if total else 0
            print(f"\n  {label}:")
            print(f"    Success: {ok}/{total} ({100*ok/total:.0f}%)")
            print(f"    Empty: {empty}, Errors: {errors}")
            print(f"    Retried: {retried}, Retry succeeded: {retry_succeeded}")
            print(f"    Latency: avg={avg_lat:.1f}s P50={p50:.1f}s P95={p95:.1f}s")
            print(f"    Avg eval tokens: {avg_eval:.0f}")
            print(f"    Avg content tokens: {avg_content_tok:.0f}")
            print(f"    Thinking leakage: {leaks}/{total}")
            return {
                "success": ok, "total": total,
                "empty": empty, "errors": errors,
                "retried": retried, "retry_succeeded": retry_succeeded,
                "avg_lat": avg_lat, "p50": p50, "p95": p95,
                "avg_eval": avg_eval, "avg_content_tok": avg_content_tok,
                "leaks": leaks,
            }

        s_true = summarize(think_true_results, "think=true")
        s_false = summarize(think_false_results, "think=false")

        # ── PER-SCENARIO COMPARISON ──
        print(f"\n{'='*80}")
        print("PER-SCENARIO COMPARISON")
        print(f"{'='*80}")
        print(f"  {'Scenario':<25} {'think=true':<20} {'think=false':<20} {'Winner'}")
        print(f"  {'-'*25} {'-'*20} {'-'*20} {'-'*20}")

        true_by_id = {r.scenario_id: r for r in think_true_results}
        false_by_id = {r.scenario_id: r for r in think_false_results}
        true_wins = 0
        false_wins = 0
        ties = 0

        for sc in SCENARIOS:
            t = true_by_id[sc["id"]]
            f = false_by_id[sc["id"]]
            t_ok = "OK" if not t.empty_content and not t.error else "FAIL"
            f_ok = "OK" if not f.empty_content and not f.error else "FAIL"
            t_leak = "LEAK" if t.leakage_count > 0 else ""
            f_leak = "LEAK" if f.leakage_count > 0 else ""

            if t_ok == "OK" and f_ok == "FAIL":
                winner = "think=true"
                true_wins += 1
            elif t_ok == "FAIL" and f_ok == "OK":
                winner = "think=false"
                false_wins += 1
            elif t_ok == "OK" and f_ok == "OK":
                # Both OK — compare leakage
                if t.leakage_count == 0 and f.leakage_count > 0:
                    winner = "think=true (no leak)"
                    true_wins += 1
                elif f.leakage_count == 0 and t.leakage_count > 0:
                    winner = "think=false (no leak)"
                    false_wins += 1
                else:
                    winner = "tie"
                    ties += 1
            else:
                winner = "both fail"
                ties += 1

            t_str = f"{t_ok} {t.latency:.0f}s"
            f_str = f"{f_ok} {f.latency:.0f}s"
            print(f"  {sc['name']:<25} {t_str:<20} {f_str:<20} {winner}")

        print(f"\n  Tally: think=true={true_wins}, think=false={false_wins}, tie={ties}")

        # ── VERDICT ──
        print(f"\n{'='*80}")
        print("VERDICT")
        print(f"{'='*80}")
        if s_true["success"] > s_false["success"]:
            print("  WINNER: think=true")
            print(f"  Success rate: {s_true['success']}/{s_true['total']} vs {s_false['success']}/{s_false['total']}")
        elif s_false["success"] > s_true["success"]:
            print("  WINNER: think=false")
            print(f"  Success rate: {s_false['success']}/{s_false['total']} vs {s_true['success']}/{s_true['total']}")
        else:
            print("  WINNER: TIE (same success rate)")
            print(f"  Both: {s_true['success']}/{s_true['total']}")

        # Leakage verdict
        if s_true["leaks"] == 0 and s_false["leaks"] > 0:
            print(f"  Leakage: think=true CLEAN, think=false has {s_false['leaks']} leaks")
        elif s_false["leaks"] == 0 and s_true["leaks"] > 0:
            print(f"  Leakage: think=false CLEAN, think=true has {s_true['leaks']} leaks")
        elif s_true["leaks"] > 0 and s_false["leaks"] > 0:
            print(f"  Leakage: BOTH have leaks (true={s_true['leaks']}, false={s_false['leaks']})")
        else:
            print("  Leakage: BOTH CLEAN")

        # Latency comparison
        if s_true["avg_lat"] > 0 and s_false["avg_lat"] > 0:
            ratio = s_true["avg_lat"] / s_false["avg_lat"]
            print(f"  Latency: think=true avg={s_true['avg_lat']:.1f}s, think=false avg={s_false['avg_lat']:.1f}s (ratio={ratio:.2f}x)")

        print(f"\n{'='*80}")
        print("DONE")
        print(f"{'='*80}")

        # Save raw results
        out_path = Path(__file__).resolve().parent.parent.parent / "docs" / "ab_benchmark_raw.json"
        out_data = []
        for r in all_results:
            out_data.append({
                "scenario_id": r.scenario_id, "category": r.category, "name": r.name,
                "mode": r.mode, "num_predict": r.num_predict, "latency": r.latency,
                "status_code": r.status_code, "content": r.content[:200],
                "thinking_preview": r.thinking[:200] if r.thinking else "",
                "content_tokens": r.content_tokens, "thinking_tokens": r.thinking_tokens,
                "total_eval": r.total_eval, "empty_content": r.empty_content,
                "retried": r.retried, "error": r.error,
                "leakage_count": r.leakage_count,
            })
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
        print(f"\nRaw results saved to {out_path}")


if __name__ == "__main__":
    from pathlib import Path
    asyncio.run(main())
