# AUTONOMY 1.0 — RECONNAISSANCE REPORT

## 1. Executive Summary

The autonomous runtime is **substantially more complete than expected**. The three-process architecture (MTProto bot → Redis Streams → LLM worker → send stream → outbound) is production-ready and handles the full inbound-to-outbound lifecycle. The commerce pipeline (signals → decision → strategy → orchestration → execution → response) is fully implemented with proper authority boundaries. Post-purchase automation (funnel advancement, confirmation message, follow-up scheduling) is live.

**The single critical gap is product selection**: `resolve_commerce_product()` only works when there is exactly 1 valid product per creator. With 0 products → no commerce. With 2+ products → ambiguous, falls back to standard LLM. The LLM tool path (`propose_product_offer`) can bypass this by proposing a specific `product_id`, but this requires the LLM to know which product to offer — which it can only discover via the `get_product_information` tool (requires knowing the ID first).

**Segments are dashboard-only** — zero runtime integration. They cannot influence product selection, response strategy, commerce decisions, or any autonomous behavior.

### What Works Autonomously Today

| Capability | Status |
|---|---|
| Inbound message reception + persistence | ✅ LIVE |
| Debounce + rate limiting | ✅ LIVE |
| Context construction (profile, history, summary, commerce) | ✅ LIVE |
| Commerce signal extraction (LLM advisory) | ✅ LIVE |
| Deterministic commerce decision | ✅ LIVE |
| Strategy generation | ✅ LIVE |
| Offer execution (when product_id is known) | ✅ LIVE |
| Response generation (commerce + standard) | ✅ LIVE |
| Auto-approval routing | ✅ LIVE |
| Telegram send (text + media + vault) | ✅ LIVE |
| Outbound persistence | ✅ LIVE |
| Post-purchase automation | ✅ LIVE |
| Purchase attribution from webhook | ✅ LIVE |
| DLQ + retry + dedup | ✅ LIVE |

### What's Missing

| Capability | Status |
|---|---|
| Multi-product selection | ⚠️ INCOMPLETE |
| Segment-aware routing | ❌ NOT IMPLEMENTED |
| Segment-influenced product selection | ❌ NOT IMPLEMENTED |
| Segment-influenced response strategy | ❌ NOT IMPLEMENTED |

---

## 2. Actual Runtime Call Graph

