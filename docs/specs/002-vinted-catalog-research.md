# 002 Vinted Catalog Research

## Goal

Discover how Vinted delivers public catalog data for a real filtered catalog URL and define the first `VintedCatalogProvider` contract.

## Scope

- Use Playwright, browser DevTools, and network inspection for research.
- Compare public URL parameters with internal request/payload parameters.
- Determine whether data comes from an HTTP endpoint, embedded HTML/Next payload, or both.
- Identify fields available for item normalization.
- Document pagination, order, request headers, cookies, and error behavior.
- Produce sanitized fixtures if useful for tests.

## Out of Scope

- Login to Vinted.
- Personal tokens or authenticated cookies.
- Captcha solving or aggressive anti-bot bypass.
- Production scraper implementation before the contract is documented.

## Interfaces

- Research document:
  - `docs/research/vinted-catalog.md`
- Future provider:
  - `VintedCatalogProvider.search(source) -> CatalogSearchResult`

## Acceptance Criteria

- The real data source for catalog items is documented with date and observed behavior.
- Required request inputs are listed.
- Available item fields are mapped to the project item model.
- Pagination behavior is documented.
- Known failure modes are documented.
- No sensitive data is stored in fixtures or docs.

## Verification

- Reproduce a single catalog fetch manually.
- Confirm at least one item can be mapped to the target item fields.
- Review docs for absence of cookies, tokens, addresses, or personal data.
