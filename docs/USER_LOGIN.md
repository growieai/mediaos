# User sign-in

The hosted console uses email/password sign-in and an opaque, server-revocable session.
An administrator provisions each account in an existing workspace. There is no anonymous
signup, automatic tenant creation or public role selection.

## First access

1. Open the private setup link supplied by the administrator.
2. Set and confirm a password. Use at least 15 characters and no more than 72 UTF-8 bytes.
3. The workspace opens after setup. On later visits, sign in with your email and password.
   The workspace comes from your account membership;
   you do not enter a workspace UUID or bearer token.

Setup links are secret, single-use and expire. Their token is in the URL fragment, which is
not sent in HTTP request targets. The UI removes the fragment after reading it. Do not put
setup links in public tickets, screenshots, repository files or analytics. There is no email
delivery dependency: an administrator distributes the private link through an approved channel.
For a lost password, ask the workspace administrator for a replacement setup link.

Signing out revokes the current session on the server. Refreshing the page restores a valid
session. Sessions expire after eight hours; reset/revocation invalidates existing sessions.
Passwords, setup tokens and session tokens are never returned in account JSON responses.

OPERATOR can create influencers and content. APPROVER can review exact evidence/content
revisions. ADMIN has both permissions. Signing in never grants an additional role, changes
tenant membership, approves content or enables a paid provider/social integration.

## Administration

Account provisioning runs with the separate migration identity, never the API's restricted
database role. Run from `backend` with the appropriate standalone maintenance environment:

```bash
python -m app.auth_admin invite --tenant TENANT_UUID --email USER_EMAIL --name 'Display name' --role OPERATOR --origin https://YOUR-HOSTNAME --output /PRIVATE/NEW-SETUP.json
python -m app.auth_admin reset --email USER_EMAIL --origin https://YOUR-HOSTNAME --output /PRIVATE/NEW-RESET.json
python -m app.auth_admin revoke --email USER_EMAIL
```

Protect the output directory and file. Keep existing service tokens and ingestion/social
identities separate from named user accounts. The advanced bearer login remains available
for existing internal tooling; it is not the normal user sign-in path.

## Security boundary

Migration `0021` stores password verifiers, invitation/session hashes, login throttles and
authentication audit records in the private database schema. Restricted runtime code cannot
read or directly write those tables. Guarded functions verify credentials before issuing a
tenant-bound session, and the existing database authentication function rechecks active
membership and session validity for each business transaction. Existing RLS, approval guards,
evidence rules and immutable content history continue to apply.

Password verification uses PostgreSQL `pgcrypto` bcrypt at cost 12 so the guarded database
transition can verify the password itself. The 72-byte limit is explicitly rejected rather
than truncated; passwords containing NUL are rejected. Unknown-account failures are generic,
and persisted bounded throttles apply before expensive verification. A failed attempt is
committed before the HTTP error is returned, so rolling back a request cannot erase throttling.
See the [PostgreSQL password hashing reference](https://www.postgresql.org/docs/18/pgcrypto.html)
and [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

Production uses a host-only `__Host-mediaos_session` cookie with HttpOnly, Secure, SameSite=Lax
and Path=/ attributes. Development on HTTP loopback uses `mediaos_session`. Cookie-authenticated
mutations and every authentication POST require an exact `Origin` matching `AUTH_PUBLIC_ORIGIN`;
the console proxy and API both enforce it. Credentials stay out of JavaScript storage and URLs.

The sign-in page and console proxy are accessible from ordinary networks. Authenticated API
operations still require a valid account and tenant membership. Raw `/v1/*` operator API paths
remain limited to configured operator CIDRs, except the existing explicitly public provider
callback/webhook/media routes. The private API and PostgreSQL have no public host ports.

## Operational scope

This is administrator-provisioned password authentication for the pilot. Email delivery,
self-service recovery, public signup, MFA, organizational SSO and automated account lifecycle
are not included. Off-site backups and alert delivery remain deployment work. Do not enable
Cloudflare proxying without separately reviewing its trusted-peer policy for raw operator APIs.
