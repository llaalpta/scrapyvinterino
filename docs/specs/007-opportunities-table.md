# 007 Opportunities Table

## Goal

Show newly detected opportunities in the private PWA so the user can inspect and act on them.

## Scope

- Display opportunity/item rows in a table.
- Show image, title, brand, size, status, price, favorites, seller, country, and detection time when available.
- Provide action affordances:
  - view item;
  - favorite;
  - prepare purchase;
  - purchase.
- Keep future authenticated actions disabled or clearly unavailable until implemented.

## Out of Scope

- Real favorite or purchase execution.
- Push notifications.
- Advanced table customization.

## Interfaces

- API:
  - list opportunities or items needed by the table.
- PWA:
  - opportunities view.
- Database:
  - `opportunities`;
  - `items`.

## Acceptance Criteria

- New opportunities appear in the table.
- The Vinted item can be opened from the `view` action.
- Future actions are visible but disabled or feature-flagged.
- Empty state is clear.
- Table remains usable on desktop and mobile widths.

## Verification

- Seed or create opportunity rows and confirm display.
- Check empty state.
- Check frontend build.
- Manual browser check at `localhost:5173`.
