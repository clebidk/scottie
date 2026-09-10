# Research: harness landscape

Read-only survey done ahead of the public release and the Meta/Drive/Shopify
onboarding connectors. Scope: what this harness already does (see
`docs/ARCHITECTURE.md`, `docs/HARNESS-MAP.md`) against six areas of prior art,
so the release borrows proven structure instead of reinventing it. Nothing
here changes code; it is a menu for future FIXLOG/roadmap entries.

For orientation, this harness today is V1 static (`docs/SPEC.md`): Python CLI,
`tenants/<name>/` data folders resolved through `harness/tenant.py`,
`cartridges/<name>/` page types (`cartridge.md` + `schema.json` + Jinja
`template.html` + `rubric.md`), an eight-stage `harness/pipeline.py` registry
(ingest -> ground -> claims gate -> write -> render -> review), a deterministic
gate + bounded model-repair loop (`harness/cli.py`), and `harness/budget.py`
wall-clock/token/call caps. No publish adapter exists yet, and eval scoring
(`cmd_score`) writes but never reads `scores.jsonl`.

## 1. Open-source ad/landing-page generators

Nothing found matches this harness's shape: a claims-gated, multi-cartridge,
multi-tenant pipeline. The visible field is much thinner:

- **jeus0522/AI-Landing-Page-Generator** -- MIT, TypeScript/Next.js, 4 stars.
  Single page type, config via `.env` API keys, no validation step, output is
  an iframe preview with a roadmap item for zip/Vercel/Netlify deploy. No
  tenant concept at all.
- **sachink1729/LLM-Agent-Landing-Page-Generator-CrewAI-Qdrant-Langchain** --
  Apache-2.0, Python/Jupyter notebook, 15 stars. CrewAI agents plus a Qdrant
  vector store for product grounding; no documented validation or publish
  step.
- Broader searches ("advertorial generator," "ad creative to landing page,"
  "shopify page generator python") surfaced only generic AI-website-builder
  toys and Shopify `llms.txt` generators (an unrelated SEO-discovery format),
  none with a claims/fact gate, a cartridge-style type system, or tenant
  isolation.

**What to borrow:** nothing structural -- this confirms the claims gate,
cartridge system, and tenant isolation are the differentiator, not a solved
problem to copy. **What to avoid:** the common pattern in this tier is
"generate once, ship the raw model output" with zero deterministic
validation; that is exactly the failure mode `docs/ARCHITECTURE.md`'s Gates
section exists to prevent, and it is worth naming in the public README as the
reason this harness looks more complicated than a typical "AI landing page
generator" repo.

## 2. Guardrail and validation frameworks

- **guardrails-ai/guardrails** (Apache-2.0, ~7.4k stars). Composes named
  **validators** into a `Guard` object; failures carry an explicit
  `OnFailAction` (retry, fix, reask, exception, filter, refrain). Validators
  are distributed as installable packages via "Guardrails Hub" (`pip install
  guardrails-ai-<validator>`).
- **instructor** (python-useinstructor.com). Validates LLM output against a
  Pydantic schema and **automatically reasks the model** on a validation
  failure, up to `max_retries`, feeding the validation error back into the
  next prompt; supports an `llm_validator` for semantic (not just structural)
  checks.
- **promptfoo**. Declarative YAML test cases with assertions/graders,
  designed for "test-driven LLM development," run from a CLI or in CI.
- **Inspect (UK AISI)**. Splits an eval into **dataset -> solver -> scorer**,
  and persists a structured log/transcript per run, viewable in "Inspect
  View" and exportable to a dataframe for later analysis.

**What to borrow (concrete):**
- *`OnFailAction`-style naming.* `harness/cli.py`'s repair loop already does
  retry-then-fix-then-stop; adopting Guardrails' explicit vocabulary
  (`fix` = `apply_deterministic_fixes`, `reask` = the model repair call,
  `exception` = `ClaimsGateFailure`) as documented labels in
  `docs/ARCHITECTURE.md`'s Gates section makes the existing design legible to
  a new contributor. Effort: Low. Impact: Low-Med (clarity, not capability).