```
Telegram User Message
  │
  ▼
[chatbotv2/handlers.py:25] handle_incoming_message()
  ├── check_rate_limit()                    ← Redis (ratelimit:{user_id})
  ├── upsert_user()                         ← PostgreSQL (users)
  ├── save_inbound_message()                ← PostgreSQL (messages, 1st persist)
  ├── publish_event("message.created")      ← Redis Pub/Sub
  └── debounce_enqueue()                    ← Redis (SET NX + RPUSH)
        │
        ▼  (debounce window owner only)
[chatbotv2/handlers.py:100] _wait_and_process()
  ├── sleep(debounce_window_seconds)
  ├── get_debounced_messages()              ← Redis (LRANGE + DEL)
  ├── resolve persona                       ← Redis cache → PostgreSQL
  └── enqueue_inbound()                     ← Redis Stream XADD "inbound_messages"
        │
        ▼  (separate LLM worker process)
[workers/llm_worker.py:567] run_worker() loop
  ├── requeue_stalled_messages()            ← Redis XAUTOCLAIM
  ├── read_inbound()                        ← Redis XREADGROUP
  │
  ▼
[workers/llm_worker.py:353] process_message()
  ├── acquire_user_lock()                   ← Redis SET NX
  ├── upsert_user()                         ← PostgreSQL (2nd persist)
  ├── save_inbound_message()                ← PostgreSQL (2nd persist)
  ├── is_user_auto_reply_excluded()         ← PostgreSQL
  ├── resolve_single_application_creator()  ← PostgreSQL (creator_integrations)
  │
  ├── build_context()                       ← PostgreSQL + Redis cache
  │     ├── get_user()                      ← PostgreSQL
  │     ├── get_user_profile()              ← PostgreSQL
  │     ├── build_system_prompt()           ← Pure function
  │     ├── get_latest_summary()            ← PostgreSQL
  │     ├── build_llm_context()             ← PostgreSQL (7 queries)
  │     ├── retrieve_relevant_history()     ← Gemini embedding + pgvector
  │     └── get_recent_messages()           ← PostgreSQL
  │
  ├── publish_event("ai.generation_started")
  │
  ├── _try_commerce_draft()                 ← COMMERCE PIPELINE
  │     ├── resolve_single_application_creator()
  │     ├── resolve_commerce_product()      ← PostgreSQL (fangate_products)
  │     ├── resolve_and_run_commerce()
  │     │     ├── resolve_commerce_state()  ← PostgreSQL (user, product, offers)
  │     │     └── run_commerce_pipeline()
  │     │           ├── extract_commerce_signals() ← Gemini (advisory)
  │     │           ├── decide_from_signals()      ← deterministic
  │     │           ├── build_strategy()            ← deterministic
  │     │           ├── orchestrate_commerce()
  │     │           │     ├── decide_commerce_action()
  │     │           │     ├── build_strategy()
  │     │           │     └── execute_ppv()         ← Fangate (live verify + offer)
  │     │           └── generate_commerce_response() ← Gemini (language only)
  │     └── select_commerce_response()      ← deterministic selection
  │
  ├── [if commerce selected] USE_COMMERCE_RESPONSE as draft
  ├── [if tools enabled] generate_draft_with_tools() ← Gemini + tool loop
  │     ├── dispatch_tool() for each function call
  │     │     ├── get_purchase_history     ← PostgreSQL (READ)
  │     │     ├── get_active_offers        ← PostgreSQL (READ)
  │     │     ├── get_product_information  ← PostgreSQL (READ)
  │     │     ├── propose_follow_up        ← PostgreSQL (PROPOSAL)
  │     │     └── propose_product_offer    ← Commerce pipeline (PROPOSAL)
  │     └── loop until text or max_tool_calls
  ├── [else] generate_draft()               ← Gemini (plain)
  │
  ├── score_draft()                         ← Gemini (cheap) + keyword scan
  │
  ├── [score ≥ threshold + no flags] → enqueue_send() ← Redis Stream XADD "send_messages"
  ├── [else] → add_to_operator_queue()      ← PostgreSQL
  │
  ├── post_process() (fire-and-forget)
  │     ├── extract_and_update_profile()    ← Gemini + PostgreSQL
  │     └── maybe_summarize()               ← Gemini + PostgreSQL
  │
  └── release_user_lock()                   ← Redis DEL
        │
        ▼  (main bot process, concurrent)
[chatbotv2/main.py:72] _process_send_stream()
  ├── requeue_stalled_send_messages()       ← Redis XAUTOCLAIM
  ├── release_stale_reservations()          ← PostgreSQL (vault)
  ├── read_send_messages()                  ← Redis XREADGROUP "send_messages"
  │
  ▼  (per message)
  ├── dedup check                           ← Redis EXISTS
  ├── rate limiting                         ← Redis Lua (token bucket)
  ├── entity resolution                     ← Telethon API
  ├── vault delivery reservation            ← PostgreSQL (vault_media_deliveries)
  ├── media validation                      ← HTTPS check + size check
  ├── TELEGRAM SEND                         ← Telethon send_message / send_file
  ├── dedup mark                            ← Redis SETEX
  ├── ACK                                   ← Redis XACK
  ├── save_outbound_after_send()            ← PostgreSQL (messages)
  ├── finalize_delivery()                   ← PostgreSQL (vault_media_deliveries)
  └── publish_event("message.sent")         ← Redis Pub/Sub
```

---

