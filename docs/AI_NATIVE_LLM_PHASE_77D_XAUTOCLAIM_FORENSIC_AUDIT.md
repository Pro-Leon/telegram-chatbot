# Phase 77D — XAUTOCLAIM Stalled-Message Recovery Forensic Audit (Stage A — READ ONLY)

**Date:** 2026-09-03
**Mode:** READ-ONLY, no production code/config/Redis/DB mutation
**P0 Trigger:** `db/redis.py:243 XAUTOCLAIM discards fields`
**Client:** `redis 8.1.0` `redis.asyncio.Redis.from_url protocol 2` `pyproject.toml:11 redis>=4.5`
**Streams:** `INBOUND_STREAM=inbound_messages` `SEND_STREAM=send_messages` `DLQ_STREAM=dead_letter_queue` `db/redis.py:15-18` Groups `llm_workers` / `send_workers` `40-41`

---

## 1. Executive Summary

**Confirmed payload-loss bug in both recovery paths.** `db/redis.py:177` `requeue_stalled_send_messages` and `243` `requeue_stalled_messages` call `XAUTOCLAIM` correctly, receive `(cursor, [(id, {fields})])`, then **discard every `fields` dict** via `[msg_id for msg_id,_ in result[1]]`. Callers `workers/llm_worker.py:1745` and `chatbotv2/main.py:82` log only `stale_ids`, never process reclaimed payload. `XREADGROUP >` will never deliver already-pending entries, so the message stays PEL-claimed, idle grows, next loop re-claims same IDs (same discard), infinite reclaim loop, **exactly-once delivery broken → zero delivery for that message after crash**. No compensating `XRANGE`/`XREAD` exists. DLQ, dedup, creator isolation, cursor remain correct but never reached. Fix is to preserve full entries and feed them into existing `_process_send_stream` / `process_message` paths before `>` read, keeping XACK/DLQ/dedup unchanged.

---

## 2. Redis Stream Contract (producer XADD)

**Inbound producer** `db/redis.py:184 enqueue_inbound(data)` + `chatbotv2/handlers.py:160` `_wait_and_process -> enqueue_inbound`:

```python
# handlers.py:148-170
message_data = {user_id, content, telegram_message_id, username, first_name, persona, generation_id=md5(user:content:tgId)}
# redis.py:184-195
data = {k: str(v) for ...}
if "generation_id" not in data: data["generation_id"]=md5(...)
msg_id = await r.xadd(INBOUND_STREAM, data)  # id="*" auto, decode_responses True  redis.py:28
```

Returned `msg_id` e.g. `171467...-0`. Fields as stored (all strings, `decode_responses=True`):

| field | type (Redis) | meaning | producer | required | example |
|-------|--------------|---------|----------|----------|---------|
| `user_id` | string(int) | Telegram fan id | handlers.py:160 | yes | `12345` |
| `content` | string | fan's raw text | handlers.py:160 | yes | `hey beautiful` |
| `telegram_message_id` | string(int) | inbound telegram id for dedup | handlers.py:160 | yes | `100` |
| `username` | string | Telegram username | handlers.py:160 | no | `u` |
| `first_name` | string | Fan first name | handlers.py:160 | no | `Alex` |
| `persona` | string | resolved persona text (creator) | handlers.py:158 `get_cached_user_persona` | no | `You are Sunny...` |
| `generation_id` | string(32 hex) | deterministic `md5(user:content:tgId)` for idempotency + telemetry | `redis.py:188` or `handlers.py:63` | yes (auto) | `cb510...` |
| `creator_id` | string(int) | scoping, from `resolve_single_application_creator` | `redis.py:184` caller hands via handlers? actually handlers enqueues message_data with creator_id optional, `enqueue_inbound` preserves | optional | `1` |

No `retry_count`/`attempt` — at-least-once via PEL alone.

**Send producer** `db/redis.py:65 enqueue_send(message_data, dedup_id, generation_id, creator_id)` called from `workers/llm_worker.py:1442` + `workers/send_worker.py:29` + `chatbotv2/main.py:128,192` + `db/redis.py:718` replay:

```python
# redis.py:65-78
data = {k: str(v) for k,v in message_data.items()}
data["dedup_id"]=dedup_id; data["generation_id"]=str(gid); data["creator_id"]=str(cid)
await r.xadd(SEND_STREAM, data, id="*")
```

