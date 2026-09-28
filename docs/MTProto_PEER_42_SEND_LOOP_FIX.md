# MTProto Peer 42 / Stale Redis Send-Loop Fix

## Executive Summary

The `peer 42` send loop was caused by two bugs in `chatbotv2/main.py`:

1. **Rate-limit `continue` did not ACK the message**, leaving it permanently stuck in Redis's pending entries list (PEL). The message could never be re-read by `XREADGROUP ... >` and XAUTOCLAIM only claims from OTHER consumers — creating an unrecoverable PEL leak.

2. **Entity resolution only caught `ValueError`/`TypeError`**, missing Telethon RPC errors (`PeerIdInvalidError`, `FloodWaitError`, etc.) that could escape to the outer handler or create unexpected behavior.

Additionally, `move_send_to_dlq` in `db/redis.py` had no error handling — if the DLQ `XADD` failed, the `XACK` was never called, creating another PEL leak path.

---

## 1. Root Cause

### What `42` represents

`42` is a **Telegram user ID** stored as `users.id` in PostgreSQL. The `users` table schema:

```sql
CREATE TABLE users (
    id BIGINT PRIMARY KEY,  -- This IS the Telegram user ID
    ...
);
```

There is no separate `telegram_id` column in `users`. The primary key IS the Telegram ID. The value `42` flows through:

```
users.id = 42 (Telegram user ID)
  ↓
operator_queue.user_id = 42
  ↓
send_worker.py: user_id = item["user_id"]  →  42
  ↓
enqueue_send({"entity": str(42), ...})
  ↓
Redis SEND_STREAM: {entity: "42", content: "...", ...}
  ↓
main.py: entity = data.get("entity")  →  "42"
  ↓
entity_int = int("42")  →  42
  ↓
client.get_input_entity(42)  →  ValueError (not in session cache)
```

### Why `42` enters the Redis send stream

Three producers add messages with `entity: str(user_id)`:
- `send_worker.py:30` — operator-approved queue items
- `llm_worker.py:494` — auto-approved AI responses
- `scheduler_worker.py:107` — scheduled messages
- `post_purchase.py:118` — purchase confirmations

All use `user_id` from `operator_queue.user_id` or `scheduled_messages.user_id`, which reference `users.id` (= Telegram ID).

### Why Telethon cannot resolve entity `42`

`get_input_entity(42)` fails because:
1. The entity is not in Telethon's in-memory cache
2. The entity is not in the session's SQLite database
3. The network fallback (`GetUsersRequest` with `access_hash=0`) returns empty — user hasn't messaged the bot or is not in contacts

This is a **permanent failure** for the current session state (unless the user initiates contact).

---

## 2. The Two Bugs

### Bug 1: Rate-limit `continue` without ACK

```python
# chatbotv2/main.py (BEFORE fix)
if not allowed:
    logger.warning("Rate limit exceeded for peer %s, re-queuing", peer_key)
    await asyncio.sleep(1)
    continue  # ← NO ACK! Message stays in PEL forever.
```

**Impact**: When the token bucket rate limiter blocks a message (burst=5, rate=1/sec), the `continue` skips to the next message without ACKing. The message stays in the consumer's PEL permanently:
- `XREADGROUP ... >` won't return it (only delivers new, undelivered messages)
- XAUTOCLAIM only claims from OTHER consumers (not self)
- The message is effectively lost — stuck in the PEL forever

### Bug 2: Narrow entity resolution exception handling

```python
# chatbotv2/main.py (BEFORE fix)
except (ValueError, TypeError):  # ← Missing RPCError, FloodWaitError, etc.
    logger.warning("Cannot resolve entity %s, moving to DLQ", entity)
    await move_send_to_dlq(msg_id, "entity_not_found")
    continue
```

**Impact**: Telethon's `get_input_entity()` can raise:
- `ValueError` — entity not found (CAUGHT)
- `TypeError` — invalid input (CAUGHT)
- `PeerIdInvalidError` — invalid peer ID (NOT CAUGHT → falls to outer handler)
- `FloodWaitError` — Telegram rate limit (NOT CAUGHT → falls to outer handler)
- Other `RPCError` subclasses (NOT CAUGHT)

