"""Cycle 96: photos added and curated on the review site's Photos page
(docs/LISTICLE-SITE.md "Photos", docs/IMAGES.md section 12).

An added photo goes into the same tenant photo library as `harness photos
import` (brand/photo-library.json + photo-library/<id>.jpg) and through the
same steps, so the cycle 75 matcher, the cycle 93 resolver and the first
screen use it like any other library photo:

  save_batch    checks one upload (many files) and keeps the files under
                tenants/<t>/jobs/photo-uploads/<batch>/ for one
                photos_import job (nothing slow runs in a web request)
  run_import    the job: import (photo_library.import_paths: derivatives +
                manifest rows), measure (images.measure_file), then per
                photo the cycle 75 product tag (photo_library.tag_photos,
                only an untagged photo) and the cycle 93 look tag
                (images.tag_missing, only when shot or quality is missing).
                Progress "k of n tagged" in the job's `progress` column. The
                model cost is reserved against and recorded in the tenant's
                daily cap like every model stage.
  edit_photo    one person's change to one photo (features, shot, mood,
                model, excluded, old mark, focal point, delete to .trash,
                restore), written under the manifest lock. Person values are
                kept when a photo is tagged or measured again.
  used_in       the runs that picked a photo (<run>/.image-selection.json)
  coverage      the cycle 93 audit plus a plain-English "what to shoot next"

No cache anywhere: every reader (ground.facts_for, images.candidates, this
page) loads photo-library.json from disk, so a new photo is offered to the
next page that is rendered.
"""
import datetime
import json
import os
import re
import secrets
import shutil
from pathlib import Path

from PIL import Image

from . import asset_describe
from . import budget as budget_mod
from . import images
from . import photo_library as pl
from . import textutil
from .anthropic_client import make_client
from .budget import Budget, BudgetExceeded
from .log import RunLog

MB = 1024 * 1024
MAX_FILE_BYTES = 40 * MB
MAX_BATCH_FILES = 30
MAX_BATCH_BYTES = 400 * MB
# Pull from Drive: at most this many new files per pull.
MAX_DRIVE_FILES = 60
PENDING_DIRNAME = "photo-uploads"
JOB_TARGET = "photos:library"
TRASH_DIRNAME = ".trash"
USED_IN_MAX = 5
_CHUNK = MB
# The page's cost line. From harness/pricing.py: the cycle 75 product tag
# (claude-sonnet-5; ~1.1k text + ~5.8k reference-strip tokens, read from the
# prompt cache after the first photo, + ~1.4k photo tokens, ~350 out) is
# about $0.008, the cycle 93 look tag (claude-haiku-4-5, ~1.9k in, ~150 out)
# about $0.003. The first photo of a batch writes the cache: about $0.026.
# Cycle 75 measured $1.13 for 172 product calls ($0.0066 a call).
TAG_COST_PER_PHOTO_USD = 0.01
_BATCH_RE = re.compile(r"^[a-f0-9]{16}$")

# extension on the uploaded name -> the extension its bytes must have
ALLOWED_EXTENSIONS = {".jpg": ".jpg", ".jpeg": ".jpg", ".png": ".png", ".webp": ".webp",
                      ".heic": ".heic", ".heif": ".heic"}
_HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")


class PhotoUploadRejected(Exception):
    """A refused upload: `status` is the HTTP status (400 or 413)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class PhotoEditRefused(Exception):
    """A refused edit; nothing was written."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def sniff_photo(head):
    """The extension a file's first bytes say it is (.jpg .png .webp .heic), or None."""
    head = bytes(head or b"")
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS:
        return ".heic"
    return None


