"""Cycle 112: which harness version made each run, and the data behind the
review site's /versions timeline and /showcase page (harness/site_versions.py).

  - current(): {commit, date, cycle} of the repo this process runs from: git
    short HEAD, its commit date, and the newest "## Cycle N" heading of
    docs/FIXLOG.md at HEAD. Cached per process. runstate.init_state writes it
    into every new state.json as "harness_version".
  - backfill(): runs from before cycle 112 get a derived "harness_version"
    (derived: true). The cycle is the newest cycle that was on master at the
    run's start time: a cycle's time is when its first FIXLOG heading reached
    master (first-parent git history), else its FIXLOG date at 00:00. The
    commit is the newest first-parent commit at or before that time.
  - Archives (tenant.yaml versions.archives: read-only folders of older runs)
    are never written: their runs are described in
    tenants/<t>/runs/versions-index.json.
  - timeline() / showcase(): the rows and cards the two pages show.
  - thumb(): a run page's first phone screen (390 x 844, shown at 240 wide),
    cached under tenants/<t>/runs/thumbs/.
"""
import bisect
import datetime
import functools
import json
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from . import runstate, textutil

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXLOG_REL = "docs/FIXLOG.md"
INDEX_NAME = "versions-index.json"
THUMBS_DIR = "thumbs"
SCREEN = (390, 844)
THUMB_SIZE = (240, 520)
RECENT_S = 600  # backfill leaves a state.json changed in the last 10 minutes alone (a run may be writing it)

# tenant.yaml versions.archives {key: folder}: read-only folders of older
# runs. A folder's runs are <folder>/out/* when it has out/, else <folder>/*;
# its A/B records are <folder>/abtests/*.json; PUBLISHED-STORE-PAGES.txt maps
# runs to store pages. tenant.yaml versions.milestones {cycle: label}: the
# labels on the timeline.
PUBLISHED_FILE = "PUBLISHED-STORE-PAGES.txt"

LISTICLE_STYLES = ("reasons", "mistakes", "questions", "myths", "tested")
LISTICLE_HEROES = ("face", "story", "display")
PASSED_STATES = ("needs_review", "approved", "published", "rejected")
_FINANCING = re.compile(r"financ|per month|/mo\b|monthly|\bapr\b|bread pay", re.I)

_HEADING = re.compile(r"^## Cycle (\d+)([a-z]?)\b(.*)$")
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_RUN_TIME = re.compile(r"^(\d{8})-(\d{4})(\d{2})?(?:-|$)")


# ---------------------------------------------------------------------------
# FIXLOG cycles and git
# ---------------------------------------------------------------------------

def cycle_key(cycle):
    """"104b" -> (104, "b"); sorts cycles by number, then letter."""
    m = re.match(r"(\d+)([a-z]?)$", str(cycle or ""))
    return (int(m.group(1)), m.group(2)) if m else (-1, "")


def _clean(text):
    text = re.sub(r"\s*[,;]\s*(?=[,;)]|$)", "", _DATE.sub("", text))
    return text.strip(" -—:;,()").strip()


def _summary(rest):
    """A heading's one-line summary: the text in its first parentheses
    without the date, else the text before them, else the text after them."""
    text = rest.strip()
    start = text.find("(")
    if start < 0:
        return _clean(text)
    end = text.rfind(")") if text.endswith(")") else text.find(")", start)
    inner = _clean(text[start + 1:end if end > start else len(text)])
    return inner or _clean(text[:start]) or _clean(text[end + 1:] if end > start else "")


def parse_fixlog(text):
    """{cycle: {"cycle", "date", "summary"}} for every "## Cycle N" heading.
    A cycle with several headings (110 A, 110 B, ...) keeps its first
    heading's summary and its earliest date."""
    cycles = {}
    for line in (text or "").splitlines():
        m = _HEADING.match(line)
        if not m:
            continue
        cycle = m.group(1) + m.group(2)
        dates = _DATE.findall(m.group(3))
        entry = cycles.setdefault(cycle, {"cycle": cycle, "date": None, "summary": _summary(m.group(3))})
        if dates and (entry["date"] is None or dates[0] < entry["date"]):
            entry["date"] = dates[0]
    return cycles


def _git(repo, *args, timeout=60):
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _local(iso):
    """A git ISO time as a naive local datetime (run ids and state.json use local time)."""
    try:
        at = datetime.datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return at.astimezone().replace(tzinfo=None) if at.tzinfo else at


