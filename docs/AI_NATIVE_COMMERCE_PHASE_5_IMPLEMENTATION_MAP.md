# AI-Native Commerce — Phase 5 Implementation Map (Forensic, Read-Only)

**Date:** 2026-08-29  
**Method:** Re-read of `docs/AI_NATIVE_COMMERCE_PHASE_1_FORENSIC_IMPLEMENTATION_MAP.md` (52 KB), `PHASE_2_MAP.md`, `PHASE_2_FINAL_REPORT.md`, `PHASE_3_MAP.md`, plus current `workers/llm_worker 530 / memory/context 453 / commerce/* / core/conversation_state / response_mode / question_policy / persona_self / capability_contract / scoring / llm_tools / integrations/dropfans / db/*` as installed after Phase 2.2 + 3 fixes. No production code modified in this map.

---

## 1. Current End-to-End (Verified)

`Telegram NewMessage → handlers 142 persona cache 600s → debounce 3s → Redis XADD inbound_messages 171 → llm_worker 970 XREADGROUP llm_workers → process_message 467 lock 60s → build_qwen3_context 530 (persona fan facts Stage Rules + STATE|PROFILE|COMMERCE aftercare filtered + SUMMARY + IDENTITY/CONVERSATION/ABOUT SUNNY/CAPABILITIES/RESPONSE/QUESTION) → _try_commerce_draft 566 (state 169 read-only, product cheapest-unpurchased rank P1-01, decision 23-branch PURE, strategy, execution advisory lock) → derive_commercial_objective → COMMERCIAL OBJECTIVE + COMMERCIAL STATE desire/temperature/window system → Qwen/qwen2.5:3b Ollama (think:false 0.7/0.8/1.5 num_predict 200) vs scoring Ollama format:json → routing ≥0.80 no flags auto else queue → enqueue_send dedup md5 826 → _process_send_stream 77 dedup+rate 1/s burst5+Telethon → Telegram → post_process profile[-10:] + summarize 20 → poll reconcile_dropfans_sales 251 record_dropfans_sale product_id synthetic fix → attribute → funnel converted → aftercare pending → schedule 24h → vault synthesize → Telegram sales_url`

---

## 2. Phased Baseline Verification

| Component | Claimed Phase 2.2 / Phase 3 | Live? | File:Line |
|---|---|---|---|
| `conversation_state` NEW/ESTABLISHED/RETURNING + identity regex + topic/open/ tone | `core/conversation_state.py` 8 fields + fallback >8 | **WIRED** |
| `response_mode` 8 modes + deterministic 7-rule planner | `core/response_mode.py 14-82` | **WIRED** |
| `question_policy` MAX_CONSEC+1-per-3 wired | `core/question_policy 11+214 sales_window` | **WIRED** (was dead `MAX_PER_3`) |
| `persona_self` ABOUT SUNNY | `core/persona_self 3 facts` | **WIRED** |
| `capability_contract` send_photo:no | `core/capability_contract 15 false` | **WIRED** |
| `desire ladder` 0-8 + decay ×0.7 | `commerce/desire.py NEW` | **WIRED** |
| `temperature` COLD/WARM/HOT bounded | `commerce/temperature.py NEW` | **WIRED** |
| `content_matching` title-token + purchased exclusion | `commerce/content_matching.py` | **WIRED** |
| `vault_taxonomy` Subject—Setting—Format | `commerce/vault_taxonomy.py NEW` | **WIRED** |
| `offer_readiness` READY | `commerce/offer_readiness.py NEW` | **WIRED** |
| `sales_window` NO/BUILDING/OPEN/COOLDOWN/AFTERCARE | `commerce/sales_window.py NEW` `derive_sales_window(desire,temp,readiness,aftercare,cooldown)` | **WIRED** via `workers 580 COMMERCIAL STATE` |
| `tip fatigue` 30d via `tool_audit_log` | `commerce/dao 559` | **WIRED** |
| `aftercare` pending/sent surface | `context_assembler 585 read + context 283 keyword` | **WIRED** |
| `multi-product` cheapest rank | `product_selection 233` | **WIRED** |
| `delivery` reserve/finalize UNIQUE | `db/vault 46/80` | **WIRED** |
| buyer per-item `downloadUrl` grant | **No API** `client 269-627` inventory no buyer param | **BLOCKED** |

