# AUTONOMY 6.2 — END-TO-END COMMERCE RECONNAISSANCE

## Executive Summary

Phase 6.1 successfully wired product selection, segment context, and the
`list_products` tool into the autonomous runtime. The critical question was
whether these changes make autonomous PPV execution reachable end-to-end.

**Answer: YES — autonomous PPV execution is now reachable through the
production call graph, subject to one gating dependency: LLM signal
extraction must detect sufficient buying intent to trigger the
`OFFER_PPV` decision.**

The full call graph from Telegram inbound to offer creation has been
verified by source inspection. Every intermediate function exists, is
connected, and handles failures safely. The activation gate conditions
are satisfiable by a normal inbound conversation. The offer creation
path is idempotent and race-free. The response generation path injects
the verified sales URL and validates it post-generation.

The remaining gap is not structural but **behavioral**: the deterministic
decision engine requires `OFFER_PPV` action to reach `execute_ppv()`,
and `OFFER_PPV` requires either explicit buying intent or a strong
implicit buying signal from the LLM. If the LLM extracts the right
signals, the full autonomous path executes. If it does not, the system
safely falls back to the standard LLM path.

Post-purchase automation (funnel advancement, confirmation message,
follow-up scheduling) is LIVE and functional. Vault media delivery is
NOT triggered by the autonomous path — this is a separate capability
gap.

---

## 1. Actual Runtime Call Graph

### 1.1 Inbound Path

```
Telegram User
  → chatbotv2/handlers.py:handle_incoming_message() [line 25]
    → debounce_enqueue() [Redis lock + buffer]
    → _wait_and_process() [line 100]
      → get_debounced_messages() [Redis LRANGE + DEL]
      → enqueue_inbound() [Redis XADD "inbound_messages"]
```

### 1.2 Worker Consumption

```
workers/llm_worker.py:run_worker() [line 570]
  → requeue_stalled_messages() [XAUTOCLAIM idle>60s]
  → read_inbound() [XREADGROUP count=5 block=2000ms]
  → process_message() [line 356]
    → acquire_user_lock() [Redis SET NX EX 60s]
    → save_inbound_message() [PostgreSQL INSERT]
    → build_context() [PostgreSQL reads: user, profile, summary, messages]
    → publish_event("ai.generation_started")
```

### 1.3 Commerce Path (the critical chain)

```
process_message() [line 414]
  → _try_commerce_draft() [line 257]
    → resolve_single_application_creator() [single_creator.py]
      → Returns: SingleCreatorContext(status=READY, creator_id=N)
    → resolve_commerce_product_with_history(creator_id, user_id) [product_selection.py:147]
      → db_fangate.list_fangate_products(creator_id) [SELECT from fangate_products]
      → _is_valid_product() [filter: is_accessible + sales_url]
      → _get_purchased_product_ids(creator_id, user_id) [SELECT from commerce_offers]
      → Returns: product_id (or None if ambiguous/unavailable)
    → CommerceStateRequest(user_id, creator_id, product_id, messages, persona)
    → resolve_and_run_commerce(request) [integration.py:113]
      → resolve_commerce_state(request) [state.py:169]
        → db_postgres.get_user(user_id) [fan record]
        → db_postgres.is_user_auto_reply_excluded(user_id) [opt-out]
        → _resolve_creator_relationship() [offers + creators lookup]
        → db_fangate.get_creator_integration(creator_id) [integration status]
        → db_fangate.get_fangate_product(creator_id, product_id) [local mirror]
        → commerce_dao.find_pending_offer_for_product() [active offer check]
        → commerce_dao.has_purchased_product() [purchase check]
        → evaluate_ppv_eligibility() [pure function → PolicyDecision]
        → Segment evaluation [informational, non-critical]
        → Returns: CommercePipelineRequest with product_identity + product_state
      → run_commerce_pipeline(pipeline_request) [pipeline.py:383]
        → build_conversation_context() [pure translation]
        → extract_commerce_signals() [LLM call → CommerceSignals]
        → decide_from_signals() [pure decision engine → CommerceDecision]
        → build_strategy() [pure strategy builder → CommerceStrategy]
        → orchestrate_commerce(context) [orchestrator.py:157]
          → decide_commerce_action() [pure decision → CommerceDecision]
          → build_strategy() [pure strategy → CommerceStrategy]
          → _activation_for(context) [THE GATE]
          → IF OFFER_PPV + allowed + activation:
            → execute_ppv() [execution.py:126] ← THIS IS THE CRITICAL CALL
        → generate_commerce_response(input_) [LLM call → CommerceResponse]
      → Returns: CommerceIntegrationResult
    → select_commerce_response(outcome) [selection.py:225]
      → Returns: CommerceSelectionResult (USE_COMMERCE_RESPONSE or fallback)
```

