"""Qwen3 Think-Mode A/B Forensic Benchmark.

Calls the remote Ollama endpoint directly via httpx.
Tests think=true vs think=false with identical CRM workload.
"""
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import httpx

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Configuration ──────────────────────────────────────────────────────────

BASE_URL = "https://ollama.brestalogistics.co.ke"
MODEL = "qwen3:4b"
ENDPOINT = "/api/chat"

# Auth from .env
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

# Qwen3 official non-thinking parameters
QWEN3_PARAMS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0,
    "presence_penalty": 1.5,
}

NUM_PREDICT_DEFAULT = 300
NUM_PREDICT_LOW = 150
NUM_PREDICT_MIN = 80

# ── CRM System Prompt (from build_qwen3_system_prompt) ─────────────────────

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

# ── Test Scenarios ─────────────────────────────────────────────────────────

SCENARIOS = [
    # A. RAPPORT
    {"id": 1, "category": "rapport", "name": "simple_greeting", "message": "hey"},
    {"id": 2, "category": "rapport", "name": "casual_conversation", "message": "what's up? how was your day?"},
    {"id": 3, "category": "rapport", "name": "fan_asking_creator_doing", "message": "what are you doing right now?"},
    {"id": 4, "category": "rapport", "name": "returning_fan_hours", "message": "hey! i'm back, been a few hours"},
    {"id": 5, "category": "rapport", "name": "returning_fan_days", "message": "hey sarah, it's been like a week since i last messaged you"},
    # B. EMOTIONAL / RELATIONSHIP
    {"id": 6, "category": "emotional", "name": "fan_misses_creator", "message": "i really missed you today"},
    {"id": 7, "category": "emotional", "name": "fan_compliment", "message": "you're honestly one of the most beautiful people i've ever seen"},
    {"id": 8, "category": "emotional", "name": "fan_affectionate", "message": "you mean so much to me, you know that?"},
    {"id": 9, "category": "emotional", "name": "fan_feeling_ignored", "message": "do you even care that i'm here? feels like you've been distant"},
    {"id": 10, "category": "emotional", "name": "fan_disappointed", "message": "i was really hoping to hear from you yesterday"},
    # C. COMMERCIAL
    {"id": 11, "category": "commercial", "name": "curious_about_content", "message": "what kind of stuff do you usually post?"},
    {"id": 12, "category": "commercial", "name": "explicit_buying_intent", "message": "i want to buy your content, how do i do that?"},
    {"id": 13, "category": "commercial", "name": "asking_price", "message": "how much does it cost?"},
    {"id": 14, "category": "commercial", "name": "price_too_high", "message": "that's way too expensive for me"},
    {"id": 15, "category": "commercial", "name": "hesitating", "message": "i'm thinking about it but i'm not sure yet"},
    # D. REJECTION / BOUNDARIES
    {"id": 16, "category": "rejection", "name": "declines_offer", "message": "no thanks, i'm not interested in buying anything"},
    {"id": 17, "category": "rejection", "name": "stop_asking", "message": "please stop asking me to buy things"},
    {"id": 18, "category": "rejection", "name": "annoyed", "message": "you're being really annoying right now"},
    {"id": 19, "category": "rejection", "name": "asks_for_human", "message": "can i talk to a real person?"},
    {"id": 20, "category": "rejection", "name": "asks_if_bot", "message": "are you a bot? be honest with me"},
    # E. TIPS
    {"id": 21, "category": "tips", "name": "tip_opportunity", "message": "you deserve a tip for being so sweet to me"},
    {"id": 22, "category": "tips", "name": "fan_supports", "message": "i want to support you somehow, you really brighten my day"},
    # F. AFTERCARE
    {"id": 23, "category": "aftercare", "name": "post_purchase", "message": "hey! just got your content, it's amazing"},
    {"id": 24, "category": "aftercare", "name": "thanks_after_purchase", "message": "thank you so much for the content, really worth it"},
    # G. AMBIGUOUS
    {"id": 25, "category": "ambiguous", "name": "short_message", "message": "k"},
    {"id": 26, "category": "ambiguous", "name": "multi_intent", "message": "hey i miss you but also can you tell me about your new stuff?"},
    {"id": 27, "category": "ambiguous", "name": "contextual", "message": "that thing you posted yesterday was so cool"},
    {"id": 28, "category": "ambiguous", "name": "low_info", "message": "mm"},
    # H. EXTENDED
    {"id": 29, "category": "rapport", "name": "emoji_message", "message": "😊😊😊"},
    {"id": 30, "category": "emotional", "name": "vulnerable", "message": "i've been having a really hard time lately and talking to you helps"},
]