## 3. Autonomous Lifecycle Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    INBOUND LIFECYCLE                         │
│                                                             │
│  Telegram ──→ handlers.py ──→ debounce ──→ Redis Stream     │
│                                                             │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PROCESSING LIFECYCLE                      │
│                                                             │
│  LLM Worker ──→ Context ──→ Commerce ──→ LLM ──→ Score     │
│                    │           │                    │        │
│                    │           ▼                    │        │
│                    │     ┌──────────┐               │        │
│                    │     │ Signals  │               │        │
│                    │     │ Decision │               │        │
│                    │     │ Strategy │               │        │
│                    │     │ Execute  │               │        │
│                    │     │ Response │               │        │
│                    │     └──────────┘               │        │
│                    │                                │        │
│                    ▼                                ▼        │
│              ┌──────────┐                   ┌──────────┐    │
│              │ Commerce │                   │ Standard │    │
│              │ Response │                   │   LLM    │    │
│              └──────────┘                   └──────────┘    │
│                    │                                │        │
│                    └────────────┬───────────────────┘        │
│                                 ▼                            │
│                          ┌──────────┐                        │
│                          │ Routing  │                        │
│                          └──────────┘                        │
│                            │       │                         │
│                       ┌────┘       └────┐                   │
│                       ▼                 ▼                   │
│               ┌──────────┐       ┌──────────┐              │
│               │ Auto-    │       │ Operator │              │
│               │ Approved │       │  Queue   │              │
│               └──────────┘       └──────────┘              │
│                       │                 │                   │
│                       ▼                 ▼                   │
│               ┌──────────┐       ┌──────────┐              │
│               │  Send    │       │  Send    │              │
│               │ Stream   │       │  Worker  │              │
│               └──────────┘       └──────────┘              │
│                       │                 │                   │
│                       └────────┬────────┘                   │
│                                ▼                            │
└────────────────────────────────┬────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────┐
│                    OUTBOUND LIFECYCLE                        │
│                                                             │
│  Send Stream ──→ Dedup ──→ Rate Limit ──→ Vault Reserve     │
│       │                                        │            │
│       ▼                                        ▼            │
│  Telegram Send ──→ Outbound Persist ──→ Vault Finalize      │
│       │                                        │            │
│       ▼                                        ▼            │
│  Events Published                    Post-Purchase          │
│                                      ├─ Funnel Advance      │
│                                      ├─ Confirmation Msg    │
│                                      └─ Follow-up Schedule  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Reachability Matrix

| Component | File | Reachable from Inbound? | Production Status |
|---|---|---|---|
| Inbound handler | handlers.py:25 | ✅ Entry point | LIVE |
| Debounce | handlers.py:73 | ✅ Direct | LIVE |
| Redis stream enqueue | db/redis.py:156 | ✅ Direct | LIVE |
| LLM worker dequeue | llm_worker.py:567 | ✅ Consumer | LIVE |
| Context builder | memory/context.py:119 | ✅ Called by process_message | LIVE |
| Commerce context | memory/context_assembler.py:429 | ✅ Called by build_context | LIVE |
| Product selection | commerce/product_selection.py:60 | ✅ Called by _try_commerce_draft | LIVE |
| Commerce state | commerce/state.py:169 | ✅ Called by integration.py | LIVE |
| Signal extraction | commerce/deepseek.py:164 | ✅ Called by pipeline.py | LIVE |
| Decision engine | commerce/decision.py:194 | ✅ Called by signals.py | LIVE |
| Strategy builder | commerce/strategy.py:171 | ✅ Called by orchestrator.py | LIVE |
| Orchestrator | commerce/orchestrator.py:157 | ✅ Called by pipeline.py | LIVE |
| PPV execution | commerce/execution.py:126 | ✅ Called by orchestrator.py | LIVE |
| Commerce response | commerce/deepseek_response.py:465 | ✅ Called by pipeline.py | LIVE |
| Selection logic | commerce/selection.py:225 | ✅ Called by llm_worker.py | LIVE |
| LLM tools | core/llm_tools.py:777 | ✅ Called by generate_draft_with_tools | LIVE |
| Scoring | core/scoring.py:77 | ✅ Called by process_message | LIVE |
| Send stream | db/redis.py:65 | ✅ Called by enqueue_send | LIVE |
| Send processor | main.py:72 | ✅ Consumer | LIVE |
| Vault reserve | db/vault.py:46 | ✅ Called by send processor | LIVE |
| Vault finalize | db/vault.py:80 | ✅ Called by send processor | LIVE |
| Post-purchase | commerce/post_purchase.py:147 | ✅ Called by webhook handler | LIVE |
| Attribution | commerce/dao.py:248 | ✅ Called by webhook handler | LIVE |
| Segment evaluator | segments/evaluator.py | ❌ Not called by any runtime path | DASHBOARD ONLY |
| Segment models | segments/models.py | ❌ Not called by any runtime path | DASHBOARD ONLY |

---

## 5. Commerce Execution Audit

### Pipeline Status

| Stage | File:Line | Status | Notes |
|---|---|---|---|
| Signal extraction | deepseek.py:164 | LIVE | Gemini V4 Flash, advisory only |
| Signal → context | signals.py:171 | LIVE | Pure mapping |
| Decision engine | decision.py:194 | LIVE | 14-step priority rules |
| Strategy builder | strategy.py:171 | LIVE | Deterministic translation |
| Orchestrator | orchestrator.py:157 | LIVE | Gates execute_ppv |
| PPV execution | execution.py:126 | LIVE | Full 11-step validation |
| Response generation | deepseek_response.py:465 | LIVE | Gemini, language only |
| Selection | selection.py:225 | LIVE | USE_COMMERCE_RESPONSE or fallback |