- *promptfoo's declarative test-case file.* `evals/rubric.md` is prose only,
  and `cmd_score` (`harness/cli.py`) writes `scores.jsonl` but
  `docs/HARNESS-MAP.md` notes "no aggregation, no report command, and nothing
  reads the file back." Borrow the shape -- a `tenants/<t>/evals/cases/*.yaml`
  file per fixture with an expected claim set / word-range / forbidden-term
  assertions, run by a new `harness eval` command that replays a fixture and
  diffs `page.json` against the assertions -- not promptfoo itself (adding
  the dependency would be a Kitchen Sink move against a harness whose own
  claims gate already outperforms generic assertion matching for this
  domain). Effort: Med. Impact: High -- this is the one gap `HARNESS-MAP.md`
  already flags as unbuilt.
- *Inspect's dataset/solver/scorer split, applied to the score sheet.*
  Recording gate pass/fail, repair-attempt count, and cost alongside the
  human's four axis scores in `scores.jsonl` turns it into an aggregable
  dataset for a future `harness score report` command. Effort: Low (schema
  addition only). Impact: Med.

**What to avoid:** installing guardrails-ai, instructor, or promptfoo as
dependencies. The domain-specific gate logic in `harness/claims.py` (locked
topics, semantic matching, warranty wording) is more precise than any generic
validator library would be for this exact problem, and `docs/HARNESS-MAP.md`
already lists `claims.py`'s gate logic as "do not rewrite." Borrow vocabulary
and file shapes, not the libraries themselves.

## 3. Agent harness / workflow projects

- **LangGraph** (now docs.langchain.com). `StateGraph` with `add_node`/
  `add_edge`, `START`/`END` markers; the pitch is combining "hand-coded,
  deterministic logic with LLM-driven decision-making in a single graph" and
  **checkpointing** so an agent "can persist through failures ... resuming
  from where they left off."
- **Prefect**. Flows are decorated Python functions; declarative
  `retries=`/`retry_delay_seconds=` and `timeout_seconds=` on a task/flow;
  automatic state tracking (Scheduled -> Pending -> Running -> terminal) and
  an events stream for monitoring.
- **OpenAI Agents SDK**. Agents/tools/handoffs plus a **guardrails** layer
  that runs "in parallel with agent execution to fail fast," and built-in
  per-run **tracing** that visualizes tool calls and handoffs.

**What to borrow (concrete):**
- *Declarative retry/budget config.* `harness/budget.py`'s caps (300s /
  220,000 tokens / 14 calls) and `MAX_REPAIR_ATTEMPTS = 2` are Python
  constants. Prefect's pattern of surfacing these as named, overridable
  config rather than buried constants suggests lifting them into
  `tenant.yaml` (a `budget:` block, tenant-overridable) the same way
  `claims/config.json` already overrides `tenant.yaml`. Effort: Low. Impact:
  Med -- lets an operator tune a slow tenant's budget without a code change.
- *Checkpoint/resume.* Today a `BudgetExceeded` or crash mid-pipeline
  discards the run's spend; `prepare_run` -> `render_pages` reruns from
  scratch next time. LangGraph's checkpoint-and-resume model suggests
  persisting `RunState` (already a real object in `harness/pipeline.py`) to
  disk after each stage, so a retried run can skip `ingest`/`ground` if
  `ad_brief.json`/`facts_pack.json` already exist and are unchanged. Effort:
  Med. Impact: Med (mainly a cost-saver on retries, not correctness).
- *A guardrails-as-parallel-gate framing for docs, not code.* The OpenAI SDK's
  "guardrails run in parallel, fail fast" line matches how `gate_ad_claims`
  already runs before `write_pages` starts. Worth citing in the public README
  as prior art for the STOP-with-no-partial-page design. Effort: Low. Impact:
  Low.