def fixlog_text(repo=REPO_ROOT):
    path = Path(repo) / FIXLOG_REL
    return path.read_text(errors="ignore") if path.is_file() else ""


@functools.lru_cache(maxsize=None)
def _current(repo):
    commit, _, date = _git(repo, "log", "-1", "--format=%h %cI").strip().partition(" ")
    cycles = parse_fixlog(_git(repo, "show", f"HEAD:{FIXLOG_REL}") or fixlog_text(repo))
    return (commit or None, date[:10] or None, max(cycles, key=cycle_key) if cycles else None)


def current(repo=None):
    """{"commit", "date", "cycle"} of the harness this process runs from
    (cached per process; a deploy restarts the services)."""
    commit, date, cycle = _current(str(repo or REPO_ROOT))
    return {"commit": commit, "date": date, "cycle": cycle}


def heading_times(repo=REPO_ROOT):
    """{cycle: local datetime}: when each cycle's first FIXLOG heading reached
    master (first-parent history of HEAD). {} without git."""
    out = _git(repo, "log", "--first-parent", "-m", "--reverse", "--format=>%cI", "-p", "-U0", "--", FIXLOG_REL)
    times, at = {}, None
    for line in out.splitlines():
        if line.startswith(">"):
            at = _local(line[1:])
        elif line.startswith("+## Cycle") and at:
            m = _HEADING.match(line[1:])
            if m:
                times.setdefault(m.group(1) + m.group(2), at)
    return times


def master_commits(repo=REPO_ROOT):
    """[(local datetime, short commit)], oldest first, first-parent history of HEAD."""
    rows = []
    for line in _git(repo, "log", "--first-parent", "--format=%h %cI").splitlines():
        commit, _, iso = line.partition(" ")
        at = _local(iso)
        if at:
            rows.append((at, commit))
    return sorted(rows)


def cycle_times(text, times):
    """[(datetime, cycle)], oldest first: each cycle's time on master, else
    its FIXLOG date at 00:00 (a cycle with neither is left out)."""
    rows = []
    for cycle, info in parse_fixlog(text).items():
        at = times.get(cycle)
        if at is None and info["date"]:
            at = datetime.datetime.fromisoformat(info["date"])
        if at is not None:
            rows.append((at, cycle))
    return sorted(rows)


def derive_cycle(run_at, rows):
    """The newest (highest-numbered) cycle on master at run_at; None for a
    run older than every dated cycle."""
    seen = [cycle for at, cycle in rows if at <= run_at]
    return max(seen, key=cycle_key) if seen else None


def derive_commit(run_at, commits):
    i = bisect.bisect_right([at for at, _c in commits], run_at)
    return commits[i - 1] if i else (None, None)


def derive(run_at, rows, commits):
    at, commit = derive_commit(run_at, commits) if run_at else (None, None)
    return {"commit": commit, "date": at.date().isoformat() if at else None,
            "cycle": derive_cycle(run_at, rows) if run_at else None, "derived": True}


@functools.lru_cache(maxsize=None)
def _derivation(repo):
    return cycle_times(fixlog_text(repo), heading_times(repo)), master_commits(repo)


def run_started(run_id, state):
    """The run's start time: its first history entry, else its run id."""
    for entry in (state or {}).get("history") or []:
        try:
            return datetime.datetime.fromisoformat(str(entry.get("at") or ""))
        except ValueError:
            continue
    m = _RUN_TIME.match(run_id or "")
    if m:
        return datetime.datetime.strptime(m.group(1) + m.group(2) + (m.group(3) or "00"), "%Y%m%d%H%M%S")
    return None


# ---------------------------------------------------------------------------
# runs: out/ and the archives
# ---------------------------------------------------------------------------

def archives(tenant):
    """[(key, runs folder, archive folder)] that exist."""
    found = []
    for key, root in (tenant.get("versions.archives") or {}).items():
        root = Path(str(root)).expanduser()
        if root.is_dir():
            found.append((key, root / "out" if (root / "out").is_dir() else root, root))
    return found


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _jev(state):
    from .site import _jev_composite

    jev = state.get("jev") or {}
    shipped = next((d for d in jev.get("drafts") or [] if d.get("draft") == jev.get("shipped")), None)
    return _jev_composite(shipped)


