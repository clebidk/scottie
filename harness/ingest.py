"""Turn one ad (video, still, or text) into ad_brief.json.

input -> resolve (local path or Drive link/id) -> detect type -> transcribe
(whisper) / read on-image text (Claude vision) / passthrough -> one Claude
call -> validated ad_brief dict.
"""
import base64
import json
import mimetypes
import subprocess
from pathlib import Path

from . import tenant as tenant_mod
from .anthropic_client import thinking_kwargs
from .jsonutil import extract_json
from .sources.drive import download_drive_file, parse_drive_id

# Re-exported: the pipeline and the renderer both reach Drive through ingest.
__all__ = ["download_drive_file", "parse_drive_id", "resolve_input", "run_ingest"]

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".webm"}
STILL_EXT = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_EXT = {".txt", ".md"}

# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------

def resolve_input(input_arg, workdir):
    """Resolve the CLI input argument to a local file path: an existing local
    path is used as-is; otherwise it's treated as a Drive link/id and
    downloaded into workdir."""
    local = Path(input_arg)
    if local.exists():
        return local

    file_id = parse_drive_id(input_arg)
    if file_id is None:
        raise ValueError(f"could not resolve input as a local path or a Drive link/id: {input_arg}")
    return download_drive_file(file_id, workdir)


def detect_type(path):
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in STILL_EXT:
        return "still"
    if ext in TEXT_EXT:
        return "text"
    raise ValueError(f"unrecognized input file extension: {ext!r} (path={path})")


# ---------------------------------------------------------------------------
# Video -> transcript (ffmpeg + whisper.cpp)
# ---------------------------------------------------------------------------

def whisper_initial_prompt(tenant=None):
    """whisper-cli's own vocabulary has no idea a tenant's brand or model names
    exist -- one fixture was heard as "Sonna" for "Sauna". whisper.cpp's initial
    prompt biases decoding toward a short vocabulary list without changing the
    model; it is not a transcript prefix, so it never appears in the output.
    Comes from tenant.yaml's whisper_prompt."""
    tenant = tenant or tenant_mod.active()
    return tenant.get("whisper_prompt") or ""


def __getattr__(name):
    """WHISPER_INITIAL_PROMPT stays readable as a module attribute (it was one
    before it became tenant data), resolved through the active tenant."""
    if name == "WHISPER_INITIAL_PROMPT":
        return whisper_initial_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def video_to_transcript(path, workdir, ffmpeg_bin, whisper_bin, whisper_model, initial_prompt=None):
    path = Path(path)
    workdir = Path(workdir)
    wav_path = workdir / (path.stem + ".wav")

    subprocess.run(
        [
            ffmpeg_bin, "-y", "-v", "error",
            "-i", str(path),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(wav_path),
        ],
        check=True, capture_output=True, text=True,
    )

    cmd = [whisper_bin, "-m", str(whisper_model), "-f", str(wav_path), "-nt", "-np", "-t", "3"]
    prompt = whisper_initial_prompt() if initial_prompt is None else initial_prompt
    if prompt:
        cmd += ["--prompt", prompt]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Still -> on-image text (Claude vision)
# ---------------------------------------------------------------------------

VISION_SYSTEM = (
    "You transcribe on-image text exactly as it appears. Output ALL text visible "
    "in the image, verbatim, grouped by visual block (e.g. headline, bullet list, "
    "badge, price pill). One block per line group. Do not add commentary, layout "
    "description, or any text that is not literally printed in the image."
)


def still_to_text(path, client, model, budget, log):
    path = Path(path)
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    media_type = mimetypes.guess_type(str(path))[0] or "image/png"

    budget.check()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        # Verbatim transcription task, no reasoning needed -- disable thinking
        # when the model accepts the param (Haiku 4.5 doesn't; see
        # anthropic_client.thinking_kwargs).
        system=VISION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                    {"type": "text", "text": "Transcribe all on-image text verbatim, grouped by visual block."},
                ],
            }
        ],
        **thinking_kwargs(model),
    )
    usage = response.usage
    budget.record_call(usage.input_tokens, usage.output_tokens)
    log.call(
        "ingest.vision", model, usage.input_tokens, usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
    )

    text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(text_parts).strip()


# ---------------------------------------------------------------------------
# ad_brief.json
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "hook": str,
    "promise": str,
    "angle": str,
    "claims_made": list,
    "speaker_experience": list,
    "features_shown": list,
    "objections_raised": list,
    "cta": str,
    "tone": str,
    "speaker_pov": str,
    "source_file": str,
    "input_type": str,
    "transcript_or_text": str,
    # Fix cycle 16 item 7 (Thursday queue item 3, "audience named in H1"): the
    # specific audience the ad itself names, e.g. "busy parents", "apartment
    # dwellers" -- "" (never omitted) when the ad doesn't name one. A
    # cartridge's headline rule uses this to name the reader directly instead
    # of a generic "you" when the ad gave it something specific to say.
    "audience": str,
}