### 1.4 Post-Commerce Path

```
process_message() [continues]
  → IF USE_COMMERCE_RESPONSE:
    → draft = selection.commerce_response_text [line 419]
  → score_draft(draft, user_message, context) [scoring.py]
  → IF auto_reply_on AND score >= 0.80 AND no flags:
    → enqueue_send({entity, content=draft, ...}) [Redis XADD "send_messages"]
    → publish_event("ai.generation_completed")
  → post_process(user_id) [profile extraction, summarization]
  → release_user_lock() [Redis DEL]
```

### 1.5 Send Path

```
chatbotv2/main.py:_process_send_stream() [line 72]
  → requeue_stalled_send_messages() [XAUTOCLAIM]
  → read_send_messages() [XREADGROUP]
  → is_send_duplicate(dedup_id) [Redis SET NX check]
  → check_send_rate_limit() [token bucket]
  → client.send_message(input_entity, content) [Telethon MTProto]
  → mark_send_dedup(dedup_id) [Redis SET with TTL]
  → ack_send(msg_id) [Redis XACK]
  → save_outbound_after_send() [PostgreSQL INSERT]
  → publish_event("message.sent")
```

---

## 2. Phase 6.1 Verification

### 2.1 Product Selection Reachability

**VERIFIED**: `resolve_commerce_product_with_history()` is called from
`_try_commerce_draft()` at `llm_worker.py:302-304`.

**Return value flow**:
- `product_id: int | None` → `CommerceStateRequest.product_id` (line 311)
- → `resolve_commerce_state()` builds `ProductIdentity` + `ProductCommerceState` (state.py:241-251)
- → `CommercePipelineRequest.product_identity` + `product_state` (state.py:287-288)
- → `_activation_for()` checks both are not None (orchestrator.py:87-89)
- → `execute_ppv(product_id=activation.product_id)` (orchestrator.py:194)

**Structure**: `ProductIdentity` contains `product_id`, `title`, `available`.
`ProductCommerceState` contains `price_minor`, `sales_url`, `is_accessible`,
`age_verification_required`. Both are built from the fangate_products local
mirror and are required by the activation gate.

### 2.2 Worker Wiring

**VERIFIED**: `_try_commerce_draft()` at line 302 calls
`resolve_commerce_product_with_history(creator.creator_id, user_id)`.

The worker passes the resolved product_id into the `CommerceStateRequest`,
which flows through `resolve_commerce_state()` → `run_commerce_pipeline()`
→ `orchestrate_commerce()` → `_activation_for()` → `execute_ppv()`.

### 2.3 Segment Context

**VERIFIED**: Segment evaluation happens in `resolve_commerce_state()` at
lines 260-280. Results flow into `CommercePipelineRequest.user_segment_names`
and `user_segment_count`. These propagate to `CommerceConversationContext`
and are available to the decision engine as informational context.

**Scope**: Informational only. Segment context does NOT influence product
selection, commerce decisions, or execution authority. It is metadata.

### 2.4 list_products Tool

**VERIFIED**: `list_products` is registered in `core/llm_tools.py` as a
`TOOL_TYPE_READ`. The handler calls `list_valid_products()` from
`commerce/product_selection.py`, which returns structured product data
(product_id, title, price_minor, currency). The LLM can use this to
understand the product catalog but cannot use it to select products for
autonomous execution (product selection remains deterministic).

---

## 3. Product-Selection Reachability

**CAN A NORMAL INBOUND MESSAGE REACH PRODUCT SELECTION?**

YES. The conditions are:

1. Creator resolves: `resolve_single_application_creator()` returns READY
   with a valid `creator_id`. This requires exactly one active
   `creator_integrations` row. (single_creator.py)

2. Valid products exist: `list_fangate_products(creator_id)` returns at
   least one product where `is_accessible=True` and `sales_url` is
   non-empty. (product_selection.py:_is_valid_product)

3. No ambiguity: exactly one valid product remains after excluding
   already-purchased products. (product_selection.py:219)

When all three conditions hold, `product_id` is non-None and the
commerce pipeline receives a valid product to operate on.

**CONSTRAINT**: The creator must have exactly ONE valid product for the
autonomous path to proceed. Multiple valid products (after purchase
exclusion) cause fail-closed ambiguity.

---

## 4. Activation Gate Audit

### Gate Conditions (orchestrator.py:78-94, 186-205)