Stream `send_messages` fields (all strings):

| field | producer | required |
|-------|----------|----------|
| `entity` | `llm_worker 1442 str(user_id)` / `send_worker 31` | yes |
| `content` | draft / approved draft | yes |
| `draft_content` | same as content | yes |
| `was_edited` | `False` `send_worker 34` | yes |
| `was_auto_approved` | `confidence>=0.80` | yes |
| `confidence_score` | `0.0-1.0` | yes |
| `operator_id` | `send_worker 38` | no |
| `save_to_db` | `True` | yes |
| `dedup_id` | `md5(user:msg:tgId)` `llm_worker 393` or `queue_item:{id}` `send_worker 28` | yes (idempotency) |
| `generation_id` | `md5` | yes |
| `creator_id` | creator scoping | yes |
| `media_type, media_path, fangate_media_id, product_id` | optional media send `main.py 230` | optional |

Creator isolation via `creator_id` in payload + `send_dedup:{creator}:{dedup}`.

---

## 3. Normal XREADGROUP Flow (happy path — proven correct)

**Inbound** `db/redis.py:199 read_inbound(consumer_name, count 5/10, block 2000)`:

```python
messages = await r.xreadgroup(CONSUMER_GROUP="llm_workers", consumer_name, {INBOUND_STREAM: ">"}, count=count, block=block_ms)
# returns list[tuple[str, list[tuple[str, dict]]]] e.g. [("inbound_messages", [("171...-0", {user_id:"1",...})])]
return messages or []
```

**LLM worker** `workers/llm_worker.py:1755` loop:

```python
messages = await read_inbound(worker_id, count=5, block_ms=2000)
for stream, stream_messages in messages:
    for msg_id, data in stream_messages:
        msg_data = {user_id:int(data["user_id"]), user_message:data["content"], telegram_message_id:int(data["telegram_message_id"]), ...}
        await process_message(**msg_data)   # OneCall path 1042
        await ack_inbound(msg_id)           # XACK 219
        except: await move_to_dlq(msg_id, "processing_error", payload=dict(data))
        finally: await release_user_lock(user_id)
```

Extraction is `data["user_id"]` etc. — **expects full fields dict**. Proven 210 tests pass (`test_phase77b` + `test_ai_resilience`).

**Send** `db/redis.py:96 read_send_messages` identical with `SEND_CONSUMER_GROUP="send_workers"` `count 10 block 2000` → `chatbotv2/main.py:94` `_process_send_stream`:

```python
messages = await read_send_messages("bot_main", count=10, block_ms=2000)
for stream, msgs in messages:
    for msg_id, data in msgs:
        dedup_id=data.get("dedup_id"); creator_id=...
        if is_send_duplicate(dedup_id, creator_id): await ack_send(msg_id); continue
        ... rate limit, blacklist, reserve_delivery, send_file/send_message, mark_send_dedup, ack_send ...
        except: await move_send_to_dlq(msg_id, "send_error", payload=dict(data))
```

Same shape `list[tuple[str, list[tuple[str, dict]]]]`. `decode_responses=True` `redis.py:28` ensures `dict` values are strings.

---

## 4. Pending-Message Lifecycle

```
XADD inbound_messages * {fields} -> length ++
  |
  v
XREADGROUP llm_workers worker_1 {inbound_messages: ">"} -> entry moves from stream to PEL (Pending Entries List) for group llm_workers consumer worker_1, idle 0, delivery count 1, return payload to caller.
  |
  |-- success path: process_message -> ack_inbound XACK -> PEL entry removed, stream entry remains until XTRIM (not used)
  |
  |-- crash before ack: worker dies/leaves after XREADGROUP but before XACK -> PEL entry stays with consumer worker_1, idle increases.
  |
  |-- XAUTOCLAIM idle 60000 (llm) / 30000 (send) claims pending >idle to new consumer -> PEL ownership transferred to new consumer (bot_main/worker_2), idle reset 0, delivery count ++, return (next_cursor, [(id, fields)])
       - If new consumer processes and ACKs: PEL removed.
       - If DLQ: XADD dead_letter_queue + XACK.
       - If re-claim loop without processing: idle grows again, next XAUTOCLAIM re-claims same IDs.
```