def heic_supported():
    """True when Pillow can open HEIC here: pillow-heif is installed (it is
    a project dependency since cycle 96) and registered as Pillow's opener."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 -- not installed, or a broken build: refuse HEIC
        pass
    return ".heic" in Image.registered_extensions()


def pending_dir(tenant, batch):
    if not _BATCH_RE.match(str(batch or "")):
        raise ValueError(f"bad photo batch name {batch!r}")
    return Path(tenant.jobs_dir) / PENDING_DIRNAME / batch


def _base_name(filename):
    return str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


def _stored_stem(name):
    stem = textutil.safe_filename(os.path.splitext(name)[0], fallback="photo")[:80] or "photo"
    # A person's file name never claims a Drive id (photo_library._DRIVE_ID_RE).
    return re.sub(r"^asset-drive-", "", stem) or "photo"


def save_batch(tenant, files, *, label_model=None):
    """Checks and saves one upload: `files` is [(stream, filename)]. The type
    comes from each file's first bytes and must agree with its extension. All
    or nothing: any refusal removes the whole batch. Returns {"batch",
    "files": [{"name", "file", "bytes"}], "bytes"}."""
    files = [(s, n) for s, n in files if s is not None and _base_name(n)]
    if not files:
        raise PhotoUploadRejected("Choose one or more photos.")
    if len(files) > MAX_BATCH_FILES:
        raise PhotoUploadRejected(f"At most {MAX_BATCH_FILES} photos at once; you chose {len(files)}.", 413)
    batch = secrets.token_hex(8)
    directory = pending_dir(tenant, batch)
    directory.mkdir(parents=True)
    saved, total, heic_ok = [], 0, None
    try:
        for index, (stream, filename) in enumerate(files, 1):
            name = _base_name(filename)
            given = os.path.splitext(name)[1].lower()
            if given not in ALLOWED_EXTENSIONS:
                raise PhotoUploadRejected(f"{name}: not a photo this page takes (jpg, png, webp or heic).")
            head = stream.read(64)
            ext = sniff_photo(head)
            if ext is None or ext != ALLOWED_EXTENSIONS[given]:
                raise PhotoUploadRejected(f"{name}: the content is not a valid {given[1:]} photo "
                                          "(or does not match its extension).")
            if ext == ".heic":
                heic_ok = heic_supported() if heic_ok is None else heic_ok
                if not heic_ok:
                    raise PhotoUploadRejected(
                        f"{name}: this server cannot read HEIC photos yet. Export it as JPEG and upload it again "
                        "(iPhone: Settings > Camera > Formats > Most Compatible, or share it as JPEG).")
            dest = directory / f"{index:03d}-{_stored_stem(name)}{ext}"
            size = 0
            with open(dest, "wb") as fh:
                chunk = head
                while chunk:
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise PhotoUploadRejected(f"{name} is over the {MAX_FILE_BYTES // MB} MB limit for one "
                                                  "photo.", 413)
                    if total > MAX_BATCH_BYTES:
                        raise PhotoUploadRejected(f"The photos are over {MAX_BATCH_BYTES // MB} MB together. "
                                                  "Upload fewer at once.", 413)
                    fh.write(chunk)
                    chunk = stream.read(_CHUNK)
            saved.append({"name": name[:200], "file": dest.name, "bytes": size})
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    (directory / "batch.json").write_text(json.dumps({"files": saved, "label_model": label_model}, indent=1))
    return {"batch": batch, "files": saved, "bytes": total}


def new_drive_batch(tenant):
    """An empty pending folder for a "Pull from Drive" job."""
    batch = secrets.token_hex(8)
    pending_dir(tenant, batch).mkdir(parents=True)
    return batch


def drive_folder(tenant):
    """tenant.yaml photo_library.drive_folder (a Drive folder id or URL), or None."""
    value = tenant.get("photo_library.drive_folder") if tenant else None
    return str(value).strip() if value else None


def _download_drive(tenant, folder, directory, *, log):
    """Downloads the files of the Drive folder (and its subfolders) that are
    not in the library yet into `directory`, named
    asset-drive-<id>__<name> so the import records the Drive id (and with
    it the folder model label brand/assets.json gives, cycle 75). Uses the
    brand import's public-folder listing and download (no OAuth)."""
    from . import brand_import

    known = pl.library_drive_ids(tenant)
    entries = [e for e in brand_import.enumerate_source(drive_folder=folder)
               if os.path.splitext(e["name"])[1].lower() in ALLOWED_EXTENSIONS and e.get("id") not in known]
    if len(entries) > MAX_DRIVE_FILES:
        log(f"{len(entries)} new files in the Drive folder; taking the first {MAX_DRIVE_FILES}")
        entries = entries[:MAX_DRIVE_FILES]
    files = []
    for entry in brand_import.download_incoming(entries, directory):
        path = Path(entry["path"])
        with open(path, "rb") as fh:
            ext = sniff_photo(fh.read(64))
        if ext is None or path.stat().st_size > MAX_FILE_BYTES:
            log(f"skipped {entry['name']}: not a photo, or over {MAX_FILE_BYTES // MB} MB")
            path.unlink()
            continue
        dest = directory / f"asset-drive-{entry['id']}__{_stored_stem(entry['name'])}{ext}"
        path.replace(dest)
        files.append({"name": entry["name"][:200], "file": dest.name, "bytes": dest.stat().st_size})
    (directory / "manifest.json").unlink(missing_ok=True)
    (directory / "batch.json").write_text(json.dumps({"files": files, "label_model": None}, indent=1))
    return files