def describe(source, run_dir, state, version):
    """One run as the two pages need it (no Path objects: archive runs are
    stored in versions-index.json)."""
    run_dir = Path(run_dir)
    brief = _read_json(run_dir / "ad_brief.json")
    pages = []
    for page in state.get("pages") or {}:
        page_dir = run_dir / textutil.safe_filename(page)
        page_json = _read_json(page_dir / "page.json")
        choice = (state.get("listicle") if page == "listicle" else state.get(page)) or {}
        if not isinstance(choice, dict):
            choice = {}
        published = (state.get("published_pages") or {}).get(page) or {}
        pages.append({
            "page": page, "rendered": (page_dir / "index.html").is_file(),
            "look": choice.get("look") or page_json.get("look") or "",
            "hero": choice.get("hero_style") or page_json.get("hero_style") or "",
            "style": choice.get("style") or page_json.get("style") or "",
            "quality": choice.get("quality") if isinstance(choice.get("quality"), dict) else {},
            "published_url": published.get("url") if isinstance(published, dict) else None,
        })
    words = " ".join(str(brief.get(k) or "") for k in ("hook", "angle", "promise", "cta"))
    run_at = run_started(run_dir.name, state)
    return {
        "source": source, "run_id": run_dir.name, "at": run_at.isoformat() if run_at else None,
        "cycle": (version or {}).get("cycle"), "commit": (version or {}).get("commit"),
        "derived": bool((version or {}).get("derived")), "state": state.get("state"),
        "passed": state.get("state") in PASSED_STATES, "dry_run": bool(state.get("dry_run")), "pages": pages,
        "input_type": brief.get("input_type") or "", "offer": bool(brief.get("offer")),
        "financing": bool(_FINANCING.search(words)), "jev": _jev(state),
    }


def _version_for(run_dir, state, repo):
    version = state.get("harness_version")
    if isinstance(version, dict) and version.get("cycle"):
        return version
    rows, commits = _derivation(str(repo))
    return derive(run_started(Path(run_dir).name, state), rows, commits)


def out_runs(tenant, repo=None):
    runs = []
    out = Path(tenant.out_dir)
    for run_dir in sorted(out.iterdir()) if out.is_dir() else ():
        state = _read_json(run_dir / "state.json")
        if state:
            runs.append(describe("out", run_dir, state, _version_for(run_dir, state, repo or REPO_ROOT)))
    return runs


def index_path(tenant):
    return Path(tenant.runs_dir) / INDEX_NAME


def archived_runs(tenant):
    return _read_json(index_path(tenant)).get("runs") or []


def all_runs(tenant, repo=None):
    """Every run of out/ and the archive index, test-suite (dry) runs left out, newest first."""
    runs = [r for r in out_runs(tenant, repo) + archived_runs(tenant) if not r.get("dry_run")]
    return sorted(runs, key=lambda r: r.get("at") or "", reverse=True)


def costs(tenant):
    """{run_id: cost} from runs/spend-ledger.jsonl (each run's last final line)."""
    found = {}
    try:
        lines = (Path(tenant.runs_dir) / "spend-ledger.jsonl").read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and "cost_estimate" in entry and entry.get("run_id"):
            found[entry["run_id"]] = float(entry["cost_estimate"] or 0)
    return found


def live_links(tenant, runs=()):
    """{(run_id, page): {"url", "replaced"}}: store pages from A/B arms
    (abtests/*.json, also the archives'), post-live records (state.json
    published_pages) and PUBLISHED-STORE-PAGES.txt. A url that several runs
    used (listicle-test-1 was rebuilt four times) is "replaced" for every run
    but the newest."""
    pairs = []
    folders = [Path(tenant.abtests_dir)] + [root / "abtests" for _k, _r, root in archives(tenant)]
    for folder in folders:
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
            for v in _read_json(path).get("variants") or []:
                if not isinstance(v, dict):
                    continue
                run_id = v.get("run_id") or Path(v.get("run_dir") or "").name
                page = v.get("page") or v.get("cartridge")
                url = (v.get("shopify") or {}).get("url") if isinstance(v.get("shopify"), dict) else None
                if run_id and page and url:
                    pairs.append((run_id, page, url))
    for _key, _runs, root in archives(tenant):
        try:
            lines = (root / PUBLISHED_FILE).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) == 3 and parts[2].startswith("http"):
                pairs.append(tuple(parts))
    for run in runs:
        for p in run.get("pages") or []:
            if p.get("published_url"):
                pairs.append((run["run_id"], p["page"], p["published_url"]))
    newest = {}
    for run_id, _page, url in pairs:
        newest[url] = max(newest.get(url, ""), run_id)
    return {(run_id, page): {"url": url, "replaced": newest[url] != run_id} for run_id, page, url in pairs}


