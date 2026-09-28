# Fangate OpenAPI vs Repository Contract Inventory

**Generated**: 2026-08-22  
**OpenAPI source**: https://docs.fangate.app/openapi.json  
**Local spec**: `docs/integrations/fangate/openapi.json`  
**SHA-256**: `bd574ce85685f62e5ea52c0809c3848fb02faab8567d91ff3eb9c7c698caa1f0`

---

## Summary

| Metric | Value |
|---|---|
| OpenAPI paths | 37 unique paths, 52 operations |
| Repository unique paths | 22 |
| Shared paths | 12 |
| In OpenAPI only | 25 paths (not used by repo) |
| In repo only | 10 paths (undocumented by OpenAPI) |
| Parameter mismatches | 1 |
| Dead-code defense | 1 |

---

## Section 1: Repository Endpoints (28 methods, 22 unique paths)

| # | Client Method | HTTP | API Path | OpenAPI Match |
|---|---|---|---|---|
| 1 | `list_products` | GET | `/products` | MATCH |
| 2 | `get_product` | GET | `/products/{product_id}` | MATCH |
| 3 | `update_product` | PATCH | `/products/{product_id}` | MATCH |
| 4 | `update_product_price` | PATCH | `/products/{product_id}/price` | MATCH |
| 5 | `toggle_product_collection` | POST | `/products/{product_id}/collection` | MATCH |
| 6 | `update_product_folder` | PATCH | `/products/{product_id}/folder` | MATCH |
| 7 | `create_price_link` | POST | `/products/{product_id}/price-links` | MATCH |
| 8 | `delete_product` | DELETE | `/products/{product_id}` | MATCH |
| 9 | `create_product` | POST | `/products` | MATCH |
| 10 | `list_content_folders` | GET | `/content-folders` | MATCH |
| 11 | `create_content_folder` | POST | `/content-folders` | MATCH |
| 12 | `update_content_folder` | PATCH | `/content-folders/{folder_id}` | MATCH |
| 13 | `delete_content_folder` | DELETE | `/content-folders/{folder_id}` | MATCH |
| 14 | `get_dashboard_summary` | GET | `/dashboard/summary` | MATCH |
| 15 | `get_wallet` | GET | `/wallet` | MATCH |
| 16 | `create_webhook` | POST | `/webhooks` | **MISSING** |
| 17 | `list_webhooks` | GET | `/webhooks` | **MISSING** |
| 18 | `update_webhook` | PATCH | `/webhooks/{webhook_id}` | **MISSING** |
| 19 | `delete_webhook` | DELETE | `/webhooks/{webhook_id}` | **MISSING** |
| 20 | `upload_product_media` | POST | `/products/{product_id}/media` | **MISSING** |
| 21 | `blur_product_media` | POST | `/products/{product_id}/media/blur` | **MISSING** |
| 22 | `get_product_analytics` | GET | `/products/{product_id}/analytics` | **MISSING** |
| 23 | `trigger_product_epoch` | POST | `/products/{product_id}/epoch` | **MISSING** |
| 24 | `create_product_offer` | POST | `/products/{product_id}/offers` | **MISSING** |
| 25 | `send_product_offer` | POST | `/products/{product_id}/offers/{offer_id}/send` | **MISSING** |
| 26 | `update_product_offer` | PATCH | `/products/{product_id}/offers/{offer_id}` | **MISSING** |
| 27 | `revoke_product_offer` | DELETE | `/products/{product_id}/offers/{offer_id}` | **MISSING** |
| 28 | `test_webhook` | POST | `/webhooks/test` | **MISSING** |

**Result**: 12/28 match. **16 methods (10 unique paths) are undocumented by OpenAPI.**

---

## Section 2: Undocumented Endpoints (In repo, missing from OpenAPI)

### Product Media
| Client Method | HTTP | Path | Lines |
|---|---|---|---|
| `upload_product_media` | POST | `/products/{product_id}/media` | client.py:486 |
| `blur_product_media` | POST | `/products/{product_id}/media/blur` | client.py:495 |

### Product Analytics & Epoch
| Client Method | HTTP | Path | Lines |
|---|---|---|---|
| `get_product_analytics` | GET | `/products/{product_id}/analytics` | client.py:502 |
| `trigger_product_epoch` | POST | `/products/{product_id}/epoch` | client.py:509 |

