# 009 Authenticated Actions

## Goal

Support future personal authenticated Vinted actions from the private app after public catalog monitoring is stable.

## Scope

- Mark favorites.
- Discover checkout options:
  - payment methods;
  - shipping methods;
  - address options;
  - pickup points.
- Prepare purchase with selected options.
- Execute purchase only after explicit user confirmation.
- Store redacted audit records for each action.

## Interfaces

- API/PWA:
  - action request endpoints;
  - confirmation UI.
- Worker:
  - authenticated action processor.
- Database:
  - `action_requests`;
  - `action_executions`;
  - `checkout_snapshots`.
- Configuration:
  - feature flags and local secrets.
  - `ACTION_REQUESTS_ENABLED=false` by default.

## Acceptance Criteria

- Authenticated actions are disabled by default.
- Action request endpoints do not accept requests while `ACTION_REQUESTS_ENABLED=false`.
- Secrets are read only from ignored local config or future encrypted storage.
- Every action has an audit trail with redacted request/response.
- Purchase validates price, currency, availability, shipping, and payment choice immediately before submission.
- Purchase requires explicit UI confirmation.

## Verification

- Feature flag off prevents all authenticated actions.
- Redaction tests cover cookies, tokens, addresses, and payment fields.
- Dry-run or research mode can inspect flow without submitting purchase.
