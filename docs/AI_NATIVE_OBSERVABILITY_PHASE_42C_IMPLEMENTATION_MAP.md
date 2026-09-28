# AI_NATIVE_OBSERVABILITY_PHASE_42C_IMPLEMENTATION_MAP
**Stage B -- Surgical Observability Upgrade**
**Date: 2026-08-31 | Scope: Realtime Execution, Commerce Events, Creator Isolation**

## Overview
Additive, minimal changes via existing `core/event_bus.py` + PubSub + WebSocket + polling fallback. No new worker/queue/stream/LLM.

## Step 1 -- Creator-scoped event envelope
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `core/event_bus.py:13` | `publish_event` | Add optional `creator_id: int|None=None` param, include in `event` dict | Creator isolation without breaking legacy (omit=None) | Low, backward compat | `test_creator_a_does_not_receive_creator_b_event` |

## Step 2 -- Server-side WebSocket creator isolation
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `chatbotv2/dashboard/ws_manager.py:12` | `ManagedConnection` | Add `creator_ids: set[int]` field | Store per-connection creator scope | Low | `test_creator_isolation_ws` |
| `chatbotv2/dashboard/ws_manager.py:28` | `connect` | Add `creator_ids` param | Capture creator at connect | Low | Same |
| `chatbotv2/dashboard/ws_manager.py:45` | `broadcast` | Add `creator_id` param, filter `if creator_id not in conn.creator_ids: continue` before dialog/global logic | Server-side isolation, not frontend | Low, legacy with empty set allowed | Same |
| `chatbotv2/dashboard/event_subscriber.py:60` | `start_event_subscriber` | Extract `creator_id=event.get("creator_id")` and pass to `broadcast` | Forward scoping | Low | Same |
| `chatbotv2/dashboard/routes/ws.py:32` | `websocket_endpoint` | Resolve `creator_id` via `resolve_single_application_creator` and `connect(..., creator_ids={cid})` | Bind dashboard to creator | Low, single-creator deployment | Same |

## Step 3 -- Fan/debounce creator isolation
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `db/redis.py:303` | `_debounce_key` + `debounce_enqueue`/`get_debounced_messages` | Add `_debounce_key(user_id, creator_id)` -> `debounce:creator:{cid}:user:{uid}` with fallback to legacy `debounce:{uid}` | Prevent cross-creator debounce collision for same Telegram user | Low, fallback preserves legacy | `test_debounce_keys_creator_scoped` |
| `chatbotv2/handlers.py:68` | `handle_incoming_message` | Compute `generation_id` at intake, resolve `_creator_id`, publish `message.created` with `creator_id`, `debounce_enqueue` with `creator_id`, `create_task(_wait_and_process(..., creator_id))` | Preserve debounce isolation and creator scoping | Low | `test_generation_id_not_creator_specific_but_debounce_is` |

## Step 4 -- Fix existing event producers
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `workers/llm_worker.py:625` etc | `publish_event` for `ai.generation_*`/`suggestion.created` | Add `creator_id=_creator_id` to 8 publish calls | Creator scoping for generation lifecycle | Low | `test_phase42_correlation` still passes |
| `chatbotv2/main.py:99` | `_process_send_stream` | Extract `creator_id` from `data.get("creator_id")`, pass to `publish_event` for `message.sent`/`vault.media_sent`/`send_failed`, preserve on requeue | Creator scoping for send | Low | `test_message_sent_correlation` |
| `db/redis.py:65` | `enqueue_send` | Add `creator_id` param and propagate `data["creator_id"]` | Preserve creator through send stream | Low | `test_send_stream_preserves_id` |
| `chatbotv2/dashboard/routes/notes.py`, `tags.py`, `attention.py`, `bulk_ops.py`, `queue.py` | publish for `note/tag/attention/assigned` | Change `scope="global"` to `scope="user", dialog_id=user_id, creator_id=_creator_id` after resolving single creator | Fix P0 global broadcast | Low | `test_creator_isolation_ws` (notes) |