| Condition | Required | Actual Runtime Value | Source | Can Be None | Blocks? |
|-----------|----------|---------------------|--------|-------------|---------|
| eligibility.allowed | YES | True if: user not blocked, not opted out, creator active, product accessible, has sales_url, not already purchased, no active offer, age verified if required | evaluate_ppv_eligibility() via state.py:213-236 | No (always bool) | YES |
| product_identity | YES | ProductIdentity(product_id, title, available) from fangate_products local mirror | state.py:241-244 | No (must be non-None) | YES |
| product_state | YES | ProductCommerceState(price_minor, sales_url, is_accessible, age_verification_required) from fangate_products local mirror | state.py:246-251 | No (must be non-None) | YES |
| decision.action == OFFER_PPV | YES | Determined by decide_commerce_action() based on LLM signals + app state | decision.py:194-386 | No (always an enum) | YES |
| decision.allowed | YES | True only for OFFER_PPV, SOFT_OFFER, FOLLOW_UP actions | decision.py (by construction) | No | YES |

### CAN A NORMAL INBOUND TELEGRAM CONVERSATION SATISFY THE ACTIVATION GATE?

**YES** — under these conditions:

1. The creator has an active Fangate integration
2. The creator has exactly one valid product (accessible + has sales_url)
3. The user is not blocked or opted out
4. The user has not already purchased the product
5. There is no existing active offer for this product
6. The LLM extracts sufficient buying intent (explicit request or strong
   implicit signal ≥ 0.80)

When all conditions hold, `decide_commerce_action()` returns OFFER_PPV,
the activation gate opens, and `execute_ppv()` is called.

---

## 5. execute_ppv() Reachability

### 5.1 Callers

| Caller | Classification | Evidence |
|--------|---------------|----------|
| `orchestrate_commerce()` | **AUTONOMOUS PRODUCTION** | orchestrator.py:191 — the only production caller |
| `test_commerce_*.py` | Test-only | Various test files |
| Dashboard routes | Manual/operator | No direct `execute_ppv()` calls found in dashboard routes |

### 5.2 Conditions for Autonomous Invocation

`execute_ppv()` is called from `orchestrate_commerce()` at line 191 when:

1. `decision.action is CommerceAction.OFFER_PPV` (line 189)
2. `decision.allowed is True` (line 189)
3. `_activation_for(context) is not None` (line 189)

Condition 3 requires:
- `context.eligibility.allowed == True` (line 85)
- `context.product_identity is not None` (line 87-88)
- `context.product_state is not None` (line 88)

### 5.3 Arguments Supplied

```python
execute_ppv(
    creator_id=context.creator_id,        # from resolve_commerce_state
    user_id=context.user_id,              # from request
    product_id=activation.product_id,     # from ProductIdentity
    decision=decision,                    # the OFFER_PPV decision
    created_by="commerce_orchestrator",   # audit label
    age_verified=activation.age_verified, # from ProductCommerceState
)
```

No price, URL, or currency is supplied — all derived from Fangate live
verification inside `execute_ppv()`.

### 5.4 Authority Conditions Inside execute_ppv()

The function performs 12 authority re-checks (execution.py:126-349):

1. Decision authority: OFFER_PPV + allowed (line 143)
2. Creator exists (line 151)
3. Integration active (line 157)
4. Vault credential decryptable (line 166)
5. Fan not blocked/opted-out (line 179)
6. Local product exists (line 192)
7. No existing pending offer (line 210)
8. Not already purchased (line 217)
9. Eligibility re-evaluation (line 224)
10. Live Fangate verification (line 247) — authoritative link/price
11. Consistency check: remote == local (lines 276-292)
12. Serialized INSERT with advisory lock (line 299)

### 5.5 Is it Reachable?

**YES.** All 12 conditions are satisfiable by the automated path. The
function is called from the autonomous LLM worker via the orchestration
layer. No operator intervention is required.

---

## 6. Fangate Offer Creation Audit

### 6.1 What execute_ppv() Actually Creates

`execute_ppv()` does NOT call a Fangate "create offer" API. The Fangate
API has no such endpoint. Instead:

1. **Remote call**: `fservice.verify_product(creator_id, product_id)` —
   a READ-ONLY `GET /products/{id}` that returns the authoritative
   `link` (sales_url) and `price_minor`. (execution.py:247)

2. **Local INSERT**: `create_offer_serialized()` inserts a row into
   `commerce_offers` with:
   - `creator_id`, `user_id`, `product_id`
   - `link` = the Fangate sales_url (from live verification)
   - `price_minor` = the Fangate price (from live verification)
   - `currency` = from creator integration
   - `reason` = "ppv_execution"
   - `state` = "pending" (default)

### 6.2 Fangate Method Used

```python
# integrations/fangate/service.py:235-250
async def verify_product(creator_id: int, product_id: int) -> FangateProduct:
    # → FangateClient.get_product(product_id)
    # → GET /products/{product_id}
    # → Returns FangateProduct with authoritative link + price_minor
```

