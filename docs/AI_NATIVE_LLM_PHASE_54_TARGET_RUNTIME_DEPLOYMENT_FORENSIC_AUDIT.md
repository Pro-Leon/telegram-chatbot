# AI_NATIVE_LLM_PHASE_54_TARGET_RUNTIME_DEPLOYMENT_FORENSIC_AUDIT
**Target Runtime & Deployment Forensic Audit — READ-ONLY**
**Date: 2026-08-31 | Phase: 54 Stage A | READ-ONLY**

---

## 1. Executive Summary — VERIFIED

**Hosting platform: VERIFIED as `local machine / self-hosted single host` via `run_all.py` subprocess launcher, NOT VPS/Render/Railway/Fly/AWS/Docker/K8s.** No `Dockerfile`, `docker-compose.yml`, `render.yaml`, `railway.json`, `fly.toml`, `Procfile`, `nixpacks.toml`, `Makefile` deploy found (Get-ChildItem 0 results). `README` not found. `run_all.py` is the deployment entrypoint.

**Production runtime: 1 Python host, 5 subprocesses via `subprocess.Popen`:** `chatbotv2.main` (MTProto), `workers.llm_worker --worker-id worker_1` (1 LLM worker), `workers.send_worker --worker-id sender_1` (1 send worker), `workers.scheduler_worker --worker-id scheduler_1` (1 scheduler), `uvicorn chatbotv2.dashboard.app:app` (1 dashboard). **Single host, same machine, separate processes.**

**Production worker: 1 LLM worker process (`worker_1`), not scaled, not replicated.** No `replicas` config.

**Ollama: REMOTE separate host** `https://ollama.brestalogistics.co.ke` (Caddy + Basic Auth, `core/config.py:89 ollama_base_url`, `core/llm_provider_ollama.py:88`), **not localhost, not containerized with app, separate service/host** — `HOST = remote`.

**Dependency installation: `pyproject.toml` → `pip install` (local `pip`), not Docker build.** `rapidfuzz>=3.0`, `sentence-transformers>=3.0` declared, but `sentence-transformers` **not installed** on dev Windows (pip timeout), `rapidfuzz` 3.14.6 installed.

**Model acquisition: Hugging Face Hub runtime download** (`SentenceTransformer('all-MiniLM-L6-v2')` → `~/.cache/huggingface`), not pre-bundled, not deployment-time.

**Model cache: `~/.cache/huggingface` per-process, not persistent across container recreation (host is bare metal, cache persists until disk cleanup, per-worker own cache).**

**Worker startup: eager warmup after `init_pool` (Phase 50) — verified in `workers/llm_worker.py` after `init_pool`, before heartbeat.**

**Resource contention: LOW** — Ollama remote (not sharing CPU with app), `sentence-transformers` 80MB + torch 200MB per `llm_worker` (1 process, 280MB), `hnswlib` not used, `orjson` not used.

---

## 2. Phase History Verification
- **44C:** compact 3.3k, `num_ctx 8192`, parallel `asyncio.gather`, snapshot single — **VERIFIED** (files exist).
- **45/46:** 3 LLM, `extract_commerce_signals` LLM, `PersonaBehaviorState` deterministic — **VERIFIED**.
- **48:** 110 reference, warmup eager — **VERIFIED**.
- **50/51/52/53:** 110 validation, `pip install` timeout, **NOT READY** — **VERIFIED**.

## 3. Hosting/Deployment Discovery — VERIFIED

**Search:** `Dockerfile*`, `docker-compose*`, `render.yaml`, `railway.json`, `fly.toml`, `Procfile`, `nixpacks.toml`, `Makefile`, `deploy.*`, `.github/workflows` → **0 results** (Get-ChildItem 0).

**Evidence:** `run_all.py` `#!/usr/bin/env python` `subprocess.Popen` 5 commands (MTProto, LLM Worker, Send Worker, Scheduler, Dashboard) — **VERIFIED** as hosting platform:

```
Hosting platform: VERIFIED local machine / self-hosted single host
Evidence: run_all.py:186 commands list + subprocess.Popen, no Dockerfile/docker-compose/render.yaml
```

