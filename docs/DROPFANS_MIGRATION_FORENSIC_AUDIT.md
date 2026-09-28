# DROPFANS MIGRATION — FORENSIC CODE AUDIT

**Date:** 2026-08-25
**Auditor:** opencode (automated forensic analysis)
**Scope:** Complete DropFans migration verification — Fangate-only enforcement

---

## Executive Verdict

**FAIL**

The migration is **NOT production-ready**. The codebase has **4 active Fangate runtime fallback paths** in the autonomous commerce pipeline, **24 live Fangate HTTP dashboard routes**, **zero dedicated DropFans tests**, and **5 skipped tests with gutted bodies**. The `fangate_products` table is the sole product table used by both providers, making Fangate structurally inseparable from the product layer. The DB migration has not been applied.

---

## Provider Architecture

### Actual Runtime Provider Flow

```
INBOUND (Telegram → commerce)
═══════════════════════════════════════════════════════════════════
Telegram inbound
→ chatbotv2/handlers.py
→ debounce_enqueue()
→ Redis stream
→ workers/llm_worker.py:process_message()
→ _try_commerce_draft()
→ commerce/single_creator.py:resolve_single_application_creator()
  ├─ TRY: db.dropfans.get_any_creator_id_with_dropfans()    [DROPFANS]
  └─ EXCEPT/NULL: db.fangate.list_active_creator_ids()        [FANGATE FALLBACK]
→ commerce/product_selection.py:resolve_commerce_product()
  └─ db.fangate.list_fangate_products()                       [DB-ONLY TABLE READ]
→ commerce/state.py:resolve_commerce_state()
  ├─ TRY: db.dropfans.get_dropfans_integration()             [DROPFANS]
  ├─ EXCEPT/NULL: db.fangate.get_creator_integration()       [FANGATE FALLBACK]
  └─ db.fangate.get_fangate_product()                         [DB-ONLY TABLE READ]
→ commerce/execution.py:execute_ppv()
  ├─ TRY: db.dropfans.get_dropfans_integration()             [DROPFANS]
  ├─ EXCEPT: db.fangate.get_creator_integration()             [FANGATE FALLBACK]
  ├─ SELECT FROM fangate_products WHERE...                    [DB-ONLY TABLE READ]
  └─ integrations/dropfans/service.py:build_checkout_url()   [DROPFANS]

FULFILLMENT (webhook → attribution → delivery)
═══════════════════════════════════════════════════════════════════
POST /api/fangate/webhooks/{creator_id}                       [FANGATE HTTP]
→ integrations/fangate/service.py:receive_webhook()
  ├─ db.fangate.get_creator_integration()                     [DB-ONLY]
  ├─ fangate.security.decrypt_secret()                        [FANGATE CRYPTO]
  ├─ fangate.client.verify_webhook_signature()                [FANGATE HTTP]
  └─ db.fangate.upsert_fangate_transaction()                  [DB-ONLY]
→ commerce/dao.py:attribute_purchase_from_webhook()           [DB-ONLY]
→ commerce/post_purchase.py:handle_post_purchase()
  ├─ db.fangate.get_fangate_product()                         [DB-ONLY]
  └─ [product_type=="dropfans"] → TEXT MESSAGE PATH            [TEXT DELIVERY]

DASHBOARD (operator UI)
═══════════════════════════════════════════════════════════════════
Dashboard UI → /api/fangate/creators/{id}/...
→ integrations/fangate/service.py                              [FANGATE HTTP]
→ integrations/fangate/client.py → Fangate API                 [FANGATE HTTP]

SCHEDULER (reconciliation)
═══════════════════════════════════════════════════════════════════
scheduler_worker.py:reconcile_purchases()
→ commerce/reconciliation.py:reconcile_all()
  ├─ reconcile_dropfans_sales()                                [DROPFANS HTTP]
  └─ reconcile_unattributed_purchases()
      └─ db.fangate.get_fangate_product()                      [DB-ONLY]
```

---

## Fangate Runtime References

### Active Production Path References

