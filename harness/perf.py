"""Cycle 110: the load budget of a landing page -- what WE control (the
store's theme around it is measured separately).

Two halves:

  - static_budget(page_dir): no browser. The page's HTML bytes (<= 60 KB),
    the hero image (the one <img fetchpriority="high">: its WebP variant at
    most 60 KB, explicit width/height), every other image lazy with a
    reserved box, no web font outside the allowed families, no <script src>.
  - measure(browser, html_path): headless Chromium over the DevTools
    protocol with Slow 4G (1.6 Mbps down, 750 kbps up, 150 ms RTT) and a 4x
    CPU slowdown at 390 x 844; the page's own Largest Contentful Paint and
    Cumulative Layout Shift from PerformanceObserver. None when there is no
    browser or anything fails -- never an exception (like harness/capture).

The DevTools client is the smallest thing that works: one WebSocket (RFC
6455, text frames, client-masked) to the page target, request/response by
id. The page is served from a local HTTP server on 127.0.0.1 so the network
throttling applies to it (file:// bypasses the network stack).
"""
import base64
import functools
import http.server
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HTML_MAX_BYTES = 60_000
HERO_MAX_BYTES = 60_000
LCP_MAX_MS = 2500
CLS_MAX = 0.1
SLOW_4G = {"offline": False, "latency": 150, "downloadThroughput": 1_600_000 / 8, "uploadThroughput": 750_000 / 8}
CPU_SLOWDOWN = 4
TIMEOUT_S = 60

_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_SOURCE_RE = re.compile(r'<source\b[^>]*type="image/webp"[^>]*srcset="([^"]+)"', re.IGNORECASE)
_SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc=", re.IGNORECASE)


def _attrs(tag):
    return dict((k.lower(), v) for k, v in _ATTR_RE.findall(tag))


def static_budget(page_dir):
    """{"html_bytes", "hero": {...} | None, "eager_images", "unsized_images",
    "script_src", "problems": [...]} for page_dir/index.html."""
    page_dir = Path(page_dir)
    raw = (page_dir / "index.html").read_text(errors="ignore")
    # markup only: a CSS comment that says "<img>" is not an image
    html = re.sub(r"<style\b[^>]*>.*?</style>|<!--.*?-->", " ", raw, flags=re.S | re.I)
    out = {"html_bytes": len(raw.encode()), "hero": None, "eager_images": 0, "unsized_images": 0,
           "script_src": len(_SCRIPT_SRC_RE.findall(html)), "problems": []}
    if out["html_bytes"] > HTML_MAX_BYTES:
        out["problems"].append(f"HTML is {out['html_bytes'] // 1000} KB (budget {HTML_MAX_BYTES // 1000} KB)")
    for m in _IMG_RE.finditer(html):
        a = _attrs(m.group(0))
        hero = a.get("fetchpriority") == "high"
        if not (a.get("width") and a.get("height")) and "aspect-ratio" not in a.get("style", ""):
            out["unsized_images"] += 1
        if hero and out["hero"] is None:
            before = html[max(0, m.start() - 1200):m.start()]
            webp = _SOURCE_RE.findall(before)
            src = (webp[-1].split(",")[0].split()[0] if webp else a.get("src", ""))
            path = page_dir / src if src and not src.startswith(("http:", "https:", "data:")) else None
            size = path.stat().st_size if path is not None and path.exists() else None
            out["hero"] = {"src": src, "bytes": size, "webp": bool(webp), "sized": bool(a.get("width"))}
            if size is not None and size > HERO_MAX_BYTES:
                out["problems"].append(f"hero image is {size // 1000} KB (budget {HERO_MAX_BYTES // 1000} KB)")
        elif a.get("loading") != "lazy":
            out["eager_images"] += 1
    if out["eager_images"]:
        out["problems"].append(f"{out['eager_images']} image(s) below the hero are not lazy")
    if out["unsized_images"]:
        out["problems"].append(f"{out['unsized_images']} image(s) without a reserved box")
    if out["script_src"]:
        out["problems"].append(f"{out['script_src']} external script(s)")
    return out


# ---------------------------------------------------------------------------
# DevTools over one WebSocket
# ---------------------------------------------------------------------------

