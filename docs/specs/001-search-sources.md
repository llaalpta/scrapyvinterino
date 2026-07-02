# 001 Search Sources

## Goal

Allow the user to configure Vinted catalog search URLs from the private app and persist them as reusable monitoring sources.

## Scope

- Create a search source with a display name and original Vinted catalog URL.
- List configured sources through API and PWA.
- Store a normalized representation of query parameters.
- Keep sources active by default.
- Validate that the URL is an anonymous public Vinted catalog URL before saving it.

## Out of Scope

- Executing searches.
- Validating that Vinted returns results.
- Scheduler settings beyond storing existing placeholder config.
- Authenticated Vinted sessions.

## Interfaces

- API:
  - `GET /api/sources`
  - `POST /api/sources`
- PWA:
  - source creation form;
  - source count and visible source list.
- Database:
  - `search_sources`.

## URL Rules

- Accepted schemes: `http` and `https`.
- Accepted hosts for the MVP: `www.vinted.es` and `vinted.es`.
- Accepted path: `/catalog` or `/catalog/`.
- Surrounding whitespace is stripped before saving; the remaining URL string is preserved as entered.
- Query parameters are parsed with blank values preserved and stored by sorted key.
- URL validation must not call Vinted and must not trigger scraping.

## Acceptance Criteria

- A valid Vinted catalog URL can be saved with a name.
- Saved sources are visible after refresh.
- The original URL is preserved unchanged except for surrounding whitespace trimming.
- Query parameters are stored in normalized JSON.
- Invalid URL input is rejected by the API.
- Creating a source does not trigger scraping.

## Verification

- API smoke test for create/list.
- PWA manual check for saving a source.
- Confirm `search_sources` row includes `url` and `normalized_query`.