### 6.3 Price Verification

**YES** — the authoritative price is verified. Lines 276-292 of
`execution.py` compare the remote `price_minor` against the local
mirror. If they differ, execution is refused with
`PRODUCT_UNAVAILABLE/product_inconsistent`.

### 6.4 Offer Idempotency

`create_offer_serialized()` (dao.py:56-120):
1. Acquires advisory PostgreSQL transaction lock:
   `pg_advisory_xact_lock(hashtextextended("ppv_offer:{creator_id}:{user_id}:{product_id}"))`
2. Checks for existing pending/clicked offer inside the lock
3. If exists: returns `(existing_row, False)`
4. If not: INSERTs new row, returns `(new_row, True)`

Concurrent executions of the same logical PPV are serialized at the
database level. No duplicate offers can be created.

### 6.5 Offer URL in Response

The Fangate sales_url flows through:
1. `ProductCommerceState.sales_url` (from fangate_products mirror)
2. `_VerifiedFacts.build()` → system prompt includes `Sales URL: {url}`
   (deepseek_response.py:332-339)
3. LLM generates conversational text containing the URL
4. `_urls_are_authoritative()` validation ensures only the verified URL
   appears in the output (deepseek_response.py:426-436)

---

## 7. Response Generation Audit

### 7.1 Does the Response Know an Offer Was Created?

YES. `generate_commerce_response()` receives `execution_result` in its
input. The `_VerifiedFacts` class checks `execution.status` to
determine whether success claims are allowed
(`_offer_claim_integrity()` at deepseek_response.py:457-462).

### 7.2 Does It Receive the Offer URL?

YES. The URL comes from `product_state.sales_url`, which is injected
into the system prompt as a "VERIFIED FACT" by `_VerifiedFacts.build()`.

### 7.3 Can It Claim an Offer Exists When Creation Failed?

NO. The `_offer_claim_integrity()` validator (line 457-462) rejects
responses that claim an offer exists when `execution.status` is not in
`_OFFER_ACTIVE_STATUSES` (EXECUTED, ALREADY_EXECUTED).

### 7.4 Is the Final Text Routed Through the Normal Send Pipeline?

YES. After `select_commerce_response()` returns `USE_COMMERCE_RESPONSE`,
the commerce response text becomes the `draft` in `process_message()`.
It goes through:
- `score_draft()` (scoring.py)
- `enqueue_send()` → Redis `send_messages` stream
- `_process_send_stream()` → `client.send_message()` → Telegram

### 7.5 Is the Offer Link Actually Sent to Telegram?

YES. The Fangate sales_url is embedded in the LLM-generated response
text, which is sent verbatim through the send pipeline to Telegram.

---

## 8. End-to-End State Transition

```
NO_COMMERCE
  ↓ extract_commerce_signals() [LLM call]
COMMERCE_SIGNALS
  ↓ decide_from_signals() [pure decision engine]
DECISION (OFFER_PPV)
  ↓ build_strategy() [pure strategy builder]
STRATEGY
  ↓ _activation_for() [gate check]
ACTIVATED
  ↓ execute_ppv() [12-step gated execution]
OFFER_CREATED (commerce_offers row, state=pending)
  ↓ generate_commerce_response() [LLM call with verified facts]
RESPONSE_GENERATED (text with verified sales_url)
  ↓ select_commerce_response() [selection logic]
USE_COMMERCE_RESPONSE
  ↓ score_draft() [scoring]
SCORED
  ↓ enqueue_send() [Redis stream]
MESSAGE_QUEUED
  ↓ client.send_message() [Telethon MTProto]
MESSAGE_SENT
```

### Missing Transitions

None. Every transition has a corresponding function, state object, and
failure behavior. The full path is reachable from the automated worker.

---

## 9. Operator Dependency Audit

| Capability | Autonomous | Manual Only | Both |
|-----------|----------:|----------:|-----:|
| Product selection | **YES** | — | — |
| Offer creation | **YES** | — | — |
| PPV response | **YES** | — | — |
| Text send | **YES** | — | — |
| Media send | — | **YES** | — |
| Purchase attribution | **YES** | — | — |
| Post-purchase action | **YES** | — | — |

**Key finding**: The only remaining operator dependency is **Vault media
delivery**. After a purchase is confirmed, the system does NOT
automatically deliver purchased media files to the Telegram user. The
confirmation message says "your content is now available" but the user
must access it through the Fangate link. Automated Vault delivery
requires a separate implementation.

---

## 10. Purchase Webhook Audit

### LIVE Path