**What to avoid:** LangGraph/CrewAI/Prefect/Dagster themselves. This harness's
pipeline is eight fixed stages with two real branch points (a claims STOP, a
gate-repair loop); a graph-execution engine or a full orchestrator is
over-built for that shape (Section III, Simplicity) and would replace
`harness/pipeline.py`'s already-tested `STAGES`/`execute` registry -- which
`docs/HARNESS-MAP.md` explicitly protects ("`harness run` and `harness
workflow run` both go through it, so they cannot drift apart") -- for no
behavior change.

## 4. Multi-tenant config layouts

- **dbt**. `dbt_project.yml` (project structure, versioned, no secrets) is
  separate from `profiles.yml` (connection details, kept out of the repo);
  the project file references a **named profile**, not inline credentials.
- **Terraform workspaces**. One config, many isolated **state** files via
  `terraform.workspace`; explicitly documented as unsuitable for anything
  needing separate credentials or access boundaries -- "not appropriate for
  system decomposition."
- **Home Assistant**. `configuration.yaml` plus a separate `secrets.yaml`;
  integrations are added via UI/config without touching core code; most
  config changes reload live without a restart.
- **mkdocs**. Plugins are installable Python packages that register
  themselves via `pyproject.toml` entry points, not folders the core repo
  must know about in advance.

**What to borrow (concrete):**
- *dbt's named-profile indirection for secrets.* `tenants/<t>/.env` already
  keeps secrets out of `tenant.yaml`, which is the dbt split done correctly.
  One gap: nothing stops a future onboarding flow from writing a raw token
  into `tenant.yaml` by habit. Borrow the naming discipline explicitly in
  `docs/TENANT-ONBOARDING.md`: any new credential (Meta token, Drive OAuth
  refresh token, Shopify app secret) goes in `.env`/keyring, never
  `tenant.yaml`, and `tenant.yaml` only ever holds a *reference* (e.g.
  `meta.ad_account_id`, not `meta.access_token`). Effort: Low. Impact: Med --
  this is exactly the kind of thing that goes wrong quietly during the
  planned Meta/Drive onboarding work.
- *mkdocs' entry-points registry, for the public release -- deferred.* Today
  `discover_cartridges()` finds any `cartridges/<name>/` folder with a
  `cartridge.md`, fine for a single repo. mkdocs' pattern (a plugin is a
  separate installable package registering a `pyproject.toml` entry point) is
  the natural next step once third parties want to ship a cartridge without
  forking. Effort: Med. Impact: High, but **defer** -- per Section III
  (Simplicity), this is an "in case we need to" abstraction until a second
  cartridge author outside Peak Saunas actually exists. Flag in the roadmap
  only.
- *Confirming the current tenant-folder model beats Terraform workspaces.*
  Terraform's own docs warn workspaces are state-only isolation, not
  credential/access isolation. `tenants/<name>/` already isolates data,
  claims, brand, and (via `.env`) credentials per company -- strictly more
  isolation than a workspace gives. No change needed; cite this as validation
  that the existing design, not a workspace-style scheme, is correct for a
  system where tenants genuinely must not see each other's claims store.

**What to avoid:** Terraform-style workspaces (shared state, one set of
credentials) for anything resembling tenant separation -- wrong isolation
model for a system where `tenants/peak-saunas/claims/verified.json` must
never leak into another company's run.

## 5. Release engineering for a public Python CLI

- **Keep a Changelog**. `Added`/`Changed`/`Deprecated`/`Removed`/`Fixed`/
  `Security` sections per dated version, with an `Unreleased` section at the
  top; explicitly warns against using raw commit logs as a changelog.
- **PyPI Trusted Publishing**. OIDC-based, per-project tokens that "expire
  automatically" -- no stored API token. Requires `id-token: write` at the
  job level in the GitHub Actions workflow, and a `pypi` GitHub Environment
  configured for manual-approval before a run can publish.
- **pre-commit**. A `.pre-commit-config.yaml` listing hook repos (e.g. ruff,
  mypy) with a pinned `rev`; each hook runs in its own isolated environment.
- **License trade-off** (choosealicense.com): AGPL-3.0's distinguishing
  clause is that a modified version running as a network service must offer
  its users the corresponding source -- MIT and Apache-2.0 carry no such
  clause. Apache-2.0 adds an explicit patent grant on top of MIT's bare
  permissive terms; otherwise the practical obligations (attribution, license
  text) are the same.

**What to borrow (concrete):**
- *`CHANGELOG.md` in Keep a Changelog format*, started now, before the first
  public tag, so `v0.1.0` isn't the first entry with no history above it.
  Effort: Low. Impact: High -- required scaffolding for semantic versioning
  the task already calls out.
- *Conventional Commits + release-please.* Commit messages of the form
  `feat: ...` / `fix: ...` / `feat!: ...` let release-please open a
  standing "Release PR" that computes the next semver bump and drafts the
  changelog automatically; merging it cuts the release and tag. Effort: Low
  to adopt the commit convention, Med to wire the GitHub Action. Impact:
  High -- removes hand-maintained version bumps entirely.
- *PyPI Trusted Publishing wired to a tagged-release GitHub Actions job*,
  `id-token: write`, a `pypi` environment with required reviewers. Effort:
  Low (one workflow file). Impact: High -- this is the standard, and the
  alternative (a long-lived API token in repo secrets) is a real security
  regression for a public package.
- *pre-commit with ruff (lint + format) and mypy*, pinned revs, run in CI and
  locally. Effort: Low. Impact: Med -- catches the kind of drift
  `docs/HARNESS-MAP.md`'s "do not rewrite" sections are trying to prevent
  (e.g. a stray import reintroducing a company-specific string into
  `harness/` or `cartridges/`, which the test suite already checks for at
  runtime; pre-commit catches it before a commit lands instead).
- *Dependabot* for the handful of real dependencies (`pyproject.toml`);
  low-maintenance, standard GitHub-native config.

**Licensing recommendation:** **Apache-2.0.** MIT is simpler but gives up the
explicit patent grant Apache-2.0 adds for free -- relevant the moment this
becomes a public repo other companies' engineers touch. AGPL-3.0's network-use
clause is the wrong trade here: the SPEC.md roadmap already names a possible
future hosted offering, and AGPL would force Peak Saunas's *own* hosted
version to publish its source to every user who interacts with it over the
network -- the opposite of what a hosted-product option needs. Apache-2.0
keeps the tool freely forkable (aligned with "public GitHub release") while
leaving the door open to a separately-licensed hosted product later, which is
the standard open-core pattern. Trade-off stated plainly: Apache-2.0 does not
stop a competitor from taking the code and hosting it themselves without
contributing back -- AGPL would, at the cost of also constraining Peak
Saunas's own hosted plans. Given the roadmap explicitly wants the hosted-later
option, Apache-2.0 is the better fit.

## 6. Connector APIs for onboarding

- **Meta Marketing API.** `ads_read` (plus `ads_management` if the app also
  needs to manage the ad account) is the permission for pulling ads/creatives.
  Critically: **"If your app is only managing your ad account, standard
  access to the `ads_read` and `ads_management` permissions are sufficient"**
  -- i.e. no App Review / advanced-access submission is required for a tool
  that only ever reads *the tenant's own* ad account, which is this harness's
  actual use case. Advanced access (needed only for a multi-customer app) adds
  an ongoing bar: 500+ Marketing API calls in the last 15 days and under 15%
  error rate to keep it. No review-timeline commitment is published.
- **Google Drive API.** `drive.file` is the narrow, "non-sensitive" scope --
  per-file access to what the user opens/picks via the app, with "a more
  streamlined verification process" than `drive.readonly`, which grants
  blanket read access to the whole Drive and carries a heavier Google
  verification bar for a public OAuth client.
- **Shopify Admin API.** `fileCreate` (GraphQL) needs one of `write_files` /
  `write_themes` / `write_images`, runs async (poll `fileStatus`), and
  handles filename collisions with a configurable resolution mode.
  `pageCreate` (GraphQL) needs `write_content` or `write_online_store_pages`
  and takes `title`/`handle`/`body`/`isPublished`/`templateSuffix`.

**What to borrow (concrete) for the onboarding flow:**
- *Meta:* default the wizard to `ads_read` only, standard access, scoped to
  the tenant's own connected ad account -- skip an App Review submission flow
  entirely unless a cross-customer use case shows up later. Shrinks the
  "planned: Meta ads connector" scope in the README. Effort: Low. Impact:
  High (removes a multi-week external review dependency).
- *Drive:* onboard via `drive.file` + a Picker-style folder selection, not a
  blanket `drive.readonly` consent screen -- matches `harness/sources/drive.py`'s
  existing link-based ingestion (it already only touches files it's given a
  link to) and avoids Google's heavier verification bar for a broad scope.
  Effort: Low. Impact: Med-High.
- *Shopify:* when the publish adapter in `docs/HARNESS-MAP.md`'s "Phase 2/3"
  file-touch list gets built, request `write_content` (or
  `write_online_store_pages`) + `write_files`, and use GraphQL
  `pageCreate`/`fileCreate` rather than the REST endpoints the legacy
  `create_page.py` tool uses -- Shopify is steering new integrations to
  GraphQL, and `fileCreate`'s async + collision-handling behavior is worth
  designing around from day one. Effort: Med (already a planned file-touch).
  Impact: High.
- *Onboarding checklist* for `docs/TENANT-ONBOARDING.md`: Meta ad account ID
  + `ads_read` token (no review needed for self-managed accounts); Drive
  folder picked via `drive.file`; Shopify store domain + custom app with
  `write_content`+`write_files`. Effort: Low. Impact: High.

---

## Top 12 borrowings, ranked

| # | Borrowing | From | Effort | Impact |
|---|---|---|---|---|
| 1 | Meta onboarding defaults to `ads_read`-only, standard access, no App Review | Meta Marketing API docs | Low | High |
| 2 | PyPI Trusted Publishing (OIDC, `id-token: write`, manual-approval `pypi` env) | packaging.python.org | Low | High |
| 3 | `CHANGELOG.md` in Keep a Changelog format, started pre-v0.1.0 | keepachangelog.com | Low | High |
| 4 | Declarative eval-case files (`evals/cases/*.yaml`) + a `harness eval` report command | promptfoo, Inspect | Med | High |
| 5 | Shopify publish adapter built on GraphQL `pageCreate`/`fileCreate`, not REST | shopify.dev | Med | High |
| 6 | Drive onboarding via `drive.file` + Picker, not `drive.readonly` | Google Drive API docs | Low | Med-High |
| 7 | Conventional Commits + release-please for automated version/changelog | conventionalcommits.org, release-please | Low-Med | High |
| 8 | Secrets-in-`.env`-only discipline, `tenant.yaml` holds references not tokens | dbt profiles/project split | Low | Med |
| 9 | Budget caps (`harness/budget.py`) lifted into tenant-overridable config | Prefect retries/timeout pattern | Low | Med |
| 10 | pre-commit with pinned ruff + mypy hooks, plus Dependabot | pre-commit.com | Low | Med |
| 11 | `OnFailAction`-style vocabulary documented for the existing repair loop | guardrails-ai | Low | Low-Med |
| 12 | Checkpoint/resume `RunState` so a retried run skips completed stages | LangGraph checkpointing | Med | Med |

## Do not adopt

- **A graph/orchestrator engine (LangGraph, CrewAI, Prefect, Dagster) in
  place of `harness/pipeline.py`.** Eight fixed stages with two branch points
  do not need a general execution engine, and `docs/HARNESS-MAP.md` already
  protects the current `STAGES`/`execute` registry as load-bearing.
- **guardrails-ai / instructor / promptfoo as runtime dependencies.**
  `harness/claims.py`'s domain-specific gate (locked topics, semantic claim
  matching, warranty wording) is more precise than a generic validator
  library for this exact problem, and is explicitly flagged "do not rewrite."
  Borrow naming and file-format ideas only.
- **Guardrails Hub's install-a-validator-as-a-package model**, and mkdocs'
  entry-points plugin registry, right now. Both solve "a third party ships a
  component without forking the repo" -- a real need only once a cartridge or
  publisher adapter author outside Peak Saunas exists. Building it now is the
  "in case we need to" abstraction Section III warns against.
- **Terraform-style workspaces** as a mental model for tenant isolation --
  Terraform's own docs say workspaces aren't meant for credential/access
  separation, which is exactly what `tenants/<name>/` needs to guarantee.
- **AGPL-3.0.** Its network-use clause cuts against the roadmap's own
  "may later be offered hosted" option.

## Sources

(all fetched 2026-09-10)

- github.com/jeus0522/AI-Landing-Page-Generator -- MIT, 4 stars, single page
  type, `.env` config, no validation step.
- github.com/sachink1729/LLM-Agent-Landing-Page-Generator-CrewAI-Qdrant-Langchain
  -- Apache-2.0, 15 stars, CrewAI + Qdrant, no documented validation/publish.
- github.com/guardrails-ai/guardrails -- "Multiple validators can be
  combined together into Input and Output Guards"; Apache-2.0, ~7.4k stars.
- python.useinstructor.com -- "Automatically reask the model when
  validation fails."
- promptfoo.dev/docs/intro -- "test-driven LLM development"; declarative
  test cases, CI-friendly CLI.
- inspect.aisi.org.uk -- dataset/solver/scorer architecture; "framework for
  frontier AI evaluations."
- docs.langchain.com/oss/python/langgraph/overview -- "persist through
  failures and can run for extended periods, resuming from where they left
  off."
- docs.prefect.io/v3/concepts/flows -- "Retries can be performed on
  failure, with configurable delay and retry limits."
- openai.github.io/openai-agents-python -- guardrails "fail fast when
  checks do not pass"; built-in tracing.
- docs.getdbt.com/reference/dbt_project.yml -- project config vs.
  `profiles.yml` connection/secrets split.
- developer.hashicorp.com/terraform/language/state/workspaces -- "not
  appropriate for system decomposition or deployments requiring separate
  credentials."
- home-assistant.io/docs/configuration -- `configuration.yaml` /
  `secrets.yaml` split; live-reload without restart.
- keepachangelog.com/en/1.1.0 -- section headings and "Unreleased"
  convention.
- packaging.python.org publishing-via-github-actions guide -- Trusted
  Publishing, `id-token: write`, manual-approval `pypi` environment.
- choosealicense.com/licenses/agpl-3.0 -- network-use source-disclosure
  clause, contrasted with MIT/Apache-2.0.
- pre-commit.com -- `.pre-commit-config.yaml` structure, per-hook isolated
  environments.
- developers.facebook.com/docs/marketing-api/access -- "standard access to
  the `ads_read` and `ads_management` permissions are sufficient" for an
  app managing only its own ad account.
- developers.google.com/drive/api/guides/about-auth -- `drive.file` narrow
  per-file scope, "more streamlined verification process."
- shopify.dev/docs/api/admin-graphql/latest/mutations/fileCreate --
  `write_files`/`write_themes`/`write_images` scopes, async via
  `fileStatus`.
- shopify.dev/docs/api/admin-graphql/latest/mutations/pageCreate --
  `write_content`/`write_online_store_pages` scopes, title/handle/body/
  isPublished/templateSuffix fields.