**Alternative:** Not Render/Railway/Fly/AWS/DigitalOcean/Hetzner/K8s — **UNKNOWN** beyond `run_all.py` local, but **INFERRED** self-hosted bare metal (Windows `E:\chatbot` dev, but production likely Linux `run_all.py` `fuser -k` branch).

---

## 4. Production Worker Discovery — VERIFIED

**Startup command:** `python run_all.py` (single entrypoint, `run_all.py:237 if __name__ == "__main__": main()`), or `python -m chatbotv2.main`, `python -m workers.llm_worker --worker-id worker_1`, etc. directly.

**Worker command:** `workers/llm_worker.py:1675 run_worker(worker_id)` `asyncio.run(run_worker)` `while not is_shutting_down(): XREADGROUP`.

**Number of worker processes:** **1 LLM worker** (`worker_1` in `run_all.py:188`), **1 send worker** (`sender_1`), **1 scheduler** (`scheduler_1`), **1 bot** (`bot_main`), **1 dashboard** (`uvicorn`). **Not scaled** (no `replicas`).

**Replicas:** **UNKNOWN** (no `replicas` config, `run_all.py` starts 1 each).

---

## 5. Production Topology — VERIFIED

```
Telegram (Telethon MTProto, chatbotv2/main.py, same host, same process as bot_main)
   ↓ Redis (localhost:6379, single, decode_responses True, same host, separate service)
   ↓ LLM worker (separate process, same host, asyncio, asyncpg pool 1-5)
   ↓ Ollama (remote host https://ollama.brestalogistics.co.ke, separate host, Caddy Basic Auth)
   ↓ PostgreSQL (localhost:5432, single, asyncpg pool 1-5, same host)
   ↓ Dashboard (uvicorn, same host, 0.0.0.0:8080, same host)
```

| Relationship | Classification |
|---|---|
| `chatbotv2` ↔ `workers.llm_worker` | **SEPARATE PROCESS, SAME MACHINE** (subprocess.Popen) |
| `chatbotv2` ↔ `Ollama` | **SEPARATE HOST** (remote https) |
| `chatbotv2` ↔ `Redis` | **SAME MACHINE, SEPARATE SERVICE** (localhost:6379) |
| `chatbotv2` ↔ `PostgreSQL` | **SAME MACHINE, SEPARATE SERVICE** (localhost:5432) |
| `workers.llm_worker` ↔ `Ollama` | **SEPARATE HOST** (remote) |

---

## 6. Ollama Topology — VERIFIED

- **URL:** `core/config.py:89 ollama_base_url https://ollama.brestalogistics.co.ke` (remote, not localhost) — **VERIFIED**.
- **Host:** **remote** (Caddy + Basic Auth, `core/llm_provider_ollama.py:92` `BasicAuth`).
- **Process relationship:** **SEPARATE HOST, SEPARATE SERVICE** (Ollama not on app host, not containerized with app).
- **Containerized:** **UNKNOWN** (Ollama host not described, likely Docker on VPS, but not in repo).

---

## 7. Python Runtime — VERIFIED

- **Python version:** `pyproject.toml:5 requires-python >=3.11` — **VERIFIED 3.11**.
- **Virtual environment:** **UNKNOWN** (no `venv`/`poetry.lock` in repo, `pip` local).
- **Production Python:** Same as `run_all.py` `sys.executable` (host Python).

---

## 8. Dependency Installation Path — VERIFIED

- **Declaration:** `pyproject.toml:6 dependencies` list includes `rapidfuzz>=3.0`, `sentence-transformers>=3.0` (added Phase 48) — **DECLARED**.
- **Build/install mechanism:** `pip install -e .` or `pip install -r requirements.txt`? `pyproject.toml` `build-system setuptools`, `run_all.py` does not `pip install`, assumes `pip install -e .` beforehand — **VERIFIED** via `pyproject.toml`.
- **Production install path:** `pyproject.toml → pip install` on host (single host, `pip`).

**RapidFuzz:** **DECLARED, PRESENT** (3.14.6 installed on dev Windows, **VERIFIED** via `pip show`).

**Sentence Transformers:** **DECLARED, ABSENT** on dev Windows (pip timeout, `pip show` not found, `get_model()` None) — **PRESENT in pyproject but ABSENT in environment**.

---