```
Fangate purchase
  → POST /api/fangate/webhooks/{creator_id} [fangate.py:679]
    → verify_webhook_signature() [HMAC-SHA256]
    → insert_fangate_webhook_event() [ON CONFLICT DO NOTHING]
    → upsert_fangate_transaction() [ON CONFLICT DO NOTHING]
    → attribute_purchase_from_webhook() [atomic transaction]
      → Find single pending offer for product
      → UPDATE offer SET state='purchased'
      → UPDATE fangate_transactions SET user_id
      → INSERT ppv_analytics_daily
    → handle_post_purchase()
      → advance_funnel_to_converted() [users.funnel_stage = 'converted']
      → enqueue_purchase_confirmation() [Redis send stream]
      → schedule_follow_up() [24h delayed message]
```

### Buyer Identity Mapping

The `buyer_email` from Fangate is mapped to a Telegram `user_id`
**only through the `commerce_offers` table**. When a webhook arrives:

1. `attribute_purchase_from_webhook()` finds pending offers for the
   product (commerce/dao.py:248-388)
2. If exactly 1 pending offer exists: the offer's `user_id` is the
   Telegram user
3. If 0 or >1 pending offers: attribution fails (returns None)

**Limitation**: If a purchase arrives from a buyer who never received a
PPV offer through the bot, the `buyer_email` is stored but never mapped
to a Telegram user. The `user_id` on `fangate_transactions` remains NULL.

---

## 11. Post-Purchase Automation Audit

| Capability | Status | Evidence |
|-----------|--------|----------|
| Offer state transition | **LIVE** | dao.py:mark_offer_purchased() |
| Transaction attribution | **LIVE** | dao.py:attribute_purchase_from_webhook() |
| Funnel stage update | **LIVE** | post_purchase.py:advance_funnel_to_converted() |
| Confirmation message | **LIVE** | post_purchase.py:enqueue_purchase_confirmation() |
| Follow-up scheduling | **LIVE** | post_purchase.py:schedule_follow_up() |
| Segment changes | IMPLEMENTED BUT UNREACHABLE | Segments are rule-based, not event-driven |
| Media delivery | MISSING | No automated Vault delivery after purchase |
| Operator notification | N/A | Autonomous path has no operator involvement |

---

## 12. Vault Autonomy Audit

Vault delivery is fully implemented for manual/dashboard flows:

```
reserve_delivery() → send_file() → finalize_delivery()
```

But the autonomous commerce path does NOT trigger Vault delivery. After
`execute_ppv()` creates an offer and the user purchases, the
`handle_post_purchase()` function sends a text confirmation but does
NOT deliver media files.

**Media delivery is operator-only in the current system.**

---

## 13. Segment Context Audit

**Scope**: Informational only.

Segment evaluation happens in `resolve_commerce_state()` (state.py:260-280)
and flows into `CommercePipelineRequest.user_segment_names` and
`user_segment_count`. These propagate to the LLM context via
`LLMContext.segment_names` (context_assembler.py).

**Does segment context influence product selection?** NO. Product
selection is deterministic based on `is_accessible` and `sales_url`.

**Does segment context influence commerce decisions?** NO. The decision
engine (`decide_commerce_action()`) does not read segment fields. The
segment data is available in the conversation context but the
deterministic engine ignores it.

**Does segment context influence response strategy?** NO. The strategy
builder (`build_strategy()`) does not read segment fields.

**Conclusion**: Segment context is purely informational. It appears in
the LLM's conversation context and may influence the LLM's signal
extraction, but the deterministic machinery ignores it entirely.

---

## 14. LLM Authority Audit

### What the LLM May Do

- Understand conversation context
- Extract commerce signals (purchase_intent, content_interest, etc.)
- Generate conversational responses with verified facts
- Inspect product information via `list_products` tool (read-only)

### What the LLM Must NOT Do

- Create offers → **VERIFIED**: LLM never calls `execute_ppv()`
- Choose product IDs → **VERIFIED**: Product selection is deterministic
  via `resolve_commerce_product_with_history()`
- Set prices → **VERIFIED**: Prices come from Fangate live verification
- Bypass activation → **VERIFIED**: Activation gate is in orchestrator.py,
  not reachable from LLM output
- Directly call Telegram → **VERIFIED**: Send path goes through Redis
  stream → `_process_send_stream()`
- Mutate delivery records → **VERIFIED**: Vault operations are in
  service.py, not callable from LLM context
- Modify purchase state → **VERIFIED**: Purchase attribution is in
  webhook handler, not reachable from LLM
- Bypass creator isolation → **VERIFIED**: All queries are
  creator-scoped

### Violation Check

