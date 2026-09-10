# Advertorial Environment
Provisioned 2026-09-08 on prod (deploy@peak-server, x86_64, 3 cores, 15GB RAM).
Everything lives under /home/deploy/advertorial.

## Paths & Versions
- Venv Python: `~/advertorial/.venv/bin/python` (base /usr/bin/python3, 3.14.4)
- pip 26.2.1, anthropic 1.4.0, python-dotenv 1.2.3
- cmake (pip-installed, venv-local) 4.4.3 at `~/advertorial/.venv/bin/cmake`
- whisper.cpp source: `~/advertorial/vendor/whisper.cpp` (ggml v0.23.0, commit c44b60b)
- whisper-cli: `~/advertorial/vendor/whisper.cpp/build/bin/whisper-cli` (--help OK)
- Model: `~/advertorial/models/ggml-small.en.bin` (487,614,201 bytes, matches expected)
- ffmpeg: /usr/bin/ffmpeg 8.0.1-3ubuntu2

## Transcription Timing
Fixture: `~/advertorial/tenants/peak-saunas/fixtures/hidden-costs-v2.wav` (16kHz mono), -t 3.
Wall clock: 8.422s real / 23.881s user / 0.608s sys.
Output matched `transcript-small.txt` exactly (no word diffs).

## ffmpeg mov->wav
`hidden-costs-v2.mov` -> 16kHz mono pcm_s16le wav: 883,504 bytes, matching
the existing wav fixture exactly. No errors under `-v error`.

## Anthropic SDK Smoke Test
Loaded .env via python-dotenv (never printed). Called `claude-sonnet-5`,
max_tokens=20, succeeded on first try. Response: "OK". smoke.py deleted after.

## GitOps Notes
ops-deploy.timer (root-owned, ~2min cadence) did not touch ~/advertorial
across a firing. `git -C /home/deploy status` fails: "dubious ownership"
(root .git, deploy-owned home) - not fixed; fix touches ~/.gitconfig,
outside advertorial.