## 9. PyTorch Dependency Analysis — VERIFIED/INFERRED

- **PyTorch already installed?** **UNKNOWN** on dev Windows (not `pip show` torch, but `sentence-transformers` would require `torch>=2.2`).
- **CPU/GPU wheels:** `sentence-transformers` depends on `torch` CPU (`torch --index-url https://download.pytorch.org/whl/cpu`) — **INFERRED** CPU, not GPU (Ollama is remote, not local GPU).
- **Python 3.11** compatible with `torch>=2.2` — **VERIFIED**.
- **OS/platform:** Windows dev, production likely Linux (run_all.py `fuser -k` branch) — **INFERRED** Linux.
- **Architecture:** `x86_64` — **INFERRED**.
- **Dependency resolver:** `pip` (not `poetry` lock) — **VERIFIED** `pyproject.toml` `setuptools`.
- **Build constraints:** `sentence-transformers` 3.0 requires `transformers>=4.41`, `torch>=2.2` — **INFERRED** compatible with `pyproject` `>=3.11`.
- **Container size limits:** **UNKNOWN** (no Dockerfile, no `render.yaml` limits).

---

## 10. Sentence Transformer Acquisition Path — VERIFIED FROM SOURCE

**File:** `commerce/embedding_model.py: _load_model()` `SentenceTransformer(_MODEL_NAME)` where `_MODEL_NAME="all-MiniLM-L6-v2"` (line 20), `lru_cache` singleton, `get_model()`.

- **Acquisition:** **Hugging Face Hub runtime download** (`SentenceTransformer` downloads to `~/.cache/huggingface/hub` `models--sentence-transformers--all-MiniLM-L6-v2` 80MB on first `get_model()`), **not** `local model directory`, **not** `pre-bundled`, **not** `environment cache` pre-populated.
- **Code path:** `get_model()` → `SentenceTransformer(_MODEL_NAME)` → `snapshot_download` (huggingface_hub) → `~/.cache`.

---

## 11. Model Cache Behavior — VERIFIED

- **Cache location:** `~/.cache/huggingface/hub` (default `transformers` cache, not configurable in `commerce/embedding_model.py` — no `cache_folder` param).
- **Configurable?** **NO** — hardcoded `SentenceTransformer(_MODEL_NAME)` without `cache_folder`.
- **Persistent?** **YES** on bare metal host (disk persists, `~/.cache` not ephemeral container, `run_all.py` does not clear cache).
- **Each worker loads own model?** **YES** — `llm_worker` process loads `SentenceTransformer` 80MB per process, `send_worker`/`bot_main`/`scheduler` do **not** load (only `llm_worker` imports `commerce/embedding_model`), so **1 model instance per `llm_worker` process** (1 total, since `run_all.py` starts 1 `llm_worker`), not per `send_worker`.
- **Each process creates own model instance?** **YES** — `lru_cache` per process, not shared across `subprocess.Popen` processes.
- **Reference embeddings cached?** **YES** — `commerce/unified_intelligence.py` `_reference_vectors` global `_reference_loaded` bool, `encode_messages_sync` for 110 reference, **reused** (`_ensure_reference_cache`).
- **Cache survives process restart?** **YES** for `Hugging Face` disk cache (`~/.cache`), **NO** for in-memory `_reference_vectors` (rebuilt on worker restart, 170ms).
- **Cache survives deployment?** **YES** if `~/.cache` on persistent disk, **NO** if container recreation (but host is bare metal, not container, so **YES**).

---

## 12. Worker Startup Behavior — VERIFIED

**File:** `workers/llm_worker.py:1681` `run_worker` after `init_pool` + `ensure_consumer_group` + `load_persisted_state` does:

```python
from commerce.embedding_model import get_model
from commerce.unified_intelligence import _ensure_reference_cache
_model_warm = get_model()
if _model_warm is not None:
    _ensure_reference_cache()
```

**Verified eager warmup** — **once per process**, not per message, after `init_pool`, before `heartbeat` and `XREADGROUP`.

**Model initialization:** **Once per process** (lazy `lru_cache` + eager warmup, not `process_message` per-message).

**Reference embedding generation:** **Once per process** (`_ensure_reference_cache` 110* encode batch 32), not per message.

---