# ---------------------------------------------------------------------------
# The photos_import job
# ---------------------------------------------------------------------------

def _row(tenant, pid):
    return pl.photos_by_id(pl.load_manifest(tenant)).get(pid)


def _done(row):
    """Tagged as far as this library goes: the product tag, and the look
    fields for a photo a page may use."""
    return bool(row and row.get("tags")) and (not pl._usable(row) or not images.needs_vision(row))


def run_import(tenant, payload, *, progress=lambda _step: None, log=print):
    """The photos_import job (harness/jobs.py handle_photos_import). Raises
    jobs.JobFailed when nothing could be imported, or when the daily cap
    stops the tagging (the imported photos stay, untagged ones are simply
    not used until tagged). The pending folder is removed whatever happens."""
    from .jobs import JobFailed

    directory = pending_dir(tenant, payload["batch"])
    by = payload.get("by") or ""
    origin = "drive" if payload.get("kind") == "drive" else "upload"
    try:
        if origin == "drive":
            progress("downloading from Drive")
            directory.mkdir(parents=True, exist_ok=True)
            _download_drive(tenant, payload["folder"], directory, log=log)
        meta = {}
        if (directory / "batch.json").is_file():
            meta = json.loads((directory / "batch.json").read_text())
        paths = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in pl.IMPORT_EXTS) \
            if directory.is_dir() else []
        if not paths:
            if origin == "drive":
                return {"photos": 0, "added": 0, "existing": 0, "duplicates": 0, "skipped": 0, "tagged": 0,
                        "untagged": 0, "cost_usd": 0.0, "ids": [], "origin": origin}
            raise JobFailed("the uploaded photos are gone (was the job run twice?); upload them again")
        if any(p.suffix.lower() == ".heic" for p in paths):
            heic_supported()
        progress(f"importing {len(paths)} photo{'s' if len(paths) != 1 else ''}")
        counts, results = pl.import_paths(tenant, paths, log=log, restore_removed=True)
        names = {f["file"]: f["name"] for f in meta.get("files") or []}
        ids = []
        for r in results:
            if r["id"] is None or r["id"] in ids:
                continue
            ids.append(r["id"])
            if r["status"] == "added":
                fields = {"source_name": names.get(r["path"].name) or r["path"].name, "origin": origin,
                          "added_by": by}
                if meta.get("label_model"):
                    fields["label_model"] = meta["label_model"]
                pl.update_photo(tenant, r["id"], lambda row, f=fields: row.update(f))
        if not ids:
            raise JobFailed(f"no photo could be read ({counts['skipped']} skipped); see the worker log")
        progress("measuring")
        for pid in ids:
            row = _row(tenant, pid)
            path = pl.local_file(tenant, row) if row else None
            if path is not None and path.exists():
                measured = images.measure_file(path)
                pl.update_photo(tenant, pid, lambda r, m=measured: r.__setitem__(
                    images.FIELD, images.merge_image_tags(r, m)))
        result = dict(counts, photos=len(ids), ids=ids[:60], origin=origin)
        result.update(_tag(tenant, ids, progress=progress, log=log))
        if result.get("stopped"):
            raise JobFailed(f"daily cap reached after {result['tagged']} of {len(ids)} tagged: {result['stopped']}. "
                            "The photos are in the library; an untagged photo is not used yet. Upload the same "
                            "photos again tomorrow to tag the rest (they are not added twice).")
        return result
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _tag(tenant, ids, *, progress, log):
    """Tags `ids` one photo at a time (product tag, then look fields), with
    progress "k of n tagged". Returns {"tagged", "untagged", "failed",
    "cost_usd", "stopped"}."""
    n = len(ids)
    todo = [pid for pid in ids if not _done(_row(tenant, pid))]
    progress(f"{n - len(todo)} of {n} tagged")
    if not todo:
        return {"tagged": n, "untagged": 0, "failed": 0, "cost_usd": 0.0, "stopped": None}
    run_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-photos-upload"
    runlog = RunLog(run_id, Path(tenant.runs_dir) / f"{run_id}.log")
    budget = Budget(wall_s=3600, tokens=5_000_000, calls=3 * len(todo) + 5)
    today = datetime.date.today().isoformat()
    stopped, failed, cost = None, 0, 0.0
    try:
        try:
            budget_mod.reserve_spend(tenant, run_id=run_id, today_iso=today, log=runlog)
        except BudgetExceeded as e:
            return {"tagged": n - len(todo), "untagged": len(todo), "failed": 0, "cost_usd": 0.0,
                    "stopped": str(e)}
        try:
            client = make_client()
            refs = pl.build_reference_images(tenant, log=lambda m: runlog.event("photos_tag", m))
            product_model = tenant.get("photo_library.tag_model") or pl.DEFAULT_TAG_MODEL
            look_model = asset_describe.vision_model_for(tenant)
            forbidden = asset_describe.forbidden_terms_for(tenant)
            done_before = n - len(todo)
            for i, pid in enumerate(todo, 1):
                row = _row(tenant, pid)
                if row is not None and not row.get("tags"):
                    failed += pl.tag_photos(tenant, client=client, model=product_model, budget=budget, log=runlog,
                                            refs=refs, ids={pid}, forbidden_terms=forbidden)["failed"]
                    row = _row(tenant, pid)
                if row is not None and pl._usable(row) and images.needs_vision(row):
                    failed += images.tag_missing(tenant, client=client, model=look_model, budget=budget,
                                                 log=runlog, ids={pid})["failed"]
                progress(f"{done_before + i} of {n} tagged")
        except BudgetExceeded as e:
            stopped = str(e)
        cost = runlog.cost_estimate()
        budget_mod.record_spend(tenant, run_id=run_id, cost=cost, today_iso=today, log=runlog)
    finally:
        runlog.close()
    tagged = sum(1 for pid in ids if _done(_row(tenant, pid)))
    log(f"photos: {tagged} of {n} tagged, cost ${cost:.4f}")
    return {"tagged": tagged, "untagged": n - tagged, "failed": failed, "cost_usd": round(cost, 4),
            "stopped": stopped}


