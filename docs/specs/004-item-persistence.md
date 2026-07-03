# 004 Item Persistence

## Goal

Persist public Vinted catalog items in a normalized format while preserving useful raw data for future parser changes.

## Scope

- Normalize item id, title, brand, price, currency, size, condition, seller, favorites, URL, image URL, and raw payload.
- Upsert by `vinted_item_id`.
- Update `last_seen_at` when an existing item appears again.
- Keep `first_seen_at` stable.
- Persist items during manual runs.
- Later monitor specs reinterpret `items_new` as monitor-new seen items; this spec only owns idempotent `items` upsert behavior.
- Refresh the PWA item table after a manual run.

## Out of Scope

- Monitor-scoped seen dedupe.
- Opportunity creation.
- Filters.
- `source_seen_items`.
- Checkout or authenticated item details.

## Interfaces

- Provider output:
  - normalized item candidate DTO.
- Database:
  - `items`.
- API/PWA:
  - existing manual run endpoint persists items;
  - existing items endpoint returns persisted items.

## Acceptance Criteria

- New items are inserted once.
- Existing items are updated without changing their primary identity.
- Missing optional fields do not fail persistence.
- Raw payload is stored only if sanitized.
- URLs are preserved for opening Vinted item pages.
- Manual runs update `items_found`; monitor specs own the final `items_new` meaning.
- Running the same result twice does not duplicate items.
- PWA displays persisted items after a run.

## Verification

- Persist a fixture item.
- Persist the same fixture twice and confirm a single item row.
- Persist an item with missing optional fields.
- Run a manual search and confirm items are stored.
- Run the same search twice and confirm item rows are not duplicated.
- Confirm `source_seen_items`, filters, and opportunities remain unchanged.
