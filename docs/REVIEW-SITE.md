# The reviewer web app (Cycle 26)

`harness serve --tenant <t> [--host 127.0.0.1] [--port 4870]` runs a small
Flask app (harness/serve.py) so a reviewer can see and act on a run from a
browser instead of the CLI. It binds `127.0.0.1` only by default -- nothing
about this command opens the server to the network by itself. Every action
the site takes (score, approve, reject, request changes) goes through the
exact same functions `harness score`/`approve`/`reject` use
(harness/runstate.py, harness/cli.py's `record_score`) -- this app never
writes `state.json` or `evals/scores.jsonl` on its own.

## Auth

Two modes, tried in this order on every request:

1. **Cloudflare Access**, when the tenant env has `REVIEW_TRUST_CF_ACCESS=true`
   and the request carries a `Cf-Access-Authenticated-User-Email` header --
   that email is the reviewer, and it must match a `tenant.yaml` `reviewers`
   entry or the request is refused with 403. A request that reaches this app
   directly (bypassing the tunnel, so no CF header is present) falls back to
   basic auth below rather than being trusted.
2. **HTTP basic auth**: username is a reviewer email from `tenant.yaml`,
   password is `REVIEW_PASSWORD` from the tenant env, compared with
   `hmac.compare_digest`. The app refuses to start at all if `REVIEW_PASSWORD`
   is not set in the tenant's `.env` -- basic auth is always the fallback
   path, so a working password is required either way.

No env value (`REVIEW_PASSWORD`, an API key, anything else) is ever logged,
printed, or rendered into a page.

## Running it

As a systemd `--user` service, via `crons/install.sh --with-review <tenant>`
(see `crons/harness-review.service` -- `Restart=on-failure`, its
`EnvironmentFile` points at `tenants/<tenant>/.env`). This is the only unit
`install.sh` enables and starts itself; every scheduled digest/refresh timer
it also renders is left for the operator to `systemctl --user enable --now`.

If `systemctl --user` is not available on the host (no user session or
lingering enabled), start it directly instead, still bound to localhost:

```
nohup /path/to/advertorial/.venv/bin/harness serve --tenant peak-saunas --port 4870 \
  >>/path/to/advertorial/tenants/peak-saunas/runs/review-server.log 2>&1 &
```

## Interim access: SSH tunnel

Before any public exposure is set up, reach the site from your own machine
with an SSH tunnel to the server:

```
ssh -L 4870:127.0.0.1:4870 prod
```

then open `http://localhost:4870`. Basic auth still applies.

## Public exposure

The server never opens `harness serve`'s port to the internet by itself.
Public exposure is an operator decision -- add ONE of the following.
**Cloudflare Access with email OTP in front of the tunnel is the
recommended gate**, since it gives every reviewer a second, revocable factor
on top of `REVIEW_PASSWORD` (and lets you turn on
`REVIEW_TRUST_CF_ACCESS=true` so reviewers stop needing to type the shared
password at all).

### Option A: Cloudflare Tunnel + Access (recommended)

Tunnel ingress (`~/.cloudflared/config.yml` on the server, alongside every
other tunnel hostname this deployment already routes):

```yaml
ingress:
  - hostname: review.peaksaunasteam.com
    service: http://127.0.0.1:4870
  # ... existing ingress rules ...
  - service: http_status:404
```

Then, in the Cloudflare Zero Trust dashboard, add an Access application for
`review.peaksaunasteam.com` with an email-OTP (or Google/GitHub) identity
provider, policy restricted to the reviewer emails in `tenant.yaml`'s
`reviewers` list. Once that's live, set `REVIEW_TRUST_CF_ACCESS=true` in
`tenants/<tenant>/.env` so the app trusts Access's
`Cf-Access-Authenticated-User-Email` header instead of prompting for basic
auth (a request that reaches the app directly, without that header, still
falls back to basic auth -- see Auth above).

### Option B: Caddy vhost

If this deployment terminates TLS with Caddy instead of (or in addition to)
a tunnel, add a vhost (`Caddyfile`, alongside the storefront's own vhosts):

```
review.peaksaunasteam.com {
    reverse_proxy 127.0.0.1:4870
}
```

With Caddy alone (no Cloudflare Access in front), leave
`REVIEW_TRUST_CF_ACCESS` unset -- reviewers authenticate with basic auth
(`REVIEW_PASSWORD`) only. Adding a second factor here means putting Access
(or an equivalent) in front of the vhost too.

Neither option is applied by this harness or by `crons/install.sh` --
`/etc/caddy` and `cloudflared`'s own config are edited by the operator, by
hand, outside this repo.

## Setting REVIEW_PASSWORD

Generate one and append it to the tenant's `.env` -- never print the value
itself:

```
echo "REVIEW_PASSWORD=$(openssl rand -base64 15)" >> tenants/peak-saunas/.env
```

Read it back later (on the server, when you actually need to log in) with:

```
grep ^REVIEW_PASSWORD ~/advertorial/tenants/peak-saunas/.env
```
