# 005 Deduplication and Opportunities

## Goal

Detect which fetched items are new for a search source and create opportunities without duplicates.

## Scope

- Track item visibility per source.
- Use `vinted_item_id` as global item identity.
- Use `source_seen_items` to know if a source has seen an item before.
- Create opportunities only for newly seen items that pass filters.
- Prevent duplicate opportunities for the same source, item, and rule.

## Out of Scope

- Notification delivery.
- Scheduler.
- Authenticated actions.

## Interfaces

- Database:
  - `source_seen_items`;
  - `opportunities`;
  - `items`;
  - `filter_rules`.

## Acceptance Criteria

- First time an item appears for a source, it is considered new.
- Re-running the same source with the same item does not create a duplicate opportunity.
- The same item can be new for a different source.
- Seen records keep first and last seen run references.
- Opportunity creation is idempotent.

## Verification

- Run the same fixture twice and confirm no duplicate opportunity.
- Run the same item under two sources and confirm source-specific seen tracking.
- Confirm database uniqueness protects against duplicate opportunities.