| # | File | Line(s) | Symbol | Classification | Reachable From | Action |
|---|------|---------|--------|----------------|----------------|--------|
| 1 | `commerce/execution.py` | 126-140 | `fdb.get_creator_integration()` | **ACTIVE FALLBACK** | Telegram → commerce → execution | **REMOVE** |
| 2 | `commerce/state.py` | 190-202 | `db_fangate.get_creator_integration()` | **ACTIVE FALLBACK** | Telegram → commerce → state | **REMOVE** |
| 3 | `commerce/single_creator.py` | 67-76 | `db_fangate.list_active_creator_ids()` | **ACTIVE FALLBACK** | Telegram → commerce → creator resolution | **REMOVE** |
| 4 | `memory/context_assembler.py` | 347-362 | `get_creator_integration()` | **ACTIVE FALLBACK** | Telegram → context assembly → LLM | **REMOVE** |
| 5 | `chatbotv2/dashboard/routes/fangate.py` | 145-760 | 24 Fangate HTTP routes | **ACTIVE DASHBOARD** | Dashboard UI | **DISABLE/REMOVE** |
| 6 | `chatbotv2/dashboard/routes/fangate.py` | 1015 | Webhook receiver | **ACTIVE HTTP** | External webhook calls | **DISABLE** |
| 7 | `chatbotv2/dashboard/routes/vault.py` | 20,35,76,96,117,137,156,181,199,233,251,306,322,339,413 | `FangateError`, `fdb` | **ACTIVE DASHBOARD** | Dashboard UI | **DISABLE** |
| 8 | `chatbotv2/dashboard/routes/pages.py` | 9,189,217,261 | `fdb`, `fangate_media_id` | **ACTIVE DASHBOARD** | Dashboard UI | **DISABLE** |
| 9 | `chatbotv2/dashboard/app.py` | 134-138,169-170 | `fangate_router`, `register_fangate_exception_handlers` | **ACTIVE APP INIT** | App startup | **DISABLE** |
| 10 | `chatbotv2/dashboard/schemas.py` | 97,101,116,132,136 | `FANGATE_MIN_PRICE_MINOR` | **ACTIVE VALIDATION** | Dashboard API | **RENAME** |
| 11 | `chatbotv2/dashboard/routes/followups.py` | 14 | `_require_creator` from fangate routes | **ACTIVE IMPORT** | Followups routes | **REFACTOR** |
| 12 | `integrations/fangate/service.py` | 703-907 | `receive_webhook()` | **ACTIVE SERVICE** | Dashboard webhook receiver | **DISABLE** |
| 13 | `integrations/fangate/client.py` | 79-547 | `FangateClient` | **ACTIVE CLIENT** | Fangate service | **DISABLE** |
| 14 | `integrations/fangate/security.py` | 42-78 | `CredentialVault` | **ACTIVE CRYPTO** | Fangate service | **DISABLE** |
| 15 | `core/config.py` | 47-49 | `fangate_api_base_url`, `fangate_api_timeout`, `fangate_enc_key` | **ACTIVE CONFIG** | Fangate client, security | **REMOVE** |
| 16 | `core/health.py` | 84,105-138 | `check_fangate()`, `fdb.list_active_creator_ids()` | **SEMI-DEAD** | Health endpoint (check_fangate not called) | **REMOVE** |
| 17 | `vault/service.py` | 13,39-40,83-84,96,130-189,222,273,315 | `fdb.list_fangate_products()`, `fangate_media_id` | **ACTIVE VAULT** | Vault operations | **RENAME/REFACTOR** |
| 18 | `vault/models.py` | 75,114,125 | `FangateProduct`, `fangate_media_id` | **ACTIVE MODELS** | Vault delivery | **RENAME** |
| 19 | `vault/aggregate.py` | 22-26 | `fangate_products` references | **ACTIVE AGGREGATE** | Vault media listing | **RENAME** |
| 20 | `chatbotv2/main.py` | 171-298 | `fangate_media_id` in send worker | **ACTIVE DELIVERY** | Send worker | **RENAME** |
| 21 | `core/llm_tools.py` | 426,480,527,735 | `LEFT JOIN fangate_products`, `get_fangate_product` | **ACTIVE LLM** | LLM tool calls | **RENAME** |
| 22 | `memory/context_assembler.py` | 195,246,313,343,356 | `LEFT JOIN fangate_products`, `get_fangate_product`, `get_creator` | **ACTIVE CONTEXT** | LLM context | **RENAME** |
| 23 | `commerce/product_selection.py` | 47,111,178,253,265 | `db_fangate.list_fangate_products()`, `get_creator_integration()` | **ACTIVE SELECTION** | Product resolution | **RENAME** |
| 24 | `commerce/post_purchase.py` | 278-537 | `db.fangate.get_fangate_product()`, `fangate_media_id` | **ACTIVE FULFILLMENT** | Post-purchase | **RENAME** |
| 25 | `commerce/reconciliation.py` | 39,58,186,258 | `db.fangate` imports | **ACTIVE RECONCILIATION** | Scheduler | **RENAME** |
| 26 | `commerce/dao.py` | 336,394,512,569 | `fangate_transactions` queries | **ACTIVE DAO** | Attribution | **RENAME** |
| 27 | `db/dropfans.py` | 79-260 | `INSERT INTO fangate_products`, `SELECT FROM fangate_products` | **ACTIVE DB OPS** | Dropfans service | **RENAME TABLE** |
| 28 | `db/fangate.py` | entire module | 30+ functions | **ACTIVE DB LAYER** | Commerce, vault, dashboard | **RENAME** |
| 29 | `db/vault.py` | 20-474 | `fangate_media_id` parameter | **ACTIVE DELIVERY** | Vault delivery | **RENAME** |
| 30 | `db/postgres.py` | 327-356 | `fangate_media_id` column | **ACTIVE MESSAGING** | Message persistence | **RENAME** |