# ---------------------------------------------------------------------------
# Edits (one photo, one person)
# ---------------------------------------------------------------------------

MODEL_ANY = "any"


def model_choice(row):
    """The photo's model as the Model menu shows it: a model slug, "unknown",
    "any" (model-agnostic), or "" (not tagged yet, nothing set)."""
    tags = row.get("tags")
    if tags:
        if tags["product"] != pl.UNKNOWN:
            return tags["product"]
        return MODEL_ANY if tags["model_agnostic"] else pl.UNKNOWN
    for c in reversed(row.get("corrections") or []):
        if isinstance(c, dict) and c.get("field") == "product":
            return MODEL_ANY if c.get("agnostic") else c.get("value") or ""
    return ""


def parse_features(text, vocabulary):
    """A comma list typed by a person -> the features_visible list (in the
    vocabulary's order). "red light panel" is read as "red-light-panel".
    Raises PhotoEditRefused naming any word that is not in the vocabulary."""
    wanted, unknown = set(), []
    for part in str(text or "").split(","):
        word = re.sub(r"[\s_]+", "-", part.strip().lower())
        if not word:
            continue
        if word in vocabulary:
            wanted.add(word)
        else:
            unknown.append(part.strip())
    if unknown:
        raise PhotoEditRefused(f"Not a tag this library uses: {', '.join(unknown)}. Use: {', '.join(vocabulary)}.")
    return [f for f in vocabulary if f in wanted]