# ---------------------------------------------------------------------------
# backfill and the archive index
# ---------------------------------------------------------------------------

def backfill(tenant, *, dry_run=False, repo=None, log=print):
    """Derived harness_version for every out/ run without one, and the
    archive index. Returns {"written", "kept", "recent", "archived", "by_cycle"}."""
    repo = repo or REPO_ROOT
    rows, commits = cycle_times(fixlog_text(repo), heading_times(repo)), master_commits(repo)
    summary = {"written": 0, "kept": 0, "recent": 0, "archived": 0, "by_cycle": {}}
    out = Path(tenant.out_dir)
    for run_dir in sorted(out.iterdir()) if out.is_dir() else ():
        path = runstate.state_path(run_dir)
        state = _read_json(path)
        if not state:
            continue
        if isinstance(state.get("harness_version"), dict):
            summary["kept"] += 1
            continue
        if time.time() - path.stat().st_mtime < RECENT_S:
            summary["recent"] += 1
            log(f"{run_dir.name}: state.json changed in the last {RECENT_S // 60} minutes, left alone")
            continue
        version = derive(run_started(run_dir.name, state), rows, commits)
        cycle = version["cycle"] or "none"
        summary["by_cycle"][cycle] = summary["by_cycle"].get(cycle, 0) + 1
        summary["written"] += 1
        if not dry_run:
            state["harness_version"] = version
            runstate.save_state(run_dir, state)
    records = []
    for key, runs_root, _root in archives(tenant):
        for run_dir in sorted(runs_root.iterdir()):
            state = _read_json(run_dir / "state.json") if run_dir.is_dir() else {}
            if not state:
                continue
            version = derive(run_started(run_dir.name, state), rows, commits)
            records.append(describe(key, run_dir, state, version))
            cycle = version["cycle"] or "none"
            summary["by_cycle"][cycle] = summary["by_cycle"].get(cycle, 0) + 1
    summary["archived"] = len(records)
    if not dry_run:
        index = {"built_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "archives": {key: str(root) for key, _r, root in archives(tenant)}, "runs": records}
        path = index_path(tenant)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=1) + "\n")
        tmp.replace(path)
    return summary


# ---------------------------------------------------------------------------
# the two pages' data
# ---------------------------------------------------------------------------

def primary_page(run):
    """The page a run's thumbnail shows: its first rendered page."""
    return next((p for p in run.get("pages") or [] if p.get("rendered")), None)


def timeline(tenant, repo=None):
    """Rows newest cycle first: every cycle that produced runs, plus each
    milestone cycle without runs."""
    runs = all_runs(tenant, repo)
    cost = costs(tenant)
    live = live_links(tenant, runs)
    cycles = parse_fixlog(fixlog_text(repo or REPO_ROOT))
    milestones = {str(k): str(v) for k, v in (tenant.get("versions.milestones") or {}).items()}
    groups = {}
    for run in runs:
        groups.setdefault(run.get("cycle") or "", []).append(run)
    rows = []
    for cycle in set(groups) | set(milestones):
        group = groups.get(cycle, [])
        info = cycles.get(cycle) or {}
        spent = [cost[r["run_id"]] for r in group if r["run_id"] in cost]
        examples = [(r, primary_page(r)) for r in group if r["passed"] and primary_page(r)][:3]
        rows.append({
            "cycle": cycle, "date": info.get("date"), "summary": info.get("summary") or "",
            "milestone": milestones.get(cycle, ""), "runs": len(group),
            "derived": sum(1 for r in group if r.get("derived")),
            "pass_rate": (sum(1 for r in group if r["passed"]) / len(group)) if group else None,
            "median_cost": statistics.median(spent) if spent else None,
            "examples": [{"run": r, "page": p, "live": live.get((r["run_id"], p["page"]))} for r, p in examples],
        })
    rows.sort(key=lambda row: cycle_key(row["cycle"]), reverse=True)
    return rows