### Product Offers (4 endpoints)
| Client Method | HTTP | Path | Lines |
|---|---|---|---|
| `create_product_offer` | POST | `/products/{product_id}/offers` | client.py:527 |
| `send_product_offer` | POST | `/products/{product_id}/offers/{offer_id}/send` | client.py:538 |
| `update_product_offer` | PATCH | `/products/{product_id}/offers/{offer_id}` | client.py:554 |
| `revoke_product_offer` | DELETE | `/products/{product_id}/offers/{offer_id}` | client.py:563 |

### Webhook Management (4 endpoints)
| Client Method | HTTP | Path | Lines |
|---|---|---|---|
| `create_webhook` | POST | `/webhooks` | client.py:369 |
| `list_webhooks` | GET | `/webhooks` | client.py:383 |
| `update_webhook` | PATCH | `/webhooks/{webhook_id}` | client.py:416 |
| `delete_webhook` | DELETE | `/webhooks/{webhook_id}` | client.py:422 |

### Webhook Test
| Client Method | HTTP | Path | Lines |
|---|---|---|---|
| `test_webhook` | POST | `/webhooks/test` | client.py:569 |

---

## Section 3: OpenAPI Endpoints NOT Used by Repository

The OpenAPI spec documents 37 unique paths. Only 22 are used by the repo. The remaining 15 paths (with all their operations) are **not used**:

| OpenAPI Path | Methods | Tag | Notes |
|---|---|---|---|
| `/api/affiliate/invited` | GET | Affiliate | |
| `/api/affiliate/invitation` | GET | Affiliate | |
| `/api/affiliate/invitation/code` | GET | Affiliate | |
| `/api/app-data` | GET | App Data | |
| `/api/checkout/crypto/nowpayments` | POST | Checkout | |
| `/api/checkout/crypto/nowpayments/status/{orderId}` | GET | Checkout | |
| `/api/collection/profile` | GET, PATCH | Collection Profile | |
| `/api/collection/profile/image` | POST, DELETE | Collection Profile | |
| `/api/consents/store-or-send` | POST | Consents | |
| `/api/creators/search` | GET | Creators | |
| `/api/dashboard/summary/aggregate` | POST | Dashboard | |
| `/api/feedback` | POST | Feedback | |
| `/api/user/fcm` | POST, DELETE | FCM Tokens | |
| `/api/user/api-keys` | GET, POST | User & Auth | |
| `/api/user/api-keys/{tokenId}` | DELETE | User & Auth | |
| `/api/register` | POST | User & Auth | |
| `/api/login` | POST | User & Auth | |
| `/api/login/batch` | POST | User & Auth | |
| `/api/logout` | POST | User & Auth | |
| `/api/user/password` | PATCH | User & Auth | |
| `/api/user/password/reset` | POST | User & Auth | |
| `/api/user/email/verify` | POST | User & Auth | |
| `/api/user` | GET, DELETE | User & Auth | |
| `/api/user/profile/has-adult` | PATCH | User & Auth | |
| `/api/user/sessions` | GET | User & Auth | |
| `/api/user/sessions/{tokenId}` | DELETE | User & Auth | |
| `/api/user/linked-accounts` | GET, POST | User & Auth | |
| `/api/user/linked-accounts/{childUserId}` | DELETE | User & Auth | |
| `/api/user/linked-accounts/{childUserId}/session` | POST | User & Auth | |
| `/api/upload-sessions` | POST | Upload Sessions | |
| `/api/upload-sessions/{uploadSession}` | GET, DELETE | Upload Sessions | |
| `/api/upload-sessions/{uploadSession}/complete` | POST | Upload Sessions | |
| `/api/upload-sessions/{uploadSession}/parts` | POST | Upload Sessions | |
| `/api/upload-sessions/{uploadSession}/remote` | POST | Upload Sessions | |
| `/api/veriff/create` | GET | Veriff | |
| `/api/wallet/affiliate` | GET | Wallet | |
| `/api/wallet/cashout` | POST | Wallet | |
| `/api/webhooks/nowpayments` | POST | Webhooks | |
| `/api/yoti/session/create` | POST | Yoti Verification | |
| `/api/yoti/webhook` | POST | Yoti Verification | |

---

## Section 4: Parameter Mismatches

### `list_products` — missing `folder_state` parameter