def current_features(row):
    if row.get("tags"):
        return list(row["tags"]["features_visible"])
    feats = []
    return pl.apply_corrections(row, {"features_visible": feats})["features_visible"]


def _ensure_measured(tenant, row):
    it = row.get(images.FIELD) or {}
    if it.get("measured") == images.MEASURE_VERSION and not images.validate_image_tags(it):
        return
    path = pl.local_file(tenant, row)
    if not path.exists():
        raise PhotoEditRefused("This photo's file is not on this server, so it cannot be measured; set this "
                               "after `harness photos import`.")
    row[images.FIELD] = images.merge_image_tags(row, images.measure_file(path))


def _set_shot(tenant, row, value):
    _ensure_measured(tenant, row)
    it = row[images.FIELD]
    if value:
        it["shot"], it["shot_source"] = value, "person"
    else:  # back to automatic: the vision tagger fills it again
        it["shot"] = None
        it.pop("shot_source", None)


def _set_mood(tenant, row, value):
    _ensure_measured(tenant, row)
    it = row[images.FIELD]
    if value:
        it["mood"], it["mood_source"] = value, "person"
    else:
        path = pl.local_file(tenant, row)
        if path.exists():
            it["mood"] = images.measure_file(path)["mood"]
        it.pop("mood_source", None)


def _set_model(row, value, by):
    agnostic = value == MODEL_ANY
    product = pl.UNKNOWN if value in (MODEL_ANY, pl.UNKNOWN) else value
    row.setdefault("corrections", []).append({"field": "product", "value": product, "agnostic": agnostic,
                                              "by": by, "at": _now()})
    if row.get("tags"):
        row["tags"] = pl.apply_corrections(row, dict(row["tags"]))
    it = row.get(images.FIELD)
    if isinstance(it, dict):
        it["model"] = None if product == pl.UNKNOWN else product


def _set_features(row, wanted, by):
    have = current_features(row)
    removed = [f for f in have if f not in wanted]
    added = [f for f in wanted if f not in have]
    row.setdefault("corrections", []).append({"field": "features_visible", "removed": removed, "added": added,
                                              "by": by, "at": _now()})
    if row.get("tags"):
        row["tags"] = pl.apply_corrections(row, dict(row["tags"]))
    return removed, added


def trash_dir(tenant):
    return pl.files_dir(tenant) / TRASH_DIRNAME