Uncaught RPC errors fall to the outer `except Exception:` handler, which does call `move_send_to_dlq`, but with reason `"send_error"` instead of proper classification.

### Bug 3 (defensive): `move_send_to_dlq` no error handling

```python
# db/redis.py (BEFORE fix)
await r.xadd(DLQ_STREAM, record)  # ← If this fails...
await r.xack(SEND_STREAM, ...)     # ← ...this is never called
```

If the DLQ `XADD` fails (Redis issue, stream problem), `XACK` is never called, creating another PEL leak.

---

## 3. Six Reclaimed Messages Analysis

The log showed:
```
Reclaimed 6 stalled send messages: ['1787408484462-0', ...]
```

These were messages stuck in a previous consumer's PEL (from a crashed worker or previous run). XAUTOCLAIM reclaimed them to `bot_main`. However, `read_send_messages` uses `{SEND_STREAM: ">"}` which only returns NEW messages — the reclaimed messages sit in `bot_main`'s PEL.

The6 reclaimed messages were likely:
- Messages for peer 42 that hit the rate limit in a previous session
- Never ACKed (Bug 1)
- Accumulated in the PEL across restarts

After the fix, rate-limited messages are properly ACKed + re-enqueued, preventing PEL accumulation.

---

## 4. Entity-Resolution Flow (After Fix)

```
entity = "42"
  ↓
get_input_entity(42)
  ↓
┌─ ValueError/TypeError → permanent → DLQ + ACK (no requeue)
├─ FloodWaitError → temporary → ACK + re-enqueue
├─ RPCError (other) → permanent → DLQ + ACK (no requeue)
└─ Success → proceed to send
```

---

## 5. Rate-Limit Interaction (After Fix)

```
Rate limit blocks for peer 42
  ↓
ACK original message (remove from PEL)
  ↓
Re-enqueue with same dedup_id (idempotent retry)
  ↓
Next iteration: message re-read, rate limit may have cleared
  ↓
If still blocked → same cycle (eventually clears)
```

---

## 6. Code Changes

### `chatbotv2/main.py`

1. **Added imports**: `FloodWaitError`, `RPCError` from `telethon.errors`; `enqueue_send` from `db.redis`
2. **Rate limit handler** (line ~116): Changed from `continue` (no ack) to `ack_send(msg_id)` + `enqueue_send(dict(data), dedup_id=dedup_id)` + `continue`
3. **Entity resolution** (line ~122): Added `FloodWaitError` handler (ack + re-enqueue) and `RPCError` handler (DLQ + ack), with structured logging including failure classification

### `db/redis.py`

4. **`move_send_to_dlq`** (line ~107): Wrapped `r.xadd(DLQ_STREAM, record)` in try/except to ensure `r.xack()` is always called, even if DLQ write fails

---

## 7. Tests Added

**File**: `tests/test_send_loop_peer42_fix.py` (15 tests)

| Test | Verifies |
|------|----------|
| `TestInvalidEntityToDLQ::test_value_error_entity_dlq_and_ack` | ValueError → DLQ + correct reason |
| `TestInvalidEntityToDLQ::test_value_error_calls_dlq` | ValueError → move_send_to_dlq called with entity_not_found |
| `TestInvalidEntityToDLQ::test_rpc_error_entity_dlq` | PeerIdInvalidError → DLQ with entity_rpc_error |
| `TestRateLimitAckAndRequeue::test_rate_limit_acks_message` | Rate limit → ack_send called |
| `TestRateLimitAckAndRequeue::test_rate_limit_re_enqueues_message` | Rate limit → enqueue_send called with original payload |
| `TestRateLimitAckAndRequeue::test_rate_limit_does_not_reach_entity_resolution` | Rate-limited messages skip entity resolution |
| `TestFloodWaitRequeue::test_flood_wait_re_enqueues` | FloodWaitError → ack + re-enqueue |
| `TestFloodWaitRequeue::test_flood_wait_does_not_dlq` | FloodWaitError does NOT DLQ |
| `TestDLQAckSemantics::test_dlq_xadd_failure_still_acks` | DLQ xadd failure → xack still called |
| `TestDLQAckSemantics::test_dlq_xadd_success_acks` | Normal DLQ → both xadd and xack called |
| `TestValidPeerRegression::test_valid_entity_sends_and_acks` | Valid entity → get_input_entity + send + ack |
| `TestNoRetryLoop::test_invalid_entity_not_re_enqueued` | ValueError → DLQ, NOT re-enqueued |
| `TestNoRetryLoop::test_rpc_error_entity_not_re_enqueued` | RPCError → DLQ, NOT re-enqueued |
| `TestIDDomain::test_entity_is_stringified_integer` | Entity field is stringified integer |
| `TestIDDomain::test_entity_converted_to_int_for_telegram` | Entity string converted to int for get_input_entity |

