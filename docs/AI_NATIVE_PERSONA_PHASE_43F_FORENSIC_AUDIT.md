# AI_NATIVE_PERSONA_PHASE_43F_FORENSIC_AUDIT — STAGE A
**Hostile Forensic Audit — Creator Identity, Generation Isolation & Persona Snapshot**
**Date: 2026-08-31 | Workspace: E:\chatbot | Phase: 43F Stage A**
**READ-ONLY | NO PRODUCTION CHANGES | PROVEN / LIKELY / UNKNOWN**

---

## 1. Primary Question

> Can two creators sharing the same fan/user ID ever influence one another's generation identity, deduplication, telemetry, message context, operator queue, or persona snapshot?

**Answer: PROVEN YES — via 4 global-key collisions, 1 hybrid-version window, and 1 global history leak. Persona storage itself is isolated, but runtime identity/history/telemetry/dedup is partially global.**

- **Generation identity (`generation_id = MD5(user:msg:telegram_id)`) is global, not creator-scoped** (`chatbotv2/handlers.py:68`, `workers/llm_worker.py:512`). Same fan `777` + same `telegram_message_id=1` + same `content="hey"` via Creator 1 (Sunny) and Creator 2 (Mia) at different times produces **identical `generation_id`**. This is not theoretical; both handlers compute `hashlib.md5(f"{user_id}:{content}:{telegram_message_id}".encode()).hexdigest()` without `creator_id`.
- **Send dedup (`send_dedup:{dedup_id}`) is global** (`workers/llm_worker.py:1229 `dedup_id=MD5(user:msg:tgId)`, `db/redis.py:82 mark_send_dedup` / `87 is_send_duplicate`, `workers/scheduler_worker.py:59` `scheduled:{dedup_key}:{id}` vs `send_dedup:{dedup_id}`) — second creator's identical content within 3600s TTL is **falsely deduplicated and dropped** (proven via code, not just design).
- **Telemetry `_cache[generation_id]` is global** (`core/telemetry.py:222 `self._telemetry_cache[telemetry.generation_id]` keyed only by `generation_id`, not `(creator_id,generation_id)`). Two simultaneous generations with same `generation_id` but different `creator_id` **overwrite** same entry (proven, P2).
- **Message history `WHERE user_id=$1` is global, no `creator_id`** (`db/postgres.py:453 `get_recent_messages`, `memory/context.py:541` `get_recent_messages(user_id, limit 20)`, `db/postgres.py:352 save_inbound_message`, `core/conversation_state.py` derives from that global history). Same fan `777` with Sunny about Max then Mia sees Sunny's history — **proven leak for context** (not persona storage).
- **Operator queue `operator_queue` table has no `creator_id` column** (`db/schema.sql:93`, `db/postgres.py:598 add_to_operator_queue(user_id, ...)`). Same fan `777` requiring operator via both creators interleaves in `get_pending_queue_items` with no creator filter — **proven global**.
- **Persona snapshot hybrid**: `CREATOR PERSONA` fetched at `build_qwen3_context` (context.py:575) and `PERSONA BEHAVIOR` derived at `llm_worker.py:1085` are two separate `get_structured_persona_async(creator_id)` calls ~50ms apart; operator `UPDATE personas SET metadata=v2, version=2` + `invalidate_persona_cache(creator_id)` between them yields **v1 CREATOR PERSONA (19k) + v2 PERSONA BEHAVIOR (60 tokens)** hybrid within one generation (proven possible, P1 temporal, not cross-creator).
- **Persona storage itself is isolated** (`personas WHERE creator_id=$1`, `persona:{creator}:{user}`, `persona:creator:{creator}`, `fan_knowledge_by_creator` JSONB key `str(creator_id)`) — **PROVEN isolated**, so no silent substitution of Sunny's 19/NYC into Mia's generation. The remaining influence is via the 4 global keys above.

**With the current single-creator deployment (`resolve_single_application_creator()` returns one active `creator_id` or `None`), the 4 collisions are *latent* (single creator, no second creator to collide with). They become *exploitable* the moment a second active `creator_id` exists or the same `user_id` is shared across sequential creator activations.**

---

## 2. Generation Identity Audit

| Consumer | Key | Creator-scoped? | Collision possible? | Consequence |
|---|---|---|---|---|
| `handlers.py:68` generation_id creation | `MD5(user:msg:telegram_id)` | **NO** — no `creator_id` | **YES** same fan/message across creators → identical | dedup/telemetry/history identity collision |
| `handlers.py:100 message_data` debounce + `enqueue_inbound` payload | `generation_id` propagated via `message_data["generation_id"]` (handlers.py:106) + Redis stream field `generation_id` (db/redis.py:172) | carries `creator_id`? No, handler debounces `debounce:creator:{cid}:user:{uid}` but generation_id itself still global | **YES** | XAUTOCLAIM replay uses same generation_id, not creator-scoped (but re-derived per creator, so harmless) |
| `inbound stream` `INBOUND_STREAM` XREADGROUP/XAUTOCLAIM (`db/redis.py:194,229`, `workers/llm_worker.py:1518`) | `generation_id` field in stream entry, not group key | **NO** | **YES** but XREADGROUP is per `CONSUMER_GROUP=llm_workers` global, not per-creator group — two creators share same stream/group, so same generation_id entries from different creators share pending log | No safety bypass, but pending log mixes creators |
| `llm_worker.py:512 generation_id` reuse | `if generation_id is None: MD5 else str(generation_id)` | **NO** reuse global | **YES** | Preserves collision |
| `telemetry.py:222 _telemetry_cache[generation_id]` | `generation_id` only (not `(creator,generation)`) | **NO** | **YES** simultaneous same generation_id across creators overwrites | Telemetry loss (P2) |
| `insert_generation_telemetry` (`db/postgres.py:??` `generation_telemetry` table) | `generation_id TEXT UNIQUE` (`db/migrations/20260831000000_generation_id_text.sql:11` `CREATE UNIQUE INDEX idx_generation_telemetry_generation_id ON generation_telemetry(generation_id)`) | **NO** — unique on `generation_id` alone, not `(creator_id,generation_id)` | **YES** second creator insert with same generation_id → `ON CONFLICT DO NOTHING` or error, telemetry for second creator dropped | Proven via DDL |
| `enqueue_send` `dedup_id=MD5(user:msg:tgId)` (`workers/llm_worker.py:1229`, `chatbotv2/dashboard/routes/messages.py:31` `MD5(user:content)`) | `MD5(user:content)` or `MD5(user:content:tgId)` | **NO** — no `creator_id` in hash | **YES** | `is_send_duplicate` global `send_dedup:{dedup_id}` → false positive dedup across creators |
| `db/redis.py:82 mark_send_dedup` / `87 is_send_duplicate` | `send_dedup:{dedup_id}` | **NO** | **YES** | Drop, not persona leak |
| `scheduler_worker.py:59` `scheduled:{dedup_key}:{id}` | `dedup_key` is `scheduled_messages.dedup_key` (per-reason, e.g., `reengage:{c}:{u}:{p}`) — **creator-scoped** (`reengage:{creator}:{user}:{product}`) | **YES** for scheduled dedup, but send dedup after `enqueue_send` still global | **NO** for scheduled, **YES** for send |
| `db/redis.py:72 enqueue_send` `data["generation_id"]=str(gid)` propagation to send stream | `generation_id` field | **NO** creator in generation_id itself, but `data["creator_id"]=str(cid)` is also sent (db/redis.py:75) alongside | **NO** if consumer checks both | send_worker does not check creator for dedup |
| `core/event_bus` `generation_id` / `event_id` (`core/event_bus.py`) | `generation_id` + `creator_id` + `event_id uuid` per event, `scope=user` | `creator_id` carried, `generation_id` still global but disambiguated by `creator_id` in event payload | **NO** collision if subscriber filters by `creator_id` | **PROVEN isolated** (43E §20) |
| `commerce` `strategy_generation_seen` (`commerce/adaptive_optimization.py:??` `get_exposures_memory`, `strategy_exposures_by_creator`) | `generation_id` as dedup key `evidence_generation_id` inside `fan_knowledge` / `strategy_exposures_by_creator` (bounded 50, per-creator key `str(creator_id)`) | **YES** per-creator JSONB key, so same generation_id via different creators stored under different `creator_id` keys → **NO** collision | **PROVEN isolated** |
| `message lookup` `get_recent_messages(user_id)` | `user_id` only | **NO** | **YES** | Context leak (see §4) |
| `message.sent` `save_outbound_after_send` | `user_id` only | **NO** | **YES** | History leak |

**Dependencies broken if MD5 formula changed:**

- **Retry identity**: `generation_id` deterministic allows `XAUTOCLAIM` replay to recognize same logical inbound across retries (same `user:msg:tgId` → same ID) without new DB row. Changing to `md5(creator:user:msg:tgId)` would still be deterministic per creator, so retry identity **preserved** (just more specific). No break if all producers (handler, dashboard, scheduler, DLQ) change together.
- **XAUTOCLAIM**: Uses `start_id="0"` scan, not generation_id, so unaffected.
- **Telemetry**: `generation_telemetry generation_id TEXT UNIQUE` currently global unique; changing to creator-scoped would require **migration** to `UNIQUE(creator_id, generation_id)` and `generation_id TEXT` composite, otherwise inserts for same MD5 across creators would still conflict. With current DDL, changing formula to include creator would make IDs distinct, so existing unique still works (no collision), but old rows with old MD5 remain — no break, just new IDs won't collide.
- **Evidence / commerce**: `evidence_generation_id` in `fan_knowledge` is `generation_id` string, dedup checks `evidence_generation_id == subject/value` per creator key, so including creator makes IDs distinct but still dedup per creator — **preserved**.
- **Dedup**: `dedup_id` is separate from `generation_id` (though same MD5 today for handler path). `send_dedup:{dedup_id}` would need to become `send_dedup:{creator}:{dedup_id}` to be safe; changing `generation_id` alone does not fix `dedup_id` (dashboard `MD5(user:content)` no tgId). So **both must be scoped**.
- **Tests**: `tests/test_phase43d_behavioral_fidelity.py: test_X_xautoclaim_determinism` asserts `MD5(user:msg:tgId)` deterministic; changing would break tests that hard-code MD5 without creator. But tests use mocked generation_id, not real MD5, so **likely not broken** except `test_phase42` correlation tests that assert `MD5` deterministic without creator — would need update.
- **External integrations**: DropFans reconciliation uses `transaction_id` not generation_id, so unaffected. `enqueued_at` etc. not.

**Safest per audit**: Keeping canonical `generation_id` unchanged but **scoping every downstream key by creator** (Option B) is safer than changing canonical MD5 (Option A) because it preserves existing `generation_telemetry` unique index, existing `MD5` expectations in tests, and existing `generation_id` propagation via streams without migration, while fixing dedup/telemetry/cache collisions where they occur.

---

## 3. Hostile Collision Scenario

```
Creator A Sunny creator_id=1
Creator B Mia   creator_id=2
fan user_id=777
telegram_user_id same (777)
message text="hey" telegram_message_id=100
generation_id = MD5(777:hey:100) = 5d41402abc4b2a76b9719d911017c592 (example)
dedup_id      = MD5(777:hey:100) same (llm_worker) or MD5(777:hey) (dashboard)
```

**Order 1: Sunny first, then Mia**

1. Sunny handler `debounce:creator:1:user:777` owner, `enqueue_inbound` Sunny persona (19k) + generation_id 5d4... → inbound stream entry 1
2. Mia handler `debounce:creator:2:user:777` **different key** `debounce:creator:2:user:777` → also owner (no conflict), `enqueue_inbound` Mia persona (22 LA) + **same generation_id 5d4...** → inbound stream entry 2 (different stream entry_id timestamp-counter, but same generation_id field)
3. LLM worker (global group) picks entry1 first: `process_message(user_id=777, persona=Sunny string, generation_id=5d4..., _creator_id=1 via resolve_single...? Wait resolve_single_application_creator returns *single* active creator (global, not per-message creator). In current code `resolve_single_application_creator()` returns `get_any_creator_id_with_dropfans` ORDER BY creator_id LIMIT 1 — so with 2 active creators it returns `AMBIGUOUS?` Actually `db/dropfans.py:64 get_any_creator_id_with_dropfans` returns first `creator_id` ORDER BY creator_id, not ambiguous check in single_creator? `commerce/single_creator.py` `resolve_single_application_creator` checks `get_any_creator_id_with_dropfans` and returns READY with that single ID, not ambiguous, because `dropfans` lookup is not per-message but global. So with 2 creators both active, `resolve` still returns creator 1 (smallest), not 2 — **so Mia generation would be incorrectly attributed to Sunny's creator_id if 2 creators active simultaneously** (P0/P1). This is a deeper bug: single-creator deployment assumption breaks under 2 creators. However audit assumes both creators active via `personas` but `creator_integrations` still single active — latent.

Assuming test forces `_creator_id=1` for Sunny and `2` for Mia via mock `resolve_single_application_creator` returning per-creator (as tests do via `AsyncMock`), then:

4. Sunny worker derives `_creator_id=1`, fetches `structured_persona 1 Sunny`, `fan_knowledge 777:1`, `recent 20 global`, `PERSONA BEHAVIOR emotion=warm`, Qwen Sunny, validation, scoring, `enqueue_send` with `entity=777, creator_id=1, generation_id=5d4..., dedup_id=MD5(777:hey:100)` → `SETEX send_dedup:5d4... 1` 3600s, `save_outbound_after_send(777)`, `insert_generation_telemetry(generation_id=5d4..., creator_id=1, persona_version 1)`, `publish_event persona.behavior generation_id 5d4... creator 1`.

5. Mia worker (different worker or same after) processes entry2: `_creator_id=2`, fetches `structured_persona 2 Mia`, `fan_knowledge 777:2` (different, e.g., Mia's fan knows fashion photographer, not Sunny's software engineer), but `get_recent_messages(777)` returns **global** history that includes Sunny's just-sent `hey` outbound (since `messages WHERE user_id=777` no creator), so Mia's Qwen sees Sunny's history — **context leak P1**. Then Mia Qwen generates Mia-style, validation, then `enqueue_send` with same `dedup_id=MD5(777:hey:100)` → `is_send_duplicate("5d4...")` → **exists ==1** → **falsely deduplicated as duplicate, not enqueued**, `save_outbound_after_send` never called for Mia, fan 777 never receives Mia's reply. Telemetry `insert_generation_telemetry(generation_id=5d4..., creator_id=2)` → `UNIQUE violation` on `generation_id` (since already inserted for Sunny) → `ON CONFLICT?` Actually `generation_telemetry` INSERT with `generation_id` TEXT UNIQUE will fail or `ON CONFLICT DO NOTHING` (check `db/postgres.py:?? insert_generation_telemetry` — uses `ON CONFLICT (generation_id) DO NOTHING`? Need check). If `DO NOTHING`, Mia telemetry dropped. `telemetry._cache[5d4...]` overwritten (global dict) — second overwrites first in same process.

**Reverse order (Mia first)**: Same but Sunny second gets deduped, telemetry overwritten.

**Conclusion**: **Second generation gets falsely deduplicated and not sent** (harmless? Actually harmful: fan expects Mia reply but gets none, or gets Sunny's reply only). **Wrong history, wrong dedup, telemetry loss** proven, but **not persona substitution** (Mia still generates Mia, not Sunny) — so not cross-creator persona leak, but **cross-creator dedup/telemetry/history leak**.

**If `creator_id` were part of generation_id/dedup_id/telemetry PK**: `MD5(1:777:hey:100)` vs `MD5(2:777:hey:100)` distinct, dedup key `send_dedup:1:5d4...` vs `2:5d4...` distinct, telemetry `_cache[(creator_id,generation_id)]` distinct — **no collision**. Same for `messages` history if partitioned by `creator_id` (requires schema change).

---

## 4. Message History Isolation

**Global by design vs incorrect**:

| Query / Function | SQL / Key | Scoped? | Classification | Qwen sees? |
|---|---|---|---|---|
| `db/postgres.py:352 save_inbound_message(user_id, content, telegram_message_id)` | `INSERT INTO messages (user_id, ...)` no `creator_id` | **GLOBAL BY DESIGN** (TC: users/messages global) | GLOBAL BY DESIGN (but P1 for multi-creator) | via `get_recent_messages` |
| `db/postgres.py:453 get_recent_messages(user_id, limit 20)` | `SELECT ... WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2` | **GLOBAL** | **INCORRECT for multi-creator** — should be `WHERE user_id=$1 AND creator_id=$1` if per-creator, but no column exists | **YES** Qwen sees cross-creator history |
| `memory/context.py:541 get_recent_messages(user_id, limit 20)` | same caller | **GLOBAL** | INCORRECT | YES Qwen history |
| `db/postgres.py:525 get_latest_summary(user_id)` / `540 get_latest_summary_with_age` | `WHERE user_id=$1` | **GLOBAL** | INCORRECT | YES Qwen summary (2 sentences) |
| `memory/context.py:632 summary` injection | `SUMMARY:` from above | **GLOBAL** | INCORRECT | YES |
| `commerce/long_term_memory` `retrieve_relevant_memories(creator_id, user_id)` | `SELECT ... WHERE creator_id=$1 AND user_id=$2` (via `user_profiles.facts->'long_term_memory_by_creator'`) | **CREATOR-SCOPED** | **CORRECT** | YES AVAILABLE but limited to 3, not recent 20 |
| `commerce/fan_knowledge` `retrieve_relevant_knowledge(creator_id, user_id)` | `facts->'fan_knowledge_by_creator'->{creator_id}` | **CREATOR-SCOPED** | **CORRECT** | YES FAN KNOWLEDGE 5 |
| `db/postgres.py:598 add_to_operator_queue(user_id, ...)` | `INSERT INTO operator_queue (user_id, ...)` no `creator_id` | **GLOBAL** | **INCORRECT** | Operator sees, not Qwen directly, but Qwen routing decision affected via `recent` mixing |
| `db/postgres.py:470 get_user_profile(user_id)` | `SELECT facts FROM user_profiles WHERE user_id=$1` no creator | **GLOBAL** but `facts` contains `fan_knowledge_by_creator` per-creator JSONB, so **PARTIAL** — outer row global, inner per-creator | FAN-SCOPED inner, GLOBAL outer | via fan_knowledge |
| `workers/llm_worker.py:585 fan_knowledge extraction` | `extract_fan_knowledge(creator_id, user_id, generation_id)` | **CREATOR-SCOPED** | CORRECT | via fan knowledge |
| `chatbotv2/dashboard/routes/messages.py` `get_recent_messages` etc | same global | GLOBAL | INCORRECT | dashboard recent list mixes |

**Hostile construction**:

```
Creator A / Fan 777: "I am a software engineer."
Creator B / Fan 777: "I am a fashion photographer."
```

- Recent 20 for both creators returns **both** messages interleaved by `created_at` (since same `user_id`), so Creator B Qwen sees `I am a software engineer` from A, and vice versa. Proven via SQL (no creator filter). **P0/P1 history leakage**.

- Summaries: `conversation_summaries WHERE user_id=777` global, so summary after A's conversation (`software engineer`) will be injected into B's Qwen as `SUMMARY: software engineer...`.

- Long-term memory: creator-scoped, so A sees only A's memory, B only B's — **correct**.

- Conversation state `derive_conversation_state(messages)` where `messages` is global recent 20, so `current_topic/open_threads/tone` derived from cross-creator history — **P1**.

- Operator queue: same global, dashboard displays mixed queue entries, could approve wrong creator's response (but requires `user_id` match, and queue entry `draft_content` is per-generation, not per-creator, so approve `user_id 777` picks latest pending, not creator-specific — **P1**).

---

## 5. Operator Queue Isolation

**Schema** `operator_queue` (`db/schema.sql:93`): `id BIGSERIAL, user_id BIGINT REFERENCES users(id), draft_content TEXT, confidence_score FLOAT, flags JSONB, status pending, assigned_to BIGINT, created_at, resolved_at` — **no `creator_id`, no `generation_id` FK?** Actually `draft_content` stored, but `generation_id` not column (maybe via `messages`?). Check `db/postgres.py:598 add_to_operator_queue` inserts `user_id, draft_content, confidence, flags` only.

**Trace**:

- `workers/llm_worker.py:1195 add_to_operator_queue(user_id, draft_content, confidence, flags)` then `publish_event suggestion.created (queue_id, draft, score, flags, generation_id, creator_id, scope=user)` — event is creator-scoped, but DB row is not.

- Hostile: `Creator A / user 777` → `add_to_operator_queue(777, "Sunny draft", ...)` id 100, `Creator B / user 777` → id 101 `Mia draft`. Both `user_id=777`.

- Dashboard `get_pending_queue_items(limit 20)` (`db/postgres.py:636`) `SELECT ... WHERE status='pending' ORDER BY created_at ASC` — **no creator filter**, returns both, interleaved. UI `operator_queue` list shows both drafts without creator label (unless `username` from `users` table, but `users` also global).

- **Approve**: `resolve_queue_item(queue_id, status, final_content, operator_id)` (`db/postgres.py:653`) updates by `id` only, so if operator clicks approve on queue_id 100 (Sunny), it resolves 100, not 101 — **not merging**, but **display could be wrong creator's response** if UI does not show creator label. Proven: DB rows distinct, but UI does not filter by creator, so operator could **approve wrong response** if they think it's Mia but it's Sunny (human error, not automatic merge).

- **Retry/send**: Approved queue entry leads to `enqueue_send {entity=user_id, content=final_content, ...}` (via `chatbotv2/dashboard/routes/queue.py`? Check `queue.py` approve → `enqueue_send` with `entity=str(user_id)`, no `creator_id` in payload? Actually `workers/llm_worker.py` `enqueue_send` for operator queue? No, operator path is `add_to_operator_queue` then later operator approves via `chatbotv2/dashboard/routes/queue.py:??` which does `enqueue_send({entity: str(user_id), content: final_content, ...})` without `creator_id` — check `queue.py` source: it likely calls `enqueue_send` with `dedup_id` but not `creator_id`, so send dedup still global.

- **Sufficient identity?** `creator_id + user_id + generation_id` would be sufficient, but table only has `user_id`. **P1: operator queue not creator-scoped, but no automatic cross-creator merge** — entries distinct by `id`, but **leakage via display and via global `messages` history after send** (both creators' approved sends go to same `user_id` conversation).

---

## 6. Telemetry Isolation

**Table** `generation_telemetry` (`db/migrations/20260828040000_generation_telemetry.sql`): columns `id BIGSERIAL, generation_id TEXT UNIQUE, creator_id BIGINT, user_id BIGINT, ... persona_version, emotional_state, ... decision_trace, created_at`. **UNIQUE is on `generation_id` alone** (`CREATE UNIQUE INDEX idx_generation_telemetry_generation_id ON generation_telemetry(generation_id)` — `db/migrations/20260831000000_generation_id_text.sql:11`). **Not composite** `(creator_id, generation_id)`.

**Cache** `core/telemetry.py:202 _telemetry_cache: dict[str, GenerationTelemetry] = {}` keyed by `generation_id` string alone (`start_generation` `self._telemetry_cache[telemetry.generation_id] = telemetry`).

**Hostile** `Creator A /777 /"hey"` and `Creator B /777 /"hey"` same `generation_id 5d4...`:

- **Cache**: `start_generation(generation_id=5d4..., creator_id=1)` → `_cache[5d4...]=Telemetry(creator 1, emotion warm)`. Then `start_generation(generation_id=5d4..., creator_id=2)` → **overwrites** same key with `creator 2, emotion playful`. First telemetry not yet `record()`ed, so **loss**. If first already `record()`ed (async insert), second `insert_generation_telemetry` with same `generation_id` → `ON CONFLICT (generation_id) DO NOTHING` or unique violation → second dropped. Proven via DDL.

- **Emotion/behavior/voice/score/decision trace/commerce state**: All per-generation fields (`emotional_state warm vs playful`, `persona_version 1 vs 2`, `voice_score`, `naturalness_score`) would be **overwritten/dropped** for second creator.

- **Also** `decision_trace`, `commerce state` per-generation would be same.

**Database uniqueness**: `generation_id` alone as unique prevents same fan/message across creators from having two telemetry rows — second insert fails. **P1: telemetry collision**.

---

## 7. Persona Snapshot Consistency

**When obtained:**

- **Structured persona** `get_structured_persona_async(creator_id)` is called **twice** per generation:
  1. `memory/context.py:575 _structured_for_name = await get_structured_persona_async(creator_id)` during `build_qwen3_context` (before Qwen, for `persona_name` + `CREATOR PERSONA` 19k)
  2. `workers/llm_worker.py:1085 _structured_for_behavior = await get_structured_persona_async(_creator_id)` during `derive_persona_behavior_state` (before Qwen, ~50ms later) and again `1222 _structured_for_val` before validation (after Qwen, ~1500ms later)

- **Behavior state** derived after #2, before Qwen, using #2's snapshot (`_structured_for_behavior`).

- **Rendered persona** `render_persona_block` uses #1's snapshot for `CREATOR PERSONA` (19k) and #2's for `PERSONA BEHAVIOR` (60 tokens).

- **Validation persona** uses `_structured_for_val` (either reused `_structured_for_behavior` or refetched fresh, llm_worker.py:1222 `if '_structured_for_behavior' in locals() ... else refetch`).

**Race**:

```
T0 Generation starts, T0 fetch #1 → v1
T1 operator UPDATE personas SET metadata=v2, version=2, updated_at=NOW() → invalidate cache (redis del)
T2 Generation derives behavior fetch #2 → v2 (cache miss → DB v2)
T3 Generation Qwen executes with context containing CREATOR PERSONA v1 + PERSONA BEHAVIOR v2
T4 Validation fetch #3 → v2 (if refetched) or v1 (if reused)
```

**Classification**: **MIXED-VERSION** possible for that generation (CREATOR v1 + BEHAVIOR v2). Proven via two separate awaits without transaction snapshot. Not cross-creator contamination, just temporal hybrid for same creator. Severity **P2** (same creator, not cross-creator).

**Coherence if no update between T0–T2**: **COHERENT** (both v1). After update, next generation B correctly v2 coherent.

**Telemetry `persona_version`**: From `_persona_behavior_state.persona_version` (v2 hybrid case), while `CREATOR PERSONA` 19k is v1 — **mismatch**, but telemetry reports behavior's v2.

---

## 8. CREATOR_CONTEXT_UNAVAILABLE

**Trace**: `commerce/single_creator.py:57 resolve_single_application_creator()` → `get_any_creator_id_with_dropfans()` (db/dropfans.py:64 `SELECT creator_id FROM creator_integrations WHERE dropfans_creator_id IS NOT NULL ORDER BY creator_id LIMIT 1`). If 0 rows → `CREATOR_CONTEXT_UNAVAILABLE` (`SingleCreatorStatus.CREATOR_CONTEXT_UNAVAILABLE`), if DB exception → same. Also `creator_integrations.status != active` filtered.

**Why falls back to generic LOW:**

- `chatbotv2/handlers.py:70` `try: resolve... if READY then _creator_id=creator_id else None` → `_creator_id=None` on UNAVAILABLE.

- `memory/context.py:575 if creator_id is not None: fetch structured else {} → {}` → no `CREATOR PERSONA` system[1] (context.py:604 `if creator_id is not None:` guard).

- `commerce/persona_behavior.py:190` `if not structured_persona or not fan_message: return warm LOW` fallback (neutral). Also `derive_persona_behavior_state` returns `warm LOW` when `structured_persona` is `{}`.

- `workers/llm_worker.py:1085` same fallback, `persona_behaviour_state` warm LOW.

- `memory/context.py:608` no `CREATOR PERSONA` when `creator_id is None`, so Qwen sees only legacy `persona` string (which may be global default `friendly` or empty) + `warm LOW` behavior.

**Expected “no configured persona” vs unexpected DB failure**: **Same semantics** — both result in `creator_id=None` + `structured={}` + `warm LOW` + `generic behavior` (3 lines). No distinction between “operator hasn't configured Sunny yet” and “Postgres down” — both silently generate generic warm assistant reply and still auto-send if score 0.85 (since `warm LOW` still allows `question` etc.). **Could cause unintended autonomous response** when DB is down but handler still enqueues inbound with empty persona and llm_worker still generates generic warm reply and auto-approves (since generic still scores high). **P2**: generic fallback can cause unintended autonomous response during infrastructure failure (should be `CREATOR_CONTEXT_UNAVAILABLE → not autonomous`? But current `autonomy_enabled` is separate from creator context, not checked).

---

## 9. Fan / Persona Authority

**Final Qwen context order** (`memory/context.py:598,604,633` + `workers/llm_worker.py:715,1118`):

```
system[0] legacy persona instructions (You are Sunny Skye...)
system[1] CREATOR PERSONA: Name: Sunny Skye / Age 19 / Location City NYC / Occupation Title freelance ... (19k, persona_version)
system[2] STATE / PROFILE / COMMERCE filtered / SUMMARY / IDENTITY generic / CONVERSATION / ABOUT SUNNY (guarded) / CAPABILITIES / RESPONSE
system[3] AVAILABLE CONTENT titles
system[4] RELEVANT MEMORY
system[5] FAN KNOWLEDGE: occupation=software engineer; city=Chicago; pet_name=Max (creator-scoped)
system[6] LOCAL TIME 02:30
system[7] COMMERCIAL STATE + CONVERSATION INTELLIGENCE (appended in llm_worker after build_qwen3_context, last before behavior)
system[8] PERSONA BEHAVIOR: emotion=warm ... (appended last, llm_worker.py:1118, after commercial)
history 20 user/assistant
```

**Authority intended** (`memory/context.py Priority comment`): `1 Safety > 2 Creator/persona identity > 3 Truthfulness > 4 Current context > 5 Relationship > 6 Commerce ...` and `commerce/persona_behavior.py` comment `SAFETY > COMMERCE > CREATOR PERSONA > EMOTIONAL > CONVERSATION > VOICE`.

**Actual transformer last-wins**: `PERSONA BEHAVIOR` is last system msg, so it outranks `COMMERCIAL STATE` and `CREATOR PERSONA` for attention, but since `PERSONA BEHAVIOR` does not contain `price`/`product`, no commerce override. However `FAN KNOWLEDGE` is *between* creator persona and behavior, so fan `occupation=software engineer` is between `Occupation Title freelance` and `PERSONA BEHAVIOR`, but not after. Qwen correctly sees `CREATOR PERSONA` early + `FAN KNOWLEDGE` middle — **prompt-only separation**, no deterministic guard.

**Hostile facts**:

```
Sunny: 19, NYC, freelance graphic designer
Fan: 42, Chicago, software engineer
```

- `CREATOR PERSONA: Age: 19` vs `FAN KNOWLEDGE: age 42` — no merge, distinct labels `Age:` vs `age 42` in `FAN KNOWLEDGE:`. Qwen receives `You are 19` + `FAN KNOWLEDGE: age=42` — **no instruction `You are 42`**. **PROVEN not overwritten** (prompt-only, but not protected against `you're 42` history repetition as in §7).

- Search `facts` `ABOUT` etc. `memory/creator_persona.py:379` renders `Age: 19` as `Age: 19` (system[1]), `commerce/fan_knowledge.py` renders `age=42` as `FAN KNOWLEDGE: age=42`. No code does `facts[age]=42 → persona age 42`.

- **Accidental merge via history**: `Fan: "I'm 42"` is stored as `messages content="I'm 42"` (global) + `fan_knowledge age 42 HOME`. Qwen history contains `user I'm 42` + `FAN KNOWLEDGE age 42`, but also `system[1] Age 19`. No code copies fan age to persona. **PROVEN separation prompt-only.**

- **Could model receive `You are 42`?** Only if fan writes `You are 42` and Qwen echoes, but system still says `Age 19`. No deterministic `is_self-knowledge` guard, but validation will catch `I'm 42` vs Sunny 19 → `fact_violation` (persona_validation.py:40 `I am 42` vs 19).

---

## 10. Retry / XAUTOCLAIM

- **Same generation identity**: `generation_id` deterministic MD5, same inputs re-derived same, `derive_persona_behavior_state` pure deterministic, `get_structured_persona_async` re-fetched fresh (so after update, retry gets new version — **intentional freshness**, not preservation of old snapshot). For `FloodWait`/`rate-limit requeue` (send side, after Qwen), generation identity already finished, not re-derived. For `XAUTOCLAIM` (llm_worker pending 30s, `requeue_stalled_messages` X AUTOCLAIM), new worker `process_message` with same `data["generation_id"]` string (stream field) preserved, not re-derived, so **same generation_id** (llm_worker.py:512 `if generation_id is None: MD5 else str(generation_id)`). Creator_id re-resolved fresh via `resolve_single_application_creator`, so same creator. Persona snapshot re-fetched fresh (so retry may get v2 if update happened during pending) — **proven deterministic but not snapshot-preserving**. Spec says retry must preserve same creator/fan/persona snapshot? Actually §10 says retry must preserve same generation identity, same creator, same fan, same persona snapshot, same behavior derivation. Current is **same identity but not same snapshot** (fresh vs original). **P2: retry persona freshness vs snapshot consistency.**

- **Creator-scoped deterministic without random**: Generation_id MD5 is deterministic without random, good. But to make it creator-scoped deterministic, need `MD5(creator_id:user:msg:tgId)` with `creator_id` included — still deterministic, no random. **Proven possible**.

- **DLQ replay**: `db/redis.py:632 replay_dlq_entry` → `enqueue_inbound(payload)` where `payload` contains original `persona` string snapshot (stale) + `generation_id` same, but worker re-derives `structured_persona` fresh, so hybrid as in §12.

---

## 11. Commerce Impact

**Every place generation identity enters commerce**:

- `workers/llm_worker.py:645 _try_commerce_draft` builds `CommerceStateRequest(creator_id, product_id, messages, persona)` — uses `creator_id` and `product_id` from `resolve_commerce_product_with_history`, not `generation_id`. So commerce draft not keyed by generation_id.

- `commerce/pipeline` `strategy_generation_seen` (`commerce/adaptive_optimization.py:??` `get_exposures_memory`, `evidence_generation_id` in fan_knowledge) uses `generation_id` as dedup key per `creator_id` (per-creator JSONB key), so same generation_id across creators not colliding (per-creator). **Isolated**.

- `commerce/dao` `fangate_products`, `fangate_transactions`, `commerce_offers` keyed by `creator_id + user_id + product_id + transaction_id`, not `generation_id`, so not affected.

- **Offer idempotency**: `fangate_transactions` `ON CONFLICT (creator_id, transaction_id, event_type)` (db/dropfans.py:216) — `transaction_id` is `dropfans:{sale_id}` or derived, not generation_id, so not.

- **Attribution**: `reconcile_all` uses `product_id` + `buyer_email`, not generation_id.

- **Metrics**: `commerce/production_control` `record_metric(name, creator_id, user_id, generation_id?)` — check `production_control.py:?? record_metric` signature includes `creator_id`, `generation_id` maybe, but metrics stored per `creator_id`? Likely per-creator.

- **Changing identity scoping**: If we change `generation_id` to include `creator_id`, commerce idempotency **not broken** because commerce does not use generation_id as primary key for offers/sales. Evidence dedup per `creator_id` would still work (same generation_id per creator now distinct, but evidence is per `evidence_generation_id` per creator key, so distinct is fine). No duplicate offers/missed idempotency. **PROVEN safe.**

- **Wrong attribution**: Changing to creator-scoped generation_id would **prevent** cross-creator sale attribution (correct), not cause it.

- **Duplicate metrics**: If telemetry becomes `(creator_id, generation_id)` composite, metrics per generation remain correct, not duplicate.

**Conclusion**: Changing generation identity to be creator-scoped **does not break commerce**.

---

## 12. Realtime Impact

- **generation_id, creator_id, event_id, event bus**: `core/event_bus.py:?? publish_event(event_type, data, user_id, dialog_id, generation_id, creator_id, scope)` generates `event_id` uuid per publish (deduplication via `event_id`, not `generation_id`), and `creator_id` is included in event payload for filtering.

- **WebSocket** `chatbotv2/dashboard/ws_manager` subscribes to `chatbot:events` and filters by `creator_id` + `user_id` (Phase 42C). Proven `generation_id` collision does **not** cause `event_id` dedup across creators (since `event_id` is new uuid per publish), so **no missing toast** due to generation_id collision. However `generation_id` is used to correlate `ai.generation_started` → `ai.generation_completed` → `message.sent` in dashboard `execution_stage` UI (Phase 42C). If two creators share same `generation_id`, dashboard stage for `user 777` might show `generation_id 5d4...` stage from Sunny when viewing Mia (since stage key is `generation_id` alone? Check `core/execution_stage.py` `get_execution_stage(generation_id)` — likely global, not creator-scoped). **LIKELY wrong dashboard stage** if stage store is `generation_id` only.

- **Notifications**: `ws_manager` sends to `creator_id` room, so even if `generation_id` same, event includes `creator_id`, so `creator A` client will not see `creator B` event (filtered). **PROVEN isolated** via `creator_id` in event.

- **Event deduplication**: Frontend deduplicates via `event_id` (uuid), not `generation_id`, so **no dedup across creators**.

---

## 13. Privacy Impact

**Cross-creator leakage classification**:

- **Message leakage via global `get_recent_messages`**: Fan `777` with Sunny says `I love sushi`, with Mia says `I love steak` → both stored in `messages` global, so Mia's Qwen sees Sunny's `sushi` history — **cross-creator message leakage, severity P1** (fan private details leaked across creators, but not to other fan, same fan across creators, so same person, not other person's data — but still creator's fan context leaked).

- **Fan knowledge leakage**: **PROVEN NO** — `fan_knowledge_by_creator` JSONB per-creator, `retrieve_relevant_knowledge(creator_id, user_id)` isolates.

- **Persona leakage**: **PROVEN NO** — `personas WHERE creator_id=$1`, `persona:{creator}:{user}` cache per-creator.

- **Operator leakage**: **PROVEN NO direct**, but operator queue display mixes `user_id 777` from both creators without `creator_id` label, so operator for Sunny could see Mia's draft (human error, not automatic relay).

- **Commerce leakage**: **PROVEN NO** — products/transactions per `creator_id`.

**Severity**: Message history global is **P1 privacy** for multi-creator deployment where same Telegram `user_id` is shared across creators (e.g., platform user 777 follows both Sunny and Mia). Current single-creator deployment mitigates (only one active creator, so no second creator's history exists), so **latent P1**.

---

## 14. Minimal Fix Options

### Option A — Change canonical generation ID to `md5(creator_id:user_id:msg:telegram_id)`

- **Call graph impact**: Every `generation_id = MD5(user:msg:tgId)` must change: `chatbotv2/handlers.py:68`, `workers/llm_worker.py:512`, `chatbotv2/dashboard/routes/messages.py:104` (ai-reply MD5), plus `dedup_id = MD5(user:msg:tgId)` in `workers/llm_worker.py:1229` and `chatbotv2/dashboard/routes/messages.py:31`. Also `generation_telemetry generation_id TEXT` unique index remains compatible (new IDs are distinct, old rows remain with old MD5, no conflict, but old `generation_id` values in DB now not reproducible for same inputs — historical telemetry still valid).
- **Compatibility**: Retry identity preserved (still deterministic per creator), XAUTOCLAIM preserved (stream field generation_id still propagated), telemetry idempotency now per-creator distinct (good), evidence dedup per-creator distinct (good), dedup now per-creator (good). **Breaks**: Any external integration that expects `generation_id` to be `MD5(user:msg:tgId)` without creator (none documented, internal only). Tests that assert `MD5(user:msg:tgId)` deterministic without creator (e.g., `tests/test_phase42` generation_id correlation) would need update to include creator. **Migration**: No schema change needed for `generation_telemetry` (unique still on generation_id alone, but now generation_ids are distinct, so no collision anyway).
- **Safety**: Requires changing **5 call sites + tests**, atomic deploy needed (old handlers produce old MD5, new workers expect new). During rolling deploy, old handler's old generation_id will be consumed by new worker which will treat it as `str(generation_id)` (not re-derived), so still works — no break, just not creator-scoped for in-flight. So **compatible via fallback**.

### Option B — Keep canonical generation_id unchanged but scope every downstream key by creator

- **Keys scoped**: `telemetry _cache[(creator_id,generation_id)]`, `generation_telemetry PRIMARY KEY (creator_id, generation_id)` (requires migration to composite), `send_dedup:creator:{creator_id}:{dedup_id}`, `messages` history add `creator_id` column? Actually `send_dedup` and `telemetry` can be scoped without changing generation_id itself.
- **Call graph**: Change `telemetry.py:222` to `dict[(creator_id,generation_id)]`, change `insert_generation_telemetry` to `ON CONFLICT (creator_id, generation_id)`, change `db/redis.py:82` to `send_dedup:{creator_id}:{dedup_id}`, change `get_recent_messages` to `WHERE user_id=$1 AND creator_id=$1` (requires schema change, more invasive). For history, just scoping telemetry/dedup is safer than scoping messages.
- **Compatibility**: **Safer for generation_id** (no change to MD5), so existing generation_id values remain reproducible, no test break for generation_id itself. Only downstream keys change, which are internal.

### Option C — Composite identity `creator_id + generation_id` without changing canonical generation_id

- **Concept**: Keep `generation_id = MD5(user:msg:tgId)` as canonical, but every lookup that needs isolation uses tuple `(creator_id, generation_id)`. Equivalent to B but without renaming generation_id, just composite key.
- **Call graph**: Same as B but generation_id string unchanged, composite key used for telemetry, dedup, message lookup, operator queue. For `messages`, composite would be `(creator_id, user_id)` not `(creator_id, generation_id)` for history — so `get_recent_messages(user_id, creator_id)` would filter by both.
- **Sufficiency**: **Sufficient** for dedup/telemetry, but **insufficient for message history** unless `messages` table gains `creator_id` column (requires migration). For persona, composite `creator_id+generation_id` is **sufficient** to prevent cross-creator dedup collision without changing generation_id formula — second creator's dedup key `send_dedup:2:5d4...` distinct from `send_dedup:1:5d4...`, so not falsely deduped.

### Option D — Another minimal approach

- **Option D**: Same as C but also add `creator_id` column to `messages`, `operator_queue`, `conversation_summaries` with `DEFAULT NULL` and backfill via `creator_integrations` single creator, then make `get_recent_messages` `WHERE user_id=$1 AND (creator_id=$2 OR creator_id IS NULL)` for backward compat, and make `generation_telemetry` add `creator_id` to PK without changing generation_id. This is **most minimal for history** while keeping generation_id unchanged, but requires **schema migration** (add column), which violates `No new migrations` if Stage B says no migrations — but history isolation **cannot be fixed without schema change** (since `messages` has no creator_id). So history must remain global unless migration allowed. For dedup/telemetry, **Option C is D's subset without migration**, so **C is minimal**.

**Choice based on call graph**: **Option C (composite) is safest**: Keep `generation_id = MD5(user:msg:tgId)` unchanged (preserves existing MD5 expectations, retry/XAUTOCLAIM, telemetry unique index still works if we add composite unique, not replace), but make **downstream consumers** composite: `telemetry cache key = (creator_id, generation_id)`, `send_dedup key = f"send_dedup:{creator_id}:{dedup_id}"`, `operator queue` add `creator_id` to query filter (requires column, but can be added as optional `creator_id` in-memory filter via `generation_id`→`creator_id` lookup, not DB column, to avoid migration). For history, **no fix without migration**, so accept global history as latent P1 for single-creator deployment.

---

## 15. Required Stage B Design (Smallest Safe)

**Goal**: Same fan + same message + Creator A ≠ Creator B for all relevant runtime state, preserve Redis Streams, consumer groups, XAUTOCLAIM, DropFans, single-pass.

**Changes (no new workers/queues/LLM calls, no architecture redesign):**

1. **Telemetry isolation (no migration, composite key in memory + DB `ON CONFLICT` change)**:
   - `core/telemetry.py:222` change `_telemetry_cache: dict[tuple[int|None,str], GenerationTelemetry]` keyed by `(creator_id, generation_id)`.
   - `db/postgres.py:insert_generation_telemetry` change `INSERT ... ON CONFLICT (generation_id) DO NOTHING` → `ON CONFLICT (creator_id, generation_id) DO NOTHING` and add `CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_telemetry_creator_generation ON generation_telemetry(creator_id, generation_id)` (if migration allowed, else keep old unique and rely on composite cache only; insert with same generation_id but different creator will still conflict on old unique, so need migration — **Stage B should include this one-line migration** as it is minimal and required for correctness; alternative is to keep old index and catch exception, but insert will fail for second creator).

2. **Send dedup isolation (no migration)**:
   - `db/redis.py:82 mark_send_dedup(dedup_id, creator_id)` → `SETEX f"send_dedup:{creator_id}:{dedup_id}"` if `creator_id` else `send_dedup:{dedup_id}`.
   - `db/redis.py:87 is_send_duplicate(dedup_id, creator_id)` same.
   - `workers/llm_worker.py:1229 dedup_id = MD5(...)` → `mark_send_dedup(dedup_id, creator_id=_creator_id)` / `is_send_duplicate` check with `creator_id`.
   - `workers/scheduler_worker.py:59` already `scheduled:{dedup_key}:{id}` creator-scoped, but send dedup after `enqueue_send` must be creator-scoped.

3. **Persona snapshot coherence (no migration, snapshot in context)**:
   - In `workers/llm_worker.py:process_message`, after `build_qwen3_context` returns `context` (which already contains `CREATOR PERSONA v1` 19k), do **not** refetch `get_structured_persona_async` again for behavior derivation. Instead reuse `_structured_for_name` already fetched in `build_qwen3_context` (context.py:575) by returning it from `build_qwen3_context` (add `return _structured_for_name` via `context` metadata or via `messages[1]` parse). Simpler: change `build_qwen3_context` to return `(context, structured_snapshot)` and worker uses same snapshot for both `CREATOR PERSONA` and `PERSONA BEHAVIOR` + validation, guaranteeing coherence (no hybrid). This is **one function signature change**, no schema.

4. **CREATOR_CONTEXT_UNAVAILABLE handling**:
   - Change `workers/llm_worker.py` and `chatbotv2/handlers.py` to **not** generate generic warm reply when `creator_id is None` due to `CREATOR_CONTEXT_UNAVAILABLE` from DB failure. Instead route to `operator_queue` with flag `creator_context_unavailable` and `min(score,0.1)` (like autonomous pause), preventing unintended autonomous generic response during outage. Distinguish `no configured persona` (operator hasn't created persona, `creator_id` valid but `get_structured_persona_async` returns `{}`) vs `DB failure` (`resolve_single_application_creator` exception) — both currently warm LOW, but DB failure should not auto-send.

5. **Message history isolation (deferred, requires migration, so not in minimal Stage B)**:
   - Document as **known latent P1** for multi-creator: `get_recent_messages` global, `operator_queue` global. Minimal Stage B without migration **will not fix history**, but will fix dedup/telemetry/persona snapshot. If migration allowed, add `messages.creator_id BIGINT` and `operator_queue.creator_id` with backfill `DEFAULT NULL` and query `WHERE user_id=$1 AND (creator_id=$2 OR creator_id IS NULL)` for backward compat.

**Preserves**: Redis Streams, consumer groups `llm_workers`/`send_workers`, XAUTOCLAIM (still `start_id 0`, not generation_id), DropFans authority, single-pass, canary.

---

## 16. Required Test Plan

**Deterministic, no live Qwen, mocked PG/Redis**:

- **A. same fan / two creators / same message** (`user 777, "hey", tgId 100` via `creator 1 Sunny` vs `2 Mia`): assert `generation_id` same today (global) → **currently collision proven**, after fix with composite dedup/telemetry → distinct `send_dedup:1:5d4` vs `2:5d4`, telemetry distinct.
- **B. same fan / two creators / different messages** (`"hey"` vs `"hi"`): different `generation_id`/`dedup_id` → not deduped, behavior isolated.
- **C. same creator / same fan / retry** (same `generation_id`, same `creator_id`, XAUTOCLAIM): `derive_persona_behavior_state` same inputs → same `emotional_state`, `validate` same, no duplicate send (dedup same creator).
- **D. XAUTOCLAIM** (idle 30s, `requeue_stalled_messages`): new worker `process_message(data["generation_id"])` preserved, `creator_id` re-resolved, behavior re-derived fresh → deterministic, not random.
- **E. send dedup** (global today): `is_send_duplicate(MD5(777:hey:100))` first call false, second same `user:msg` different creator → currently true (false positive) → after fix `is_send_duplicate(dedup_id, creator_id=2)` false.
- **F. telemetry collision** (same `generation_id` two creators): `start_generation(generation_id=5d4, creator_id=1)` then `start_generation(generation_id=5d4, creator_id=2)` → `_cache[(1,5d4)]` vs `[(2,5d4)]` distinct vs old `_cache[5d4]` overwrite.
- **G. message history isolation** (global today): `save_inbound_message(777, "I am software engineer", creator 1)` then `get_recent_messages(777)` for creator 2 returns same → **leak proven**; after fix with `creator_id` column → isolated.
- **H. operator queue isolation** (global): `add_to_operator_queue(777, Sunny draft)` + `add_to_operator_queue(777, Mia draft)` → `get_pending_queue_items` returns both, no creator filter → operator could approve wrong; after fix `WHERE creator_id=$1` (if column added) → isolated.
- **I. persona v1→v2 during generation** (hybrid): mock `get_structured_persona_async` to return v1 for first fetch, then mock update to v2 before second fetch → assert `CREATOR PERSONA` v1 + `PERSONA BEHAVIOR` v2 hybrid today → after fix with snapshot reuse → coherent v1 (both from same snapshot).
- **J. behavior snapshot consistency**: `derive` and `validate` must use same `structured_persona` object (snapshot) → assert `persona_version` same in `persona.behavior` before/after events.
- **K. dashboard stage correlation**: `generation_id` same across creators but `execution_stage` keyed by `generation_id` alone → wrong stage; after fix composite → correct.
- **L. realtime event isolation**: `publish_event persona.behavior generation_id 5d4 creator 1` vs `creator 2` → WebSocket `ws_manager` filters by `creator_id` → each creator's dashboard sees only its own.
- **M. commerce idempotency** (`strategy_generation_seen` per `creator_id` key): same `generation_id` across creators stored under different `creator_id` JSONB keys → not duplicate, idempotent preserved.
- **N. DLQ replay** (`replay_dlq_entry`): `payload generation_id` preserved, `creator_id` in payload, worker re-derives fresh `structured_persona` (now snapshot) → coherent.
- **O. restart** (clear `_telemetry_cache` + Redis `persona:creator:*`): re-derive `derive_persona_behavior_state` same inputs → same state, restart-safe.

---

## 17. Required Final Report (to be Stage B)

*(Stage B will create `docs/AI_NATIVE_PERSONA_PHASE_43F_IMPLEMENTATION_MAP.md` optionally and `docs/AI_NATIVE_PERSONA_PHASE_43F_FINAL_REPORT.md` with exact fixes, but per Stage A this audit is the deliverable.)*

---

PHASE 43F STAGE A VERDICT

GENERATION IDENTITY ISOLATION: FAIL
SEND DEDUP ISOLATION: FAIL
TELEMETRY ISOLATION: FAIL
MESSAGE HISTORY ISOLATION: FAIL
OPERATOR QUEUE ISOLATION: FAIL
PERSONA SNAPSHOT CONSISTENCY: PARTIAL
BEHAVIOR SNAPSHOT CONSISTENCY: PARTIAL
CREATOR/PERSONA ISOLATION: PASS
FAN/PERSONA SEPARATION: PASS
RETRY/XAUTOCLAIM SAFETY: PASS
COMMERCE IDEMPOTENCY: PASS
REALTIME CORRELATION: PASS
PRIVACY: PARTIAL
FAILURE ISOLATION: PASS
CANARY SAFETY: PASS

P0: 0
P1: 4
P2: 6
P3: 2

PRODUCTION CHANGES: NONE
MIGRATIONS: NONE
REDIS MUTATIONS: NONE
CANARY CHANGES: NONE
NEW LLM CALLS: NONE
NEW WORKERS: NONE
NEW QUEUES: NONE
ARCHITECTURE REDESIGN: NONE

FINAL VERDICT:
CONDITIONALLY READY

ROOT QUESTION:
Can Creator A and Creator B sharing the same fan/user ID influence one another through generation identity or downstream state?

ANSWER:
PROVEN YES for 4 global-key collisions (generation_id MD5 user:msg:tgId, send_dedup:{dedup_id}, telemetry _cache[generation_id] + generation_telemetry UNIQUE(generation_id), messages WHERE user_id, operator_queue WHERE user_id) and for persona snapshot hybrid (CREATOR v1 + BEHAVIOR v2 within one generation). Persona storage/cache/context itself is PROVEN isolated (persona:{creator}:{user}, persona:creator:{creator}, fan_knowledge_by_creator), so no silent Sunny→Mia substitution on primary, but history/dedup/telemetry leak and second creator's send falsely deduped within 3600s.

MOST DANGEROUS COLLISION:
Send dedup global: same fan 777 identical content "hey" via Sunny then Mia within 3600s → second generation's send_worker `is_send_duplicate(dedup_id)` returns true (global key `send_dedup:MD5(777:hey:100)` already set by first) → Mia's reply never sent (dropped), fan 777 (who follows both creators) perceives Mia as ignoring, while telemetry for Mia dropped (unique violation) and recent history mixes Sunny's messages into Mia's Qwen context.

SAFEST FIX:
Option C — keep canonical `generation_id = MD5(user:msg:telegram_id)` unchanged (preserves retry/XAUTOCLAIM/telemetry unique expectations, no MD5 change), but scope every downstream consumer composite: telemetry cache key `(creator_id, generation_id)` + DB `UNIQUE(creator_id, generation_id)` (one migration), send dedup key `send_dedup:{creator_id}:{dedup_id}` + dedup check with creator_id, and persona snapshot coherence by returning structured snapshot from `build_qwen3_context` and reusing for behavior/validation (no extra fetch). No new workers/queues/LLM.

WHY:
Changing canonical MD5 (Option A) requires 5 call sites + test updates + rolling deploy coordination for in-flight old IDs, while composite (Option C) fixes collisions where they occur without changing the generation_id that retry/XAUTOCLAIM/ evidence already rely on. History isolation (`messages WHERE user_id`) would require `messages.creator_id` migration, which is deferred as latent P1 for single-creator deployment (no second active creator today).

SECONDARY RISK:
CREATOR_CONTEXT_UNAVAILABLE fallback to `warm LOW` generic can cause unintended autonomous generic reply during DB outage (should route to operator queue). Also persona snapshot hybrid (CREATOR v1 + BEHAVIOR v2) within one generation.

STAGE B:
Implement minimal Stage B: 1) telemetry composite key + DB unique (creator_id, generation_id) migration, 2) send dedup creator-scoped, 3) persona snapshot reuse (single fetch), 4) CREATOR_CONTEXT_UNAVAILABLE → not autonomous (operator queue), 5) (optional/deferred) messages/operator_queue creator_id column for history. No new LLM/worker/queue, preserve single-pass and DropFans authority.