### Dead / Orphaned References

| # | File | Symbol | Classification | Action |
|---|------|--------|----------------|--------|
| 1 | `core/health.py:105-138` | `check_fangate()` | DEAD — not called from production | **REMOVE** |
| 2 | `commerce/execution.py:68` | `ExecutionStatus.FANGATE_ERROR` alias | DEAD — only in tests/comments | **REMOVE** |
| 3 | `integrations/fangate/contract.py` | Entire 749-line module | DEAD — script-only | **REMOVE** |
| 4 | `integrations/fangate/models.py` | 4 `Dashboard*` classes | DEAD — never imported | **REMOVE** |
| 5 | `db/fangate.py` | `update_integration_currency` | DEAD — never called | **REMOVE** |
| 6 | `db/fangate.py` | `list_integrated_creators` | DEAD — never called | **REMOVE** |
| 7 | `db/fangate.py` | `get_any_creator_id_with_integration` | DEAD — only in pages.py | **REMOVE** |
| 8 | `integrations/dropfans/security.py:99` | `_sanitize()` | DEAD — no-op stub | **REMOVE** |
| 9 | `integrations/dropfans/client.py:190` | `_request_multipart()` | DEAD — never called | **REMOVE** |
| 10 | `integrations/dropfans/client.py:234-240` | `get_timezone()`/`set_timezone()` | DEAD — never called | **REMOVE** |
| 11 | `integrations/dropfans/client.py:302-308` | `move_vault_item()`/`update_vault_tags()` | DEAD — no service wrapper | **REMOVE** |
| 12 | `integrations/dropfans/service.py:292` | `get_drop()` wrapper | DEAD — never called externally | **REMOVE** |
| 13 | `chatbotv2/dashboard/routes/fangate.py:878-957` | 4 phantom stubs | WOULD CRASH — undefined functions | **REMOVE** |
| 14 | `integrations/__init__.py:1` | `"""Fangate et al."""` | COSMETIC docstring | **UPDATE** |
| 15 | `commerce/__init__.py:3,5` | Fangate docstrings | COSMETIC | **UPDATE** |

---

## Dropfans Runtime Paths

### Verified Active Dropfans Paths

| # | File | Function | Dropfans Operation | Status |
|---|------|----------|-------------------|--------|
| 1 | `integrations/dropfans/client.py` | `DropfansClient._request()` | HTTP Bearer auth, all API calls | **PASS** |
| 2 | `integrations/dropfans/client.py` | `check_drop_status()` | Batch POST `/drops/check-status` (200 IDs) | **PASS** |
| 3 | `integrations/dropfans/client.py` | `get_balance()` | GET `/balance` | **PASS** |
| 4 | `integrations/dropfans/client.py` | `list_vault()` | GET `/vault` with pagination | **PASS** |
| 5 | `integrations/dropfans/client.py` | `create_drop()` | POST `/drops` | **PASS** |
| 6 | `integrations/dropfans/client.py` | `get_earnings()` | GET `/earnings` | **PASS** |
| 7 | `integrations/dropfans/service.py` | `connect_creator()` | Validate → encrypt → store | **PASS** |
| 8 | `integrations/dropfans/service.py` | `build_checkout_url()` | Get links → extract telegram template | **PASS** |
| 9 | `integrations/dropfans/service.py` | `create_drop()` | Build checkout URL → create drop | **PASS** |
| 10 | `integrations/dropfans/service.py` | `reconcile_sales()` | Poll → check recorded → record | **PASS** |
| 11 | `integrations/dropfans/service.py` | `get_earnings_summary()` | Get earnings → aggregate by type | **PASS** |
| 12 | `integrations/dropfans/service.py` | `get_balance_info()` | Get balance → format | **PASS** |
| 13 | `db/dropfans.py` | `upsert_dropfans_product()` | INSERT INTO fangate_products | **PASS** |
| 14 | `db/dropfans.py` | `record_dropfans_sale()` | INSERT INTO fangate_transactions | **PASS** |
| 15 | `db/dropfans.py` | `has_dropfans_sale_been_recorded()` | SELECT COUNT from fangate_transactions | **PASS** |
| 16 | `db/dropfans.py` | `get_dropfans_integration()` | SELECT from creator_integrations | **PASS** |
| 17 | `db/dropfans.py` | `find_dropfans_product()` | SELECT from fangate_products WHERE raw->>'dropfans_product_id' | **PASS** |
| 18 | `commerce/execution.py:258-265` | `dservice.build_checkout_url()` | Dropfans checkout URL build | **PASS** |
| 19 | `commerce/reconciliation.py` | `reconcile_dropfans_sales()` | Calls dservice.reconcile_sales() | **PASS** |
| 20 | `chatbotv2/dashboard/routes/fangate.py:192-333` | Dropfans integration routes | Connect, status, vault, earnings | **PASS** |

