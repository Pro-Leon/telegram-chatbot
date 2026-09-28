# Commerce DB Rebuild Spec (db/fangate.py + core tables)

Status: SPEC (extracted, not yet implemented). Source: exhaustive call-site
+ test + docs extraction (audit 2026-09-28). Every signature, SQL shape,
and constraint below is evidenced — nothing invented. Items marked
INFERRED are the minimal fill required where no assertion exists.

## Phase 1 (this spec): 6 tables + db/fangate.py (26 functions)

Phase 2 (later): db/dropfans.py (21 fns) + vault/segments tables.
Phase 3 (later): db/migrate.py runner + remaining modules + live proofs.

## Tables (see db/migrations/001_commerce_core.sql for DDL)

- `creators(id BIGSERIAL PK, name, display_name, timestamps)`
- `creator_integrations(creator_id PK/FK, encrypted_api_key, api_key_name,
  fangate_account_id, currency_code, status, webhook_id,
  encrypted_webhook_secret, dropfans_* (3 cols), last_success/error_at,
  last_error, timestamps)` + status index
- `fangate_products(id BIGINT PK-part, creator_id FK, product_type, title,
  preview(_blurred), price_minor CHECK>=0, in_collection, link, sales_url,
  counters, folder(_id), media JSONB, adult/age/epoch/consent/download/
  accessible flags, descriptions, raw JSONB, synced_at)` +
  `UNIQUE(creator_id,id)` + creator index
- `fangate_transactions(id BIGSERIAL PK, creator_id FK, user_id FK NULL,
  transaction_id, event_type, buyer_email, buyer_external_id,
  seller_earning NUMERIC(12,2), currency, product_id, set_price,
  occurred_at, delivery_id, created_at)` +
  `UNIQUE(creator_id,transaction_id,event_type)` + partial user index +
  creator/created index
- `fangate_wallet_entries(id BIGSERIAL PK, creator_id FK, wallet_tx_id,
  amount_minor, txn_type, created_at TEXT (provider string),
  title, raw, synced_at)` + `UNIQUE(creator_id,wallet_tx_id)`
- `fangate_webhook_events(id BIGSERIAL PK, creator_id FK, delivery_id,
  event, payload JSONB, signature_valid, processed, created_at)` +
  `UNIQUE(creator_id,delivery_id)` + creator/processed index

## Function contracts (all async; pool via db.postgres.get_pool)

Creators/integrations: `get_creator(id)->row|None` /
`get_creator_integration(cid)->row|None` / `list_creators()->[rows]` /
`create_creator(name, display_name)->row` /
`upsert_creator_integration(cid, encrypted_api_key, *, api_key_name=None,
currency_code=None)->row` (ON CONFLICT (creator_id) DO UPDATE, status active;
never log secrets) / `list_active_creator_ids()->[int] sorted`
(SQL contains `status = 'active'` + `ORDER BY creator_id`) /
`get_any_creator_id_with_integration()->int|None` (ORDER BY, LIMIT 1) /
`list_integration_statuses()->[creator_id,status,last_success_at,
last_error_at,last_error]` / `record_integration_success(cid)` (status
active + timestamp, clear error) / `record_integration_error(cid, msg)`
(status error + stamp) / `update_integration_webhook(cid, webhook_id,
encrypted_secret)` (None/None on delete).

Products: `list_fangate_products(cid, limit=100, offset=0)` (creator scope,
id ASC, NO validity filtering — callers filter) / `count_fangate_products`
/ `get_fangate_product(cid, pid)` (SQL contains
`WHERE creator_id = $1 AND id = $2`, None → caller 404s) /
`upsert_fangate_product(cid, FangateProduct)` (ON CONFLICT upsert,
creator-scoped, after remote success only) /
`delete_fangate_product(cid, pid)` (scoped hard delete) /
`find_product_by_media_id(cid, media_id)` (JSONB media match, INFERRED).