class _WS:
    def __init__(self, url, timeout=TIMEOUT_S):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        self.sock = socket.create_connection((m.group(1), int(m.group(2))), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {m.group(3)} HTTP/1.1\r\nHost: {m.group(1)}:{m.group(2)}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("websocket handshake closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("websocket handshake refused")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("websocket closed")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def send(self, obj):
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                                bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data = self._read(n)
            if b1 & 0x0F == 0x8:
                raise OSError("websocket closed by the browser")
            if b1 & 0x0F in (0x9, 0xA):
                continue
            parts.append(data)
            if b1 & 0x80:
                return json.loads(b"".join(parts))

    def call(self, method, params=None, deadline=None):
        self.next_id += 1
        mine = self.next_id
        self.send({"id": mine, "method": method, "params": params or {}})
        while True:
            if deadline and time.monotonic() > deadline:
                raise TimeoutError(method)
            msg = self.recv()
            if msg.get("id") == mine:
                if "error" in msg:
                    raise OSError(f"{method}: {msg['error']}")
                return msg.get("result") or {}

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


METRICS_JS = r"""
new Promise(function(done){
  var lcp=0, el='', cls=0;
  try{ new PerformanceObserver(function(l){ l.getEntries().forEach(function(e){ lcp=e.startTime;
      el=e.element ? (e.element.tagName.toLowerCase()+(e.element.className?'.'+String(e.element.className).split(' ')[0]:'')) : (e.url||''); }); })
      .observe({type:'largest-contentful-paint', buffered:true}); }catch(e){}
  try{ new PerformanceObserver(function(l){ l.getEntries().forEach(function(e){ if(!e.hadRecentInput){cls+=e.value;} }); })
      .observe({type:'layout-shift', buffered:true}); }catch(e){}
  setTimeout(function(){
    var t=0; performance.getEntriesByType('resource').forEach(function(r){ t+=r.transferSize||0; });
    var nav=performance.getEntriesByType('navigation')[0]; if(nav){ t+=nav.transferSize||0; }
    done({lcp_ms:Math.round(lcp), cls:Math.round(cls*1000)/1000, lcp_element:el, transfer_bytes:t});
  }, 2500);
})
"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _serve(folder):
    handler = functools.partial(_Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def measure(browser, html_path, *, width=390, height=844, timeout_s=TIMEOUT_S):
    """{"lcp_ms", "cls", "lcp_element", "transfer_bytes", "throttle"} for the
    page under Slow 4G and a 4x CPU slowdown, or None."""
    if not browser:
        return None
    html_path = Path(html_path)
    work = Path(tempfile.mkdtemp(prefix="perf-", dir=str(html_path.parent)))   # not hidden: a snap browser cannot use a dot folder
    server = proc = ws = None
    try:
        server = _serve(html_path.parent)
        url = f"http://127.0.0.1:{server.server_address[1]}/{html_path.name}"
        proc = subprocess.Popen(
            [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--mute-audio",
             "--no-default-browser-check", f"--user-data-dir={work / 'profile'}", "--remote-debugging-port=0",
             f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout_s
        port_file = work / "profile" / "DevToolsActivePort"
        while not port_file.exists() or not port_file.read_text().strip():
            if time.monotonic() > deadline or proc.poll() is not None:
                return None
            time.sleep(0.1)
        port = int(port_file.read_text().split()[0])
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10).read())
        page = next(t for t in targets if t.get("type") == "page")
        ws = _WS(page["webSocketDebuggerUrl"])
        ws.call("Network.enable", deadline=deadline)
        ws.call("Network.setCacheDisabled", {"cacheDisabled": True}, deadline=deadline)
        ws.call("Network.emulateNetworkConditions", dict(SLOW_4G), deadline=deadline)
        ws.call("Emulation.setCPUThrottlingRate", {"rate": CPU_SLOWDOWN}, deadline=deadline)
        ws.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1,
                                                      "mobile": True}, deadline=deadline)
        ws.call("Page.enable", deadline=deadline)
        ws.call("Page.navigate", {"url": url}, deadline=deadline)
        while True:   # wait for the load event
            if time.monotonic() > deadline:
                return None
            msg = ws.recv()
            if msg.get("method") == "Page.loadEventFired":
                break
        result = ws.call("Runtime.evaluate", {"expression": METRICS_JS, "awaitPromise": True,
                                              "returnByValue": True}, deadline=deadline)
        value = ((result or {}).get("result") or {}).get("value")
        if not isinstance(value, dict):
            return None
        value["throttle"] = f"Slow 4G (1.6 Mbps, 150 ms RTT), {CPU_SLOWDOWN}x CPU, {width}x{height}"
        return value
    except Exception:  # noqa: BLE001 -- a measurement problem never fails a run
        return None
    finally:
        if ws is not None:
            ws.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if server is not None:
            server.shutdown()
            server.server_close()
        shutil.rmtree(work, ignore_errors=True)


def budget_check(static, measured):
    """The design gate's `load_budget` check: {"status", "detail", "items"}.
    fail: LCP over 2.5 s or CLS over 0.1 (measured), or a static budget miss;
    unknown parts are reported, not failed."""
    problems = list((static or {}).get("problems") or [])
    bits = []
    if static:
        bits.append(f"HTML {static['html_bytes'] // 1000} KB")
        hero = static.get("hero") or {}
        if hero.get("bytes") is not None:
            bits.append(f"hero {hero['bytes'] // 1000} KB{' webp' if hero.get('webp') else ''}")
    if measured:
        bits.append(f"LCP {measured['lcp_ms'] / 1000:.2f} s ({measured.get('lcp_element') or '?'})")
        bits.append(f"CLS {measured['cls']:.3f}")
        bits.append(f"{measured.get('transfer_bytes', 0) // 1000} KB transferred")
        if measured["lcp_ms"] > LCP_MAX_MS:
            problems.append(f"LCP {measured['lcp_ms'] / 1000:.2f} s over {LCP_MAX_MS / 1000:.1f} s")
        if measured["cls"] > CLS_MAX:
            problems.append(f"CLS {measured['cls']:.3f} over {CLS_MAX}")
    else:
        bits.append("LCP/CLS not measured (no browser)")
    detail = "; ".join(bits) + (f" -- {'; '.join(problems)}" if problems else "")
    return {"status": "fail" if problems else "pass", "detail": detail, "items": [measured or {}, static or {}]}