---

## Suspicious / Hallucinated Code

| # | File | Code | Problem | Severity | Recommendation |
|---|------|------|---------|----------|----------------|
| 1 | `chatbotv2/dashboard/routes/fangate.py:943-957` | `get_product_analytics()` returns hardcoded zeros | **FAKE ANALYTICS** — returns `{"views": 0, "clicks": 0, ...}` | **HIGH** | Implement real analytics or remove |
| 2 | `chatbotv2/dashboard/routes/fangate.py:878-957` | 4 stub functions calling undefined services | **PHANTOM ROUTES** — `generate_blur_preview`, `trigger_epoch`, `test_webhook`, `get_product_analytics` would crash at runtime | **MEDIUM** | Remove stubs |
| 3 | `integrations/dropfans/security.py:99-101` | `_sanitize()` is a no-op stub | **DEAD CODE** — never imported, never called | **LOW** | Remove |
| 4 | `integrations/dropfans/client.py:190-221` | `_request_multipart()` defined but never called | **DEAD CODE** — no caller | **LOW** | Remove |
| 5 | `integrations/dropfans/client.py:234-240` | `get_timezone()`/`set_timezone()` defined but never called | **DEAD CODE** — no caller | **LOW** | Remove |
| 6 | `integrations/dropfans/client.py:302-308` | `move_vault_item()`/`update_vault_tags()` defined but no service wrapper | **DEAD CODE** — no external caller | **LOW** | Remove or implement service wrappers |
| 7 | `integrations/dropfans/service.py:292` | `get_drop()` wrapper never called externally | **DEAD CODE** — no external caller | **LOW** | Remove or expose |
| 8 | `integrations/dropfans/service.py:337` | Hardcoded `https://www.dropfans.io/buy/{drop_id}` | **SHOULD USE CONFIG** — does not derive from `settings.dropfans_api_base_url` | **LOW** | Use `settings.dropfans_api_base_url` |
| 9 | `commerce/execution.py:68` | `ExecutionStatus.FANGATE_ERROR = ExecutionStatus.PROVIDER_ERROR` | **DEAD ALIAS** — backward compat alias used only in tests | **LOW** | Remove after tests updated |
| 10 | `tests/test_commerce_execution.py:37` | `_LOCAL_PRODUCT` fixture | **UNUSED FIXTURE** — `_LOCAL_PRODUCT_ROW` used instead | **LOW** | Remove |

---

## Exception Handling Audit

