"""Cycle 92: headless-Chromium screenshots and a DOM probe, shared by the
Library thumbnails (harness/library.py) and the design gate
(harness/design_gate.py).

Two browser calls, both on a STAGED copy of the page: a hidden sibling file in
the page's own folder (so relative assets resolve) with

  - the tenant's local brand fonts injected at the top of <head>
    (harness/fonts.py, file:// urls; a page that already carries the block,
    e.g. a review html built with fonts, keeps its own), and
  - a small script that makes every image eager, asks the browser to load
    one face of every declared font family, and waits for
    `document.fonts.ready` -- so a capture never shows a fallback font only
    because the face had not loaded yet.

`screenshot` frames the staged page in an iframe of exactly the asked size
inside a wrapper page (a headless window can keep a wider layout viewport
than --window-size asks for) and gives the page a virtual-time budget to
fetch fonts and images. `probe` loads the same frame with
`--dump-dom`: the staged page measures itself (scroll width, height, images,
text cut off, tap targets, text over photos in the first screen, declared
and used font families) and posts the result to the wrapper, which writes it
into the DOM that Chrome dumps. Chrome on some hosts writes its output and
then does not exit; both calls stop it once the output is complete.

No browser, a crash or a timeout: `screenshot` returns False and `probe`
returns None -- never an exception.
"""
import html
import json
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from . import fonts as fonts_mod

BROWSERS = ("chromium", "google-chrome", "chromium-browser")
MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
BROWSER_ENV = "HARNESS_CHROME"
DEFAULT_TIMEOUT_S = 90
VIRTUAL_TIME_MS = 6000

# Runs inside the staged page. PROBE adds the measurements and the post.
PREP_JS = r"""
(function(){
  function wait(ms){return new Promise(function(r){setTimeout(r,ms);});}
  window.__captureReady = (async function(){
    var imgs=[].slice.call(document.images);
    imgs.forEach(function(i){ if(i.loading==='lazy'){ i.loading='eager'; } });
    var fams={};
    document.fonts.forEach(function(f){ fams[f.family.replace(/^["']|["']$/g,'')]=1; });
    await Promise.race([Promise.all(Object.keys(fams).map(function(f){
      return document.fonts.load('16px "'+f+'"').catch(function(){});})), wait(6000)]);
    await Promise.race([document.fonts.ready, wait(6000)]);
    await Promise.race([Promise.all(imgs.map(function(i){
      return i.complete ? 0 : new Promise(function(r){ i.addEventListener('load',r); i.addEventListener('error',r); });
    })), wait(6000)]);
  })();
})();
"""