def edit_photo(tenant, pid, action, form, *, by):
    """Applies one edit to photo `pid` and writes the manifest (under the
    lock, atomically; a change that would make the manifest invalid is
    refused and nothing is written). `form` is the posted fields. Returns
    [(audit action, detail)] -- one per changed field (an empty list when
    nothing changed). Raises PhotoEditRefused or KeyError (no such photo)."""
    vocabulary = pl.features_for(tenant)
    models = pl.model_slugs(tenant)
    changes = []

    def apply(row):
        if row.get("removed") and action != "restore":
            raise PhotoEditRefused("This photo is in the trash. Restore it first.")
        if action == "save":
            if "features" in form:
                wanted = parse_features(form.get("features"), vocabulary)
                if wanted != current_features(row):
                    removed, added = _set_features(row, wanted, by)
                    changes.append(("photo_tags", {"removed": removed, "added": added}))
            shot = (form.get("shot") or "").strip()
            if "shot" in form and shot != ((row.get(images.FIELD) or {}).get("shot") or ""):
                if shot and shot not in images.SHOTS:
                    raise PhotoEditRefused(f"Unknown shot {shot!r}.")
                _set_shot(tenant, row, shot)
                changes.append(("photo_shot", {"shot": shot or "automatic"}))
            mood = (form.get("mood") or "").strip()
            it = row.get(images.FIELD) or {}
            current_mood = it.get("mood") if it.get("mood_source") == "person" else ""
            if "mood" in form and mood != current_mood:
                if mood and mood not in images.MOODS:
                    raise PhotoEditRefused(f"Unknown mood {mood!r}.")
                _set_mood(tenant, row, mood)
                changes.append(("photo_mood", {"mood": mood or "measured"}))
            model = (form.get("model") or "").strip()
            if "model" in form and model and model != model_choice(row):
                if model not in models and model not in (pl.UNKNOWN, MODEL_ANY):
                    raise PhotoEditRefused(f"Unknown model {model!r}.")
                _set_model(row, model, by)
                changes.append(("photo_model", {"model": model}))
        elif action in ("excluded", "old_mark"):
            value = str(form.get("value") or "") == "1"
            if action == "excluded":
                if bool(row.get("excluded")) != value:
                    row["excluded"] = value
                    changes.append(("photo_excluded", {"excluded": value}))
            else:
                if not row.get("tags"):
                    raise PhotoEditRefused("This photo is not tagged yet; mark the old logo after tagging.")
                if bool(row["tags"].get("old_logo_visible")) != value:
                    row["tags"]["old_logo_visible"] = value
                    changes.append(("photo_old_mark", {"old_logo_visible": value}))
        elif action == "focal":
            try:
                x, y = float(form.get("x")), float(form.get("y"))
            except (TypeError, ValueError):
                raise PhotoEditRefused("The focal point needs x and y between 0 and 1.") from None
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise PhotoEditRefused("The focal point needs x and y between 0 and 1.")
            _ensure_measured(tenant, row)
            it = row[images.FIELD]
            it["focal"], it["focal_source"] = {"x": round(x, 2), "y": round(y, 2)}, "person"
            changes.append(("photo_focal", {"x": round(x, 2), "y": round(y, 2)}))
        elif action == "delete":
            src = pl.local_file(tenant, row)
            if src.exists():
                trash_dir(tenant).mkdir(parents=True, exist_ok=True)
                src.replace(trash_dir(tenant) / row["file"])
            row["removed"] = {"by": by, "at": _now(), "was_excluded": bool(row.get("excluded"))}
            row["excluded"] = True
            changes.append(("photo_delete", {"file": f"{pl.FILES_DIRNAME}/{TRASH_DIRNAME}/{row['file']}"}))
        elif action == "restore":
            removed = row.pop("removed", None)
            if removed is None:
                return
            trashed = trash_dir(tenant) / row["file"]
            if trashed.exists():
                trashed.replace(pl.local_file(tenant, row))
            row["excluded"] = bool(removed.get("was_excluded"))
            changes.append(("photo_restore", {}))
        else:
            raise PhotoEditRefused(f"Unknown action {action!r}.")

    try:
        pl.update_photo(tenant, pid, apply, validate=True)
    except pl.ManifestInvalid as e:
        raise PhotoEditRefused(f"That change would make the library invalid: {e}") from None
    return changes


# ---------------------------------------------------------------------------
# Used in, coverage
# ---------------------------------------------------------------------------

def _picked_ids(data):
    """[(cartridge, [ids])] from one .image-selection.json: each cartridge's
    used ids, plus the cycle 93 slot picks."""
    out = []
    for key, value in data.items():
        if key not in ("matches", "slots") and isinstance(value, list):
            out.append((key, [str(v) for v in value]))
    for cartridge, slots in (data.get("slots") or {}).items():
        ids = [p.get("id") for s in slots or [] for p in s.get("picks") or [] if isinstance(p, dict)]
        out.append((cartridge, [str(i) for i in ids if i]))
    return out


