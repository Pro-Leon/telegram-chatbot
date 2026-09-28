import time, asyncio, statistics
from commerce.embedding_model import get_model
from commerce.unified_intelligence import analyze_message, normalize_message, _ensure_reference_cache
from commerce.intent_corpus import INTENT_CORPUS

# Warmup
print("Warming up...")
t0=time.monotonic()
model=get_model()
print(f"Model load warm: {time.monotonic()-t0:.2f}s, model={model is not None}")
if model is not None:
    t0=time.monotonic()
    _ensure_reference_cache()
    print(f"Reference cache: {time.monotonic()-t0:.3f}s, 110 vectors")

# Benchmark warm
import asyncio

async def bench():
    msgs = ["hey", "I want to buy", "how much is it?", "lol", "my dog Max", "I love your content", "can I tip you?", "I just paid", "where is my link?", "thanks!"]
    # Warm once
    await analyze_message("warmup hello")
    times=[]
    for msg in msgs*10:  # 100 iterations
        t0=time.monotonic()
        await analyze_message(msg)
        times.append((time.monotonic()-t0)*1000)
    # p50/p95/p99
    times_sorted=sorted(times)
    def p(n): return times_sorted[int(len(times_sorted)*n/100)]
    print(f"Warm total: p50={p(50):.1f}ms p95={p(95):.1f}ms p99={p(99):.1f}ms min={min(times):.1f} max={max(times):.1f} mean={statistics.mean(times):.1f}")

asyncio.run(bench())

# RapidFuzz only benchmark
from rapidfuzz import fuzz, process
import time as t
msgs=["hey beautiful"]
for msg in msgs:
    t0=time.monotonic()
    for _ in range(100):
        process.extract(message:=msg, [e["example_text"] for e in INTENT_CORPUS], scorer=fuzz.WRatio, score_cutoff=80, limit=3)
    print(f"RapidFuzz 100x: {(time.monotonic()-t0)/100*1000:.2f}ms per")
