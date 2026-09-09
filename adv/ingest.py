"""Turn one ad (video, still, or text) into ad_brief.json.

input -> resolve (local path or Drive link/id) -> detect type -> transcribe
(whisper) / read on-image text (Claude vision) / passthrough -> one Claude
call -> validated ad_brief dict.
"""
import base64
import http.cookiejar
import mimetypes
import re
import subprocess
import urllib.request
from pathlib import Path

from .jsonutil import extract_json

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".webm"}
STILL_EXT = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_EXT = {".txt", ".md"}

_FILE_D_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")
_ID_PARAM_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)")
_BARE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{15,}$")


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------

def parse_drive_id(input_arg):
    """Extract a Google Drive file id from a /file/d/<id>/, ?id=<id>, or bare-id
    string. Returns None if none of the three patterns match."""
    m = _FILE_D_RE.search(input_arg)
    if m:
        return m.group(1)
    m = _ID_PARAM_RE.search(input_arg)
    if m:
        return m.group(1)
    if _BARE_ID_RE.match(input_arg):
        return input_arg
    return None


def download_drive_file(file_id, dest_dir):
    """Download a public Google Drive file by id, without OAuth, following the
    HTML confirm-token interstitial for large files."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    resp = opener.open(url, timeout=60)
    content_type = resp.headers.get("Content-Type", "")
    data = resp.read()
    headers = resp.headers

    if "text/html" in content_type or data[:512].lstrip()[:15].lower().startswith(b"<!doctype html") or b"<html" in data[:2000].lower():
        html = data.decode("utf-8", errors="ignore")
        confirm_m = re.search(r'confirm=([0-9A-Za-z_-]+)', html)
        uuid_m = re.search(r'name="uuid"\s+value="([^"]+)"', html)
        token = confirm_m.group(1) if confirm_m else "t"
        uuid = uuid_m.group(1) if uuid_m else ""
        url2 = (
            "https://drive.usercontent.google.com/download"
            f"?id={file_id}&export=download&confirm={token}&uuid={uuid}"
        )
        resp = opener.open(url2, timeout=60)
        data = resp.read()
        headers = resp.headers

    cd = headers.get("Content-Disposition", "")
    fname_m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    filename = fname_m.group(1) if fname_m else file_id

    dest = dest_dir / filename
    dest.write_bytes(data)
    return dest


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

def video_to_transcript(path, workdir, ffmpeg_bin, whisper_bin, whisper_model):
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

    result = subprocess.run(
        [whisper_bin, "-m", str(whisper_model), "-f", str(wav_path), "-nt", "-np", "-t", "3"],
        check=True, capture_output=True, text=True,
    )
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
        thinking={"type": "disabled"},  # verbatim transcription task, no reasoning needed
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
    )
    usage = response.usage
    budget.record_call(usage.input_tokens, usage.output_tokens)
    log.call("ingest.vision", model, usage.input_tokens, usage.output_tokens)

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

Rules:
- Every string in claims_made must be independently checkable against an outside source (a number, a named comparison, a specific claim).
- Anything that is just the speaker describing their own feelings or experience goes into speaker_experience, never claims_made. This includes a generalization about unnamed "other brands"/"most companies" when it is really just the speaker recounting their own shopping experience (e.g. "so many brands make me do this") rather than a specific, sourceable claim about a named competitor.
- Output valid JSON only. No prose before or after, no markdown fences."""


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


def build_ad_brief(*, transcript_or_text, source_file, input_type, client, model, budget, log):
    import json

    stage = "ingest.ad_brief"
    user_msg = json.dumps(
        {"source_file": source_file, "input_type": input_type, "transcript_or_text": transcript_or_text}
    )

    last_error = None
    for attempt in range(2):
        budget.check()
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            thinking={"type": "disabled"},  # structured extraction task, no reasoning needed
            system=AD_BRIEF_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        usage = response.usage
        budget.record_call(usage.input_tokens, usage.output_tokens)
        log.call(stage, model, usage.input_tokens, usage.output_tokens)

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

def drop_emf_claims(ad_brief, log):
    """Fix cycle 2 item 7: EMF is absolute -- an ad claim or feature that
    mentions it is dropped from ad_brief (logged, never a STOP), so the page
    simply never covers that angle. Returns the list of dropped strings."""
    dropped = []
    for key in ("claims_made", "features_shown"):
        kept = []
        for item in ad_brief.get(key, []):
            if isinstance(item, str) and "emf" in item.lower():
                dropped.append(item)
                log.event("ingest", f"dropped EMF claim: {item}")
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
    )
    ad_brief["_dropped_emf_claims"] = drop_emf_claims(ad_brief, log)
    return ad_brief
