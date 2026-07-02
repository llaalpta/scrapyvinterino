# 004 Item Persistence

## Goal

Persist public Vinted catalog items in a normalized format while preserving useful raw data for future parser changes.

## Scope

- Normalize item id, title, brand, price, currency, size, condition, seller, favorites, URL, image URL, and raw payload.
- Upsert by `vinted_item_id`.
- Update `last_seen_at` when an existing item appears again.
- Keep `first_seen_at` stable.

## Out of Scope

- Deduplication per source.
- Opportunity creation.
- Checkout or authenticated item details.

## Interfaces

- Provider output:
  - normalized item candidate DTO.
- Database:
  - `items`.

## Acceptance Criteria

- New items are inserted once.
- Existing items are updated without changing their primary identity.
- Missing optional fields do not fail persistence.
- Raw payload is stored only if sanitized.
- URLs are preserved for opening Vinted item pages.

## Verification

- Persist a fixture item.
- Persist the same fixture twice and confirm a single item row.
- Persist an item with missing optional fields.