Config `core/config.py:46 redis_pending_idle_ms 60000` comment `long enough for slow Gemini, short enough for crash` (AGENT.md says 30s — actual 60s inbound, send hardcoded 30000 `main.py:83`/`redis.py:159`).

---

## 5. Exact XAUTOCLAIM Implementation (bug site)

**Inbound reclaim** `db/redis.py:224 requeue_stalled_messages`:

```python
async def requeue_stalled_messages(consumer_name: str, idle_ms: int = 30000) -> tuple[int, list[str]]:
    r = await get_redis()
    try:
        result = await r.xautoclaim(
            name=INBOUND_STREAM,            # "inbound_messages"
            groupname=CONSUMER_GROUP,       # "llm_workers"
            consumername=consumer_name,     # e.g. "worker_2"
            min_idle_time=idle_ms,          # 60000
            start_id="0",                   # SCAN cursor start
            count=10,                       # max 10 per call
        )
        if result and result[1]:
            msg_ids = [msg_id for msg_id, _fields in result[1]]  # <-- DISCARDS _fields
            return len(msg_ids), msg_ids
        return 0, []
    except (redis.ResponseError, IndexError):
        return 0, []
```

**Send reclaim** `db/redis.py:158 requeue_stalled_send_messages` identical with `SEND_STREAM` / `SEND_CONSUMER_GROUP`.

**Call sites:**

- `workers/llm_worker.py:1745` `stale_count, stale_ids = await requeue_stalled_messages(worker_id, idle_ms=_settings.redis_pending_idle_ms)` -> `logger.info("Reclaimed %d %s", stale_count, stale_ids)` -> **no further use of reclaimed data**; next line `messages = await read_inbound(..., count=5, block_ms=2000)` reads only `>`.
- `chatbotv2/main.py:82` `stale_count, stale_ids = await requeue_stalled_send_messages("bot_main", idle_ms=_settings.redis_pending_idle_ms)` -> log 86 -> next line `messages = await read_send_messages("bot_main", count=10, block_ms=2000)` reads only `>`.


**Arguments:** correct per Redis command `XAUTOCLAIM key group consumer min-idle-time start [COUNT count]`. No `JUSTID`, so full fields expected.

**Redis client version:** `pyproject.toml:11 redis>=4.5` installed `8.1.0` (`python -c "import redis; print(redis.__version__)" -> 8.1.0`). `redis.asyncio.Redis.xautoclaim(name, groupname, consumername, min_idle_time, start_id="0-0", count, justid=False)` `client.py` `execute_command("XAUTOCLAIM", ...)` default `justid False` returns full entries. Code sets no `parse_justid`, so fields present.

---

## 6. Actual Client Return Structure (redis 8.1.0)

`redis/asyncio/client.py xautoclaim` (inspected):

```python
def xautoclaim(self, name, groupname, consumername, min_idle_time, start_id="0-0", count=None, justid=False) -> list[Any]:
    pieces = [name, groupname, consumername, min_idle_time, start_id]
    if count: pieces.extend([b"COUNT", count])
    if justid: pieces.append(b"JUSTID"); kwargs["parse_justid"]=True
    return self.execute_command("XAUTOCLAIM", *pieces, **kwargs)
```

Raw Redis reply (`redis.io/commands/xautoclaim`): `1) "0-0" 2) 1) 1) "171...-0" 2) 1) "user_id" 2) "1" ...` . `redis-py` parser converts to `list[Any] = [next_cursor_str, list[tuple[str, dict]]]` when `justid False`; `[next_cursor_str, list[str]]` when `justid True`. With `decode_responses True`, `dict` values are strings.

**Our code accesses `result[1]` as entries** — correct. It correctly iterates `(msg_id, _fields)` where `_fields` is the `dict` of all XADD fields. But it binds `_fields` and immediately discards.

**Proof from existing test** `tests/test_redis_recovery.py:120`:

```python
mock_redis.xautoclaim = AsyncMock(return_value=(None, [("999-0", {"data": "test"})]))
await requeue_stalled_messages("worker_1")  # test expects only ids returned — matches buggy shape, test was written to buggy code
```

That test asserts only ids, so test suite currently **encodes the bug** as expected. Green `210 passed` hides bug.

---

## 7. Payload Preservation Analysis — Which Case?