Ledger: `list/count_fangate_transactions(cid, limit=100, offset=0)` /
`upsert_fangate_transaction(cid, transaction_id, event_type, *,
buyer_email=None, seller_earning=None, currency=None, product_id=None,
set_price=None, occurred_at=None, delivery_id=None)->bool`
(SQL contains `ON CONFLICT (creator_id, transaction_id, event_type)
DO NOTHING`; True=inserted) / `list/count_fangate_wallet_entries` /
`upsert_fangate_wallet_entry(cid, entry)` (SQL contains
`ON CONFLICT (creator_id, wallet_tx_id) DO UPDATE`) /
`insert_fangate_webhook_event(cid, delivery_id, event, payload,
signature_valid)->bool` (`ON CONFLICT (creator_id, delivery_id)
DO NOTHING`) / `get_fangate_webhook_event(cid, delivery_id)`
(`WHERE creator_id = $1 AND delivery_id = $2`) /
`mark_webhook_event_processed(cid, delivery_id)` (conditional UPDATE).

## Global invariants

Creator-first (arg 0 everywhere; every statement creator-scoped);
fail-closed None (callers map to 404/UNAVAILABLE, never raise);
idempotency substrings verbatim (see spec §8 of extraction);
returns: get→row|None, list→[rows], count→int, upserts→bool|None as above;
no secrets in returns or logs (allowlisted status shape only).

## Phase 3b (implemented 2026-09-28): db/vault.py (20 functions)

Delivery ledger over `vault_media_deliveries` (DDL re-declared in
`db/migrations/20260917020000_p33_vault_deliveries.sql`; live table verified:
BIGSERIAL id PK, creator INTEGER, user BIGINT FK→users, fangate_media_id INT,
product_id INT, telegram_message_id BIGINT, sent_at/created_at TIMESTAMPTZ
DEFAULT NOW(), status TEXT DEFAULT 'sent', dropfans_media_id/_vault_item_id
TEXT, UNIQUE(creator,user,fangate_media_id), partial UNIQUE on
dropfans_vault_item_id WHERE IS NOT NULL).

Lifecycle: `reserve_delivery(cid, uid, media, product?=None)->int|None`
(INSERT pending, ON CONFLICT DO NOTHING RETURNING id) /
`finalize_delivery(rid, telegram_message_id?=None, creator_id?=None)->bool`
(UPDATE pending→sent; SQL contains `creator_id = $3`; id-only legacy path
warns) / `release_delivery(rid, creator_id?=None)->bool` (DELETE
pending-only; SQL contains `creator_id = $2`) /
`release_stale_reservations(max_age_minutes=5, batch_size=50,
skip_ids=())->[rows]` (global reaper; `CAST($1 AS numeric) *
INTERVAL '1 minute'`, `NOT (id = ANY($3))` skip shield, no make_interval).

Reads: `record_delivery(…, status sent)->bool` / `get_delivery` /
`get_delivered_media_map->{media_id: row}` / `has_user_received_media`
(sent-only) / `get_unseen_media_ids` / `list/count_deliveries` /
`get_delivery_stats->{total,unique_fans,unique_media,sent,pending}`
(None→zeros) / `get_recent_deliveries` + `get_top_fans` (LEFT JOIN users) /
`get_product_delivery_counts` + `get_media_delivery_counts->{id: cnt}` /
`get_fan_delivery_history` / `get_fan_delivery_count`.

DropFans-keyed: `finalize_dropfans_delivery(cid, uid, vault_item_id,
telegram_message_id?=None)->bool` /
`release_dropfans_delivery(cid, uid, vault_item_id)->bool` (both
pending-guarded). DropFans reserve/check SQL stays inline at
commerce/post_purchase.py + chatbotv2/main.py gateway (pinned by
test_p32_safety_foundation + deliver_product_media assertions — do not
migrate without updating those pins).