### Authority Boundaries

| Concern | Authority | Cannot Be Overridden By |
|---|---|---|
| Product identity | Deterministic (1-product) or LLM tool | LLM text generation |
| Offer price | Fangate live verification | Any code path |
| Commerce action | Deterministic decision engine | LLM signal extraction |
| Eligibility | Deterministic policy | LLM or operator |
| Cooldowns | Deterministic (hours since last) | LLM or operator |
| Offer creation | Deterministic (serialized idempotent) | Any concurrent path |
| Response text | LLM (language only) | Cannot inject prices/URLs |

---

## 6. Product-Selection Audit

### Current Mechanism

`commerce/product_selection.py:60-109` — `resolve_commerce_product(creator_id)`:

```python
async def resolve_commerce_product(creator_id: int) -> int | None:
    products = await db_fangate.list_fangate_products(creator_id, limit=200, offset=0)
    valid = [p for p in products if _is_valid_product(p)]
    if len(valid) == 1:
        return int(valid[0]["id"])
    return None  # 0 or 2+ → ambiguous → fail-closed
```

**Validity criteria**: `is_accessible=True` AND non-empty `sales_url`.

### Two Paths to product_id

| Path | Caller | product_id Source | Validation |
|---|---|---|---|
| Autonomous (`_try_commerce_draft`) | llm_worker.py:301 | `resolve_commerce_product()` | Exactly 1 valid product |
| Tool (`propose_product_offer`) | llm_tools.py:685 | `args["product_id"]` from LLM | `get_fangate_product()` existence + ownership |

### Gap Analysis

- **0 valid products**: Commerce skipped entirely → standard LLM response
- **1 valid product**: Fully autonomous → offer creation, execution, response
- **2+ valid products**: Ambiguous → falls back to standard LLM. The LLM has no way to know which product to offer unless it uses `get_product_information` tool (which requires knowing the ID)

### The LLM Tool Path

When `llm_tools_enabled` is true, the LLM can call `propose_product_offer(product_id, reason)`. This path:
1. Verifies product exists and belongs to creator (`get_fangate_product`)
2. Builds its own `CommerceStateRequest` with the LLM-provided `product_id`
3. Runs full commerce pipeline

**But**: The LLM cannot discover product IDs autonomously. It would need to call `get_product_information` first, which requires a `product_id` input — a chicken-and-egg problem.

---

## 7. Fangate Integration Audit

### Client Methods

| Method | Used by Commerce? | Used by Dashboard? |
|---|---|---|
| `get_product` | ✅ Via `verify_product()` | ✅ |
| `list_products` | ❌ | ✅ Via `sync_products` |
| `update_product` | ❌ | ✅ |
| `update_product_price` | ❌ | ✅ |
| `toggle_product_collection` | ❌ | ✅ |
| `create_price_link` | ❌ | ✅ |
| `create_product` | ❌ | ✅ |
| All other methods | ❌ | ✅ |

### Service Methods Available but Unused by Commerce

| Method | Could Support Autonomous Product Resolution? |
|---|---|
| `list_products(creator_id)` | ✅ — lists all products from local mirror |
| `get_product(creator_id, product_id)` | ✅ — single product from local mirror |
| `verify_product(creator_id, product_id)` | ✅ — live Fangate verification |
| `sync_products(creator_id)` | ✅ — refresh mirror before resolution |

### Unused Fangate Data

- `product.raw->'media'` — media arrays (used by Vault, not by commerce)
- `product.sales_url` — price link (used by product selection validity check)
- `product.is_accessible` — accessibility flag (used by product selection)
- `product.price_minor` — price (used by execution verification)

---

## 8. Purchase Attribution Audit

### Webhook → Attribution Chain

```
POST /api/fangate/webhooks/{creator_id}
  → service.receive_webhook()
    → fdb.upsert_fangate_transaction()
    → commerce.dao.attribute_purchase_from_webhook()
      → find pending offers for product
      → if exactly 1: mark_offer_purchased() + attach_transaction_user()
      → if 0 or 2+: fail closed (return None)
    → handle_post_purchase()
      → advance_funnel_to_converted()
      → enqueue_purchase_confirmation()
      → schedule_follow_up()
```

### Identity Resolution