## Step 5 -- Commerce events (deterministic, best-effort, never blocks)
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `commerce/execution.py:298` | `execute_ppv` | After `record_offer_transition` success, `try: publish_event("commerce.offer_created", {offer_id, product_id, price_minor, currency}, creator_id, user_id, scope="user")` | Make PPV visible realtime | Low, best-effort | `test_offer_created_emitted_after_success` |
| `db/dropfans.py:217` | `record_dropfans_sale` | After `INSERT ON CONFLICT DO NOTHING` where inserted, `publish_event("commerce.sale_recorded", {external_transaction_id, product_id, amount_cents, currency, attribution_status}, creator_id, user_id, scope)` -- no buyer_email | Revenue truth realtime | Low, idempotent via transaction_id | `test_browser_payload_has_no_buyer_email` |
| `commerce/dao.py` | `attribute_purchase_from_webhook` | After `logger.info` attribution, `publish_event("commerce.attribution", {transaction_id, offer_id, product_id, tier, status}, creator_id, user_id)` | Attribution visibility | Low | Static check |
| `commerce/dao.py` + `commerce/post_purchase.py` | `mark_aftercare_pending` / `handle_post_purchase` | After `UPDATE ... RETURNING id` where not None, `publish_event("commerce.aftercare", {user_id, offer_id, state}, creator_id, user_id)` | Aftercare visibility | Low | Static |
| `commerce/revenue_intelligence.py:224` | `record_funnel_transition` | After `await update_user_profile` and `persisted=True`, `publish_event("commerce.funnel_changed", {from_stage, to_stage, reason}, creator_id, user_id, generation_id)` | Funnel visibility | Low | Static |

## Step 6 -- Stage derivation
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `core/execution_stage.py` (NEW) | `ExecutionStage` enum + `derive_stage`/`derive_from_generation` | Deterministic resolver from existing evidence (message_created, generation_started/completed, send_queued, sent, failed, telemetry fields) without new DB table | Truthful stage window, not animation | Low, pure function | `test_stage_derivation_basic` |

## Step 7 -- Creator-scoped bridge queries
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `chatbotv2/dashboard/routes/live.py` (NEW) | `GET /api/live/fans`, `GET /api/live/overview`, `GET /api/generation/{id}/stage` | Creator-scoped via `resolve_single_application_creator` and `WHERE creator_id=$1` joins to `generation_telemetry`/`commerce_offers`/`fangate_transactions` | Live counts truthfully via persisted, not guess | Low, read-only, indexes exist | Manual via `test_live_fans_query_uses_index` (planned) |
| `chatbotv2/dashboard/app.py` | `include_router(live_router)` | Register new routes | Expose bridge | Low | Existing `test_database_migrations` still pass |

## Step 8-10 -- UI / Notifications / Polling
| File | Function | Change | Reason | Risk | Test |
|---|---|---|---|---|---|
| `chatbotv2/dashboard/static/js/notifications.js` (NEW) | `showToast` + `initNotifications` | Creator-scoped toasts for `commerce.offer_created`, `sale_recorded`, `aftercare`, `funnel_changed`, `message.sent` (auto-approved only), `send_failed` via `event_id` dedup | Reuse existing `realtime.js` DEDUP/reconnect | Low, no secrets | Manual |
| `chatbotv2/dashboard/templates/dashboard.html` | Include `notifications.js`, live panel `fetch /api/live/overview` 15s if `isPollingActive`, `rt.on commerce.*` reload | Live operation panel (active, generating, offers, sales, revenue, aftercare, failures) | Minimal | Low | Manual |
| `chatbotv2/dashboard/templates/chat.html` | Add `execution-stage-bar` HTML+CSS + JS `setStage` on `message.created`/`ai.generation_started`/`completed`/`message.sent`/`failed`/`commerce.*` | Execution timeline `RECEIVED->SENT` observability | Low, no new authority | Manual |
| `chatbotv2/dashboard/templates/queue.html` | Add `setInterval` 15s `fetch /api/queue/pending` if `isPollingActive` | Polling fallback where missing | Low | Manual |

## Migration
| File | Change | Reason | Risk |
|---|---|---|---|
| `db/migrations/20260831000000_generation_id_text.sql` (42B) | `ALTER generation_telemetry.generation_id TYPE TEXT` | Already done in 42B to allow md5; no new migration in 42C (MIGRATIONS: NONE as preferred, but 42B migration counts) | Low |

## Tests
- `tests/test_phase42c_observability.py` (NEW, 16 tests) covers P0 isolation, fan, generation, commerce, privacy, stage, reconnect, polling
- Updated `tests/test_phase1_regression.py` (deterministic) and `tests/test_dlq_recovery.py` (creator_id)

## Risks
- All `publish_event` are best-effort `try/except` never blocks commerce
- Old clients ignore unknown `event_type` per `realtime.js:76` handler lookup
- `enable_websocket` kill-switch still controls all
- No new worker/queue/LLM, no DropFans authority change, no sale-lost inference