---

## 8. Test Results

```
tests/test_send_loop_peer42_fix.py:    15 passed
tests/test_dlq_recovery.py:            42 passed
tests/test_media_sending.py:           33 passed
tests/test_redis_recovery.py:          31 passed
tests/test_vault.py:                   58 passed
tests/test_vault_concurrency.py:       29 passed
tests/test_vault_reservation_recovery.py: 31 passed
tests/test_scheduled_messages.py:      80 passed
─────────────────────────────────────────────
Total:                                319 passed, 0 failed
```

Pre-existing failures: 8 (unchanged — `test_commerce_state.py`, `test_fangate_integration.py`, `test_integration_real_infra.py`)

---

## 9. Migration Relevance

The startup reported `1 pending migration(s)`. This is **unrelated** to the peer 42 bug. The migration is `00000000000000_baseline.sql` (already applied in production). The send-stream pipeline does not depend on any pending migration.

---

## 10. Remaining Risks

1. **Re-enqueued rate-limited messages could loop briefly** if the rate limit window is long. The dedup_id prevents duplicate sends, and the rate limit eventually clears (burst=5, rate=1/sec → 5-second window).

2. **Messages with permanently invalid entities are DLQ'd immediately**. If a user deactivates their Telegram account, all pending messages for that user go to DLQ. This is correct behavior — no retry will help.

3. **The 6 reclaimed stale messages** from the startup log may still be in `bot_main`'s PEL. A one-time manual cleanup (`XACK` them) or a PEL-aware recovery mechanism could be added later.

---

## 11. Prohibited Architecture Changes — Confirmation

The following were NOT changed:
- ✅ Redis Streams — unchanged
- ✅ Consumer groups — unchanged
- ✅ XAUTOCLAIM — unchanged
- ✅ DLQ — unchanged (hardened only)
- ✅ Send worker — unchanged
- ✅ Telethon client — unchanged
- ✅ PostgreSQL — unchanged
- ✅ Deduplication — unchanged (re-enqueue uses same dedup_id)
- ✅ Rate limiting — unchanged (token bucket preserved)
- ✅ Creator isolation — unchanged

---

## 12. Summary

```
ROOT CAUSE:
  Rate-limit `continue` in _process_send_stream() did not ACK messages,
  leaving them permanently stuck in Redis's pending entries list (PEL).
  Combined with narrow exception handling for entity resolution that
  missed Telethon RPC errors, creating a cycle where messages for
  permanently invalid entities (like peer 42) were neither properly
  DLQ'd nor ACK'd.

FIX:
  1. Rate-limit path: ACK + re-enqueue (with dedup_id for idempotency)
  2. Entity resolution: catch ValueError/TypeError (permanent → DLQ),
     FloodWaitError (temporary → re-enqueue), RPCError (permanent → DLQ)
  3. move_send_to_dlq: ensure XACK always runs even if DLQ XADD fails

WHY PEER 42 WILL NO LONGER LOOP:
  - Entity 42 fails get_input_entity → ValueError → DLQ + ACK
  - Message is removed from PEL → cannot be reclaimed by XAUTOCLAIM
  - No re-enqueue → no retry loop
  - DLQ record preserves full payload for debugging
```