| # | File | Lines | Operation | Logged? | Fallback Behavior | Justified? | Risk |
|---|------|-------|-----------|---------|-------------------|------------|------|
| 1 | `execution.py` | 126-129 | Dropfans integration lookup | **NO** | Falls to Fangate fallback | **NO — violates Dropfans-only** | **HIGH** — DB error invisible |
| 2 | `state.py` | 192-195 | Dropfans integration lookup | **NO** | Falls to Fangate fallback | **NO — violates Dropfans-only** | **HIGH** — DB error invisible |
| 3 | `single_creator.py` | 69-74 | Dropfans creator lookup | **NO** | Falls to Fangate fallback | **NO — violates Dropfans-only** | **HIGH** — DB error invisible |
| 4 | `context_assembler.py` | 347-362 | Dropfans integration check | **YES** (inner) | Falls to Fangate fallback | **NO — violates Dropfans-only** | **MEDIUM** |
| 5 | `execution.py` | 143-145 | Credential decryption | **YES** | Returns CREATOR_NOT_READY | **YES** | OK |
| 6 | `execution.py` | 182-196 | Product lookup DB error | **YES** | Returns PRODUCT_UNAVAILABLE | **YES** | OK |
| 7 | `execution.py` | 258-265 | Checkout URL build failure | **YES** | Returns PROVIDER_ERROR | **YES** | OK |
| 8 | `execution.py` | 293-305 | Offer persistence failure | **YES** | Recovers or PERSISTENCE_FAILED | **YES** | OK |
| 9 | `execution.py` | 314-325 | Rollup failure | **YES** | Warning logged, offer stands | **YES** | OK |
| 10 | `post_purchase.py` | 380-387 | Delivery reservation (Dropfans) | **NO** | Silent return — buyer pays, gets nothing | **NO — revenue failure** | **CRITICAL** |
| 11 | `post_purchase.py` | 425-430 | release_delivery failure | **NO** | Reservation stuck pending | **NO** | **MEDIUM** |
| 12 | `reconciliation.py` | 230-238 | Post-purchase fulfillment | **YES** | Attributed but not fulfilled | **YES** but no retry | **HIGH** — no retry |
| 13 | `post_purchase.py` | 206-218 | Media delivery exception | **YES** | Buyer gets nothing | **YES** but no retry | **HIGH** — no retry |
| 14 | `post_purchase.py` | 331-336 | JSON parse of raw_media | **NO** | Empty media list, no delivery | **NO** | **MEDIUM** |
| 15 | `fangate.py:210-215` | Dropfans dashboard exception | **YES** (logger.error) | Returns 500 with str(exc) | **NO** — leaks internal errors | **MEDIUM** |

---

## Test Integrity

### Old Coverage (Pre-Migration)

| Category | Tests | Status |
|----------|-------|--------|
| Fangate remote verification | 8 tests (3 parametrized × N cases) | **SKIPPED** |
| Fangate age verification | 1 test | **SKIPPED** |
| Fangate product/price/age cross-check | 4 tests | **SKIPPED** |
| Fangate persistence ordering | 1 test | **SKIPPED** |
| Fangate webhook processing | ~50 tests | **ACTIVE** (still testing Fangate service) |
| Fangate client | ~30 tests | **ACTIVE** |
| Fangate security | ~10 tests | **ACTIVE** |
| Fangate dashboard | ~20 tests | **ACTIVE** |
| Commerce execution (non-Fangate) | ~25 tests | **ACTIVE** |
| Commerce state | ~15 tests | **ACTIVE** |

### New Coverage (Post-Migration)

| Category | Tests | Status |
|----------|-------|--------|
| Dropfans product resolution | 0 | **MISSING** |
| Dropfans API interaction | 0 | **MISSING** |
| Dropfans data transformation | 0 | **MISSING** |
| Dropfans webhook handling | N/A (no webhooks) | N/A |
| Dropfans error→status mapping | 0 | **MISSING** |
| Negative: Fangate NOT used | 0 | **MISSING** |
| Negative: FangateClient NOT instantiated | 0 | **MISSING** |

### Coverage Lost

| # | Behavior Originally Proven | Test | Impact |
|---|---------------------------|------|--------|
| 1 | `is_verif_age=True` causes denial | `test_verif_age_denied` | **HIGH** — age verification bypassed |
| 2 | Remote verification error→status mapping | `TestRemoteVerificationAuthority` | **MEDIUM** — Dropfans error mapping untested |
| 3 | Remote verification never persists on failure | `TestRemoteVerificationAuthority` | **MEDIUM** — persistence safety untested |
| 4 | Link mismatch denies execution | `test_link_mismatch_refuses` | **LOW** — no remote verification anymore |
| 5 | Price mismatch denies execution | `test_price_mismatch_refuses` | **LOW** — no remote verification anymore |
| 6 | Age flag mismatch denies execution | `test_age_flag_mismatch_refuses` | **LOW** — no remote verification anymore |
| 7 | Missing remote link denies execution | `test_missing_remote_link_refuses` | **LOW** — no remote verification anymore |
| 8 | Persistence ordering (validate→verify→insert) | `test_persistence_order_validate_then_verify_then_insert` | **MEDIUM** — URL build before INSERT untested |

### Coverage Missing

| # | Missing Coverage | Severity |
|---|-----------------|----------|
| 1 | No test verifies Dropfans product resolution in `execute_ppv` | **HIGH** |
| 2 | No test verifies Fangate-only creator is REJECTED | **HIGH** |
| 3 | No test verifies `FangateClient` is NOT instantiated during execution | **MEDIUM** |
| 4 | No test verifies `verify_product` is NOT called in execution | **MEDIUM** |
| 5 | No test for Dropfans error→status mapping | **MEDIUM** |
| 6 | No test for Dropfans checkout URL build failure | **MEDIUM** |
| 7 | No test for Dropfans credential decryption failure | **MEDIUM** |
| 8 | No test for multiple sales per product | **MEDIUM** |