# ── Thinking Leakage Detection ─────────────────────────────────────────────

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


def detect_thinking_leakage(text: str) -> list[str]:
    """Detect thinking/reasoning text leaked into visible content."""
    if not text:
        return []
    lower = text.lower()
    found = []
    for pattern in THINKING_LEAK_PATTERNS:
        if pattern in lower:
            found.append(pattern)
    return found


# ── Benchmark Harness ──────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    scenario_id: int
    category: str
    name: str
    mode: str  # "think_true" or "think_false"
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


async def run_single(
    client: httpx.AsyncClient,
    scenario: dict,
    mode: str,
    num_predict: int,
) -> BenchmarkResult:
    """Execute a single benchmark request with retry-at-500 on empty content (think=true only)."""
    think_flag = mode == "think_true"
    messages = [
        {"role": "system", "content": CRM_SYSTEM_PROMPT},
        {"role": "user", "content": scenario["message"]},
    ]

    result = BenchmarkResult(
        scenario_id=scenario["id"],
        category=scenario["category"],
        name=scenario["name"],
        mode=mode,
        num_predict=num_predict,
    )

    # --- First attempt at num_predict ---
    t0 = time.perf_counter()
    try:
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "think": think_flag,
            "options": {
                "num_predict": num_predict,
                **QWEN3_PARAMS,
            },
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

    # --- Retry at num_predict=500 if content is empty (match production adapter) ---
    if result.empty_content and think_flag and num_predict < 500:
        sys.stdout.write(f" [retry@500]")
        sys.stdout.flush()
        t1 = time.perf_counter()
        try:
            payload["options"]["num_predict"] = 500
            resp = await client.post(ENDPOINT, json=payload, timeout=180.0)
            retry_lat = time.perf_counter() - t1
            result.latency = round(first_lat + retry_lat, 2)
            result.status_code = resp.status_code
            result.num_predict = 500

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

    result.leakage = detect_thinking_leakage(result.content)
    result.leakage_count = len(result.leakage)

    return result


async def run_benchmark(
    client: httpx.AsyncClient,
    scenarios: list[dict],
    modes: list[str],
    num_predict_values: list[int],
    repetitions: int = 2,
) -> list[BenchmarkResult]:
    """Run the full benchmark matrix."""
    all_results = []

    for np in num_predict_values:
        for mode in modes:
            for rep in range(repetitions):
                for scenario in scenarios:
                    label = f"[np={np} {mode} rep={rep+1}] {scenario['name']}"
                    sys.stdout.write(f"  {label}...")
                    sys.stdout.flush()

                    result = await run_single(client, scenario, mode, np)
                    result.num_predict = np  # ensure correct
                    all_results.append(result)

                    status = "OK" if not result.empty_content and not result.error else "FAIL"
                    sys.stdout.write(
                        f" lat={result.latency:.1f}s "
                        f"eval={result.total_eval} "
                        f"content={'EMPTY' if result.empty_content else result.content_tokens} "
                        f"[{status}]"
                    )
                    if result.leakage_count:
                        sys.stdout.write(f" LEAK={result.leakage_count}")
                    print()

    return all_results


# ── Quality Scoring ────────────────────────────────────────────────────────

QUALITY_DIMENSIONS = [
    "naturalness", "conversational_relevance", "emotional_intelligence",
    "context_awareness", "conciseness", "human_flow",
    "non_pushy", "relationship_preservation",
    "rejection_handling", "aftercare_handling",
    "tip_behavior", "buying_intent_handling",
    "no_fabrication", "no_authority_violation",
    "no_prompt_leakage", "no_reasoning_leakage",
    "no_artifacts", "no_repetition",
]

