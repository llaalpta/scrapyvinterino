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
  - `filter_rules`.

## Acceptance Criteria

- Filters are evaluated in the application after fetch.
- Text matching is case-insensitive and accent-tolerant.
- Excluded and blacklist terms reject matching items.
- Missing optional item fields fail closed only for filters that require the missing field.
- Rule evaluation result is explainable enough for debugging.

## Verification

- Unit tests for each filter type.
- Combined-rule test with include/exclude/price.
- Test item with missing optional fields.