### Skipped Tests Detail

| Test | Body Status | Can Un-skip? | Equivalent Dropfans Test? |
|------|-------------|-------------|--------------------------|
| `test_verif_age_denied` | **EMPTY (`pass`)** | NO — must rewrite | NO |
| `TestRemoteVerificationAuthority` (3 tests) | **INTACT** | NO — tests Fangate-specific errors | NO |
| `test_link_mismatch_refuses` | **EMPTY (`pass`)** | NO — must rewrite | NO |
| `test_price_mismatch_refuses` | **EMPTY (`pass`)** | NO — must rewrite | NO |
| `test_age_flag_mismatch_refuses` | **EMPTY (`pass`)** | NO — must rewrite | NO |
| `test_missing_remote_link_refuses` | **EMPTY (`pass`)** | NO — must rewrite | NO |
| `test_persistence_order_validate_then_verify_then_insert` | **EMPTY (`pass`)** | NO — must rewrite | NO |

---

## Dead Code

| # | File | Symbol | Lines | Status |
|---|------|--------|-------|--------|
| 1 | `core/health.py:105-138` | `check_fangate()` | 34 | DEAD — not called from production |
| 2 | `commerce/execution.py:68` | `ExecutionStatus.FANGATE_ERROR` | 1 | DEAD — backward compat alias |
| 3 | `integrations/fangate/contract.py` | Entire module | 749 | DEAD — script-only |
| 4 | `integrations/fangate/models.py` | 4 `Dashboard*` classes | ~155 | DEAD — never imported |
| 5 | `db/fangate.py` | `update_integration_currency` | ~15 | DEAD — never called |
| 6 | `db/fangate.py` | `list_integrated_creators` | ~10 | DEAD — never called |
| 7 | `db/fangate.py` | `get_any_creator_id_with_integration` | ~10 | DEAD — only in pages.py |
| 8 | `integrations/dropfans/security.py:99` | `_sanitize()` | 5 | DEAD — no-op stub |
| 9 | `integrations/dropfans/client.py:190` | `_request_multipart()` | 20 | DEAD — never called |
| 10 | `integrations/dropfans/client.py:234-240` | `get_timezone()`/`set_timezone()` | 15 | DEAD — never called |
| 11 | `integrations/dropfans/client.py:302-308` | `move_vault_item()`/`update_vault_tags()` | 15 | DEAD — no service wrapper |
| 12 | `integrations/dropfans/service.py:292` | `get_drop()` wrapper | ~15 | DEAD — never called |
| 13 | `chatbotv2/dashboard/routes/fangate.py:878-957` | 4 phantom stubs | ~80 | WOULD CRASH |
| 14 | `tests/test_commerce_execution.py:37` | `_LOCAL_PRODUCT` fixture | 8 | UNUSED |
| **Total estimated dead code** | | | **~1,100 lines** | |

---

## Database Findings

### Table Classification

| Category | Tables | Used By |
|----------|--------|---------|
| **Fangate-specific** | `fangate_wallet_entries`, `fangate_webhook_events` | Fangate webhook processing only |
| **Shared (provider-neutral)** | `fangate_products`, `fangate_transactions`, `creator_integrations`, `commerce_offers`, `ppv_eligibility_decisions`, `ppv_analytics_daily`, `vault_media_deliveries` | Both Fangate AND Dropfans |
| **Core CRM** | `creators`, `users`, `messages`, `sessions`, `personas`, `operators` | System-wide |

### Critical Table Issue

`fangate_products` is the **sole product mirror table**. Dropfans writes into it via `db/dropfans.py:upsert_dropfans_product()` with `product_type = 'dropfans'` and `raw` JSONB containing `dropfans_product_id`. The table has NOT been renamed or migrated to a provider-neutral name.

### Migration Status

| Migration | Status | Content |
|-----------|--------|---------|
| `20260819000000_fangate_commerce.sql` | APPLIED | Creates `fangate_products`, `fangate_transactions` |
| `20260819010000_fangate_ppv_commerce.sql` | APPLIED | ALTERs `fangate_transactions` |
| `20260822000000_currency_propagation.sql` | APPLIED | Currency on `creator_integrations` |
| `20260823010000_vault_media.sql` | APPLIED | `fangate_media_id` columns |
| `20260825000000_dropfans_provider.sql` | **NOT APPLIED** | Dropfans columns on `creator_integrations`, `commerce_offers`, `ppv_eligibility_decisions`, `ppv_analytics_daily`, `vault_media_deliveries` |

### Synthetic Hash ID Risk