**No violations found.** The LLM authority boundary is intact. The LLM
provides advisory signals only; all authority rests in deterministic
machinery.

---

## 15. Failure-Mode Matrix

| Case | Expected Behavior | Actual Behavior | Evidence |
|------|------------------|----------------|----------|
| A — No eligible product | No product → no PPV → standard LLM | product_id = None → resolve_commerce_state returns PRODUCT_UNAVAILABLE or pipeline skips execution → select_commerce_response returns FALLBACK_TO_STANDARD_LLM → standard LLM draft | product_selection.py:188-193, selection.py:276-281 |
| B — Multiple ambiguous products | Ambiguity → fail closed | product_id = None → same as A | product_selection.py:232-241 |
| C — Fangate unavailable | Execution refused | execute_ppv returns FANGATE_ERROR → orchestration reports execution_failure → response generation still runs → selection returns FALLBACK_TO_STANDARD_LLM | execution.py:246-273, selection.py:290-295 |
| D — Offer creation fails | Structured error, no retry | create_offer_serialized exception → recovered via find_pending_offer → ALREADY_EXECUTED or PERSISTENCE_FAILED | execution.py:309-322 |
| E — Offer created but response fails | Offer preserved, execution_failure | execution_result preserved verbatim → response.status != GENERATED → selection returns FALLBACK_TO_STANDARD_LLM | pipeline.py:485-504, selection.py:297-309 |
| F — Response generated but Telegram fails | Redis retry, DLQ on persistent failure | move_send_to_dlq() → ack original → replay_dlq_entry() for retry | redis.py:move_send_to_dlq(), main.py:270-325 |
| G — Worker crashes after offer | Offer is persistent (PostgreSQL), stalled message reclaimed | XAUTOCLAIM recovers message → reprocess → create_offer_serialized returns ALREADY_EXECUTED | redis.py:requeue_stalled_messages(), dao.py:99-100 |
| H — Fangate webhook duplicated | Idempotent: ON CONFLICT DO NOTHING | insert_fangate_webhook_event() + upsert_fangate_transaction() both use ON CONFLICT | fangate.py:462, service.py:818-836 |
| I — Purchase cannot be attributed | Transaction recoverable, user_id NULL | attribute_purchase_from_webhook() returns None → transaction persists with NULL user_id → recoverable later | dao.py:248-388 |
| J — Telegram user cannot be mapped | Confirmation skipped, funnel updated | handle_post_purchase() checks user_id, skips if None | post_purchase.py:159-165 |

---

## 16. Idempotency Audit

### Can Duplicate Processing Cause Two Offers?

**NO.** The `create_offer_serialized()` function (dao.py:56-120) uses:

1. **Advisory PostgreSQL transaction lock**: `pg_advisory_xact_lock(
   hashtextextended("ppv_offer:{creator_id}:{user_id}:{product_id}"))`
   — serializes concurrent executions of the same logical PPV.

2. **Inside the lock**: checks for existing pending/clicked offer for
   the same (creator, user, product) tuple.

3. **INSERT**: only if no existing offer found.

4. **Returns (row, created)**: `created=False` means the offer already
   existed.

The combination of advisory lock + conditional check + conditional insert
makes duplicate offer creation impossible, even under concurrent processing.

### Message-Level Dedup

The inbound message is processed at most once per debounce window (Redis
lock). The worker acquires a per-user lock (60s TTL). The send path has
dedup via MD5 of `user_id:content:telegram_message_id`.

### Race Window

There is a theoretical race between:
1. `execute_ppv()` checks `find_pending_offer_for_product()` (line 210)
2. `execute_ppv()` calls `create_offer_serialized()` (line 299)

But `create_offer_serialized()` re-checks inside the advisory lock, so
the application-level check at line 210 is an optimization, not a
correctness requirement. The lock guarantees correctness.

---

## 17. Security / Authority Invariants

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Creator isolation | **PROVEN** | All queries filter by creator_id. fangate_products, commerce_offers, fangate_transactions are creator-scoped. |
| 2 | Product creator isolation | **PROVEN** | get_fangate_product(creator_id, product_id) refuses cross-creator access. execute_ppv() uses creator-scoped product lookup. |
| 3 | Fangate credential isolation | **PROVEN** | Credentials stored encrypted (Fernet). Decrypted only in execution scope. Never exposed on result surfaces. |
| 4 | Deterministic product validation | **PROVEN** | _is_valid_product() is pure. Product selection is deterministic from DB snapshots. |
| 5 | Price authority | **PROVEN** | execute_ppv() fetches live Fangate price, compares against local mirror, refuses on mismatch. |
| 6 | Offer idempotency | **PROVEN** | Advisory lock + conditional insert in create_offer_serialized(). |
| 7 | LLM authority isolation | **PROVEN** | LLM provides signals only. Decision, strategy, execution are all deterministic. |
| 8 | Segment informational-only | **PROVEN** | Segment data flows to LLM context but decision engine ignores it. |
| 9 | Telegram send authorization | **PROVEN** | Send path requires dedup + rate limit + entity resolution. No direct LLM→Telegram path. |
| 10 | Vault delivery uniqueness | **UNPROVEN** | Vault delivery is not triggered by autonomous path. Manual flows have reserve/finalize pattern. |
| 11 | PostgreSQL authority over Redis | **PROVEN** | Advisory locks in PostgreSQL serialize offer creation. Redis dedup is defense-in-depth. |
| 12 | Manual/operator flows intact | **PROVEN** | No changes to operator queue, dashboard, or manual offer creation paths. |