- **Case A (IDs+fields returned but fields discarded): TRUE** — proven §5/6. `result[1]` contains full fields, code discards with `[msg_id for msg_id,_ in result[1]]`.
- **Case B (returns only IDs, code should XRANGE): FALSE** — `justid False` default returns fields; no `JUSTID` flag set; no compensating `XRANGE/XREAD` exists in either worker.
- **Case C (client called incorrectly): FALSE** — call signature correct `name, groupname, consumername, min_idle_time, start_id="0", count=10`. `start_id "0"` vs `"0-0"` equivalent (Redis auto-normalizes). Arguments correct; only handling of return wrong.
- **Case D (fields preserved — finding false): FALSE** — `result:Tuple` is not forwarded.
- **Case E (fields preserved later discarded during transform): FALSE** — discarded immediately at `db/redis.py:177,243`, never reaches transform.

**Conclusion: Case A.**

---

## 8. Reclaimed-Message Worker Flow (what worker receives)

**Current buggy flow:**

```python
# workers/llm_worker.py:1743
stale_count, stale_ids = await requeue_stalled_messages(worker_id, 60000)  # -> (2, ["171...-0","171...-1"]) fields lost
logger.info("Reclaimed %d stalled inbound messages: %s", stale_count, stale_ids)
messages = await read_inbound(worker_id, count=5, block_ms=2000)  # XREADGROUP ">" -> [] because reclaimed entries are now pending for this consumer, not ">"
# -> no process_message for reclaimed IDs
# -> no ack_inbound for those IDs
# -> PEL entry remains idle, next loop XAUTOCLAIM re-claims same IDs, same discard, infinite loop, at-least-once violated -> zero delivery
```

**Send path identical** `chatbotv2/main.py:82-94` requeue then `read_send_messages ">"` — reclaimed `entity/content/dedup_id/creator_id` never re-enters `for msg_id,data in msgs` branch, so `is_send_duplicate`, `check_send_rate_limit`, `send_message`, `mark_send_dedup`, `ack_send`, `move_send_to_dlq` all skipped for reclaimed message.

**What worker *should* receive:** `result[1]` fields e.g. `{"user_id":"123", "content":"hey", "telegram_message_id":"100", "creator_id":"1", "generation_id":"cb51...", "persona":"..."}` for inbound; or `{entity:"123", content:"...", dedup_id:"md5", generation_id:"...", creator_id:"1"}` for send — identical shape to `XREADGROUP`'s `data` dict, processable by same `process_message` / `_process_send_stream` body.

**No secondary fetch exists:** No `XREAD`/`XRANGE`/`XPENDING` after claim. The fields are available in `result[1]` but dropped on the floor.

---

## 9. ACK Analysis

**Normal success ACK:**

- Inbound: `workers/llm_worker.py:1775` `await ack_inbound(msg_id)` after `process_message` returns without raise. `db/redis.py:219` `XACK inbound_messages llm_workers msg_id`.
- Send: `chatbotv2/main.py:294` `await ack_send(msg_id)` after `client.send_message` + `mark_send_dedup` or dedup path `110`/`127`.

**Normal failure ACK via DLQ:**

- Inbound: `workers/llm_worker.py:1779` `await move_to_dlq(msg_id, "processing_error", payload=dict(data), worker_id)` -> `db/redis.py:272 XADD dead_letter_queue` then `276 XACK`. Returns `True` -> PEL removed; `False` (DLQ write fail) leaves pending for retry.
- Send: `chatbotv2/main.py:421` `move_send_to_dlq` always `155 XACK` even if DLQ write fails (prevents stuck pending, original stream entry retains payload for manual recovery log).

**Reclaimed path current:** No ACK happens because reclaimed `msg_id` never reaches either `ack_*` or `move_to_dlq` call. PEL idle resets to 0 on `XAUTOCLAIM` claim (new owner), but without ACK the entry `remains` PEL for new consumer `bot_main`/`worker_2`. Next idle threshold expiry → `XAUTOCLAIM` re-claims to same (or another) consumer, logs `Reclaimed 2 ...` again, never ACKed → **permanent pending until stream entry evicted or XDEL manually** (no eviction configured). Exception before payload reconstruction cannot cause premature ACK because payload reconstruction never happens — **but it also cannot cause correct ACK**. Infinite reclaim loop.

**After fix:** ACK must happen *inside reclaimed-entries processing loop* using same `try: process_message -> ack; except -> move_to_dlq` branches, preserving no-premature-ACK invariant.