`db/dropfans.py:101`: `synthetic_id = abs(hash(dropfans_product_id)) % (2**62)` — Python `hash()` is non-deterministic across processes (randomized seed since Python 3.3). Two different Dropfans CUIDs could theoretically produce the same synthetic ID, causing silent product overwrite via `INSERT ... ON CONFLICT`.

---

## Configuration Findings

### Environment Variables

| Variable | Default | Used By | Status |
|----------|---------|---------|--------|
| `FANGATE_API_BASE_URL` | `https://fangate.info/api` | `integrations/fangate/client.py` | **ACTIVE** — Fangate client |
| `FANGATE_API_TIMEOUT` | `15.0` | `integrations/fangate/client.py` | **ACTIVE** — Fangate client |
| `FANGATE_ENC_KEY` | `None` | `integrations/fangate/security.py` | **ACTIVE** — Fangate + Dropfans fallback |
| `DROPFANS_API_BASE_URL` | `https://www.dropfans.io` | `integrations/dropfans/client.py` | **ACTIVE** |
| `DROPFANS_API_TIMEOUT` | `30.0` | `integrations/dropfans/client.py` | **ACTIVE** |
| `DROPFANS_ENC_KEY` | `None` | `integrations/dropfans/security.py` | **ACTIVE** — falls back to `FANGATE_ENC_KEY` |

### Missing .env.example Entries

`DROPFANS_API_BASE_URL`, `DROPFANS_API_TIMEOUT`, `DROPFANS_ENC_KEY` are NOT documented in `.env.example`. A new developer would not discover these variables.

### Provider Selection

No explicit provider selection flag exists. The code relies on implicit "try Dropfans first, fallback to Fangate" patterns. This violates the Dropfans-only requirement.

---

## Creator Isolation

| Subsystem | File | Pass/Fail | Detail |
|-----------|------|-----------|--------|
| Products | `db/dropfans.py` | PASS | All queries include `creator_id` WHERE clause |
| Offers | `commerce/dao.py` | PASS | All queries include `creator_id` |
| Transactions | `db/dropfans.py` | PASS | All queries include `creator_id` |
| Webhooks | N/A | N/A | No Dropfans webhooks |
| Vault | `vault/service.py` | PASS | All queries include `creator_id` |
| Analytics | `vault/service.py` | PASS | All queries include `creator_id` |
| Attribution | `commerce/dao.py` | PASS | All queries include `creator_id` |
| Delivery | `db/vault.py` | PASS | All queries include `creator_id` |

**Browser input cannot select another creator** — all routes use `auth.creator_id` from session, not URL parameter (except for admin routes which require `require_auth(require_admin=True)`).

---

## Autonomy Safety

| Authority Boundary | File | Pass/Fail | Detail |
|-------------------|------|-----------|--------|
| LLM cannot create payment offers | `commerce/execution.py` | PASS | `decision.allowed` must be True |
| Product IDs cannot be invented | `commerce/execution.py:182-196` | PASS | Product must exist in `fangate_products` |
| Provider operations deterministic | `commerce/execution.py` | PASS | All checks re-evaluated before each side effect |
| Price authority server-side | `commerce/execution.py:267` | PASS | `price_minor` from product row, not input |
| Creator isolation server-side | `commerce/execution.py` | PASS | `creator_id` from `single_creator` resolution |
| Duplicate offer prevention | `commerce/execution.py:220-231` | PASS | `find_pending_offer_for_product` + `has_purchased_product` |
| Delivery idempotency | `db/vault.py` | PASS | `ON CONFLICT DO NOTHING` on delivery reservation |
| Advisory locks | `commerce/dao.py` | PASS | `pg_advisory_xact_lock` on serialized reservation |

---

## Unrelated Regression Audit

| # | File | Change | Required for Migration? | Assessment |
|---|------|--------|------------------------|------------|
| 1 | `commerce/execution.py` | `enforce_age_verification=False` hardcoded | **QUESTIONABLE** — age verification bypassed for all products | **UNRELATED** — age verification should work with Dropfans |
| 2 | `commerce/execution.py` | `price_minor=None` removed from consistency check | **QUESTIONABLE** — PWYW semantics unclear | **UNRELATED** — was not broken |
| 3 | `vault/service.py` | `get_earnings` structure changed | **YES** — Dropfans earnings API differs | RELATED |
| 4 | `commerce/post_purchase.py` | `_MEDIA_TYPE_MAP` added | **YES** — Dropfans delivery path needs type mapping | RELATED |
| 5 | `core/health.py` | `_dropfans_cache` replaces `_fangate_cache` | **YES** — Dropfans health check | RELATED |
| 6 | `memory/context_assembler.py` | `currency = "USD"` hardcoded | **YES** — Dropfans uses USD | RELATED |
| 7 | `tests/test_commerce_execution.py` | 18 tests skipped | **YES** — Fangate verification removed | RELATED |
| 8 | `tests/test_commerce_execution.py` | `_patch_ppv` updated for pool mocking | **YES** — execution.py changed | RELATED |
| 9 | `tests/test_commerce_state.py` | `get_dropfans_integration` added to allowed | **YES** — state.py changed | RELATED |
| 10 | `tests/test_llm_tools.py` | `db.dropfans` mock added | **YES** — llm_tools.py changed | RELATED |