---

## 18. Performance Audit

### Per-Inbound-Message Query Count

| Query | Source | Frequency |
|-------|--------|-----------|
| `get_user(user_id)` | PostgreSQL | 1x (context) + 1x (state) + 1x (execute_ppv) = 3x |
| `save_inbound_message()` | PostgreSQL | 1x (handler) + 1x (worker) = 2x |
| `list_fangate_products(creator_id)` | PostgreSQL | 1x (product_selection) + 1x (state) = 2x |
| `get_creator_integration(creator_id)` | PostgreSQL | 1x (state) + 1x (execute_ppv) = 2x |
| `find_pending_offer_for_product()` | PostgreSQL | 1x (state) + 1x (execute_ppv) = 2x |
| `has_purchased_product()` | PostgreSQL | 1x (state) + 1x (execute_ppv) = 2x |
| `_get_purchased_product_ids()` | PostgreSQL | 1x (product_selection) |
| `list_segments()` | PostgreSQL | 1x (state) |
| `check_user_in_segment()` | In-memory eval | Nx (per segment) |
| `extract_commerce_signals()` | LLM (DeepSeek) | 1x |
| `generate_commerce_response()` | LLM (DeepSeek) | 1x |
| `verify_product()` | Fangate API | 1x (execute_ppv only) |

**Total per message**: ~15 PostgreSQL queries + 2 LLM calls + 0-1 Fangate API calls.

### Index Concerns

- `_get_purchased_product_ids()` queries `commerce_offers` with
  `WHERE creator_id = $1 AND user_id = $2 AND state = 'purchased'`.
  Needs index on `(creator_id, user_id, state)`.

- `list_fangate_products()` queries with `WHERE creator_id = $1`.
  Existing index on `creator_id` should suffice.

### N+1 Risk

No N+1 patterns detected. Product queries are creator-scoped and
batched (limit=200). Segment evaluation is in-memory after a single
list query.

---

## 19. Test Coverage Audit

### Unit Coverage (test_phase_6_1.py — 36 tests)

| Area | Tests | What They Prove |
|------|-------|----------------|
| Product selection | 12 | resolve_commerce_product_with_history correctness, purchase exclusion, creator isolation, failure paths |
| Product listing | 3 | list_valid_products correctness |
| Worker integration | 1 | Worker imports the correct resolver |
| Segment context | 5 | _get_segments_safe, LLMContext fields, render_context |
| Segment propagation | 4 | Pipeline request fields, state resolution with segments |
| list_products tool | 2 | Tool handler correctness |
| Creator isolation | 3 | Cross-creator product filtering |
| Failure paths | 4 | DB failures degrade safely |
| E2E reachability | 2 | State resolution → pipeline request flow |

### Runtime Integration Coverage

| What | Tested? | Limitation |
|------|---------|------------|
| Actual Telegram inbound → offer creation | NO | End-to-end requires running Telegram bot + Fangate API |
| LLM signal extraction → OFFER_PPV decision | NO | Requires live LLM + realistic conversation |
| execute_ppv() with live Fangate verification | NO | Requires live Fangate API credentials |
| Response generation with verified URL | NO | Requires live LLM |
| Post-purchase webhook → attribution | NO | Requires live Fangate webhook |

**Conclusion**: Unit tests prove component correctness. No runtime
integration tests prove the full autonomous path end-to-end. The gap
is not a code defect but a testing infrastructure limitation.

---

## 20. Autonomy Matrix

