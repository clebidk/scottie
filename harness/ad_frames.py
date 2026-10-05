"""Cycle 79: one still of the ad's own creator for the page's first screen.

The owner approved first screens that show a real person (docs/FIXLOG.md
cycle 79; GA4: the best paid page opens with a real face and an open loop).
The person is the ad's creator: the owner confirmed the usage rights cover
landing pages. This module makes that still at build time.

Video ad:
  1. ffmpeg cuts CANDIDATES frames at even points of the video (not the
     first or last 8%, where cards and end slates sit).
  2. Pillow scores each one: sharpness (edge variance) and exposure. The
     blurry and the near-black/near-white ones drop out.
  3. ONE vision call (tenant.yaml models.vision, default Haiku) sees the
     best VISION_MAX of them at VISION_EDGE px and returns, per frame: a
     face visible?, burned-in caption size, a brand mark visible?, plus the
     best frame and the face centre in it. About 1.5k input tokens: under
     $0.005 per ad (the brief's cap is $0.01).
     Without a client (or when the call fails) the sharpest frame wins and
     the record says method "heuristic".
  4. The chosen frame is saved as <run>/ad-frame/frame.jpg (long edge
     FRAME_MAX_EDGE), a square crop around the face as avatar.jpg (the
     story style's round avatar), and the record as ad-frame.json
     (provenance: source file, time stamp, every candidate's scores, the
     vision answer, method, model, cost).
Image ad: the ad image itself, ONLY when the vision check finds a person,
no retired brand mark and no claim text the page cannot verify (a rating,
a review count, a price, a percentage). The 2026-10-05 "regret" still shows
the retired mark and a third-party review count: rejected,
and the record keeps the reason.
Text ad, or nothing usable: no frame. The first screen then falls back
(harness/first_screen.py: face -> story without a face, or display).
"""
import io
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from .budget import BudgetExceeded

DIRNAME = "ad-frame"
RECORD = "ad-frame.json"
FRAME_FILE = "frame.jpg"
AVATAR_FILE = "avatar.jpg"
CANDIDATES = 8
VISION_MAX = 6
VISION_EDGE = 384
FRAME_MAX_EDGE = 1600
AVATAR_EDGE = 320
MIN_SHARPNESS = 6.0
EXPOSURE_RANGE = (35, 225)
DEFAULT_VISION_MODEL = "claude-haiku-4-5"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

FRAME_ID = "asset-adframe"
AVATAR_ID = "asset-adframe-avatar"


# ---------------------------------------------------------------------------
# Loading what a run already has
# ---------------------------------------------------------------------------

def frame_dir(run_dir):
    return Path(run_dir) / DIRNAME


def load(run_dir):
    """The run's ad-frame record with absolute file paths, or None when the
    run has no usable frame (none made, rejected, or files missing)."""
    path = frame_dir(run_dir) / RECORD
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not record.get("usable"):
        return None
    frame = frame_dir(run_dir) / FRAME_FILE
    avatar = frame_dir(run_dir) / AVATAR_FILE
    if not frame.exists():
        return None
    return dict(record, frame_path=str(frame), avatar_path=str(avatar) if avatar.exists() else None)


def page_assets(run_dir):
    """{asset_id: asset dict} for the frame and its avatar, ready for
    render.download_asset (local_path), or {}."""
    record = load(run_dir)
    if record is None:
        return {}
    alt = "A still from the ad video"
    out = {FRAME_ID: {"id": FRAME_ID, "kind": "ad-frame", "local_path": record["frame_path"],
                      "url": f"{DIRNAME}/{FRAME_FILE}", "alt": alt}}
    if record.get("avatar_path"):
        out[AVATAR_ID] = {"id": AVATAR_ID, "kind": "ad-frame", "local_path": record["avatar_path"],
                          "url": f"{DIRNAME}/{AVATAR_FILE}", "alt": alt}
    return out