QUALITY_MAX = 5  # per dimension


def score_result(result: BenchmarkResult) -> dict[str, int]:
    """Score a benchmark result on quality dimensions.

    Returns dict of dimension -> score (0-5).
    Uses heuristic scoring based on content analysis.
    """
    scores = {}
    content = result.content or ""
    lower = content.lower()

    # Skip scoring for errors/timeouts/empty
    if result.error or result.timeout or result.empty_content:
        return {dim: 0 for dim in QUALITY_DIMENSIONS}

    # 1. Naturalness: check for conversational tone
    scores["naturalness"] = 4 if any(w in lower for w in ["hey", "hi", "oh", "wow", "sure", "yeah", "totally", "absolutely"]) else 3
    if len(content.split()) < 5:
        scores["naturalness"] = min(scores["naturalness"], 3)

    # 2. Conversational relevance: length + addressing the message
    word_count = len(content.split())
    scores["conversational_relevance"] = 4 if 10 <= word_count <= 80 else (3 if 5 <= word_count <= 100 else 2)

    # 3. Emotional intelligence: empathetic language present
    empathetic = any(w in lower for w in ["sorry", "understand", "feel", "totally get", "glad", "happy", "appreciate"])
    scores["emotional_intelligence"] = 4 if empathetic else 3

    # 4. Context awareness: references to fan name, history
    scores["context_awareness"] = 3  # baseline
    if any(w in lower for w in ["alex", "new york", "photography", "hiking"]):
        scores["context_awareness"] = 4

    # 5. Conciseness: 10-60 words ideal
    scores["conciseness"] = 4 if 10 <= word_count <= 60 else (3 if 5 <= word_count <= 80 else 2)

    # 6. Human flow: no robotic patterns
    robotic = any(w in lower for w in ["as an ai", "i am an ai", "i'm an ai", "as a bot", "language model"])
    scores["human_flow"] = 2 if robotic else 4

    # 7. Non-pushy: no aggressive selling
    pushy = any(w in lower for w in ["buy now", "purchase now", "click here", "limited time", "don't miss"])
    scores["non_pushy"] = 2 if pushy else 4

    # 8. Relationship preservation
    scores["relationship_preservation"] = 4 if not pushy and not robotic else 3

    # 9. Rejection handling (for rejection scenarios)
    if result.category == "rejection":
        respectful = any(w in lower for w in ["no problem", "totally understand", "no pressure", "respect", "fair enough"])
        scores["rejection_handling"] = 4 if respectful else 3
    else:
        scores["rejection_handling"] = 3  # N/A baseline

    # 10. Aftercare handling
    if result.category == "aftercare":
        warm = any(w in lower for w in ["glad", "enjoy", "thank", "happy you", "appreciate"])
        scores["aftercare_handling"] = 4 if warm else 3
    else:
        scores["aftercare_handling"] = 3

    # 11. Tip behavior
    if result.category == "tips":
        scores["tip_behavior"] = 4 if any(w in lower for w in ["thank", "appreciate", "sweet", "kind"]) else 3
    else:
        scores["tip_behavior"] = 3

    # 12. Buying intent handling
    if result.category == "commercial":
        helpful = any(w in lower for w in ["check", "link", "profile", "would love", "glad to"])
        scores["buying_intent_handling"] = 4 if helpful else 3
    else:
        scores["buying_intent_handling"] = 3

    # 13. No fabrication: no invented URLs, prices
    fabricated = any(w in lower for w in ["http", "www.", ".com", "$", "€", "£"])
    # But prices are OK in commercial context if not fabricated
    scores["no_fabrication"] = 3 if fabricated else 4

    # 14. No authority violation: no offer creation, no DropFans mentions
    authority_violation = any(w in lower for w in ["dropfans", "fangate", "offer created", "discount applied"])
    scores["no_authority_violation"] = 2 if authority_violation else 5

    # 15. No prompt leakage
    prompt_leak = any(w in lower for w in ["system prompt", "my instructions", "i was told to", "my rules say"])
    scores["no_prompt_leakage"] = 2 if prompt_leak else 5

    # 16. No reasoning leakage
    scores["no_reasoning_leakage"] = max(1, 5 - result.leakage_count * 2)

    # 17. No artifacts (weird characters, excessive emoji)
    import re
    emoji_count = len(re.findall(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF]', content))
    scores["no_artifacts"] = 4 if emoji_count <= 2 else (3 if emoji_count <= 4 else 2)

    # 18. No repetition: check for repeated phrases
    words = lower.split()
    if len(words) > 4:
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
        unique_ratio = len(set(bigrams)) / max(len(bigrams), 1)
        scores["no_repetition"] = 4 if unique_ratio > 0.7 else (3 if unique_ratio > 0.5 else 2)
    else:
        scores["no_repetition"] = 4

    # Clamp all to 0-5
    for dim in QUALITY_DIMENSIONS:
        scores[dim] = max(0, min(QUALITY_MAX, scores.get(dim, 3)))

    return scores


# ── Reporting ──────────────────────────────────────────────────────────────

def calculate_percentile(values: list[float], pct: float) -> float:
    """Calculate percentile from a list of values."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * pct / 100)
    idx = min(idx, len(sorted_v) - 1)
    return sorted_v[idx]


def compute_stats(results: list[BenchmarkResult]) -> dict[str, Any]:
    """Compute aggregate statistics for a set of results."""
    latencies = [r.latency for r in results if r.latency > 0 and not r.timeout]
    content_tokens = [r.content_tokens for r in results if not r.empty_content]
    thinking_tokens = [r.thinking_tokens for r in results if r.thinking_tokens > 0]
    total_eval = [r.total_eval for r in results if r.total_eval > 0]

    empty_count = sum(1 for r in results if r.empty_content)
    timeout_count = sum(1 for r in results if r.timeout)
    error_count = sum(1 for r in results if r.error and not r.timeout)
    leakage_count = sum(1 for r in results if r.leakage_count > 0)

    stats = {
        "count": len(results),
        "latency": {
            "p50": round(calculate_percentile(latencies, 50), 1) if latencies else 0,
            "p75": round(calculate_percentile(latencies, 75), 1) if latencies else 0,
            "p90": round(calculate_percentile(latencies, 90), 1) if latencies else 0,
            "p95": round(calculate_percentile(latencies, 95), 1) if latencies else 0,
            "p99": round(calculate_percentile(latencies, 99), 1) if latencies else 0,
            "mean": round(statistics.mean(latencies), 1) if latencies else 0,
            "min": round(min(latencies), 1) if latencies else 0,
            "max": round(max(latencies), 1) if latencies else 0,
        },
        "tokens": {
            "content_mean": round(statistics.mean(content_tokens), 1) if content_tokens else 0,
            "thinking_mean": round(statistics.mean(thinking_tokens), 1) if thinking_tokens else 0,
            "total_eval_mean": round(statistics.mean(total_eval), 1) if total_eval else 0,
        },
        "empty_responses": empty_count,
        "timeouts": timeout_count,
        "errors": error_count,
        "thinking_leakage": leakage_count,
    }
    return stats


def compute_quality_stats(results: list[BenchmarkResult]) -> dict[str, Any]:
    """Compute quality statistics."""
    all_scores = []
    for r in results:
        scores = score_result(r)
        all_scores.append(scores)

    if not all_scores:
        return {}

    dim_avgs = {}
    for dim in QUALITY_DIMENSIONS:
        vals = [s[dim] for s in all_scores if dim in s]
        dim_avgs[dim] = round(statistics.mean(vals), 2) if vals else 0

    total_scores = [sum(s.values()) for s in all_scores]
    total_avgs = [sum(s.values()) for s in all_scores if s]

    return {
        "dimension_averages": dim_avgs,
        "total_mean": round(statistics.mean(total_scores), 1) if total_scores else 0,
        "total_median": round(statistics.median(total_scores), 1) if total_scores else 0,
        "total_min": min(total_scores) if total_scores else 0,
        "total_max": max(total_scores) if total_scores else 0,
        "max_possible": len(QUALITY_DIMENSIONS) * QUALITY_MAX,
    }


def compute_relationship_scores(results: list[BenchmarkResult]) -> dict[str, Any]:
    """Compute human-likeness / relationship scores."""
    dims = [
        "naturalness", "emotional_intelligence", "context_awareness",
        "human_flow", "non_pushy", "relationship_preservation",
        "rejection_handling", "conciseness", "conversational_relevance",
        "no_repetition",
    ]
    max_per_dim = 10  # Scale up to 10 for this score

    all_scores = []
    for r in results:
        qs = score_result(r)
        rel_score = sum(min(max_per_dim, qs.get(d, 3) * 2) for d in dims)
        all_scores.append(rel_score)

    return {
        "dimensions": dims,
        "max_per_dimension": max_per_dim,
        "max_total": len(dims) * max_per_dim,
        "scores": all_scores,
        "mean": round(statistics.mean(all_scores), 1) if all_scores else 0,
        "median": round(statistics.median(all_scores), 1) if all_scores else 0,
        "min": min(all_scores) if all_scores else 0,
        "max": max(all_scores) if all_scores else 0,
    }


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    print("=" * 80)
    print("QWEN3 THINK-MODE A/B FORENSIC BENCHMARK")
    print("=" * 80)
    print(f"Endpoint: {BASE_URL}{ENDPOINT}")
    print(f"Model: {MODEL}")
    print(f"Auth: Basic ({USERNAME}:***{'*' * min(len(API_KEY), 4)})")
    print(f"Scenarios: {len(SCENARIOS)}")
    print(f"Repetitions: 2")
    print(f"Modes: think=true, think=false")
    print(f"num_predict: {NUM_PREDICT_DEFAULT}")
    print()

    # Verify connectivity
    auth = httpx.BasicAuth(username=USERNAME, password=API_KEY) if API_KEY else None
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=httpx.Timeout(30.0),
        auth=auth,
    ) as check_client:
        sys.stdout.write("Verifying connectivity... ")
        sys.stdout.flush()
        try:
            tags_r = await check_client.get("/api/tags", timeout=10.0)
            tags_r.raise_for_status()
            models = [m.get("name", "") for m in tags_r.json().get("models", [])]
            if MODEL not in models:
                print(f"FAIL: model {MODEL} not in {models}")
                return
            print(f"OK (model={MODEL} available)")
        except Exception as e:
            print(f"FAIL: {e}")
            return

    # Run benchmark
    auth = httpx.BasicAuth(username=USERNAME, password=API_KEY) if API_KEY else None
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=httpx.Timeout(180.0),
        auth=auth,
    ) as client:
        # Phase 2: Primary A/B at num_predict=300
        print("\n" + "=" * 80)
        print("PHASE 2: PRIMARY A/B (num_predict=300)")
        print("=" * 80)
        results_300 = await run_benchmark(
            client, SCENARIOS,
            modes=["think_true", "think_false"],
            num_predict_values=[NUM_PREDICT_DEFAULT],
            repetitions=2,
        )

        # Phase 10: Output length test
        print("\n" + "=" * 80)
        print("PHASE 10: OUTPUT LENGTH TEST")
        print("=" * 80)
        # Use subset of 5 diverse scenarios for output length test
        subset = [SCENARIOS[0], SCENARIOS[5], SCENARIOS[11], SCENARIOS[16], SCENARIOS[24]]
        results_len = await run_benchmark(
            client, subset,
            modes=["think_true", "think_false"],
            num_predict_values=[NUM_PREDICT_MIN, NUM_PREDICT_LOW, NUM_PREDICT_DEFAULT],
            repetitions=1,
        )

        # Phase 9: Concurrency test (1, 2, 3 concurrent)
        print("\n" + "=" * 80)
        print("PHASE 9: CONCURRENCY TEST")
        print("=" * 80)
        concurrency_results = {}
        concurrency_scenario = SCENARIOS[0]  # simple greeting
        for conc in [1, 2, 3]:
            for mode in ["think_true", "think_false"]:
                print(f"  {conc} concurrent x {mode}...")
                tasks = []
                for _ in range(conc):
                    tasks.append(run_single(client, concurrency_scenario, mode, NUM_PREDICT_DEFAULT))
                batch = await asyncio.gather(*tasks)
                lats = [r.latency for r in batch]
                ok = sum(1 for r in batch if not r.empty_content and not r.error)
                key = f"{conc}x_{mode}"
                concurrency_results[key] = {
                    "concurrency": conc,
                    "mode": mode,
                    "latencies": lats,
                    "mean_latency": round(statistics.mean(lats), 1),
                    "success": ok,
                    "total": conc,
                }
                print(f"    mean={statistics.mean(lats):.1f}s success={ok}/{conc}")

    # Compute statistics
    print("\n" + "=" * 80)
    print("COMPUTING STATISTICS")
    print("=" * 80)

    # Split by mode
    true_300 = [r for r in results_300 if r.mode == "think_true"]
    false_300 = [r for r in results_300 if r.mode == "think_false"]

    stats_true = compute_stats(true_300)
    stats_false = compute_stats(false_300)

    quality_true = compute_quality_stats(true_300)
    quality_false = compute_quality_stats(false_300)

    rel_true = compute_relationship_scores(true_300)
    rel_false = compute_relationship_scores(false_300)

    # Latency improvement
    if stats_true["latency"]["p50"] > 0:
        latency_improvement = round(
            (stats_true["latency"]["p50"] - stats_false["latency"]["p50"])
            / stats_true["latency"]["p50"] * 100, 1
        )
    else:
        latency_improvement = 0

    # Print summary
    print(f"\nTHINK=TRUE  P50={stats_true['latency']['p50']}s  P95={stats_true['latency']['p95']}s  "
          f"empty={stats_true['empty_responses']}  leak={stats_true['thinking_leakage']}")
    print(f"THINK=FALSE P50={stats_false['latency']['p50']}s  P95={stats_false['latency']['p95']}s  "
          f"empty={stats_false['empty_responses']}  leak={stats_false['thinking_leakage']}")
    print(f"\nLatency improvement (P50): {latency_improvement}%")
    print(f"Quality TRUE:  {quality_true['total_mean']}/{quality_true['max_possible']}")
    print(f"Quality FALSE: {quality_false['total_mean']}/{quality_false['max_possible']}")
    print(f"Relationship TRUE:  {rel_true['mean']}/{rel_true['max_total']}")
    print(f"Relationship FALSE: {rel_false['mean']}/{rel_false['max_total']}")

    # Build full results JSON
    full_results = {
        "meta": {
            "endpoint": f"{BASE_URL}{ENDPOINT}",
            "model": MODEL,
            "parameters": QWEN3_PARAMS,
            "num_predict_default": NUM_PREDICT_DEFAULT,
            "scenarios": len(SCENARIOS),
            "repetitions": 2,
        },
        "think_true": {
            "stats": stats_true,
            "quality": quality_true,
            "relationship": rel_true,
        },
        "think_false": {
            "stats": stats_false,
            "quality": quality_false,
            "relationship": rel_false,
        },
        "latency_improvement_pct": latency_improvement,
        "concurrency": concurrency_results,
        "output_length": {},
        "raw_results": [asdict(r) for r in results_300 + results_len],
    }

    # Output length analysis
    for np_val in [NUM_PREDICT_MIN, NUM_PREDICT_LOW, NUM_PREDICT_DEFAULT]:
        for mode in ["think_true", "think_false"]:
            mode_results = [r for r in results_len if r.mode == mode and r.num_predict == np_val]
            if mode_results:
                s = compute_stats(mode_results)
                key = f"{mode}_np{np_val}"
                full_results["output_length"][key] = s
                empty = s["empty_responses"]
                mean_lat = s["latency"]["mean"]
                mean_tok = s["tokens"]["content_mean"]
                print(f"  {key}: lat={mean_lat}s  content_tok={mean_tok}  empty={empty}/{len(mode_results)}")

    # Save JSON
    json_path = Path(__file__).resolve().parent / "qwen3_think_mode_ab.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(full_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved to: {json_path}")

    return full_results


if __name__ == "__main__":
    from pathlib import Path
    asyncio.run(main())
