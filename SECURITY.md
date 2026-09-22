# Security

## Trust boundary

`prompt_jev` sends its non-NULL input and question specification to an external
provider. Granting a role `EXECUTE` therefore allows that role to export text
using the credentials configured for the PostgreSQL server. Session settings
select configuration; they are not a secret store or an administrative policy
boundary.

Keep credentials in the server process environment or an approved gateway. Do
not put credentials in SQL literals, role settings, or database logs. Restrict
outbound network access independently of this extension.

## Endpoint policy

Provider endpoints require HTTPS. TLS certificate and hostname verification use
Python's standard verified HTTPS behavior. Standard proxy environment variables
are honored; the extension does not disable TLS verification for proxies or
fixtures.

Administrators can limit provider selection with the server environment variable
`PROMPT_JEV_ALLOWED_PROVIDERS`, a comma-separated subset of `typesafe`, `vercel`,
and `cloudflare`. This is an additional guard, not a replacement for network
egress controls.

Local tests may set `PROMPT_JEV_ALLOW_INSECURE_LOOPBACK=1` in the PostgreSQL
server environment. It permits plain HTTP only for `localhost` or a loopback IP.
There is no session setting for this mode, so a role with function execution
cannot enable it.

Redirects are rejected. Provider error bodies are not included in SQL errors or
read for diagnostic output. Errors may include only the provider, status,
category, and a validated request ID.

## Reporting

Report vulnerabilities privately through GitHub's security advisory interface.
Do not include live credentials or customer data in a report.