---

## 10. DLQ Analysis

A reclaimed message with missing payload **cannot** reach DLQ correctly today:

- Payload for DLQ is `payload=dict(data)` from `XREADGROUP`'s `data`. For reclaimed path, `data` is lost, so `move_to_dlq` would be called with `None` or empty dict if it were called — which it isn't.
- Even if worker attempted DLQ for reclaimed ID, `replay_dlq_entry` `db/redis.py:646` expects `payload` JSON to do `enqueue_inbound(payload)` or `enqueue_send(payload)`. Empty payload returns `missing_original_payload` `690` and replay fails.
- Current `move_send_to_dlq` ACKs even if DLQ write fails, but that code is unreachable for reclaimed messages.
- **Result:** Stalled message never reaches `dead_letter_queue: XVIII`, no `replay_count`, no manual recovery entry, silently stuck in PEL.

**After fix:** Reclaimed `fields` dict is available, so `move_to_dlq(msg_id, reason, payload=dict(fields), worker_id)` will write rich record `{"message_id":msg_id, "payload": json.dumps(fields), "stream":"inbound"/"send"}` allowing `replay_dlq_entry` to `enqueue_inbound`/`enqueue_send` with preserved `generation_id`/`dedup_id`/`creator_id`.

---

## 11. Duplicate-Send Analysis (idempotency — Must Preserve)

**Existing dedup:**

- `enqueue_send` stores `dedup_id md5(user:msg:telegram_id)` `workers/llm_worker.py:393` `hashlib.md5(f"{user_id}:{content}:{telegram_message_id}".encode()).hexdigest()` or `queue_item:{id}` `send_worker.py:28`.
- `mark_send_dedup(dedup_id, ttl 3600, creator_id)` `SETEX send_dedup:{creator}:{dedup}` `db/redis.py:81`.
- `is_send_duplicate(dedup_id, creator_id)` `EXISTS` `89` checked at `_process_send_stream:108` *before* send, then `ack_send` `110` if duplicate.
- Inbound `generation_id` `md5(user:content:telegram_id)` `redis.py:188` is stored in both `inbound_messages` and forwarded to `send_messages` `67`, used for telemetry but not dedup gating (inbound dedup is not checked, but send dedup covers re-send).

**Reclaim + crash before ACK scenario:**

```
worker sends Telegram message (client.send_message) -> crashes before ack_send
  |
  -> XAUTOCLAIM reclaims same send entry to bot_main (new consumer)
  -> if fix processes reclaimed entry, is_send_duplicate(dedup_id, creator_id) will be true if mark_send_dedup already executed before crash (it is executed *after* send success at main.py:293). If crash before mark, duplicate check is false, message will be sent again -> at-most-twice Telegram send (acceptable at-least-once). If crash after mark but before ack, XAUTOCLAIM reclaimed entry will be detected as duplicate and ACKed without resending (correct).
```

Fixing XAUTOCLAIM **does not bypass dedup** — reclaimed path still goes through same `_process_send_stream` body that checks `is_send_duplicate` before `send_message`. Preserving delivery guarantees requires keeping that check.

**Inbound duplicate:** No send yet, so reclaim will simply re-run `process_message` (OneCall) and `enqueue_send` with same `dedup_id`; send dedup will prevent double outbound after XREADGROUP812 reprocessing.

---

## 12. Crash-Recovery Analysis

| Step | Normal | Reclaim buggy | Reclaim fixed |
|------|--------|---------------|---------------|
| XADD inbound | new entry `171...-0` `{user_id,content,gen_id,creator_id}` | same | same |
| XREADGROUP worker_1 ">" | delivered to worker_1 PEL | delivered to worker_1 PEL | delivered |
| worker_1 crashes before ack | PEL idle grows | same | same |
| XAUTOCLAIM worker_2 idle 60000 | **bug:** returns `(cursor, [(id, fields)])` fields discarded, log ids only | same but fields used | **fixed:** returns entries, processed |
| worker_2 processes | **bug:** nothing, next XREADGROUP ">" empty, PEL stays 1 entry for worker_2 | — | `for msg_id, fields in claimed: msg_data={user_id=int(fields["user_id"])...}; await process_message(**msg_data); await ack_inbound(msg_id)` or `move_to_dlq` on exception, `release_user_lock` |
| ACK | **bug:** never | — | XACK after processing |
| Redelivery | **bug:** infinite reclaim loop same IDs | — | Exactly-once stream consume, at-least-once overall via reclaim + DLQ |

