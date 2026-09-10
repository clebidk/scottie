---
name: ingest
purpose: Convert a raw ad (video, still image, or text) into a structured ad brief.
inputs:
  - source ad (video file, still image, text, or Google Drive id)
outputs:
  - ad_brief.json
model: claude-sonnet-5
---

## What it does
Resolves the input reference (local path or Drive id) to a file. For video,
runs ffmpeg to extract audio then whisper.cpp to produce a transcript. For a
still image, makes one Claude vision call to describe it. For plain text,
passes it through unchanged. Feeds the transcript or text into one Claude
call that extracts the ad brief.

## Rules
- Exactly one Claude call produces ad_brief.json; the transcript/vision step
  does not count claims on its own.
- ad_brief.json must contain: hook, promise, angle, claims_made,
  speaker_experience, features_shown, objections_raised, cta, tone,
  speaker_pov, source_file, input_type, transcript_or_text.
- Drop any claim mentioning EMF from claims_made before the file is written.
  The claims gate never sees an EMF claim from this stage.
- Never fabricate a claim the source did not make. If unsure, omit it —
  grounding and the claims gate cannot rescue a claim invented here.
- Do not resolve or validate claims against the claims store; that is the
  grounder's job.

## Done when
ad_brief.json exists, matches the key list above, contains no EMF-related
claim, and every entry in claims_made traces to something actually said or
shown in the source.