**OpenAPI spec** (`/api/products` GET):
```
query parameters: page, limit, sort_by, folder_id, folder_state, filterFolder
```
- `folder_state` enum: `["unassigned"]`
- `filterFolder` described but not typed

**Repository** (`list_products` at client.py:221):
```python
async def list_products(self, page=1, limit=50, sort_by="newest", folder_id=None):
```

**Drift**: Repo does NOT pass `folder_state` or `filterFolder`. This is a **minor drift** — the repo only uses `folder_id` for folder filtering, while the API also supports `folder_state` for "show only unassigned products" and `filterFolder` for general folder filtering.

**Impact**: No functional impact currently. If folder-state filtering is needed later, the param must be added.

---

## Section 5: Dead-Code Defense in `create_price_link`

**Repository** (`create_price_link` at client.py:301):
```python
if "product" in data:
    data = data["product"]
resource = data.get("resource", data)
```

**OpenAPI spec** (`/api/products/{product_id}/price-links` POST):
- Request body: `{ price, title, private_description, public_description }`
- Response 201: `{ id, product_id, url, ... }` (flat, no `resource` wrapper)

**Analysis**: The defensive `data.get("resource", data)` and `data.get("product", data)` patterns are **dead code** — the current API never returns a `resource` wrapper. However, this is **intentional backward compatibility** and does NOT need to be removed. It's a safe defensive pattern.

**Impact**: None. Code is correct and safe.

---

## Section 6: Schema Differences

### `FangateProduct` model vs OpenAPI `product.resource`

**OpenAPI `product.resource` fields** (from spec):
```
id, title, price, private_description, public_description, 
is_adult_content, is_verif_age, is_should_consent, 
has_children, is_password_protected, folder_id, 
media[], created_at, updated_at, product_url
```

**Repository `FangateProduct` model** (models.py):
```python
class FangateProduct:
    id, title, price, private_description, public_description,
    is_adult_content, is_verif_age, is_should_consent,
    has_children, folder_id, media, created_at, updated_at, product_url
    + is_epoch_enabled, is_downloadable  # EXTRA FIELDS
```

**Extra fields in repo not in OpenAPI**:
- `is_epoch_enabled` — not in OpenAPI `product.resource`
- `is_downloadable` — not in OpenAPI `product.resource`

**Missing fields in repo vs OpenAPI**:
- `is_password_protected` — in OpenAPI, not in repo model

**Impact**: Low. Extra fields are silently ignored by Pydantic. Missing field means if the API returns `is_password_protected`, it won't be captured in the model.

---

## Section 7: Drift Risk Assessment

### HIGH RISK (P1)

| Issue | Location | Risk |
|---|---|---|
| Webhook CRUD undocumented | client.py:369-422 | API could change without repo knowing |
| Product offers undocumented | client.py:527-563 | Same |
| Product media undocumented | client.py:486-495 | Same |
| Product analytics/epoch undocumented | client.py:502-509 | Same |
| `test_webhook` undocumented | client.py:569 | Same |

### MEDIUM RISK (P2)

| Issue | Location | Risk |
|---|---|---|
| `list_products` missing `folder_state` | client.py:221 | Feature gap if needed later |
| `is_password_protected` missing from model | models.py | Silent data loss |
| No webhook signature verification for NOWPayments | OpenAPI: `/api/webhooks/nowpayments` | Different auth method |

### LOW RISK (P3)

| Issue | Location | Risk |
|---|---|---|
| Dead-code defense in `create_price_link` | client.py:301 | No impact |
| Extra fields in `FangateProduct` | models.py | No impact |
| OpenAPI endpoints not used by repo | — | No impact |

---

## Section 8: Recommendations

1. **HIGH**: Test all 10 undocumented endpoints against the live Fangate API to verify they still work. Document findings.
2. **HIGH**: Open an issue/PR with Fangate to get offer, webhook CRUD, media, analytics, and epoch endpoints added to their OpenAPI spec.
3. **MEDIUM**: Add `folder_state` parameter to `list_products()` for completeness.
4. **MEDIUM**: Add `is_password_protected` field to `FangateProduct` model (or confirm it's not returned by API).
5. **LOW**: Consider whether NOWPayments webhook needs a separate signature verification path (different from Fangate's HMAC-SHA256).
6. **LOW**: Remove dead-code defense in `create_price_link` OR document it as intentional backward compatibility.
