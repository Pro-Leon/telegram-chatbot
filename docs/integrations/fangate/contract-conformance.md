# Fangate Contract Conformance

## Contract

- **Local path**: `docs\integrations\fangate\openapi.json`
- **OpenAPI version**: 3.0.0
- **API version**: 1.0.0
- **SHA-256**: `bd574ce85685f62e5ea52c0809c3848fb02faab8567d91ff3eb9c7c698caa1f0`
- **Retrieved**: 2026-08-22T21:02:14.760926Z

## Summary

- **Documented operations**: 23
- **Repository operations**: 23
- **Confirmed**: 17
- **Partial**: 0
- **Unverified**: 6
- **Missing from repo**: 49

## Confirmed

| Method | Path | Repository Method |
|--------|------|-------------------|
| GET | `/api/products` | list_products |
| GET | `/api/products/{product_id}` | get_product |
| PATCH | `/api/products/{product_id}` | update_product |
| PATCH | `/api/products/{product_id}/price` | update_product_price |
| POST | `/api/products/{product_id}/collection` | toggle_product_collection |
| PATCH | `/api/products/{product_id}/folder` | update_product_folder |
| POST | `/api/products/{product_id}/price-links` | create_price_link |
| DELETE | `/api/products/{product_id}` | delete_product |
| POST | `/api/products` | create_product |
| GET | `/api/content-folders` | list_content_folders |
| POST | `/api/content-folders` | create_content_folder |
| PATCH | `/api/content-folders/{folder_id}` | update_content_folder |
| DELETE | `/api/content-folders/{folder_id}` | delete_content_folder |
| GET | `/api/dashboard/summary` | get_dashboard_summary |
| GET | `/api/wallet` | get_wallet |
| DELETE | `/api/products/media/{media_id}` | delete_product_media |
| GET | `/api/products/collection` | list_product_collection |

## Unverified

| Method | Path | Repository Method | Reason |
|--------|------|-------------------|--------|
| POST | `/api/webhooks` | create_webhook | Endpoint absent from local OpenAPI contract |
| GET | `/api/webhooks` | list_webhooks | Endpoint absent from local OpenAPI contract |
| PATCH | `/api/webhooks/{webhook_id}` | update_webhook | Endpoint absent from local OpenAPI contract |
| DELETE | `/api/webhooks/{webhook_id}` | delete_webhook | Endpoint absent from local OpenAPI contract |
| POST | `/api/products/{product_id}/media` | upload_product_media | Endpoint absent from local OpenAPI contract |
| POST | `/api/products/{product_id}/media` | attach_product_media | Endpoint absent from local OpenAPI contract |

## Missing From Repository

| Method | Path | OperationId |
|--------|------|-------------|
| GET | `/api/affiliate/invited` | — |
| GET | `/api/affiliate/invitation` | — |
| GET | `/api/affiliate/invitation/code` | — |
| GET | `/api/user/api-keys` | — |
| POST | `/api/user/api-keys` | — |
| DELETE | `/api/user/api-keys/{tokenId}` | — |
| GET | `/api/app-data` | — |
| POST | `/api/register` | — |
| POST | `/api/login` | — |
| POST | `/api/login/batch` | — |
| PATCH | `/api/user/password` | — |
| POST | `/api/user/email/verify` | — |
| POST | `/api/user/password/reset` | — |
| POST | `/api/logout` | — |
| GET | `/api/collection/profile` | — |
| PATCH | `/api/collection/profile` | — |
| POST | `/api/collection/profile/image` | — |
| DELETE | `/api/collection/profile/image` | — |
| POST | `/api/consents/store-or-send` | — |
| PATCH | `/api/content-folders/{content_folder_id}` | — |
| DELETE | `/api/content-folders/{content_folder_id}` | — |
| GET | `/api/creators/search` | — |
| POST | `/api/dashboard/summary/aggregate` | — |
| POST | `/api/user/fcm` | — |
| DELETE | `/api/user/fcm` | — |
| POST | `/api/feedback` | — |
| GET | `/api/user/linked-accounts` | — |
| POST | `/api/user/linked-accounts` | — |
| DELETE | `/api/user/linked-accounts/{childUserId}` | — |
| POST | `/api/user/linked-accounts/{childUserId}/session` | — |
| POST | `/api/checkout/crypto/nowpayments` | — |
| GET | `/api/checkout/crypto/nowpayments/status/{orderId}` | — |
| POST | `/api/webhooks/nowpayments` | — |
| POST | `/api/upload-sessions` | — |
| GET | `/api/upload-sessions/{uploadSession}` | — |
| DELETE | `/api/upload-sessions/{uploadSession}` | — |
| POST | `/api/upload-sessions/{uploadSession}/remote` | — |
| POST | `/api/upload-sessions/{uploadSession}/parts` | — |
| POST | `/api/upload-sessions/{uploadSession}/complete` | — |
| GET | `/api/user` | — |
| DELETE | `/api/user` | — |
| PATCH | `/api/user/profile/has-adult` | — |
| GET | `/api/user/sessions` | — |
| DELETE | `/api/user/sessions/{tokenId}` | — |
| GET | `/api/veriff/create` | — |
| GET | `/api/wallet/affiliate` | — |
| POST | `/api/wallet/cashout` | — |
| POST | `/api/yoti/session/create` | — |
| POST | `/api/yoti/webhook` | — |

## Schema Drift

| Schema | Field | Contract | Repository | Classification |
|--------|-------|----------|------------|----------------|
| FangateProduct | is_downloadable | — | any | UNVERIFIED |
| FangateProduct | is_epoch_enabled | — | any | UNVERIFIED |

## Security

- **Auth type**: apiKey
- **Auth location**: header
- **Matches client**: True
- **Global security**: False

## Limitations

- Repository endpoints classified as UNVERIFIED are not invalid — they are absent from the local OpenAPI contract.
- Schema comparison covers product.resource and wallet.resource only.
- Request body field comparison is based on static analysis of client.py.
- Response parsing (defensive unwrapping) is classified but not modified.
- Webhook CRUD, offer, media, analytics, and epoch endpoints are UNVERIFIED.