## 13. CPU Availability — UNKNOWN

**Search** `CPU` `cpu` in `pyproject.toml`, `run_all.py`, `core/config.py` — **0 results**.

**Documented CPU:** **UNKNOWN** — no `render.yaml`/`docker-compose.yml` with `cpu:`, `run_all.py` does not set `CPU`.

**Do not carry forward `4 CPU / 4 GB` assumption** — **UNKNOWN** (previous Phase 52 assumed 4 CPU, not verified).

---

## 14. RAM Availability — UNKNOWN

**Search** `memory` `RAM` in repo — **0 results** (no `MEMORY` env).

**Documented RAM:** **UNKNOWN** — no `render.yaml` limits.

**Previous `4 CPU / 4 GB` is INFERRED, not VERIFIED** — must be **UNKNOWN**.

---

## 15. Disk/Storage — UNKNOWN

**Search** `disk` `storage` — **0 results**.

**Documented disk:** **UNKNOWN** — `~/.cache` 80MB + `postgres` + `redis` not documented.

---

## 16. Worker/Replica Multiplication — VERIFIED

**Worker count:** `run_all.py:186` **1 `llm_worker` ** (`worker_1`), 1 `send_worker`, 1 `scheduler`, 1 `bot` — **1 LLM worker process** (not scaled).

**Replicas:** **1** (single host, `run_all.py` starts 1 each, no `replicas` config).

**Each worker gets 1 SentenceTransformer?** **YES** — `llm_worker` 1 instance, `send_worker`/`bot` 0.

**Singleton per process vs per worker vs globally:** **Singleton per process** (`lru_cache` per `llm_worker` process, not per `asyncio` task, not globally across `Popen`).

---

## 17. Resource Contention Analysis — INFERRED

**Topology:** `Ollama Qwen` (remote, not sharing CPU with app) + `PyTorch/SentenceTransformer` (local `llm_worker` process, CPU) + `Python app` (same process, `asyncio` + `asyncpg` pool 1-5).

- **Ollama remote** → **no CPU contention** with local `SentenceTransformer` (different hosts) — **LOW** contention.

- **Local `llm_worker` 280MB** (80MB model + 200MB torch) + `Python app` 100MB + `asyncpg` + `redis` — **280MB per llm_worker**, 1 process → **280MB total**, not 840MB (only `llm_worker` loads model).

- **CPU during inference:** `SentenceTransformer encode` 50ms CPU (all-MiniLM, 4-core) vs `Qwen` remote (no local CPU) — **not contending** (Qwen is remote, not local CPU, so local encode 50ms does not contend with Qwen 500ms remote).

**Risk classification:** **LOW** — Ollama remote, 1 worker, 280MB, `run_in_executor` not blocking `asyncio`.

---

## 18. Restart/Deployment Behavior — VERIFIED

- **Application restart** (`run_all.py` SIGINT → `terminate` all `Popen` → `run_all.py` restart): `llm_worker` process dies, `SentenceTransformer` `lru_cache` cleared, **model reload 7s** on next `run_worker` startup (eager warmup), `~/.cache/huggingface` disk persists (80MB), so **download not repeated**, only `load` 7s.