**NOT via buyer_email.** The mapping is indirect:
1. Offer created with `user_id` (Telegram) + `product_id`
2. Webhook carries `product_id` but NOT `user_id`
3. `attribute_purchase_from_webhook` finds pending offers for that `product_id`
4. If exactly 1 offer → that offer's `user_id` becomes the buyer
5. `user_id` written to `fangate_transactions.user_id`

### Post-Purchase Automation

| Action | Status | Code |
|---|---|---|
| Funnel advancement (`→ converted`) | ✅ LIVE | post_purchase.py:47-83 |
| Confirmation message | ✅ LIVE | post_purchase.py:86-128 |
| 24h follow-up scheduling | ✅ LIVE | post_purchase.py:131-145 |

---

## 9. Memory/Context Audit

### Context Available to LLM

| Data | Source | Loaded? | Passed to LLM? |
|---|---|---|---|
| Persona instructions | personas table | ✅ | ✅ System prompt |
| Fan first_name | users table | ✅ | ✅ System prompt |
| Profile facts | user_profiles.facts | ✅ | ✅ System prompt |
| Funnel stage | users.funnel_stage | ✅ | ✅ System prompt + commerce context |
| Conversation summary | conversation_summaries | ✅ | ✅ System message |
| Purchase count | commerce_offers | ✅ | ✅ Commerce context |
| Recent purchases | commerce_offers + fangate_products | ✅ | ✅ Commerce context |
| Active offer | commerce_offers | ✅ | ✅ Commerce context |
| Current product | fangate_products (1-product only) | ✅ | ✅ Commerce context |
| Creator name + sales_enabled | creators + creator_integrations | ✅ | ✅ Commerce context |
| Relevant past messages | pgvector search | ✅ (triggered) | ✅ System message |
| Recent messages (30) | messages table | ✅ | ✅ Conversation turns |
| User notes | users.notes | ✅ Loaded | ❌ NOT passed |
| Conversation tags | conversation_tag_assignments | ❌ Not loaded | ❌ NOT passed |
| Attention status | conversation_attention | ❌ Not loaded | ❌ NOT passed |
| Segment membership | fan_segments | ❌ Not loaded | ❌ NOT passed |
| Username | users.username | ✅ Loaded | ❌ NOT passed |
| is_blocked | users.is_blocked | ✅ Loaded | ❌ Only in ToolAuthContext |

### Information in DB but NOT Available to Autonomous Decisions

- Segment membership
- Operator notes
- Conversation tags
- Attention status/assignment
- Operator queue counts
- Scheduled messages (except last_followup_at)
- User username
- User first_seen/last_seen

---

## 10. Segment-Awareness Audit

| Capability | Status | Evidence |
|---|---|---|
| Segment CRUD | ✅ LIVE | db/segments.py, dashboard routes |
| Segment evaluation (SQL) | ✅ LIVE | segments/evaluator.py |
| Dashboard filtering | ✅ LIVE | users, dialogs, search, analytics, bulk ops |
| Runtime product selection | ❌ NOT IMPLEMENTED | Zero references in commerce/ or workers/ |
| Runtime response strategy | ❌ NOT IMPLEMENTED | Zero references in strategy.py |
| Runtime commerce decision | ❌ NOT IMPLEMENTED | Zero references in decision.py |
| Runtime offer eligibility | ❌ NOT IMPLEMENTED | Zero references in eligibility.py |
| Runtime content selection | ❌ NOT IMPLEMENTED | Zero references in any runtime file |
| Runtime routing | ❌ NOT IMPLEMENTED | Zero references in llm_worker.py |
| Operator escalation | ❌ NOT IMPLEMENTED | Zero references in any runtime file |

**Segments are a purely operator-facing analytical tool. They have zero influence on autonomous behavior.**

---

## 11. Vault Autonomy Audit

| Capability | Status | Evidence |
|---|---|---|
| Product → media mapping | ✅ LIVE | vault/aggregate.py |
| fangate_media_id resolution | ✅ LIVE | db/fangate.py:333-361 |
| Delivery reservation | ✅ LIVE | db/vault.py:46-77 |
| Stale reservation recovery | ✅ LIVE | db/vault.py:122-169 |
| Telegram send_file | ✅ LIVE | main.py:180-187 |
| Outbound persistence | ✅ LIVE | main.py:195-218 |
| Delivery finalization | ✅ LIVE | main.py:221-234 |
| Redis dedup | ✅ LIVE | main.py:93-97 |
| HTTPS validation | ✅ LIVE | main.py:40-69 |
| Creator isolation | ✅ LIVE | All queries scoped by creator_id |
| DB uniqueness constraint | ✅ LIVE | UNIQUE(creator_id, user_id, fangate_media_id) |