---

## Required Fixes

### P0 — CRITICAL (blocks production)

| # | Fix | File | Reason |
|---|-----|------|--------|
| 1 | Remove Fangate fallback from `execute_ppv` | `commerce/execution.py:126-140` | **Fangate-only creator can have PPV executed with wrong provider** |
| 2 | Remove Fangate fallback from `resolve_commerce_state` | `commerce/state.py:190-202` | **Same violation** |
| 3 | Remove Fangate fallback from `resolve_single_application_creator` | `commerce/single_creator.py:67-76` | **Same violation** |
| 4 | Remove Fangate fallback from `_get_creator_info_safe` | `memory/context_assembler.py:347-362` | **Same violation** |
| 5 | Apply DB migration `20260825000000_dropfans_provider.sql` | `db/migrations/` | **Dropfans columns missing from tables** |
| 6 | Add logging to delivery reservation failure | `commerce/post_purchase.py:380-387` | **Buyer pays but receives nothing silently** |

### P1 — HIGH (production risk)

| # | Fix | File | Reason |
|---|-----|------|--------|
| 7 | Fix synthetic hash collision risk | `db/dropfans.py:101` | Use SHA-256 or add unique TEXT column |
| 8 | Add fulfillment retry mechanism | `commerce/post_purchase.py` | Failed deliveries permanently lost |
| 9 | Disable Fangate HTTP dashboard routes | `chatbotv2/dashboard/routes/fangate.py:145-760` | **24 live Fangate HTTP routes** |
| 10 | Disable Fangate webhook receiver | `chatbotv2/dashboard/routes/fangate.py:1015` | **External webhook calls still accepted** |
| 11 | Fix fake analytics endpoint | `chatbotv2/dashboard/routes/fangate.py:943-957` | Returns hardcoded zeros |
| 12 | Remove encryption key fallback | `integrations/dropfans/security.py:84` | Should NOT fall back to `fangate_enc_key` |

### P2 — MEDIUM (quality/correctness)

| # | Fix | File | Reason |
|---|-----|------|--------|
| 13 | Write Dropfans-specific tests | `tests/` | **Zero dedicated Dropfans tests** |
| 14 | Rewrite 5 gutted skipped tests | `tests/test_commerce_execution.py` | Cannot un-skip without rewrite |
| 15 | Remove dead code (~1,100 lines) | Multiple files | Dead Fangate code, dead Dropfans functions |
| 16 | Remove 4 phantom dashboard routes | `chatbotv2/dashboard/routes/fangate.py:878-957` | Would crash at runtime |
| 17 | Add DropFans config to `.env.example` | `.env.example` | Developer discoverability |
| 18 | Rename `fangate_products` table | DB migration | Provider-neutral naming |
| 19 | Rename `fangate_transactions` table | DB migration | Provider-neutral naming |
| 20 | Rename `fangate_media_id` columns | DB migration | Provider-neutral naming |
| 21 | Add `buyer_email` to sensitive log patterns | `core/logging_config.py` | PII in structured logs |
| 22 | Fix hardcoded fallback URL | `integrations/dropfans/service.py:337` | Should use `settings.dropfans_api_base_url` |
| 23 | Remove `ExecutionStatus.FANGATE_ERROR` alias | `commerce/execution.py:68` | Dead code after tests updated |
| 24 | Remove `FANGATE_MIN_PRICE_MINOR` naming | `chatbotv2/dashboard/schemas.py` | Rename to provider-neutral |
| 25 | Remove `check_fangate()` | `core/health.py:105-138` | Dead function |

---

## Safe To Continue?

**NO**

The repository **cannot** safely be considered a clean Dropfans-only production architecture. The minimum required fixes before production use are the 6 P0 items:

1. Remove 4 Fangate fallback paths in execution, state, creator resolution, and context assembly
2. Apply the Dropfans DB migration
3. Add logging to silent delivery failures

Without these fixes, the system can silently route through Fangate, missing Dropfans integrations, and deliver nothing to paying buyers.