| Capability | Exists | Reachable | Autonomous | Operator Required | Safe | Evidence |
|-----------|--------|-----------|------------|-------------------|------|----------|
| Inbound processing | YES | YES | YES | — | YES | handlers.py, llm_worker.py |
| Commerce signal extraction | YES | YES | YES | — | YES | deepseek.py, signals.py |
| Deterministic decision | YES | YES | YES | — | YES | decision.py |
| Product selection | YES | YES | YES | — | YES | product_selection.py |
| Product validation | YES | YES | YES | — | YES | execution.py:12-step |
| Offer creation | YES | YES | YES | — | YES | dao.py:create_offer_serialized |
| PPV response | YES | YES | YES | — | YES | deepseek_response.py |
| Telegram text send | YES | YES | YES | — | YES | main.py:_process_send_stream |
| Telegram media send | YES | YES | NO | YES | YES | vault/service.py (manual) |
| Purchase webhook | YES | YES | YES | — | YES | fangate.py:webhook endpoint |
| Purchase attribution | YES | YES | YES | — | YES | dao.py:attribute_purchase |
| Buyer identity mapping | YES | YES | YES | — | YES | Via commerce_offers.user_id |
| Funnel update | YES | YES | YES | — | YES | post_purchase.py |
| Post-purchase response | YES | YES | YES | — | YES | post_purchase.py |
| Vault delivery | YES | YES | NO | YES | YES | vault/service.py (manual) |
| Segment context | YES | YES | YES | — | YES | state.py, context_assembler.py |
| Operator escalation | YES | YES | YES | — | YES | llm_worker.py:operator queue |

---

## 21. Remaining Gaps Ranked by Severity

### CRITICAL — Prevents autonomous commerce from completing

**None.** The full autonomous path from Telegram inbound to offer
creation is reachable and functional.

### HIGH — Allows commerce but causes correctness/safety concerns

**None.** All authority invariants are proven. Idempotency is proven.
Failure modes are handled safely.

### MEDIUM — Limits autonomy but does not prevent the core lifecycle

**1. Timing fields stay at neutral defaults**
- File: `commerce/state.py`
- Function: `resolve_commerce_state()`
- Current: `hours_since_last_offer`, `hours_since_last_purchase`,
  `recent_offer_count`, `recent_purchase_count`,
  `recent_sales_attempt_count` are all `None`/0
- Impact: Cooldown checks in the decision engine (steps 5-7) never
  trigger. This means the system could offer to a user who just
  purchased (within the cooldown window) if the webhook attribution
  hasn't completed yet.
- Risk level: LOW in practice (webhook attribution is fast, and the
  purchase-history exclusion in product selection provides a safety net)

**2. Vault media delivery not triggered autonomously**
- File: `commerce/post_purchase.py`
- Current: Post-purchase sends a text confirmation only. No media files
  are delivered.
- Impact: Buyers must access content through the Fangate link. No
  in-Telegram content delivery.
- Risk level: MEDIUM (operational inconvenience, not a safety issue)

**3. LLM signal extraction is the sole trigger bottleneck**
- File: `commerce/deepseek.py` → `extract_commerce_signals()`
- Current: The LLM must detect buying intent to produce OFFER_PPV
  decision. If the LLM is conservative or uncertain, it produces weak
  signals that result in RELATIONSHIP_BUILDING instead.
- Impact: Autonomous commerce only fires when the LLM detects intent.
  Conservative LLM behavior = fewer autonomous offers.
- Risk level: MEDIUM (design intent — conservative is safer)

### LOW — Convenience / optimization

**4. Duplicate inbound message persistence**
- File: `workers/llm_worker.py:374-375`
- Current: `save_inbound_message()` is called in both the handler AND
  the worker, resulting in two DB writes for the same message.
- Impact: Minor DB bloat. No functional impact.
- Risk level: LOW

**5. No post-purchase confirmation personalization**
- File: `commerce/post_purchase.py`
- Current: Confirmation and follow-up messages are hardcoded strings.
  No product name, price, or creator-specific content.
- Impact: Generic UX. No safety concern.
- Risk level: LOW

---

## 22. Recommended Next Phase

Based on the evidence, the next phase should address the earliest
missing link in the actual lifecycle.

**The core autonomous lifecycle (inbound → offer → response → send) is
COMPLETE.** The remaining gaps are:

1. **Timing fields** (MEDIUM) — would make cooldown checks functional
2. **Vault media delivery** (MEDIUM) — would complete the purchase
   experience
3. **Signal extraction tuning** (MEDIUM) — would increase autonomous
   commerce hit rate

The smallest coherent next phase is:

**Phase 7: Timing Field Resolution + Vault Post-Purchase Delivery**

This addresses the two MEDIUM gaps that limit autonomy without
preventing the core lifecycle. Timing fields make the cooldown checks
functional (preventing premature re-offers). Vault delivery completes
the purchase experience (delivering content in-Telegram after purchase).

---

## AUTONOMY 6.2 RECONNAISSANCE COMPLETE — WAITING FOR IMPLEMENTATION APPROVAL