**Vault delivery is fully autonomous.** The send processor handles the complete reserve → send → finalize cycle without operator intervention.

---

## 12. Send-Pipeline Audit

| Component | Status | Evidence |
|---|---|---|
| Redis Streams (send_messages) | ✅ LIVE | db/redis.py:65-70 |
| DLQ (dead_letter_queue) | ✅ LIVE | db/redis.py:107-127 |
| XAUTOCLAIM | ✅ LIVE | db/redis.py:130-153 |
| Deduplication | ✅ LIVE | main.py:93-97 |
| Rate limiting | ✅ LIVE | main.py:107-114 |
| Entity resolution | ✅ LIVE | main.py:116-125 |
| Text sending | ✅ LIVE | main.py:187 |
| Media sending | ✅ LIVE | main.py:180-185 |
| Vault media sending | ✅ LIVE | main.py:134-186 |
| Outbound persistence | ✅ LIVE | main.py:195-218 |
| Event publication | ✅ LIVE | main.py:249-268 |
| UserIsBlocked handling | ✅ LIVE | main.py:270-294 |
| DLQ replay | ✅ LIVE | db/redis.py:155-180 |
| Shutdown behavior | ✅ LIVE | main.py uses asyncio tasks |

---

## 13. LLM Authority Audit

### What the LLM CAN Do

| Action | Mechanism | Authority |
|---|---|---|
| Generate conversational text | `generate_draft()` | Language only |
| Read purchase history | `get_purchase_history` tool | READ |
| Read active offers | `get_active_offers` tool | READ |
| Read product info | `get_product_information` tool | READ |
| Propose follow-up schedule | `propose_follow_up` tool | PROPOSAL (deterministic) |
| Propose product offer | `propose_product_offer` tool | PROPOSAL (routes to commerce pipeline) |
| Extract commerce signals | `extract_commerce_signals()` | Advisory (never reaches execution) |

### What the LLM CANNOT Do

| Action | Prevention |
|---|---|
| Send Telegram messages | No tool, no access to send stream |
| Modify database directly | No SQL access, no ORM |
| Access Redis | No access |
| Access Fangate API directly | No HTTP client access |
| Set offer prices | Execution uses live Fangate verification |
| Override creator identity | ToolAuthContext injected by runtime |
| Override eligibility | Deterministic policy engine |
| Override cooldowns | Deterministic decision engine |
| Choose product arbitrarily | `get_fangate_product()` verifies ownership |
| Bypass offer creation | `execute_ppv()` requires full validation |
| Send unauthorized offers | Deterministic decision engine gates all offers |

### LLM Authority Boundary