def used_in(tenant, limit=USED_IN_MAX):
    """{photo id: [(run id, cartridge)]} newest run first, at most `limit`
    runs per photo."""
    from . import ground

    out_dir = Path(tenant.out_dir)
    if not out_dir.is_dir():
        return {}
    files = []
    for run in out_dir.iterdir():
        path = run / ground._SELECTION_STATE_FILENAME
        if path.is_file():
            files.append((path.stat().st_mtime, run))
    out = {}
    for _mtime, run in sorted(files, key=lambda t: t[0], reverse=True):
        for cartridge, ids in _picked_ids(ground.load_used_asset_ids(run)):
            for pid in ids:
                if not pid.startswith(pl.ID_PREFIX):
                    continue
                runs = out.setdefault(pid, [])
                if len(runs) < limit and (run.name, cartridge) not in runs:
                    runs.append((run.name, cartridge))
    return out


# What a person should shoot for each cycle 93 audit want (images.AUDIT_WANTS).
SHOOT_ADVICE = {
    "full-bleed hero": "Wide landscape photos of a whole sauna in a real room or yard, at least 1600 px wide, "
                       "for full-width heroes.",
    "dark full-bleed hero": "The same wide shot at dusk or with the room lights low, the cabin glowing from "
                            "inside, at least 1600 px wide.",
    "dark detail-macro": "Close-ups in low, warm light: heater panels, bench edge, wood grain, door handle.",
    "bright detail-macro": "Close-ups in daylight: control panel, outlet plug, wood grain, glass door.",
    "dark room": "Inside the cabin with the lights low and the heaters on.",
    "bright room": "Inside the cabin in daylight, door open, bench and panels in view.",
    "dark exterior": "The sauna from outside at dusk or night, lit from inside.",
    "lifestyle-person": "A person using the sauna: sitting on the bench, stepping in or out, towel on.",
    "dark lifestyle-person": "A person in the sauna in low, warm light.",
}
ENOUGH = 3       # images.audit calls fewer than 3 a gap

RULES = (
    "Real photos only. An AI image is used only when the tenant allows AI renders, and always ranks last.",
    "At least 1600 px wide (landscape) for a full-width hero.",
    "No white studio background for a full-width hero: shoot the sauna in a real room or yard.",
    "The new mark or no mark. A photo that shows the old logo is never used at the top of a page.",
)


def coverage(tenant, manifest=None):
    """The cycle 93 audit of the library on this server, the photos per
    model, and "what to shoot next" (most needed first)."""
    manifest = pl.load_manifest(tenant) if manifest is None else manifest
    files = pl.files_dir(tenant)
    report = images.audit(manifest, files_dir=files if files.is_dir() else None)
    per_model = {slug: 0 for slug in pl.model_slugs(tenant)}
    agnostic = 0
    for p in manifest.get("photos", []):
        if not pl._usable(p) or (files.is_dir() and not (files / p["file"]).exists()):
            continue
        if p["tags"]["product"] in per_model:
            per_model[p["tags"]["product"]] += 1
        elif p["tags"]["model_agnostic"]:
            agnostic += 1
    shoot = []
    for slug, n in sorted(per_model.items(), key=lambda kv: kv[1]):
        if n == 0:
            shoot.append(f"The {slug.replace('-', ' ').title()}: no photo of it yet, so its pages use the store "
                         "images. Any real photo of the whole unit helps most.")
    for want in sorted(report["wants"], key=lambda w: w["n"]):
        if want["n"] < ENOUGH:
            need = ENOUGH - want["n"]
            shoot.append(f"{need} {want['label']} photo{'s' if need != 1 else ''}: {SHOOT_ADVICE[want['label']]}")
    return {"report": report, "per_model": per_model, "agnostic": agnostic, "shoot_next": shoot, "rules": RULES}