AD_BRIEF_SYSTEM = """You analyze a direct-response ad transcript (or on-image ad text) and extract a structured brief as JSON.

Output ONLY a single JSON object with exactly these keys, no markdown fences, no commentary:
- hook: string, the opening line/moment that grabs attention
- promise: string, the outcome or benefit the ad promises
- angle: string, the ad's core angle/positioning, in one sentence
- claims_made: array of strings. Each item is a distinct FACTUAL or COMPARATIVE assertion the ad makes -- a claim that could be checked against a source: a price, a spec, a comparison to a competitor, a health/medical statement, a review count or rating. Do NOT put first-person anecdotes or the speaker's own experience here.
- speaker_experience: array of strings. Each item is a first-person anecdote or subjective experience statement the speaker makes about themselves (e.g. "I hated calling for a price", "it made my mornings better"). These are not factual claims and are never fact-checked.
- features_shown: array of strings, product features/attributes shown or mentioned
- objections_raised: array of strings, objections or pain points the ad raises (usually before answering them)
- cta: string, the call to action
- tone: string, one or two words describing the ad's tone
- speaker_pov: "first_person" or "brand" -- whether the speaker talks as an individual ("I", "me") or as the brand
- source_file: string, the input filename you were given
- input_type: string, one of "video", "still", "text" as given to you
- transcript_or_text: string, the full transcript or on-image text you were given, verbatim
- audience: string, a short (2-4 word) name for the specific audience the ad itself names or clearly addresses (e.g. "busy parents", "apartment dwellers", "new homeowners") -- "" (empty string) if the ad speaks to a general reader with no specific audience named or clearly implied. Do not invent one; only report an audience the ad itself actually gives you.

Rules:
- Every string in claims_made must be independently checkable against an outside source (a number, a named comparison, a specific claim).
- Anything that is just the speaker describing their own feelings or experience goes into speaker_experience, never claims_made. This includes a generalization about unnamed "other brands"/"most companies" when it is really just the speaker recounting their own shopping experience (e.g. "so many brands make me do this") rather than a specific, sourceable claim about a named competitor.
- A statement the speaker frames as their own research, estimate, or hedge -- "I've been seeing...", "around $X", "say $X", "I did the math", "I realized..." -- goes into speaker_experience, never claims_made, even when it includes a number. This is the speaker's own approximation, not an independently checkable fact, and it is never fact-checked. Example: "I've been seeing that the average unlimited sauna membership is around $200 a month. So say $2,400 a year." is speaker_experience. By contrast, a plain factual assertion with no hedge -- "infrared sauna is on sale right now for $5,450" -- is a claim: put it in claims_made.
- Output valid JSON only. No prose before or after, no markdown fences."""


# Cycle 68: an ad pulled from Meta (harness/meta_ingest.py) sits in its inbox
# directory next to an ad.json whose media_file names it. That ad.json holds
# the copy Meta shows around the media -- primary text above it, headline and
# description under it, the button -- which a still or a video with no voice
# over often carries all of the offer in.
AD_COPY_FIELDS = ("primary_text", "headline", "description", "cta")

AD_COPY_RULES = """The input also has ad_copy: the text the ad platform shows around the video or image (primary_text above it, headline and description below it, cta on the button). It is part of the same ad, written by the brand:
- Use it for hook, promise, angle, claims_made, features_shown, objections_raised, audience, and cta, together with the transcript or on-image text. When the media has no call to action, cta comes from ad_copy.cta or the headline.
- It is brand copy, not the speaker: never put ad_copy text into speaker_experience, and do not count it when you decide speaker_pov.
- transcript_or_text is still only the transcript or on-image text you were given, verbatim. Never copy ad_copy into it."""


def load_ad_copy(path, log=None):
    """{primary_text, headline, description, cta} from the ad.json next to
    `path` when that ad.json's media_file is this file; None otherwise. A
    Meta CTA type ("SHOP_NOW") comes back as words ("Shop now")."""
    path = Path(path)
    sidecar = path.parent / "ad.json"
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError) as e:
        if log is not None:
            log.event("ingest", f"ignored unreadable ad copy sidecar {sidecar}: {type(e).__name__}")
        return None
    if not isinstance(data, dict) or data.get("media_file") != path.name:
        return None
    copy = {}
    for key in AD_COPY_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            copy[key] = value.strip()
    cta = copy.get("cta", "")
    if cta and cta.replace("_", "").isupper():
        copy["cta"] = cta.replace("_", " ").capitalize()
    return copy or None