Same for send: reclaimed fields processed through existing `_process_send_stream` item body (dedup, rate limit, blacklist, reserve, send, ack/dlq).

---

## 13. Root Cause

**Code discards XAUTOCLAIM's returned fields at the redis layer and never feeds reclaimed entries into the worker's existing processing loop.**

- `db/redis.py:177` `requeue_stalled_send_messages`: `msg_ids = [msg_id for msg_id, _fields in result[1]]` discards `_fields`.
- `db/redis.py:243` `requeue_stalled_messages`: same.

Return type `tuple[int, list[str]]` (ids only) propagates loss to callers `workers/llm_worker.py:1745` and `chatbotv2/main.py:82` which only log `stale_ids`. `XREADGROUP ">"` semantics then guarantee reclaimed entries are **not** redelivered as new; they remain PEL-orphaned.

No `justid` misuse — full fields were available, just dropped by list comprehension variable name `_fields`.

---

## 14. Severity Assessment

**P0 — Delivery-critical.** Stalled messages after worker crash/slow GC are lost (infinite reclaim without processing). For inbound: fan's message never gets OneCall reply (silently dropped). For send: approved/operator send never reaches Telegram (silent loss). DLQ never receives record, so manual replay impossible. Violates at-least-once, no-silence-loss, DLQ correctness. Frequency: `redis_pending_idle_ms 60000` inbound / `30000` send, any worker restart or `asyncio.sleep` stall > threshold triggers. Existing test suite encodes bug as expected (`test_redis_recovery.py:60` `requeue_stalled_messages` returns `(count, ids)` only), so bug undetected.

---

## 15. Exact Minimal Fix Required (Stage B)

**Principle:** Preserve/reconstruct complete stream entry → existing processing → existing ACK/DLQ → no new worker architecture.

**Preferred:** Handle full `XAUTOCLAIM` entries directly (no extra `XRANGE` round-trip).

```diff
# db/redis.py:158 requeue_stalled_send_messages
- -> tuple[int, list[str]]   msg_ids = [msg_id for msg_id,_ in result[1]]; return len(msg_ids), msg_ids
+ -> tuple[int, list[tuple[str, dict]]] or tuple[str, list[tuple]]: 
  if result and result[1]: return result[0], result[1]   # cursor, entries [(id, fields)]
  # and same for requeue_stalled_messages 224

# Callers:
# workers/llm_worker.py:1743
- stale_count, stale_ids = await requeue_stalled_messages(worker_id, idle_ms)  ; logger.info
- messages = await read_inbound(...)
+ next_cursor, claimed = await requeue_stalled_messages(...)  # claimed = [(id, fields)]
+ for claimed_id, claimed_fields in claimed:
+     try: msg_data = {"user_id":int(claimed_fields["user_id"]), ... same as XREADGROUP body ...}
+          await process_message(**msg_data); await ack_inbound(claimed_id)
+     except: await move_to_dlq(claimed_id, "processing_error", payload=dict(claimed_fields))
+     finally: await release_user_lock(...)
+ # keep existing XREADGROUP ">" loop for new messages after reclaimed batch
  # chatbotv2/main.py:82 identical for send: iterate claimed entries through same dedup->send->ack/dlq body
```

Alternative (if keeping ids-only shim): fetch payload via `XRANGE stream min=id max=id COUNT 1` per reclaimed ID before processing — adds N Redis round-trips, less minimal. Prefer direct entries.

No `replace Redis Streams`, `consumer groups`, `XAUTOCLAIM`, `DLQ`, `deduplication`, `ACK`, `creator isolation`.

---

## 16. Tests Required (Stage B)

All in new or updated `tests/test_phase77d_xautoclaim_*.py` + `tests/test_redis_recovery.py`.

