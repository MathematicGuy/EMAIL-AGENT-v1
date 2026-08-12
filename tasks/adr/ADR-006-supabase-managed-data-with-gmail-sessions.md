# ADR-006 â€” Supabase managed data services with Gmail OAuth sessions

- Status: Accepted
- Date: 2026-08-12
- Decision makers: Product/Engineering team

## Context

The application needs durable multi-user storage without exposing a database or
backend credential to browsers. It already uses Google Gmail OAuth for mailbox
consent and obtains a verified Gmail account address during the callback.

## Decision

Use Supabase as managed PostgreSQL (and, in the later document increment,
private object storage). FastAPI is the sole application/data boundary: the
browser does not call Supabase PostgREST, Storage, or Auth APIs and receives no
Supabase publishable or secret key.

Gmail OAuth remains the identity proof. On a verified callback FastAPI maps the
normalized Gmail address to an immutable application user ID, creates a
personal default workspace on first login, stores the encrypted refresh token
under that internal user, and creates an opaque app session bound to both user
and workspace. Only the SHA-256 token hash is retained in PostgreSQL; the
plaintext token is sent only as a Secure, HttpOnly, SameSite=Lax cookie.

Every FastAPI request in the Postgres runtime resolves a `VerifiedPrincipal`
from that cookie. Missing, expired, and revoked sessions receive 401; resources
outside the resolved principal receive the existing 404 non-disclosure response.

## Consequences

- `DATABASE_URL` is the only Supabase value this increment requires. It stays
  exclusively in server-side secret configuration.
- A Gmail email is an integration attribute, not a primary key or authorization
  identity. One user can own multiple Gmail mailbox connections and belong to
  multiple workspaces.
- Supabase Auth is not introduced. Session revocation, TTL, and authorization
  live in FastAPI/PostgreSQL.
- SQLite remains a local fallback only when `DATABASE_URL` is absent.

## Alternatives considered

### Supabase Auth

Rejected. It duplicates the Gmail OAuth integration already required for
mailbox consent and would introduce a second user identity lifecycle.

### Gmail email as the persistent primary key

Rejected. Email is mutable integration data and cannot safely represent a
workspace-scoped internal principal.

### Keep SQLite for deployed multi-user persistence

Rejected. It does not provide the durable shared transactional store needed by
multiple FastAPI/worker processes.