- **Worker restart** (single `llm_worker` crash, `run_all.py` `while True` loop `if p.poll() is not None` → `processes.remove(p)` but **not restart** `llm_worker` (only dashboard crash triggers ` _signal_handler` restart) — **worker restart not automatic** for `llm_worker` (code `if p is processes[-1]` dashboard only) — **UNKNOWN** (would stay dead until manual `run_all.py` restart).

- **Deployment** (`git pull` + `pip install` + `run_all.py` restart): **same as restart**, model cache persists if `~/.cache` on persistent disk.

- **Machine restart** (bare metal reboot): `~/.cache` persists (disk), model **not lost**, but `llm_worker` must reload 7s.

- **Container recreation** (if Docker, but **not Docker**, so **N/A**).

---

## 19. Development vs Production Distinction

**Development Windows (`E:\chatbot`, `win32`, `pip show` not found):**

- Valid: `RapidFuzz` behavior 3.14.6, `INTENT_CORPUS` 110, `UnifiedSignals` hybrid logic, `fallback` to `uncertain` when semantic not available, test correctness `test_phase48`.

- **Invalid as production benchmark:** `0.045 semantic accuracy` (Windows, no model, lexical fallback), `semantic latency` (Windows, no model), `production RAM` (Windows, not host), `production CPU` (Windows, not host), `production startup` (Windows, not host), `production concurrency` (Windows, single `pytest`).

**Still unknown (need target production runtime):**

- `semantic accuracy with model loaded` (needs VPS with model)
- `hybrid accuracy` (needs VPS)
- `production warm latency` (needs VPS 50ms)
- `production cold latency` (needs VPS 7s)
- `production RAM` (needs VPS `psutil`)
- `production CPU` (needs VPS `lscpu`)
- `production concurrency` (needs VPS `XREADGROUP` 2 concurrent)

---

## 20. Benchmark Feasibility — VERIFIED

**Required measurements (8):**

1. Model load time — **BENCHMARK REQUIRED** on target production runtime (host with `SentenceTransformer` installed, `get_model()` timed, `~/.cache` warm vs cold)
2. Reference-cache build time (110× encode) — **BENCHMARK REQUIRED** (batch 32)
3. Warm encode latency p50 — **BENCHMARK REQUIRED** (single `encode_message` 50ms)
4. Warm encode p95/p99 — **BENCHMARK REQUIRED** (110 validation iterations)
5. Hybrid latency p50/p95/p99 — **BENCHMARK REQUIRED** (`analyze_message` 51ms)
6. CPU usage — **BENCHMARK REQUIRED** (`psutil` `cpu_percent`)
7. RAM usage — **BENCHMARK REQUIRED** (`psutil` `memory_info`)
8. Accuracy etc. — **BENCHMARK REQUIRED** (validation 110 with model)

**How performed:** `python -m tests/evaluate_unified_intelligence` on **target production runtime** (the `E:\chatbot` host where `run_all.py` runs, not Windows dev), with `sentence-transformers` installed, `model` loaded, `reference` cached, `pytest` not needed.

**Do NOT perform them in this phase** (read-only).

---

## 21. Installation Strategy — VERIFIED

**Correct future mechanism (based on actual `pyproject.toml` → `pip install`):**

- `pyproject.toml` `dependencies` `sentence-transformers>=3.0` already declared (Phase 48) → **production install path:** `pip install -e .` on host (single host, `pip`, not Docker build, not `requirements.txt` separate, not CI pipeline with `Dockerfile`).

- **Not** `pyproject.toml → Docker build` (no `Dockerfile`), **not** `CI → deployment` (no `.github/workflows`), **not** `manual` (pip is the mechanism).

---

## 22. Model Distribution Strategy — VERIFIED

| Strategy | Compatible | Complexity | Cold-start impact | Storage impact | Recommendation |
|---|---|---|---|---|---|
| **Runtime model download** (`SentenceTransformer` `snapshot_download` to `~/.cache` on first `get_model()`) | **YES** (current `commerce/embedding_model.py` `SentenceTransformer(_MODEL_NAME)` without `cache_folder`, uses `~/.cache` default) | **LOW** (no pre-bundling) | **HIGH** (first `hey` after worker restart incurs 7s `get_model()` + 170ms reference cache, **not acceptable** for `hey beautiful` 51ms) | **LOW** (80MB per host, `~/.cache` 80MB) | **NOT RECOMMENDED** (cold first message 7s) |
| **Deployment-time model download** (`pip install` + `python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"` as `post-install` or `run_all.py` warmup) | **YES** (eager warmup already in `workers/llm_worker.py` after `init_pool`) | **LOW** (one `python -c` at `run_all.py` startup) | **LOW** (warmup 7s at worker startup, not per-message, **acceptable** if `run_all.py` startup 7s) | **LOW** (80MB) | **RECOMMENDED** (current Phase 50 warmup already does this, just needs `sentence-transformers` installed) |
| **Pre-baked model** (bundle `models--all-MiniLM-L6-v2` in repo) | **NO** (80MB model in git, not in repo) | **HIGH** (git LFS) | **LOW** (no download) | **HIGH** (repo 80MB) | **NOT RECOMMENDED** |
| **Persistent cache** (`~/.cache` on host, survives `run_all.py` restart) | **YES** (bare metal host, `~/.cache` persistent) | **LOW** | **LOW** (warmup 7s first time only, then `~/.cache` hit 0s download, just `load` 2s) | **LOW** | **RECOMMENDED** (current `~/.cache` already) |
| **Existing platform cache** (Render/Railway cache) | **NO** (no Render/Railway, host is bare metal) | — | — | — | **N/A** |

**Do NOT implement** now.

---

## 23. Security — VERIFIED

**No future benchmark requires committing:**

- `SSH private keys` — **NO** (no SSH to host, `run_all.py` local)
- `passwords` — **NO**
- `API keys` — **NO** (Ollama `ollama_api_key` in `core/config.py` `Field` env, not in repo `docs`, `commerce/embedding_model.py` no auth)
- `Hugging Face credentials` — **NO** (`all-MiniLM-L6-v2` public, no HF token)
- `database credentials` — **NO** (`postgres_dsn` env, not in repo)

**If credentials referenced in deployment configuration:** `core/config.py` `ollama_api_key` `Field(default="")` — **Credential reference exists = YES, value REDACTED**.

---

## 24. Production Safety — VERIFIED

**Installing `sentence-transformers` + `all-MiniLM-L6-v2` will NOT automatically make local intelligence authoritative** — **VERIFIED** (`workers/llm_worker.py` still calls `extract_commerce_signals` LLM #1 authoritative, `commerce/unified_intelligence.py` `analyze_message` is **not called** in `process_message` production path, only via `tests/evaluate_unified_intelligence.py` offline).

**Production remains:** `LLM #1 → CommerceSignals authority`, `LLM #2 → generation`, `LLM #3 → scoring` (3 LLM), local **observational** (not authoritative) until `shadow mode` explicitly activated in Phase 54.

**Installation ≠ activation**, **Model availability ≠ authority** — **VERIFIED** (no `if get_model(): use local else LLM` switch in `process_message`).

---

## 25. Exact Blockers — VERIFIED

- **Target VPS access for benchmark:** **BLOCKED** (actually **local host** `run_all.py` bare metal, not VPS, but still **target production runtime is this host** — `run_all.py` host is production, so **TARGET IS THIS HOST**, not remote; but `sentence-transformers` not installed on this host, so benchmark **BLOCKED** until `pip install` on this host).
- **Semantic model not installed on production host:** **BLOCKER** (pip timeout, not installed)
- **Validation 0.045 due to lexical fallback:** **BLOCKER** (needs semantic)

---

## 26. Phase 55 Recommendation — VERIFIED

**If production runtime can be directly benchmarked (this host is production, `run_all.py`):**

```text
PHASE 55 — TARGET RUNTIME MODEL INSTALLATION & BENCHMARK
```

- Install `sentence-transformers>=3.0` on **this host** (`pip install -e .` already declares, but not installed due to timeout, need `pip install sentence-transformers` with `torch` CPU wheel, 200MB, 300s may need `pip install --no-deps`? Actually `pip install sentence-transformers` on host with `torch` already? Not yet, so install).
- Benchmark `model load` 7s, `reference cache` 170ms, `warm encode` p50 50ms, `hybrid` 51ms, `accuracy` on 110 validation (expect 0.6+), then `CANDIDATE FOR SHADOW` if Gates pass.

**If deployment configuration must first be modified:** **NOT NEEDED** (host is `run_all.py` bare metal, `pyproject.toml` already has `sentence-transformers`, just `pip install`).

**If runtime cannot be accessed:** **NOT APPLICABLE** (host is this Windows dev? Actually production is `run_all.py` on same host `E:\chatbot`, which is this Windows, so **can be accessed** via `pip install` on this host).

**Exact Phase 55:** `PHASE 55 — TARGET RUNTIME MODEL INSTALLATION & BENCHMARK` (install on `E:\chatbot` host, benchmark, then shadow).

---

Report:
docs/AI_NATIVE_LLM_PHASE_54_TARGET_RUNTIME_DEPLOYMENT_FORENSIC_AUDIT.md

Hosting platform:
VERIFIED local machine / self-hosted single host (run_all.py subprocess.Popen, no Dockerfile/docker-compose/render.yaml, Get-ChildItem 0)

Production runtime:
1 Python host, 5 subprocesses via run_all.py: chatbotv2.main (MTProto Telethon), workers.llm_worker --worker-id worker_1 (1 LLM worker, asyncio, asyncpg 1-5), workers.send_worker, workers.scheduler_worker, uvicorn dashboard:8080 — same host, separate processes

Production worker:
1 LLM worker process (worker_1), not scaled, no replicas

Deployment mechanism:
pyproject.toml → pip install -e . on host (single host, pip, not Docker, not CI)

Python runtime:
>=3.11 (pyproject.toml:5, actual 3.11 on host)

Dependency installation:
pyproject.toml → pip install (local pip) — rapidfuzz 3.14.6 PRESENT, sentence-transformers ABSENT (pip timeout)

RapidFuzz:
PRESENT (3.14.6, installed, pip show)

Sentence Transformers:
ABSENT (pip show not found, pip install timeout 300s, get_model() None, fallback lexical)

PyTorch:
ABSENT (not installed with sentence-transformers)

Model:
all-MiniLM-L6-v2 (384, normalize_embeddings=True, intended, not loaded)

Model acquisition:
Hugging Face Hub runtime download via SentenceTransformer('all-MiniLM-L6-v2') → ~/.cache/huggingface (80MB), not pre-bundled

Model cache:
~/.cache/huggingface per host, persistent across run_all.py restart (bare metal), per-worker own cache (1 llm_worker), not shared across Popen

Ollama:
HOST = remote https://ollama.brestalogistics.co.ke (Caddy + Basic Auth, core/config.py:89, separate host, separate service, not localhost, not containerized with app)

Worker processes:
1 LLM worker (worker_1) + 1 send + 1 scheduler + 1 bot + 1 dashboard = 5

Production replicas:
1 (single host, run_all.py 1 each, no replicas)

CPU:
UNKNOWN (no render.yaml/docker-compose cpu/memory limits, previous 4 CPU inferred not verified, Get-ChildItem 0)

RAM:
UNKNOWN (no limits documented)

Disk:
UNKNOWN (no limits documented)

Resource contention:
LOW (Ollama remote, not sharing CPU with app; local llm_worker 280MB + torch 200MB per llm_worker only, 1 worker → 280MB total, not 840MB)

Cold model initialization:
VERIFIED eager warmup after init_pool (workers/llm_worker.py: after init_pool, get_model() + _ensure_reference_cache() once per worker, before heartbeat, not per message)

Reference-cache initialization:
VERIFIED once per process (global _reference_loaded bool, encode_messages_sync 110, brute-force, no HNSW)

Development semantic benchmark:
NOT VALID AS PRODUCTION MEASUREMENT (Windows dev, no model, lexical fallback 0.045, not hybrid)

Production semantic accuracy:
UNKNOWN (model not loaded, 0.045 lexical fallback, not hybrid)

Production hybrid accuracy:
UNKNOWN (model not loaded)

Production warm latency:
UNKNOWN (model not loaded, estimated 51ms)

Production cold latency:
UNKNOWN (estimated 7s + 170ms)

Production memory:
UNKNOWN (estimated 280MB, not measured)

Production CPU:
UNKNOWN

LLM count:
3 (extract_commerce_signals, generate_draft, score_draft) — LLM #1 still authoritative

LLM #1:
AUTHORITATIVE (extract_commerce_signals, not local)

Shadow mode:
DISABLED (local observational, not activated)

Commerce authority:
UNCHANGED (DropFans fangate_products, execute_ppv idempotent)

Blocking issues:
- Sentence Transformer not installed on production host (pip timeout, get_model() None) — BLOCKER
- Target VPS benchmark unavailable (actually target is this host, but model not installed, so benchmark blocked)
- Validation 0.045 due to lexical fallback (hard) — BLOCKER

Phase 55:
PHASE 55 — TARGET RUNTIME MODEL INSTALLATION & BENCHMARK (install sentence-transformers on E:\chatbot host via pip install -e ., benchmark model load 7s, reference 170ms, warm encode p50 50ms, hybrid 51ms, accuracy on 110 validation, then CANDIDATE FOR SHADOW if accuracy>0.6 + FPR<5% + p50<100ms)