PROBE_JS = r"""
(function(){
  function sel(el){ var s=el.tagName.toLowerCase(); if(el.id){s+='#'+el.id;}
    var c=[].slice.call(el.classList,0,2); if(c.length){s+='.'+c.join('.');} return s; }
  function own(el){ var t=''; el.childNodes.forEach(function(n){ if(n.nodeType===3){t+=n.textContent;} }); return t.replace(/\s+/g,' ').trim(); }
  function shown(el,r,cs){ return r.width>0 && r.height>0 && cs.visibility!=='hidden' && cs.display!=='none' && parseFloat(cs.opacity||'1')>0.01; }
  function bgImage(el){ for(var a=el; a && a!==document.documentElement; a=a.parentElement){
      var b=getComputedStyle(a).backgroundImage; if(b && b.indexOf('url(')>=0){return true;} } return false; }
  function clips(v){ return v==='hidden'||v==='clip'; }
  window.addEventListener('load', async function(){
    var out={};
    try{
      await window.__captureReady;
      var de=document.documentElement, body=document.body||de, vw=window.innerWidth, sy=window.scrollY;
      out.viewport_width=vw; out.scroll_width=Math.max(de.scrollWidth, body.scrollWidth);
      out.height=Math.max(de.scrollHeight, body.scrollHeight);
      out.overflow=[]; out.cut=[]; out.taps=[]; out.hero_text=[]; out.placeholders=[]; out.used_families={};
      var media=[].slice.call(document.querySelectorAll('img,video')).map(function(m){
        var r=m.getBoundingClientRect(); return {el:m, x:r.left, y:r.top+sy, w:r.width, h:r.height}; });
      var all=[].slice.call(body.querySelectorAll('*')).slice(0,6000);
      all.forEach(function(el){
        var tag=el.tagName.toLowerCase(); if(tag==='script'||tag==='style'||tag==='noscript'){return;}
        var r=el.getBoundingClientRect(), cs=getComputedStyle(el);
        if(!shown(el,r,cs)){return;}
        if(r.right>vw+1 && out.overflow.length<8){ out.overflow.push({sel:sel(el), right:Math.round(r.right)}); }
        var text=own(el);
        if(text){
          var fam=cs.fontFamily.split(',')[0].replace(/^\s*["']|["']\s*$/g,'').trim();
          out.used_families[fam]=(out.used_families[fam]||0)+1;
          if(r.top+sy<844 && out.hero_text.length<24){
            var a=r.width*r.height, over=bgImage(el);
            if(!over){ media.forEach(function(m){ if(over||m.el===el||el.contains(m.el)||m.el.contains(el)){return;}
              var ix=Math.max(0,Math.min(r.right,m.x+m.w)-Math.max(r.left,m.x));
              var iy=Math.max(0,Math.min(r.top+sy+r.height,m.y+m.h)-Math.max(r.top+sy,m.y));
              if(a>0 && ix*iy>0.5*a){over=true;} }); }
            if(over){ out.hero_text.push({sel:sel(el), text:text.slice(0,60), color:cs.color,
              size:parseFloat(cs.fontSize), weight:parseInt(cs.fontWeight,10)||400,
              rect:[Math.round(r.left),Math.round(r.top+sy),Math.round(r.width),Math.round(r.height)]}); }
          }
        }
        var hides=clips(cs.overflow)||clips(cs.overflowX)||clips(cs.overflowY);
        var clamp=cs.webkitLineClamp && cs.webkitLineClamp!=='none';
        var ell=cs.textOverflow==='ellipsis';
        if((hides||clamp||ell) && tag!=='body' && r.width>2 && r.height>2 && el.textContent.trim() && out.cut.length<12){
          var cutY=(clips(cs.overflow)||clips(cs.overflowY)||clamp) && el.scrollHeight>el.clientHeight+2;
          var cutX=(clips(cs.overflow)||clips(cs.overflowX)||ell) && el.scrollWidth>el.clientWidth+2;
          if(cutY||cutX){ out.cut.push({sel:sel(el), text:el.textContent.replace(/\s+/g,' ').trim().slice(0,60),
            axis:cutY?'y':'x'}); }
        }
        if(el.matches('a[href],button,[role=button],input[type=submit],input[type=button],summary')){
          if(r.width<44||r.height<44){
            var inline=tag==='a' && cs.display==='inline' && el.parentElement &&
              el.parentElement.textContent.trim().length>el.textContent.trim().length+10;
            if(!inline && out.taps.length<30){ out.taps.push({sel:sel(el), w:Math.round(r.width), h:Math.round(r.height),
              text:el.textContent.replace(/\s+/g,' ').trim().slice(0,40),
              cta:tag==='button'||/btn|cta|button/i.test(el.className||'')}); }
          }
        }
        if(/placeholder/i.test(el.className||'') && out.placeholders.length<12){ out.placeholders.push(sel(el)); }
      });
      out.images=[].slice.call(document.images).slice(0,80).map(function(i){
        var r=i.getBoundingClientRect();
        return {src:(i.currentSrc||i.getAttribute('src')||'').slice(-80), empty:!i.getAttribute('src'),
          loaded:i.complete && i.naturalWidth>0, w:Math.round(r.width), h:Math.round(r.height)}; });
      var declared={};
      document.fonts.forEach(function(f){ var fam=f.family.replace(/^["']|["']$/g,'');
        var d=declared[fam]=declared[fam]||{faces:0, loaded:0, error:0};
        d.faces++; if(f.status==='loaded'){d.loaded++;} if(f.status==='error'){d.error++;} });
      out.fonts=declared; out.fonts_status=document.fonts.status;
    }catch(e){ out.error=String(e); }
    parent.postMessage({__designProbe: out}, '*');
  });
})();
"""


def find_browser():
    """$HARNESS_CHROME, else a chromium/google-chrome on PATH, else the
    macOS application bundle; None when there is none."""
    env = os.environ.get(BROWSER_ENV)
    if env and Path(env).exists():
        return env
    for name in BROWSERS:
        path = shutil.which(name)
        if path:
            return path
    for path in MAC_BROWSERS:
        if Path(path).exists():
            return path
    return None


def stage_page(html_path, *, tenant=None, probe=False):
    """The hidden sibling copy of `html_path` this module loads (see the
    module docstring). The caller deletes it."""
    html_path = Path(html_path)
    text = html_path.read_text(errors="ignore")
    faces = fonts_mod.local_faces(tenant) if tenant is not None else []
    text = fonts_mod.inject(text, fonts_mod.brand_font_css(faces, "file"))
    script = "<script>" + PREP_JS + (PROBE_JS if probe else "") + "</script>"
    lower = text.lower()
    at = lower.rfind("</body>")
    text = text[:at] + script + text[at:] if at >= 0 else text + script
    staged = html_path.with_name(f".{html_path.stem}.capture-{secrets.token_hex(4)}.html")
    staged.write_text(text)
    return staged


