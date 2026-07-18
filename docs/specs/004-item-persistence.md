# 004 Item Persistence

## Goal

Persist public Vinted catalog items in a normalized format while preserving useful raw data for future parser changes.

This spec is historical. Current monitor execution persists `items` only for candidates that become opportunities; Redis owns seen-state dedupe.

## Scope

- Normalize item id, title, brand, price, currency, size, condition, seller, favorites, URL, image URL, and raw payload.
- Upsert opportunity items by `vinted_item_id`.
- Update `last_seen_at` when an opportunity item appears again.
- Keep `first_seen_at` stable.
- Persist items only when they are needed for opportunity display.
- Spec 005 owns the run-level `items_found` counter for Redis-new monitor candidates; this spec only owns idempotent `items` upsert behavior.

## Out of Scope

- Monitor-scoped seen dedupe, owned by Redis in spec 005.
- Opportunity creation.
- Filters.
- Persisting discarded or seen-only candidates.
- Checkout or authenticated item details.

## Interfaces

- Provider output:
  - normalized item candidate DTO.
- Database:
  - `items`.
- API/PWA:
- monitor run endpoints persist opportunity items;
- opportunities endpoint returns item data embedded in opportunity rows.

## Acceptance Criteria

- Opportunity items are inserted once.
- Existing items are updated without changing their primary identity.
- Missing optional fields do not fail persistence.
- Raw payload is stored only if sanitized.
- URLs are preserved for opening Vinted item pages.
- Monitor runs update the canonical `items_found` counter; raw catalog rows are diagnostics rather than persisted product results.
- Running the same result twice does not duplicate items.
- PWA displays persisted items through opportunities after a run.

## Verification

- Persist a fixture item.
- Persist the same fixture twice and confirm a single item row.
- Persist an item with missing optional fields.
- Run a monitor search and confirm opportunity items are stored.
- Run the same opportunity item twice and confirm item rows are not duplicated.
- Confirm discarded candidates are not stored as items.