---

## 3. Phase 5 Lifecycle — Current vs Desired

| Stage | Current | Desired | Gap |
|---|---|---|---|
| RELATIONSHIP | `lifecycle NEW + relationship cold, RESPONSE REACT, no offer` | Same | None |
| CURIOSITY | `desire CURIOSITY confidence 0.60 via content_curiosity` | Same | None |
| INTEREST | `desire INTEREST purchase 0.2-0.55` | Same | None |
| DESIRE | `TEASE when flirty` `response_mode 80` | Same | None |
| QUALIFICATION | `explicit_content_request → QUALIFICATION 0.75` | Same | None |
| OFFER_READY | `purchase ≥0.80 + HOT + eligible → OFFER_READY 0.95` | Same | None |
| PURCHASE | `reconcile 38 → purchased 164 → funnel converted 46` | Same | None |
| AFTERCARE | `pending/sent → AFTERCARE 450 + Qwen surface` | Same | **Needs preference re-rank** (already `AVAILABLE CONTENT` via `content_matching` fan_preferences) |
| REPEAT | `is_repeat_purchase_eligible 168h` surfaced line, not auto-offer | Same — intentional not immediate upsell | None |

Lifecycle is already **continuous** — each turn re-derives `desire+temperature+window` from durable offers/purchases + transient topics, not one-shot `ask→sell`.

---

## 4. Gap Closure Plan (Minimal)

1. **No new queue/worker/engine** — reuse existing `commerce/state→decision→strategy→execution→selection` + `vault/reserve`
2. **Vault taxonomy already safe** — operator free-text title, parse side, not rewrite.
3. **Purchase→preference loop** already via `post_process profile interests` cap 15 → next `rank_products_by_relevance fan_preferences` → repeat via `REPEAT` eligible flag.
4. **Remaining polish** is tuning `COMMERCIAL OBJECTIVE` wording compact `<80 tok` (already workers 592) — not giant prompt.

---

## 5. Tests & Telemetry

Existing `71 + 29 sales intelligence + 42 sunny` `619+ passed`; new `SalesWindow OPEN/COOLDOWN/AFTERCARE` already derived deterministically. Telemetry `GenerationTelemetry 8 sales fields` in-memory (`core/telemetry 19 sale pressure→offer_presented`) `generation_telemetry 22-col` widening deferred.

---

ROOT STATUS: FOrensically verified — Phase 4 desire/temperature/window already live via Phase 2.2, vault product_id NULL→synthetic fixed, buyer media blocked at DropFans documented — Phase 5 conversational sales execution is **continuous** without redesign
CURRENT: deterministic 23-branch PURE + ranked cheapest-unpurchased + sales intelligence bridge + aftercare surface live
DESIRED: Same deterministic core + 8-stage ladder + COLD/WARM/HOT + sales_window + relevance-ranked vault — **already wired**
PRIMARY GAP: No P0 remains; residual is per-item buyer grant blocked at DropFans (external) + tiered upsell not automated (intentional)
TOP P0 FINDINGS: None (4 closed pre-Phase 4)  TOP P1: Vault description dead local, repeat auto-offer flag surfaced not branched
VAULT CONTENT GAP: /vault owner-signed filePath 12h, /drops buyUrl + vaultItemIds 1-10, title/price/sales_url only
PURCHASE DELIVERY GAP: Unique reserve/finalize + sales_url fallback, per-item buyer downloadUrl missing
AI SALES INTELLIGENCE GAP: Ladder 0-8 + temperature + window + content relevance + offer readiness all live
REQUIRED NEXT PHASE: No code — operational polish + embedding recommender deferred
PRODUCTION CHANGES: NONE (read-only map, Phase 4 SalesWindow thin layer 15 lines already shipped)
CANARY: NOT ACTIVATED  PROVIDER: UNCHANGED (ollama/qwen2.5:3b)  ARCHITECTURE: NO REDESIGN