# ---------------------------------------------------------------------------
# Candidates and cheap scores
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def video_duration(video, ffmpeg_bin, run=subprocess.run):
    """Seconds, from ffmpeg's own banner (no ffprobe dependency)."""
    result = run([ffmpeg_bin, "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    m = _DURATION_RE.search((result.stderr or "") + (result.stdout or ""))
    if not m:
        raise ValueError(f"no duration in ffmpeg output for {video}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def candidate_times(duration, n=CANDIDATES):
    """n time stamps between 8% and 92% of the video."""
    if duration <= 0:
        return [0.0]
    lo, hi = 0.08 * duration, 0.92 * duration
    if n == 1:
        return [round((lo + hi) / 2, 2)]
    return [round(lo + (hi - lo) * i / (n - 1), 2) for i in range(n)]


def extract_candidates(video, out_dir, ffmpeg_bin, *, n=CANDIDATES, run=subprocess.run):
    """[(time, path)] for the frames ffmpeg could cut."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(candidate_times(video_duration(video, ffmpeg_bin, run=run), n), 1):
        path = out_dir / f"candidate-{i}.jpg"
        run([ffmpeg_bin, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(path)], capture_output=True, text=True)
        if path.exists() and path.stat().st_size > 0:
            frames.append((t, path))
    return frames


def frame_scores(path):
    """{"sharpness", "brightness"}: edge variance on a 320px grey copy (a
    blurry or motion-smeared frame scores low) and mean grey level."""
    with Image.open(path) as im:
        grey = im.convert("L")
        grey.thumbnail((320, 320))
        edges = grey.filter(ImageFilter.FIND_EDGES)
        w, h = edges.size
        edges = edges.crop((2, 2, max(w - 2, 3), max(h - 2, 3)))  # the filter's own border is not detail
        return {"sharpness": round(ImageStat.Stat(edges).stddev[0], 2),
                "brightness": round(ImageStat.Stat(grey).mean[0], 1)}


def usable_by_scores(scores):
    lo, hi = EXPOSURE_RANGE
    return scores["sharpness"] >= MIN_SHARPNESS and lo <= scores["brightness"] <= hi


# ---------------------------------------------------------------------------
# The vision call
# ---------------------------------------------------------------------------

FRAMES_SYSTEM = """You pick one still from a short vertical video ad for a home sauna company. The still will sit at the top of a landing page as a photo of the person who made the ad, with their own words as the caption.

You see several numbered frames. For EACH frame report:
- face: true when one person's face is clearly visible (eyes visible, not turned away, not covered, not cut off by the frame edge);
- caption: "none", "small" or "large" -- burned-in subtitles or on-screen text; "large" when text covers more than about 15% of the frame;
- logo: true when any brand mark or logo is visible (on a product, a sign, clothing or an overlay);
- sharp: true when the face (or the main subject) is in focus with no motion blur.

Then pick the best frame for a portrait photo of the creator: face visible, sharp, natural expression, no large caption, no logo if possible. If no frame shows a face, set "best" to null. Give the face centre in the best frame as fractions of its width and height.

Also report "pronoun": "she" or "he" ONLY when the creator's presented gender is unambiguous across the frames; otherwise "unclear". Never guess.

Return ONLY JSON: {"frames": [{"index": 1, "face": true, "caption": "none", "logo": false, "sharp": true}, ...], "best": 1, "face_center": [0.5, 0.3], "pronoun": "unclear", "reason": "<one short sentence>"}"""

IMAGE_SYSTEM = """You check one still image ad for a home sauna company before it is shown on the company's landing page as a photo of the ad's creator.

Report:
- person: true when a real person's face is clearly visible;
- old_logo: true when the image shows {retired_mark} (on the product, its glass, a panel, or as an overlay). Any other brand mark: report it under "logos";
- logos: the brand marks you can see, as short descriptions ([] when none);
- claim_text: every piece of on-image text that states a rating, a review count, a number of customers, a price, a percentage, a discount, an award, a health or medical outcome, or names a review site (copy it word for word; [] when none);
- face_center: the face centre as fractions of width and height, or null;
- pronoun: "she" or "he" ONLY when the person's presented gender is unambiguous, else "unclear". Never guess.

Return ONLY JSON: {{"person": true, "old_logo": false, "logos": [], "claim_text": [], "face_center": [0.5, 0.3], "pronoun": "unclear"}}"""
DEFAULT_RETIRED_MARK = "the company's retired logo"


def _b64(data):
    import base64

    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def small_jpeg(path, edge=VISION_EDGE):
    with Image.open(path) as im:
        im = flatten(im)
        im.thumbnail((edge, edge), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


def flatten(im):
    """RGB copy of `im`; transparency goes onto white."""
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return im.convert("RGB")


def _call(client, model, system, content, budget, log, *, max_tokens=500):
    from .anthropic_client import thinking_kwargs
    from .asset_describe import _pricing_model_id

    budget.check()
    response = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": content}], **thinking_kwargs(model),
    )
    usage = response.usage
    budget.record_call(usage.input_tokens, usage.output_tokens)
    log.call("ad_frame", _pricing_model_id(model), usage.input_tokens, usage.output_tokens,
             cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
             cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0)
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    return text, usage


def _cost(model, usage):
    from . import pricing
    from .asset_describe import _pricing_model_id

    try:
        return round(pricing.calculate_cost(_pricing_model_id(model), usage.input_tokens, usage.output_tokens), 5)
    except Exception:
        return None


def _center(value):
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) for v in value)):
        return [round(min(max(float(v), 0.0), 1.0), 3) for v in value]
    return None


def parse_frames_response(text, count):
    from .jsonutil import extract_json

    try:
        data = extract_json(text)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
        return None
    frames = {}
    for f in data["frames"]:
        if isinstance(f, dict) and isinstance(f.get("index"), int) and 1 <= f["index"] <= count:
            frames[f["index"]] = {
                "face": f.get("face") is True,
                "caption": f.get("caption") if f.get("caption") in ("none", "small", "large") else "large",
                "logo": f.get("logo") is True,
                "sharp": f.get("sharp") is not False,
            }
    best = data.get("best")
    best = best if isinstance(best, int) and best in frames else None
    return {"frames": frames, "best": best, "face_center": _center(data.get("face_center")),
            "pronoun": _pronoun(data.get("pronoun")), "reason": str(data.get("reason") or "")[:200]}


def _pronoun(value):
    """'she' | 'he' | None -- anything else (unclear, missing) is None."""
    value = str(value or "").strip().lower()
    return value if value in ("she", "he") else None


def choose(vision, ordered):
    """The index (1-based into `ordered`) to use, by the vision answer: its
    own pick when that frame has a face and no large caption; else the first
    frame (sharpest first) that has a face, no large caption and no logo;
    else one with a face and no large caption. When EVERY face frame carries
    a large burned-in caption (a captioned-throughout video -- the athlete
    ad of 2026-10-05), the caption is not avoidable: the vision pick (or the
    first face frame) with no logo. None when no frame shows a face."""
    frames = vision["frames"]

    def ok(i, *, logo_ok, caption_ok=False):
        f = frames.get(i)
        return bool(f and f["face"] and f["sharp"] and (caption_ok or f["caption"] != "large")
                    and (logo_ok or not f["logo"]))

    if vision["best"] and ok(vision["best"], logo_ok=True):
        return vision["best"]
    for logo_ok in (False, True):
        for i in range(1, len(ordered) + 1):
            if ok(i, logo_ok=logo_ok):
                return i
    if vision["best"] and ok(vision["best"], logo_ok=False, caption_ok=True):
        return vision["best"]
    for i in range(1, len(ordered) + 1):
        if ok(i, logo_ok=False, caption_ok=True):
            return i
    return None


def parse_image_response(text):
    from .jsonutil import extract_json

    try:
        data = extract_json(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    claims = [str(c) for c in (data.get("claim_text") or []) if str(c).strip()]
    return {"person": data.get("person") is True, "old_logo": data.get("old_logo") is True,
            "logos": [str(x) for x in (data.get("logos") or [])][:6], "claim_text": claims[:10],
            "face_center": _center(data.get("face_center")), "pronoun": _pronoun(data.get("pronoun"))}


def image_rejection(check):
    """Why an image ad may not be used as the first-screen photo, or None."""
    if check is None:
        return "the vision check gave no usable answer"
    if check["old_logo"]:
        return "shows the retired brand mark"
    if check["logos"]:
        # The model answered old_logo false but still saw a mark (seen live:
        # "Mountain line-drawing logo on sauna glass door"); an image ad with
        # any mark is not worth the risk.
        return "shows a brand mark: " + "; ".join(check["logos"][:3])
    if check["claim_text"]:
        return "carries claim text the page cannot verify: " + "; ".join(check["claim_text"][:3])
    if not check["person"]:
        return "shows no person"
    return None


# ---------------------------------------------------------------------------
# Writing the run asset
# ---------------------------------------------------------------------------

def _save_frame(src, out_dir, face_center):
    with Image.open(src) as im:
        im = flatten(im)
        im.thumbnail((FRAME_MAX_EDGE, FRAME_MAX_EDGE), Image.LANCZOS)
        im.save(out_dir / FRAME_FILE, format="JPEG", quality=85)
        w, h = im.size
        cx, cy = face_center or (0.5, 0.33)
        side = int(min(w, h) * 0.6)
        left = int(min(max(cx * w - side / 2, 0), w - side))
        top = int(min(max(cy * h - side / 2, 0), h - side))
        avatar = im.crop((left, top, left + side, top + side)).resize((AVATAR_EDGE, AVATAR_EDGE), Image.LANCZOS)
        avatar.save(out_dir / AVATAR_FILE, format="JPEG", quality=85)
        return w, h


def object_position(face_center):
    cx, cy = face_center or (0.5, 0.33)
    return f"{round(cx * 100)}% {round(cy * 100)}%"


def _write_record(out_dir, record):
    (out_dir / RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return record


def build_from_video(video, run_dir, *, ffmpeg_bin, client=None, model=DEFAULT_VISION_MODEL, budget=None,
                     log=None, run=subprocess.run, source=None):
    out_dir = frame_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cand_dir = out_dir / "candidates"
    record = {"version": 1, "usable": False, "source": source or {"file": Path(video).name, "kind": "video"},
              "method": None, "model": None, "cost_usd": 0.0, "candidates": [], "chosen": None}
    try:
        frames = extract_candidates(video, cand_dir, ffmpeg_bin, run=run)
    except Exception as e:  # a missing or unreadable video never fails a run
        record["rejected"] = f"ffmpeg could not cut frames: {e}"
        return _write_record(out_dir, record)
    scored = []
    for t, path in frames:
        s = frame_scores(path)
        record["candidates"].append({"t": t, "file": f"candidates/{path.name}", **s, "usable": usable_by_scores(s)})
        if usable_by_scores(s):
            scored.append((t, path, s))
    if not scored:
        record["rejected"] = "no candidate frame was sharp and exposed well enough"
        return _write_record(out_dir, record)
    scored.sort(key=lambda x: -x[2]["sharpness"])
    shown = scored[:VISION_MAX]
    pick, face_center = 0, None
    if client is not None and budget is not None and log is not None:
        content = []
        for i, (t, path, _s) in enumerate(shown, 1):
            content += [{"type": "text", "text": f"Frame {i} (t={t:.1f}s):"}, _b64(small_jpeg(path))]
        content.append({"type": "text", "text": "Return the JSON object described in your instructions."})
        try:
            text, usage = _call(client, model, FRAMES_SYSTEM, content, budget, log)
            record["model"], record["cost_usd"] = model, _cost(model, usage)
            vision = parse_frames_response(text, len(shown))
        except Exception as e:
            if isinstance(e, BudgetExceeded):
                raise
            vision = None
            record["vision_error"] = str(e)[:200]
        if vision is not None:
            record["method"] = "vision"
            record["vision"] = {"frames": {str(k): v for k, v in vision["frames"].items()},
                                "best": vision["best"], "reason": vision["reason"]}
            # review fix 1: the speaker's pronoun, only when the check was unambiguous
            record["speaker_pronoun"] = vision["pronoun"]
            chosen = choose(vision, shown)
            if chosen is None:
                record["rejected"] = "no frame shows the creator's face clearly without a large caption"
                return _write_record(out_dir, record)
            pick = chosen - 1
            face_center = vision["face_center"] if chosen == vision["best"] else None
    if record["method"] is None:
        record["method"] = "heuristic"  # sharpest well-exposed frame; face not checked
    t, path, s = shown[pick]
    w, h = _save_frame(path, out_dir, face_center)
    record.update(usable=True, chosen={"t": t, "file": f"candidates/{path.name}", **s},
                  face_center=face_center, object_position=object_position(face_center),
                  width=w, height=h, face_checked=record["method"] == "vision")
    if log:
        log.event("ad_frame", f"frame at {t:.1f}s ({record['method']}); cost ${record['cost_usd'] or 0:.4f}")
    return _write_record(out_dir, record)


def build_from_image(image, run_dir, *, client=None, model=DEFAULT_VISION_MODEL, budget=None, log=None,
                     source=None, retired_mark=None):
    out_dir = frame_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"version": 1, "usable": False, "source": source or {"file": Path(image).name, "kind": "image"},
              "method": "vision", "model": model, "cost_usd": 0.0, "candidates": [], "chosen": None}
    if client is None or budget is None or log is None:
        # An image ad is only used after the check; with no check, never.
        record.update(method=None, model=None, rejected="no vision check available")
        return _write_record(out_dir, record)
    content = [_b64(small_jpeg(image, edge=768)),
               {"type": "text", "text": "Return the JSON object described in your instructions."}]
    try:
        system = IMAGE_SYSTEM.format(retired_mark=retired_mark or DEFAULT_RETIRED_MARK)
        text, usage = _call(client, model, system, content, budget, log)
        record["cost_usd"] = _cost(model, usage)
        check = parse_image_response(text)
    except Exception as e:
        if isinstance(e, BudgetExceeded):
            raise
        check = None
        record["vision_error"] = str(e)[:200]
    record["check"] = check
    record["speaker_pronoun"] = (check or {}).get("pronoun")
    why = image_rejection(check)
    if why:
        record["rejected"] = why
        if log:
            log.event("ad_frame", f"image ad not used on the page: {why}")
        return _write_record(out_dir, record)
    w, h = _save_frame(image, out_dir, check["face_center"])
    record.update(usable=True, chosen={"file": Path(image).name}, face_center=check["face_center"],
                  object_position=object_position(check["face_center"]), width=w, height=h, face_checked=True)
    return _write_record(out_dir, record)


def build_for_input(path, run_dir, **kwargs):
    """The run asset for one ad input file (video or image); a text ad, or
    any other file, gets a record saying there is no frame."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        kwargs.pop("retired_mark", None)
        return build_from_video(path, run_dir, **kwargs)
    kwargs.pop("ffmpeg_bin", None)
    kwargs.pop("run", None)
    retired_mark = kwargs.pop("retired_mark", None)
    if ext in IMAGE_EXT:
        kwargs["retired_mark"] = retired_mark
        return build_from_image(path, run_dir, **kwargs)
    out_dir = frame_dir(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return _write_record(out_dir, {"version": 1, "usable": False, "source": {"file": path.name, "kind": "text"},
                                   "rejected": "a text ad has no picture"})