def capabilities(tenant):
    """[(id, title, match(run, page) -> bool)] for the showcase, in page order."""
    library = tenant.get("abtest.library") or []
    looks = dict.fromkeys(str(a).split(":")[1] for a in library if str(a).startswith("landing:"))
    caps = [(f"landing-{look}", f"Landing page, {look} look",
             lambda r, p, look=look: p["page"] == "landing" and p["look"] == look) for look in looks]
    for style in LISTICLE_STYLES:
        for hero in LISTICLE_HEROES:
            caps.append((f"listicle-{style}-{hero}", f"Listicle, {style} style, {hero} first screen",
                         lambda r, p, s=style, h=hero: p["page"] == "listicle" and p["style"] == s and p["hero"] == h))
    for page, title in (("comparison", "Comparison page"), ("quiz", "Quiz"), ("product-page", "Product page"),
                        ("longform", "Long-form page")):
        caps.append((page, title, lambda r, p, page=page: p["page"] == page))
    caps += [
        ("offer-led", "Offer-led sale page", lambda r, p: p["page"] == "landing" and r.get("offer")),
        ("video-ad", "Video ad (transcript) page", lambda r, p: r.get("input_type") == "video"),
        ("financing-ad", "Financing ad page", lambda r, p: r.get("financing")),
    ]
    return caps


def showcase(tenant, repo=None):
    """{"current", "cards"}: per capability the newest passing run with a
    rendered page (on any cycle; the page marks an older one), else None."""
    runs = all_runs(tenant, repo)
    cost = costs(tenant)
    live = live_links(tenant, runs)
    cards = []
    for cap_id, title, match in capabilities(tenant):
        found = next(((r, p) for r in runs if r["passed"] for p in r["pages"] if p["rendered"] and match(r, p)),
                     None)
        example = None
        if found:
            run, page = found
            example = {"run": run, "page": page, "cost": cost.get(run["run_id"]),
                       "live": live.get((run["run_id"], page["page"]))}
        cards.append({"id": cap_id, "title": title, "example": example})
    return {"current": current(repo), "cards": cards}


# ---------------------------------------------------------------------------
# thumbnails and archive pages
# ---------------------------------------------------------------------------

def page_dir(tenant, source, run_id, page):
    """The folder of one run page, out/ or an archive; None when unknown.
    Names are checked: no path parts come from the request."""
    if textutil.safe_filename(run_id) != run_id or textutil.safe_filename(page) != page or run_id.startswith("."):
        return None
    if source == "out":
        root = Path(tenant.out_dir)
    else:
        root = next((runs for key, runs, _r in archives(tenant) if key == source), None)
    if root is None:
        return None
    folder = root / run_id / page
    return folder if folder.is_dir() else None


def thumb_name(source, run_id, page):
    return textutil.safe_filename(f"{source}-{run_id}-{page}.jpg")


def _design_shot(folder):
    from . import design_gate

    chosen = (design_gate.load_scores(folder) or {}).get("chosen")
    if chosen is None:
        return None
    path = design_gate.design_dir_for(folder) / f"variant-{chosen}.png"
    return path if path.is_file() else None


def _capture(tenant, folder, work, browser):
    """A 390 x 844 shot of folder/index.html, taken from a copy in `work`
    (assets/ is a symlink): the page's own folder is never written."""
    from . import capture

    if not browser or not (folder / "index.html").is_file():
        return None
    shutil.copyfile(folder / "index.html", work / "index.html")
    if (folder / "assets").is_dir():
        (work / "assets").symlink_to((folder / "assets").resolve(), target_is_directory=True)
    shot = work / "shot.png"
    ok = capture.screenshot(browser, work / "index.html", shot, width=SCREEN[0], height=SCREEN[1], tenant=tenant)
    return shot if ok else None


def thumb(tenant, source, run_id, page, *, browser=None):
    """The cached first-screen jpg of one run page, made on first use: the
    design gate's chosen phone shot, else a capture of index.html, else the
    page's first image. None when there is nothing to show."""
    folder = page_dir(tenant, source, run_id, page)
    if folder is None:
        return None
    cache = Path(tenant.runs_dir) / THUMBS_DIR / thumb_name(source, run_id, page)
    if cache.is_file():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="stage-", dir=cache.parent))
    try:
        screen = _design_shot(folder) or _capture(tenant, folder, work, browser)
        source_img = screen
        if source_img is None and (folder / "assets").is_dir():
            images = sorted(p for p in (folder / "assets").iterdir()
                            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
            source_img = images[0] if images else None
        if source_img is None:
            return None
        with Image.open(source_img) as im:
            im = im.convert("RGB")
            if screen is not None:
                im = im.crop((0, 0, min(im.size[0], SCREEN[0]), min(im.size[1], SCREEN[1])))
            im.thumbnail(THUMB_SIZE, Image.LANCZOS)
            im.save(cache, format="JPEG", quality=80)
        return cache
    except OSError:
        return None
    finally:
        link = work / "assets"
        if link.is_symlink():
            link.unlink()
        shutil.rmtree(work, ignore_errors=True)