```
┌─────────────────────────────────────────────┐
│              LLM AUTHORITY                  │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  Generate text (conversational)     │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │  Read data (3 tools)                │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │  Propose actions (2 tools)          │    │
│  │  → routed through deterministic     │    │
│  │    pipeline for approval            │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ═══════════════════════════════════════    │
│  AUTHORITY BOUNDARY                         │
│  ═══════════════════════════════════════    │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  Deterministic application authority│    │
│  │  (decision, pricing, eligibility,   │    │
│  │   offer creation, sending)          │    │
│  └─────────────────────────────────────┘    │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 14. Failure-Mode Matrix

| Scenario | Current Behavior | Retry | DLQ | Idempotent | User-Facing | Intervention |
|---|---|---|---|---|---|---|
| A. LLM fails | ai.generation_failed event, no response | No | No | N/A | Silent (no reply) | Optional |
| B. LLM returns invalid commerce data | signal extraction returns low_information() | N/A | No | Yes | Falls back to standard LLM | No |
| C. Product selection fails | product_id=None → NO_OFFER → standard LLM | N/A | No | Yes | Normal text response | No |
| D. Product no longer exists | execute_ppv returns PRODUCT_UNAVAILABLE | N/A | No | Yes | Standard LLM response | No |
| E. Fangate price differs | execute_ppv returns PRODUCT_UNAVAILABLE | N/A | No | Yes | Standard LLM response | No |
| F. Offer creation fails | execute_ppv returns appropriate status | N/A | No | Yes | Standard LLM response | No |
| G. Telegram send fails | DLQ + message.send_failed event | XAUTOCLAIM | ✅ | Dedup | Silent | Optional |
| H. Worker crashes before send | XAUTOCLAIM reclaims (60s) | ✅ | ✅ | Dedup | Delayed | No |
| I. Worker crashes after Telegram send | Outbound persisted, finalize may be pending | N/A | No | Partial | Message sent | No |
| J. Database persistence fails | Exception propagates, DLQ | No | ✅ | Varies | Silent | Required |
| K. Fangate webhook arrives twice | Idempotent INSERT (UNIQUE delivery_id) | N/A | No | ✅ | No duplicate | No |
| L. Purchase cannot be attributed | attribution returns None, logged | N/A | No | Yes | No confirmation | Optional |
| M. Buyer cannot be mapped | 0 or 2+ offers → attribution fails | N/A | No | Yes | No confirmation | Optional |
| N. Post-purchase action fails | Best-effort, logged, doesn't break webhook | No | No | Partial | May miss confirmation | Optional |
| O. Redis dedup expires | 1h TTL → could re-send | N/A | No | Window | Possible duplicate message | No |
| P. Autonomous action attempted twice | User lock + dedup + idempotent DB | N/A | No | ✅ | No duplicate | No |

---

## 15. Invariant Verification

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | Creator isolation | ✅ PROVEN | All queries scoped by creator_id. UNIQUE constraints prevent cross-creator conflicts. |
| 2 | Deterministic commerce decision boundary | ✅ PROVEN | decision.py is pure deterministic. LLM signals are advisory only. |
| 3 | LLM authority isolation | ✅ PROVEN | LLM has 5 tools (3 read, 2 proposal). Cannot send, cannot mutate DB, cannot access Redis. |
| 4 | Offer price immutability | ✅ PROVEN | execute_ppv:275-292 verifies live Fangate price. Mismatch → PRODUCT_UNAVAILABLE. |
| 5 | Fangate price verification | ✅ PROVEN | execute_ppv calls fservice.verify_product() before offer creation. |
| 6 | Idempotent offer creation | ✅ PROVEN | create_offer_serialized with UNIQUE constraint + pre-check. |
| 7 | PostgreSQL delivery uniqueness | ✅ PROVEN | UNIQUE(creator_id, user_id, fangate_media_id) on vault_media_deliveries. |
| 8 | Vault delivery reservation safety | ✅ PROVEN | reserve_delivery uses INSERT ON CONFLICT DO NOTHING. finalize uses WHERE status='pending'. |
| 9 | Redis dedup remains optimization | ✅ PROVEN | DB constraints are authoritative. Redis dedup is additional safety layer. |
| 10 | Telegram side effects at-least-once | ✅ PROVEN | DLQ + XAUTOCLAIM ensure re-processing. Dedup window (1h) limits duplicates. |
| 11 | DLQ/XAUTOCLAIM behavior intact | ✅ PROVEN | requeue_stalled_send_messages uses XAUTOCLAIM with 60s idle threshold. |
| 12 | Existing operator/manual flows intact | ✅ PROVEN | Operator queue, send worker, manual sends all unchanged. |
| 13 | No cross-creator product exposure | ✅ PROVEN | get_fangate_product requires WHERE creator_id = $1. |
| 14 | No unauthorized autonomous sending | ✅ PROVEN | execute_ppv requires OFFER_PPV decision + allowed + activation params. |

---

## 16. Exact Autonomous Gaps

### Gap 1: Multi-Product Selection (CRITICAL)

**Current**: `resolve_commerce_product()` returns a product only when exactly 1 valid product exists. With 2+ products, commerce is skipped entirely.

**Impact**: Most creators with multiple products cannot benefit from autonomous commerce.

**What exists**: 
- `resolve_commerce_product()` — deterministic, single-product only
- `propose_product_offer` tool — LLM can propose, but can't discover IDs
- `get_product_information` tool — requires knowing the ID first

**Missing**: A mechanism to match user intent to the correct product from a multi-product catalog.

### Gap 2: Segment-Aware Routing (MODERATE)

**Current**: Segments are dashboard-only. Zero runtime integration.

**Impact**: Operators can define segments but they don't influence autonomous behavior.

**What exists**: Full segment evaluation engine (SQL compilation, rule evaluation, 35 fields).

**Missing**: Integration of segment membership into context construction, commerce decisions, or response strategy.

### Gap 3: Segment-Influenced Product Selection (MODERATE)

**Current**: Product selection is purely infrastructure-based (1-product filter). No consideration of user attributes, segment membership, or conversation context.

**Impact**: Same product offered to all users regardless of segment, purchase history, or engagement level.

**What exists**: Segment evaluation can determine membership. Commerce context has purchase history.

**Missing**: Product selection logic that considers segment membership, purchase history, or user attributes.

### Gap 4: LLM Product Discovery (LOW)

**Current**: LLM tools require knowing product_id to get product info. No "list products" tool.

**Impact**: LLM cannot autonomously discover which products exist or match user intent to products.

**What exists**: `get_product_information` tool (requires ID). `list_fangate_products` DB function.

**Missing**: A "list available products" tool that returns product catalog to the LLM.

---

## 17. Minimal Implementation Recommendations

### Recommendation 1: Multi-Product Selection Engine

**Approach**: Add a deterministic product-matching function that considers:
- User's purchase history (exclude already-purchased)
- User's funnel stage (new vs converted)
- Conversation context keywords
- Product metadata (title, price, description)

**Priority**: CRITICAL — this is the single biggest blocker for autonomous commerce.

### Recommendation 2: Segment Integration into Context

**Approach**: Add segment membership to the LLM context and commerce context:
- Query segment membership during context construction
- Include in `[APPLICATION CONTEXT — DETERMINISTIC FACTS]`
- Make available to decision engine

**Priority**: MODERATE — enables segment-aware behavior without changing architecture.

### Recommendation 3: List Products Tool

**Approach**: Add a `list_products` tool to the LLM's tool arsenal:
- Returns product catalog (id, title, price, accessibility)
- Enables LLM to match user intent to products
- Used by `propose_product_offer` tool

**Priority**: LOW — enables LLM-driven product discovery but requires multi-product selection first.

---

## 18. Explicitly Deferred Features

| Feature | Reason |
|---|---|
| Autonomous bulk messaging | Prohibited — violates architectural invariants |
| Campaign system | Prohibited — outside scope |
| LLM unrestricted tool authority | Prohibited — breaks authority boundaries |
| Cross-creator product exposure | Prohibited — breaks creator isolation |
| Local media storage | Prohibited — Fangate is source of truth |
| ORM introduction | Prohibited — raw asyncpg retained |
| Celery/task queue replacement | Prohibited — Redis Streams retained |
| Mass messaging | Prohibited — per-user processing only |

---

## 19. Files That Would Change

### For Multi-Product Selection
- `commerce/product_selection.py` — Add intent-based matching
- `commerce/state.py` — Support product selection context
- `memory/context_assembler.py` — Add product catalog to context
- `core/llm_tools.py` — Add `list_products` tool

### For Segment Integration
- `memory/context.py` — Load segment membership
- `memory/context_assembler.py` — Render segment data
- `commerce/state.py` — Accept segment context
- `commerce/decision.py` — Consider segment in decisions
- `commerce/strategy.py` — Consider segment in strategy

---

## 20. Tests That Would Be Required

### Multi-Product Selection
- Unit tests for intent-based product matching
- Integration tests for 0/1/2+ product scenarios
- Edge case tests for purchased products, inaccessible products
- Commerce pipeline tests with multi-product context

### Segment Integration
- Unit tests for segment membership loading
- Integration tests for segment-aware context construction
- Commerce decision tests with segment context
- End-to-end tests for segment-influenced routing

---

## 21. Security Implications

- **No new attack surfaces** — all changes are within existing authority boundaries
- **Creator isolation maintained** — all queries scoped by creator_id
- **LLM authority unchanged** — proposal tools still route through deterministic pipeline
- **Price immutability preserved** — Fangate verification unchanged
- **No new credentials** — using existing Fangate integration

---

## 22. Performance Implications

- **Product selection**: Additional DB query per inbound message (mitigated by caching)
- **Segment evaluation**: SQL compilation per message (mitigated by segment caching)
- **Context enrichment**: ~7 additional DB queries (mitigated by connection pooling)
- **No new external API calls** — all within existing Fangate/PostgreSQL/Redis stack

---

## 23. Recommended Implementation Order

1. **Multi-product selection** (CRITICAL) — unblocks autonomous commerce for all creators
2. **Segment context integration** (MODERATE) — enables segment-aware behavior
3. **List products tool** (LOW) — enables LLM product discovery
4. **Segment-influenced product selection** (LOW) — advanced personalization

---

RECONNAISSANCE COMPLETE — WAITING FOR IMPLEMENTATION APPROVAL