def _frame_html(src_uri, width, height, *, listen=False):
    listener = ("<pre id=\"probe\"></pre><script>addEventListener('message',function(e){"
                "if(e.data&&e.data.__designProbe){document.getElementById('probe').textContent="
                "JSON.stringify(e.data.__designProbe);}});</script>") if listen else ""
    return (f'<!doctype html><html><body style="margin:0;background:#fff">'
            f'<iframe src="{src_uri}" scrolling="no" '
            f'style="position:fixed;top:0;left:0;width:{width}px;height:{height}px;border:0"></iframe>'
            f"{listener}</body></html>")


def _browser_cmd(browser, work, width, height, extra):
    return [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--mute-audio",
            "--no-first-run", "--no-default-browser-check", "--allow-file-access-from-files",
            f"--user-data-dir={Path(work) / 'profile'}", f"--window-size={width},{height}",
            "--force-device-scale-factor=1", f"--virtual-time-budget={VIRTUAL_TIME_MS}"] + extra


def _stop(proc):
    """Stop the browser and every helper process it started (they share its
    session: the browser is started with start_new_session). A helper left
    running keeps writing into the profile folder after the main process has
    gone."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        time.sleep(0.2)


def _workdir(parent, prefix):
    """A scratch folder beside the output (see screenshot); its cleanup never
    raises over a file a dying browser helper wrote at the last moment."""
    return tempfile.TemporaryDirectory(prefix=prefix, dir=parent, ignore_cleanup_errors=True)


def _crop(png_path, width, height):
    """A browser that refused the asked window size (some refuse widths
    under 500) wrote a wider image: keep the framed area."""
    try:
        from PIL import Image

        with Image.open(png_path) as im:
            if im.size == (width, height):
                return
            box = (0, 0, min(width, im.size[0]), min(height, im.size[1]))
            cropped = im.crop(box)
        cropped.save(png_path)
    except Exception:  # noqa: BLE001 -- an unreadable file is the caller's False
        return


def screenshot(browser, html_path, png_path, *, width, height, tenant=None, popen=subprocess.Popen,
               timeout_s=DEFAULT_TIMEOUT_S):
    """A width x height PNG of html_path at scale 1. True when it was
    written; any failure is False."""
    png_path = Path(png_path)
    png_path.unlink(missing_ok=True)  # never report an older file as this capture
    png_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        staged = stage_page(html_path, tenant=tenant)
    except OSError:
        return False
    try:
        # The frame page and the profile sit next to the PNG, never in /tmp: a
        # snap-packaged Chromium (prod) has a private /tmp and sees neither.
        with _workdir(png_path.parent, "harness-shot-") as work:
            frame = Path(work) / "frame.html"
            frame.write_text(_frame_html(staged.resolve().as_uri(), width, height))
            cmd = _browser_cmd(browser, work, width, height, [f"--screenshot={png_path}", frame.as_uri()])
            try:
                proc = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                return False
            deadline = time.monotonic() + timeout_s
            last_size = -1
            try:
                while time.monotonic() < deadline and proc.poll() is None:
                    size = png_path.stat().st_size if png_path.is_file() else 0
                    if size and size == last_size:
                        break
                    last_size = size
                    time.sleep(0.3)
            finally:
                _stop(proc)
    finally:
        staged.unlink(missing_ok=True)
    if not (png_path.is_file() and png_path.stat().st_size > 0):
        return False
    _crop(png_path, width, height)
    return True


def _probe_from_dom(text):
    start = text.find('<pre id="probe">')
    if start < 0:
        return None
    end = text.find("</pre>", start)
    if end < 0:
        return None
    raw = html.unescape(text[start + len('<pre id="probe">'):end]).strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def probe(browser, html_path, *, width, height=844, tenant=None, popen=subprocess.Popen,
          timeout_s=DEFAULT_TIMEOUT_S):
    """The staged page's own measurements at `width` (see PROBE_JS), or
    None when the browser gave none."""
    html_path = Path(html_path)
    try:
        staged = stage_page(html_path, tenant=tenant, probe=True)
    except OSError:
        return None
    try:
        with _workdir(html_path.parent, "harness-probe-") as work:
            frame = Path(work) / "frame.html"
            frame.write_text(_frame_html(staged.resolve().as_uri(), width, height, listen=True))
            out_path = Path(work) / "dom.html"
            cmd = _browser_cmd(browser, work, max(width, 500), height, ["--dump-dom", frame.as_uri()])
            try:
                with open(out_path, "wb") as out:
                    proc = popen(cmd, stdout=out, stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                return None
            deadline = time.monotonic() + timeout_s
            try:
                while time.monotonic() < deadline and proc.poll() is None:
                    if out_path.is_file() and b"</html>" in out_path.read_bytes():
                        break
                    time.sleep(0.3)
            finally:
                _stop(proc)
            text = out_path.read_text(errors="ignore") if out_path.is_file() else ""
    finally:
        staged.unlink(missing_ok=True)
    return _probe_from_dom(text)