| ID | Case | What it proves |
|----|------|----------------|
| A | XAUTOCLAIM payload preservation | `requeue_stalled_*` returns `[(id, {fields})]` with all original `user_id/content/creator_id/generation_id` etc, not just ids |
| B | Reclaimed reaches worker | mocked `xautoclaim -> [(id, fields)]` → `process_message` called with those fields (same args as XREADGROUP) |
| C | Valid reclaimed send reaches Telegram path | reclaimed send `entity/content/dedup_id` flows to `client.send_message` mock, `mark_send_dedup`, `ack_send` |
| D | Invalid reclaimed → DLQ | reclaimed with bad `entity`/`content` → `move_send_to_dlq` with `payload=dict(fields)` + `XACK`, or inbound `move_to_dlq` |
| E | ACK after success | reclaimed processed `ack_inbound`/`ack_send` called exactly once |
| F | No premature ACK | failed processing before payload reconstruction not ACKed before `move_to_dlq`; DLQ write fail leaves pending |
| G | Crash recovery | XADD -> XREADGROUP leaves pending -> XAUTOCLAIM -> process -> ack (end-to-end at-least-once) |
| H | Duplicate safety | reclaimed `dedup_id` already in `send_dedup` -> `is_send_duplicate` true -> `ack_send` without `send_message` |
| I | Creator isolation | reclaimed `creator_id=1` not visible to `creator_id=2` consumer's dedup key |
| J | Multiple reclaimed | `count=10` entries all processed, single loop |
| K | Empty/no-result | `xautoclaim (None,[])` -> `read_inbound` still works, no spurious ACK |
| L | Cursor/justid | `xautoclaim` `parse_justid` false returns fields; `justid True` path not used, pagination `start_id "0"` + `count 10` iterated |

---

## 17. No Architecture Redesign Necessary

`Redis Streams + consumer groups + XAUTOCLAIM + DLQ + dedup + creator isolation` are correct primitives; bug is local list-comprehension discard and caller ignoring entries. Fix preserves names (`INBOUND_STREAM`, `SEND_STREAM`, `CONSUMER_GROUP`, `SEND_CONSUMER_GROUP`), commands (`XADD`, `XREADGROUP`, `XAUTOCLAIM`, `XACK`), retry (`dlq_max_replay 3`, `retention 604800`), dedup (`send_dedup:{creator}:{dedup} 3600`), isolation. No Celery, no new queue/worker, no retry semantics change.

```
ROOT CAUSE:
db/redis.py:177 & 243 XAUTOCLAIM returns (cursor, [(id, fields)]) but code does [msg_id for msg_id,_ in result[1]] discarding every _fields dict; callers llm_worker.py:1745 & main.py:82 only log ids and then XREADGROUP ">" never delivers already-pending claimed entries, so payload is lost and message loops reclaim without processing.

ACTUAL XAUTOCLAIM RETURN SHAPE:
redis 8.1.0 redis.asyncio xautoclaim(..., justid=False default) -> list [next_cursor_str ("0-0" when done), list[tuple[str, dict[str,str]]]] each dict full XADD fields with decode_responses True; our redis.py:168 correctly accesses result[1] as entries but binds _fields then drops it; no JUSTID, so fields are present in wire reply (redis.io XAUTOCLAIM) and in redis-py parser.

CURRENT FAILURE:
Stalled inbound_messages and send_messages with idle >60s/30s are successfully XAUTOCLAIMed (PEL ownership transferred, log "Reclaimed N") but never processed: next XREADGROUP ">" returns [] for pending, payload discarded, no ack_inbound/ack_send/no DLQ, no retry payload, infinite reclaim loop, at-least-once violated -> silent message loss.

MINIMAL FIX:
Make requeue_stalled_* return full entries (cursor, [(id, fields)]) and have run_worker/_process_send_stream iterate claimed entries through existing per-message bodies before the normal XREADGROUP > loop: for id, fields in claimed: try msg_data=int/fields -> process_message/enqueue_send + ack; except -> move_to_dlq payload=dict(fields); finally release lock. Keep XACK/DLQ/dedup/creator isolation unchanged. No extra XRANGE.

WHY THE FIX PRESERVES DELIVERY GUARANTEES:
Reclaimed entry's complete fields re-enter proven happy-path code (same int coercion, same dedup is_send_duplicate, same rate-limit/blacklist/reserve/send/mark/ack/dlq). At-least-once: XCLAIM + ACK after processing. No silent loss: payload kept for DLQ replay (dead_letter_queue payload JSON). No premature ACK: ACK only after success or after DLQ write. Deduplication unchanged creator-scoped. Creator isolation unchanged. No queue redesign.
```

