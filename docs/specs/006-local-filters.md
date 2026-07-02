# 006 Local Filters

## Goal

Apply application-owned filters after fetching Vinted results so opportunity detection is not dependent only on Vinted URL filters.

## Scope

- Filter by allowed brands.
- Filter by included and excluded terms.
- Filter by maximum price.
- Filter by size.
- Filter by condition/status.
- Filter by seller country when available.
- Filter by maximum favorite count.
- Support a blacklist of terms.
- Create opportunities for globally new, detail-enriched items that pass active filters.
- Prevent duplicate opportunities for the same item and rule across overlapping sources.
- Store source origin as opportunity metadata when useful, without treating each source as a separate notification.

## Out of Scope

- Machine learning scoring.
- Image analysis.
- Authenticated seller or checkout data.

## Interfaces

- API/PWA:
  - create and edit filter rules later in MVP.
- Worker:
  - evaluate rules during run.
- Database:
  - `filter_rules`;
  - `opportunities`.

## Acceptance Criteria

- Filters are evaluated in the application after fetch.
- Text matching is case-insensitive and accent-tolerant.
- Excluded and blacklist terms reject matching items.
- Missing optional item fields fail closed only for filters that require the missing field.
- Rule evaluation result is explainable enough for debugging.
- Opportunity creation is idempotent.
- `opportunities_created` reflects inserted opportunities.
- Overlapping sources do not create duplicate notificable opportunities for the same item and rule.

## Verification

- Unit tests for each filter type.
- Combined-rule test with include/exclude/price.
- Test item with missing optional fields.
- Run the same item twice and confirm no duplicate opportunity.
- Run the same item through overlapping sources and confirm only one opportunity is created for a matching rule.
