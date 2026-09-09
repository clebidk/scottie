# advertorial

Turns one ad (video, still, or text) into three publishable landing pages for
Peak Saunas, each in a different style, matched to the ad's angle, with every
claim traceable to a verified source. See `docs/SPEC.md` for the full contract.

## Pipeline

```
input -> ingest -> ad_brief.json -> ground -> facts_pack.json -> claims_gate -> writer x3 -> page.json x3 -> renderer -> out/<run>/<cartridge>/index.html
                                                                    | STOP (unmatched claim)
                                                                    -> unmatched_claims.json
```

## Run it

On prod (`ssh prod`, project dir `/home/deploy/advertorial`):

```
.venv/bin/adv run <input>                 # local path or a Google Drive link/id
.venv/bin/adv run <input> --cartridges article,product-page,longform --seed 42 --product <slug>
.venv/bin/adv ingest <input>               # ad_brief.json only, for debugging
```

`adv run` STOPs (exit 2) if any ad claim can't be matched to `claims/verified.json`,
and writes `unmatched_claims.json` into the run's output directory instead of a
partial page. Exit 3 means a budget cap (wall clock / tokens / Claude calls) was
hit -- also never a partial page.

## Add a claim

```
.venv/bin/adv claims add "Peak ships free on every order." --category trust --source https://peaksaunas.com/policies/shipping-policy [--approved-by Caleb]
.venv/bin/adv claims list
```

This appends to `claims/verified.json`. `claims/pending.json` holds ad claims that
appeared in creative but aren't yet sourced/approved (see that file for the current
list -- medical-grade wording, the review rating, the EMF comparison, the "4-in-1"
framing).

## Review a run

```
.venv/bin/adv review out/<run-id>   # writes <cartridge>-review.html per cartridge, images inlined as data URIs
make review RUN=<run-id> [OUT=<local-path>]   # runs the above on prod, rsyncs the review.html files back
```

## Score a run

```
.venv/bin/adv score out/<run-id> --angle 4 --brand 5 --claims 5 --publish 4 [--by Caleb] [--note "..."]
```

Appends to `evals/scores.jsonl`.

## Where things live

- `adv/` -- the harness (ingest, ground, claims gate, writer, renderer, budget, log, CLI).
- `cartridges/<name>/` -- `cartridge.md` (voice/structure rules), `schema.json`
  (page.json shape), `template.html` (Jinja), `rubric.md` (10-point check, not
  executed in V1), `exemplars/` (optional, up to 2 used per write call).
- `claims/products.json` -- scraped from `https://peaksaunas.com/products.json`.
- `claims/verified.json` -- claims with a source URL; the only things the writer
  can cite. Add to it with `adv claims add`.
- `claims/pending.json` -- ad claims seen in creative that need Caleb's sign-off
  before they can move to `verified.json`.
- `brand/` -- owned by a different agent (tokens.json, base.css, byline.html,
  NOTES.md). The renderer uses `brand/base.css` and `brand/byline.html` when they
  exist and falls back to `adv/fallback.css` / a built-in byline block (with a
  logged warning) when they don't.
- `out/<run-id>/` -- one folder per run: `ad_brief.json`, `facts_pack.json`,
  `REVIEW.md`, `unmatched_claims.json` (only on a STOP), and `<cartridge>/index.html`
  + `page.json` per cartridge written.
- `runs/<run-id>.log` -- per-run log: stages, model calls, token usage, seed,
  cartridges chosen, gate result, budget totals, an estimated cost line.
- `evals/scores.jsonl` -- human scores from `adv score`.

## Tests

```
make test
```

Creates `.venv-local/`, installs `pytest jinja2 anthropic python-dotenv`, and runs
the suite. Every test injects a fake Anthropic client (`tests/conftest.py`) --
no network calls, no real whisper/ffmpeg (the CLI dry-run test uses the `.txt`
fixture, which takes the passthrough ingest path).

## Deploy

```
make deploy   # rsync adv/ cartridges/ claims/ brand/ tests/ docs/ pyproject.toml Makefile to prod
make install  # pip install -e . && pip install jinja2, on prod
make run INPUT=fixtures/hidden-costs-v2.mov
make pull-out # rsync prod's out/ back to ./out/
```