def validate_ad_brief(data):
    if not isinstance(data, dict):
        raise ValueError("ad_brief is not a JSON object")
    errors = []
    for key, typ in REQUIRED_KEYS.items():
        if key not in data:
            errors.append(f"missing key: {key}")
        elif not isinstance(data[key], typ):
            errors.append(
                f"key {key!r} has wrong type: expected {typ.__name__}, got {type(data[key]).__name__}"
            )
    if "speaker_pov" in data and data["speaker_pov"] not in ("first_person", "brand"):
        errors.append("speaker_pov must be 'first_person' or 'brand'")
    if errors:
        raise ValueError("; ".join(errors))


def build_ad_brief(*, transcript_or_text, source_file, input_type, client, model, budget, log, ad_copy=None):
    stage = "ingest.ad_brief"
    payload = {"source_file": source_file, "input_type": input_type, "transcript_or_text": transcript_or_text}
    system = AD_BRIEF_SYSTEM
    if ad_copy:
        payload["ad_copy"] = ad_copy
        system = AD_BRIEF_SYSTEM + "\n\n" + AD_COPY_RULES
    user_msg = json.dumps(payload)

    last_error = None
    for attempt in range(2):
        budget.check()
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            # Structured extraction task, no reasoning needed -- disable
            # thinking when the model accepts the param (Haiku 4.5 doesn't;
            # see anthropic_client.thinking_kwargs).
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            **thinking_kwargs(model),
        )
        usage = response.usage
        budget.record_call(usage.input_tokens, usage.output_tokens)
        log.call(
            stage, model, usage.input_tokens, usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
        )

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            data = extract_json(text)
            validate_ad_brief(data)
            return data
        except Exception as e:  # json.JSONDecodeError or ValueError
            last_error = e
            log.event(stage, f"invalid ad_brief on attempt {attempt + 1}: {e}")
            continue

    raise ValueError(f"ad_brief validation failed after retry: {last_error}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def drop_banned_topic_claims(ad_brief, log, terms=None):
    """A tenant's absolute word bans apply to the ad itself, not just the page:
    an ad claim or feature mentioning one is dropped from ad_brief (logged,
    never a STOP), so the page simply never covers that angle. Terms come from
    the tenant's vocab.yaml. Returns the list of dropped strings."""
    from . import vocab

    if terms is None:
        terms = vocab.EMF_TERMS
    terms = [t.lower() for t in terms]
    dropped = []
    for key in ("claims_made", "features_shown"):
        kept = []
        for item in ad_brief.get(key, []):
            if isinstance(item, str) and any(t in item.lower() for t in terms):
                dropped.append(item)
                log.event("ingest", f"dropped banned-topic claim: {item}")
            else:
                kept.append(item)
        ad_brief[key] = kept
    return dropped


def run_ingest(*, input_arg, workdir, client, model, budget, log, ffmpeg_bin, whisper_bin, whisper_model):
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    budget.check()
    path = resolve_input(input_arg, workdir)
    input_type = detect_type(path)
    log.event("ingest", f"resolved input {input_arg!r} -> {path} (type={input_type})")
    ad_copy = load_ad_copy(path, log)
    if ad_copy:
        log.event("ingest", f"ad copy from {Path(path).parent / 'ad.json'}: {', '.join(sorted(ad_copy))}")

    if input_type == "video":
        transcript_or_text = video_to_transcript(path, workdir, ffmpeg_bin, whisper_bin, whisper_model)
        log.event("ingest", f"whisper transcript: {len(transcript_or_text)} chars")
    elif input_type == "still":
        transcript_or_text = still_to_text(path, client, model, budget, log)
        log.event("ingest", f"vision transcript: {len(transcript_or_text)} chars")
    else:
        transcript_or_text = Path(path).read_text().strip()
        log.event("ingest", f"text passthrough: {len(transcript_or_text)} chars")

    ad_brief = build_ad_brief(
        transcript_or_text=transcript_or_text,
        source_file=Path(path).name,
        input_type=input_type,
        client=client,
        model=model,
        budget=budget,
        log=log,
        ad_copy=ad_copy,
    )
    # The key name is part of ad_brief.json's shape (it is written to disk and
    # sent to the writer), so it keeps the historical spelling; the function
    # behind it does not have to.
    ad_brief["_dropped_emf_claims"] = drop_banned_topic_claims(ad_brief, log)
    return ad_brief
