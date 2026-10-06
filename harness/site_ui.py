"""Cycle 80: the review site's look -- one shell, one stylesheet, a few
components -- shared by the listicle site (harness/site.py) and the older
reviewer pages (harness/serve.py: runs, run detail, image library).

The brand system the generated pages use: white ground, Basalt
#161817 ink, text #1A1A1A, muted #6B6B6B, hairlines rgba(22,24,23,.12), brand
red #702B34 only as an accent (live / attention), DM Sans for the UI and
Poppins for headings, 4px radius on cards and inputs, 999px only on pill
buttons. Vanilla CSS and a few lines of JS; no build step, no framework.
"""
import html
import re
from pathlib import Path

from flask import current_app, g, request, url_for

e = html.escape

TOKENS = {
    "ink": "#161817", "text": "#1A1A1A", "muted": "#6B6B6B", "line": "rgba(22,24,23,.12)", "red": "#702B34",
}

# Cycle 80: the review site's own icon (the brand's wave mark on charcoal),
# served by harness/site.py from harness/static/site/. Only this shell links
# it -- the generated pages and the Shopify export never do (they live on the
# storefront, which has its own favicon).
STATIC_DIR = Path(__file__).resolve().parent / "static" / "site"
STATIC_FILES = {"favicon.ico": "image/x-icon", "favicon-32.png": "image/png", "apple-touch-icon.png": "image/png",
                "icon-192.png": "image/png", "icon-512.png": "image/png"}
THEME_COLOR = "#161817"


def icon_links():
    return (f'<link rel="icon" href="{url_for("site_favicon")}" sizes="any">'
            f'<link rel="icon" type="image/png" sizes="32x32" href="{url_for("site_static", name="favicon-32.png")}">'
            f'<link rel="apple-touch-icon" sizes="180x180" href="{url_for("site_static", name="apple-touch-icon.png")}">'
            f'<link rel="manifest" href="{url_for("site_manifest")}">')


def manifest(name):
    return {"name": name, "short_name": name, "start_url": "/", "display": "standalone",
            "background_color": "#ffffff", "theme_color": THEME_COLOR,
            "icons": [{"src": url_for("site_static", name=f"icon-{n}.png"), "sizes": f"{n}x{n}", "type": "image/png"}
                      for n in (192, 512)]}


FONTS_HREF = ("https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600"
              "&family=Poppins:wght@500;600&display=swap")

CSS = """
:root{--ink:#161817;--text:#1A1A1A;--muted:#6B6B6B;--faint:#9B9B9B;--line:rgba(22,24,23,.12);
  --line-2:rgba(22,24,23,.22);--hover:rgba(22,24,23,.04);--wash:#F7F7F6;--red:#702B34;
  --red-wash:rgba(112,43,52,.06);--red-line:rgba(112,43,52,.3);--r:4px;
  --ui:"DM Sans",system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --head:"Poppins","DM Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:#fff;color:var(--text);font:15px/1.55 var(--ui);overflow-wrap:anywhere;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
a{color:inherit;text-decoration-color:var(--line-2);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
h1,h2,h3{font-family:var(--head);color:var(--ink);font-weight:600;letter-spacing:-.01em;line-height:1.25;margin:0}
h1{font-size:1.55rem} h2{font-size:1.08rem} h3{font-size:.95rem}
p{margin:0 0 .8em}
code{font:.84em var(--mono);background:var(--wash);border:1px solid var(--line);padding:0 4px;border-radius:var(--r)}
img,video,iframe{max-width:100%}
.muted{color:var(--muted)} .small{font-size:.82rem} .nowrap{white-space:nowrap} .num{font-variant-numeric:tabular-nums}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
svg.i{width:16px;height:16px;flex:none;stroke:currentColor;fill:none;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}

/* top bar */
.topbar{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.94);border-bottom:1px solid var(--line);
  -webkit-backdrop-filter:saturate(1.5) blur(10px);backdrop-filter:saturate(1.5) blur(10px)}
.topbar-in{max-width:1280px;margin:0 auto;padding:0 24px;height:56px;display:flex;align-items:center;gap:28px}
.brand{display:flex;align-items:center;gap:12px;text-decoration:none;color:var(--ink);flex:none}
.brand svg{height:14px;width:auto;display:block}
.brand .wordmark{font-family:var(--head);font-weight:600;letter-spacing:.08em}
.brand .tag{font-size:.8rem;color:var(--muted);padding-left:12px;border-left:1px solid var(--line);line-height:18px}
.nav{display:flex;gap:2px;flex:1;min-width:0}
.nav a{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:var(--r);text-decoration:none;
  color:var(--muted);font-weight:500;font-size:.88rem;white-space:nowrap}
.nav a:hover{color:var(--ink);background:var(--hover)}
.nav a.on{color:var(--ink);background:var(--wash)}
.nav .badge{min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:var(--red);color:#fff;font-size:.7rem;
  display:inline-grid;place-items:center;font-weight:600}
.who{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:.82rem;white-space:nowrap;flex:none}
.avatar{width:28px;height:28px;border-radius:50%;background:var(--ink);color:#fff;display:grid;place-items:center;
  font-size:.72rem;font-weight:600;text-transform:uppercase}
main{max-width:1280px;margin:0 auto;padding:32px 24px 96px}

/* page header */
.page-head{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px 24px;margin-bottom:24px}
.page-head .sub{margin:6px 0 0;color:var(--muted);font-size:.9rem}
.page-head .actions{display:flex;gap:8px;flex-wrap:wrap}
.crumb{display:inline-flex;align-items:center;gap:6px;font-size:.84rem;color:var(--muted);text-decoration:none;margin-bottom:12px}
.crumb:hover{color:var(--ink)}
.eyebrow{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:0 0 10px}

/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:38px;padding:0 18px;border-radius:999px;
  border:1px solid var(--ink);background:var(--ink);color:#fff;font:500 .88rem/1 var(--ui);text-decoration:none;
  cursor:pointer;white-space:nowrap;transition:background-color .15s,border-color .15s,color .15s}
.btn:hover{background:#2B2D2C;border-color:#2B2D2C}
.btn:focus-visible,.iconbtn:focus-visible,.seg a:focus-visible,.nav a:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.btn.ghost{background:#fff;color:var(--ink);border-color:var(--line-2)}
.btn.ghost:hover{border-color:var(--ink);background:#fff}
.btn.danger{background:#fff;color:var(--red);border-color:var(--red-line)}
.btn.danger:hover{border-color:var(--red);background:var(--red-wash)}
.btn.live{background:var(--red);border-color:var(--red)} .btn.live:hover{background:#5E222B;border-color:#5E222B}
.btn.sm{height:30px;padding:0 13px;font-size:.8rem;gap:6px}
.btn.block{width:100%}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.iconbtn{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:var(--r);border:0;background:none;
  color:var(--muted);cursor:pointer;flex:none}
.iconbtn:hover{background:var(--hover);color:var(--ink)}
.linkbtn{background:none;border:0;padding:0;font:inherit;color:var(--muted);text-decoration:underline;
  text-decoration-color:var(--line-2);text-underline-offset:3px;cursor:pointer}

/* chips */
.chip{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 8px;border-radius:var(--r);
  border:1px solid var(--line);font-size:.74rem;font-weight:500;color:var(--muted);background:#fff;white-space:nowrap;line-height:1}
.chip.dot::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.chip.live{color:var(--red);border-color:var(--red-line);background:var(--red-wash)}
.chip.approved,.chip.pass,.chip.done{color:var(--ink);border-color:var(--line-2)}
.chip.draft,.chip.hidden,.chip.queued{color:var(--muted)}
.chip.attn,.chip.fail,.chip.failed,.chip.rejected{color:var(--red);border-color:var(--red-line)}
.chip.running{color:var(--ink);border-color:var(--line-2)}
.chip.running::before{content:"";width:8px;height:8px;border-radius:50%;border:1.5px solid currentColor;
  border-right-color:transparent;animation:spin .8s linear infinite;flex:none}
.chip.solid{background:var(--ink);color:#fff;border-color:var(--ink)}
@keyframes spin{to{transform:rotate(360deg)}}

/* cards, sections */
.card{border:1px solid var(--line);border-radius:var(--r);background:#fff;padding:20px}
.card + .card{margin-top:16px}
.two > .card,.stack > .card{margin-top:0}
.card-h{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.card-h h2,.card-h h3{display:flex;align-items:center;gap:8px}
.stack{display:flex;flex-direction:column;gap:16px}
.row{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
.hr{border:0;border-top:1px solid var(--line);margin:16px 0}
.notice{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;
  font-size:.88rem;margin:0 0 16px;background:#fff}
.notice.attn,.warn{border-color:var(--red-line);background:var(--red-wash)}
.warn{border:1px solid var(--red-line);border-radius:var(--r);padding:12px 14px;font-size:.9rem;margin:0 0 16px}
.warn p:last-child{margin:0}
.error{border:1px solid var(--red-line);border-left:3px solid var(--red);background:var(--red-wash);color:var(--red);
  border-radius:var(--r);padding:10px 14px;margin:0 0 14px;font-weight:500;font-size:.9rem}
.empty{border:1px dashed var(--line-2);border-radius:var(--r);padding:40px 20px;text-align:center;color:var(--muted)}

/* forms */
label{display:block;font-weight:500;font-size:.86rem;color:var(--ink);margin:16px 0 6px}
label .opt{color:var(--muted);font-weight:400}
input[type=text],input[type=search],input[type=email],textarea,select{width:100%;height:40px;border:1px solid var(--line-2);
  border-radius:var(--r);padding:0 12px;font:inherit;font-size:.92rem;background:#fff;color:var(--text);transition:border-color .15s}
textarea{height:auto;min-height:132px;padding:10px 12px;line-height:1.5;resize:vertical}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--ink);box-shadow:0 0 0 3px rgba(22,24,23,.08)}
input::placeholder,textarea::placeholder{color:var(--faint)}
.hint{font-size:.8rem;color:var(--muted);margin:6px 0 0}
.field-pre{display:flex;align-items:stretch;border:1px solid var(--line-2);border-radius:var(--r);background:#fff;overflow:hidden}
.field-pre:focus-within{border-color:var(--ink);box-shadow:0 0 0 3px rgba(22,24,23,.08)}
.field-pre span{display:flex;align-items:center;padding:0 0 0 12px;color:var(--muted);font-size:.86rem;white-space:nowrap}
.field-pre input{border:0;box-shadow:none!important;padding-left:2px}
.drop{position:relative;border:1px dashed var(--line-2);border-radius:var(--r);padding:28px 16px;text-align:center;
  color:var(--muted);background:var(--wash);transition:border-color .15s,background-color .15s}
.drop:hover,.drop.over{border-color:var(--ink);background:#fff}
.drop input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.drop b{color:var(--ink);font-weight:500}
.drop .file{margin-top:8px;color:var(--ink);font-size:.86rem;font-weight:500}

/* copy field */
.copy{display:flex;align-items:center;gap:4px;border:1px solid var(--line);border-radius:var(--r);height:36px;
  padding:0 3px 0 10px;background:#fff;min-width:0}
.copy input{border:0;height:32px;padding:0;font-size:.82rem;background:none;color:var(--ink);min-width:0;flex:1;box-shadow:none!important}
.copy a.iconbtn{text-decoration:none}
.copy-label{font-size:.74rem;color:var(--muted);margin:0 0 4px;display:flex;align-items:center;gap:6px}
.copy.live{border-color:var(--red-line)}

/* toolbar */
.toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:20px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:3px;gap:2px;background:#fff;max-width:100%;overflow-x:auto}
.seg a,.seg button{border:0;background:none;border-radius:999px;padding:0 14px;height:30px;display:inline-flex;align-items:center;
  gap:6px;font:500 .84rem var(--ui);color:var(--muted);text-decoration:none;cursor:pointer;white-space:nowrap}
.seg a:hover,.seg button:hover{color:var(--ink)}
.seg .on{background:var(--ink);color:#fff}
.seg .on:hover{color:#fff}
.seg .n{opacity:.65;font-variant-numeric:tabular-nums}
.search{display:flex;gap:8px;flex:1;max-width:380px;min-width:220px}
.search .field{position:relative;flex:1}
.search svg{position:absolute;left:11px;top:12px;color:var(--faint)}
.search input{padding-left:34px}
.statline{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--muted);font-size:.84rem;margin:0 0 20px}
.statline b{color:var(--ink);font-weight:600}
.busy{display:inline-flex;align-items:center;gap:8px}

/* ad cards */
.ad{border:1px solid var(--line);border-radius:var(--r);padding:20px;margin:0 0 16px;background:#fff;transition:border-color .15s}
.ad:hover{border-color:var(--line-2)}
.ad-head{display:flex;gap:16px;align-items:flex-start}
.ad-thumb{width:64px;height:64px;border-radius:var(--r);object-fit:cover;background:var(--wash);border:1px solid var(--line);
  flex:none;display:grid;place-items:center;color:var(--faint);overflow:hidden}
.ad-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.ad-main{flex:1;min-width:0}
.ad-title{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center}
.ad-title h2{font-size:1.02rem}
.ad-meta{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--muted);font-size:.8rem;margin-top:5px}
.ad-meta span{display:inline-flex;align-items:center;gap:5px}
.ad-split{margin-top:14px;max-width:560px}
.gens{display:grid;grid-template-columns:repeat(auto-fill,minmax(236px,1fr));gap:16px;margin-top:18px}
.more{margin:14px 0 0;font-size:.82rem;color:var(--muted)}

/* generation card */
.gen{border:1px solid var(--line);border-radius:var(--r);display:flex;flex-direction:column;background:#fff;min-width:0;
  transition:border-color .15s,box-shadow .2s}
.gen:hover{border-color:var(--line-2);box-shadow:0 6px 20px -8px rgba(22,24,23,.14)}
.gen.is-live{border-color:var(--red-line)}
.shot{position:relative;display:block;height:232px;overflow:hidden;background:var(--wash);border-bottom:1px solid var(--line);
  border-radius:var(--r) var(--r) 0 0}
.phone{position:absolute;left:50%;top:18px;width:152px;height:300px;margin-left:-76px;border:1.5px solid var(--ink);
  border-radius:20px;background:#fff;overflow:hidden;padding:5px}
.phone .screen{position:relative;width:100%;height:100%;border-radius:15px;overflow:hidden;background:#fff}
.phone img.poster{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.9}
.phone iframe{position:absolute;top:0;left:0;width:390px;height:844px;border:0;transform:scale(.3615);
  transform-origin:0 0;pointer-events:none;background:#fff;max-width:none}
.shot .corner{position:absolute;top:10px;left:10px;display:flex;gap:6px;z-index:2}
.gen-body{padding:14px 14px 16px;display:flex;flex-direction:column;gap:10px;flex:1}
.gen-title{font-weight:600;color:var(--ink);font-size:.9rem;line-height:1.38;display:-webkit-box;-webkit-line-clamp:3;
  -webkit-box-orient:vertical;overflow:hidden;text-decoration:none}
.gen-title:hover{text-decoration:underline}
.facts{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:.76rem;color:var(--muted)}
.facts b{color:var(--ink);font-weight:500}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.gen-actions{display:flex;gap:8px;margin-top:auto;padding-top:4px}
.gen-actions .btn{flex:1}
.abstats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
.abstats div{padding:6px 8px;border-left:1px solid var(--line)} .abstats div:first-child{border-left:0}
.abstats span{display:block;font-size:.66rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.abstats b{font-size:.86rem;color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}

/* generation view */
.gv{display:grid;grid-template-columns:minmax(0,1fr) 360px;grid-template-rows:auto 1fr;gap:16px 28px;
  grid-template-areas:"main top" "main rest";align-items:start}
.gv-main{grid-area:main;min-width:0} .gv-top{grid-area:top;min-width:0} .gv-rest{grid-area:rest;min-width:0}
.two{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:24px;align-items:start}
.gv-title{font-size:1.45rem;max-width:44ch}
.preview-bar{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px;margin:0 0 12px}
.stage{border:1px solid var(--line);border-radius:var(--r);background:var(--wash);padding:28px 16px;display:flex;
  justify-content:center;gap:24px;overflow:hidden}
.device{display:flex;flex-direction:column;align-items:center;gap:10px;min-width:0}
.device .cap{font-size:.76rem;color:var(--muted);display:flex;gap:8px;align-items:center}
.device-frame{width:390px;max-width:100%;height:min(844px,78vh);border:1.5px solid var(--ink);border-radius:28px;
  padding:8px;background:#fff}
.device-frame iframe{display:block;width:100%;height:100%;border:0;border-radius:20px;background:#fff}
.stage.desktop{padding:0;background:#fff}
.stage.desktop .device{flex:1}
.stage.desktop .device-frame{width:100%;height:80vh;border:0;border-radius:0;padding:0}
.stage.desktop .device-frame iframe{border-radius:0}
.stage.desktop .device .cap{padding:10px 12px 0}
.stage.compare .device-frame{width:360px}
.stage.desktop.compare{flex-direction:column}
dl.meta{display:grid;grid-template-columns:118px minmax(0,1fr);gap:9px 12px;font-size:.84rem;margin:0}
dl.meta dt{color:var(--muted)} dl.meta dd{margin:0;color:var(--ink);min-width:0}
dl.meta dd code{font-size:.78rem}
.side-actions{display:flex;flex-direction:column;gap:8px;margin-top:14px}
.status-line{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.status-line .when{font-size:.8rem;color:var(--muted)}
.jobline{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:.84rem}
.jobline + .jobline{margin-top:6px}
.job-box{border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;margin:14px 0 0;font-size:.84rem}
.job-box .muted{font-size:.8rem;margin-top:4px}
.job-box .error{margin:10px 0 0}

/* lists, timeline, tables */
ul.plain{list-style:none;padding:0;margin:0}
ul.plain li{padding:10px 0;border-top:1px solid var(--line);font-size:.86rem}
ul.plain li:first-child{border-top:0;padding-top:0}
.timeline{list-style:none;margin:0;padding:0}
.timeline li{position:relative;padding:0 0 14px 18px;font-size:.84rem}
.timeline li::before{content:"";position:absolute;left:3px;top:7px;width:7px;height:7px;border-radius:50%;
  background:#fff;border:1.5px solid var(--line-2)}
.timeline li::after{content:"";position:absolute;left:6.5px;top:17px;bottom:0;width:1px;background:var(--line)}
.timeline li:last-child::after{display:none}
.timeline li.hot::before{border-color:var(--red);background:var(--red)}
.timeline .t{color:var(--muted);font-size:.76rem}
.versions{display:flex;flex-wrap:wrap;gap:6px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--r)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:.86rem}
th{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;background:var(--wash);white-space:nowrap}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:rgba(22,24,23,.015)}
table.stats td,table.stats th{padding:8px 10px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.shipped td{background:var(--red-wash)}
tr.shipped td:first-child > b{color:var(--red);font-size:.72rem;font-weight:600;margin-left:6px;text-transform:uppercase;
  letter-spacing:.05em}
.score{display:inline-flex;align-items:center;gap:6px;font-variant-numeric:tabular-nums}
.score i{display:block;width:30px;height:4px;border-radius:2px;background:var(--line);overflow:hidden;position:relative}
.score i b{position:absolute;left:0;top:0;bottom:0;background:var(--ink)}
pre.detail{white-space:pre-wrap;margin:0;font:.76rem/1.45 var(--mono);color:var(--muted)}
.pager{display:flex;gap:10px;align-items:center;justify-content:center;flex-wrap:wrap;margin-top:24px}

/* dialog */
dialog{border:1px solid var(--line);border-radius:var(--r);padding:0;width:calc(100% - 32px);max-width:540px;color:var(--text);
  background:#fff}
dialog:focus,dialog:focus-visible{outline:none}
dialog::backdrop{background:rgba(22,24,23,.42)}
.dlg-h{padding:22px 24px 0;display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.dlg-h p{margin:6px 0 0;color:var(--muted);font-size:.86rem}
.dlg-b{padding:8px 24px 20px}
.dlg-f{padding:14px 24px;border-top:1px solid var(--line);display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}
.checks{list-style:none;margin:16px 0 0;padding:0;font-size:.84rem}
.checks li{display:flex;gap:10px;padding:6px 0;color:var(--text)}
.checks li svg{color:var(--ink);margin-top:2px}
.checks li.no svg{color:var(--red)}
.url-preview{margin-top:8px;font-size:.8rem;color:var(--muted)}
.url-preview b{color:var(--ink);font-weight:500}
.handle-msg{font-size:.8rem;color:var(--red);margin-top:6px;min-height:1em}

/* the older reviewer pages (harness/serve.py) */
.state-needs_review,.state-changes_requested{color:var(--red)} .state-approved,.state-published{color:var(--ink);font-weight:500}
.state-rejected{color:var(--red)} .state-generated{color:var(--muted)}
.page-block{border:1px solid var(--line);border-radius:var(--r);padding:20px;margin:16px 0}
.page-block h2{display:flex;gap:10px;align-items:center;margin-bottom:12px}
.page-block iframe{width:100%;height:700px;border:1px solid var(--line);border-radius:var(--r);display:block;background:#fff}
.page-block iframe.mobile{width:390px}
.toggle-btn{height:30px;padding:0 12px;border-radius:999px;border:1px solid var(--line-2);background:#fff;font:500 .8rem var(--ui);
  color:var(--ink);cursor:pointer;margin:0 6px 10px 0}
.toggle-btn:hover{border-color:var(--ink)}
form.feedback{margin-top:12px}
form.feedback label{display:inline-block;min-width:60px;margin:8px 6px 8px 0}
form.feedback select{width:auto;height:34px;margin-right:12px}
.help{color:var(--muted);font-size:.82rem}
.actions{display:flex;flex-wrap:wrap;gap:8px}
.actions button,form > button[type=submit]:not(.btn){height:36px;padding:0 16px;border-radius:999px;border:1px solid var(--ink);
  background:var(--ink);color:#fff;font:500 .84rem var(--ui);cursor:pointer}
.actions button[value=changes]{background:#fff;color:var(--ink);border-color:var(--line-2)}
.actions button[value=reject]{background:#fff;color:var(--red);border-color:var(--red-line)}
.history{font-size:.8rem;color:var(--muted)}
.source-ad{background:var(--wash);border:1px solid var(--line);padding:14px;border-radius:var(--r);white-space:pre-wrap;font-size:.9rem}
.legacy h2{margin:28px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.image-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px;margin:16px 0}
.image-card{border:1px solid var(--line);border-radius:var(--r);padding:10px;font-size:.8rem;background:#fff}
.image-card img{width:100%;height:150px;object-fit:contain;background:var(--wash);display:block;border-radius:var(--r);margin-bottom:8px}
.image-card.excluded{opacity:.5}
.image-card .asset-id{font-family:var(--mono);font-size:.7rem;color:var(--muted);word-break:break-all}
.image-card .excluded-tag{color:var(--red);font-weight:600;font-size:.72rem;letter-spacing:.06em}
.image-card textarea{min-height:56px;margin:6px 0;font-size:.82rem}
.image-card select,.image-card input[type=text]{height:32px;margin:3px 0;font-size:.82rem}
.pool-counts{color:var(--muted)}
.linkrow{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}
.linkrow a{display:inline-flex;align-items:center;height:28px;padding:0 10px;border:1px solid var(--line);border-radius:var(--r);
  text-decoration:none;font-size:.8rem;color:var(--muted)}
.linkrow a:hover{border-color:var(--ink);color:var(--ink)}

/* phones */
@media (max-width:1040px){
  .gv{grid-template-columns:minmax(0,1fr);grid-template-rows:none;grid-template-areas:"top" "main" "rest"}
  .two{grid-template-columns:minmax(0,1fr)}
}
@media (max-width:760px){
  body{font-size:15px}
  .topbar-in{height:auto;flex-wrap:wrap;gap:0;padding:10px 16px 0}
  .brand{flex:1;height:36px}
  .who .email{display:none}
  .nav{order:3;flex-basis:100%;overflow-x:auto;padding:6px 16px 8px;margin:0 -16px;scrollbar-width:none}
  .nav::-webkit-scrollbar{display:none}
  main{padding:20px 16px 72px}
  h1{font-size:1.3rem} .gv-title{font-size:1.2rem}
  .page-head{margin-bottom:18px}
  .page-head .actions{width:100%} .page-head .actions .btn{flex:1}
  .toolbar{flex-direction:column;align-items:stretch}
  .search{max-width:none;min-width:0}
  .ad{padding:16px}
  .ad-thumb{width:52px;height:52px}
  .gens{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(232px,80%);grid-template-columns:none;overflow-x:auto;
    scroll-snap-type:x mandatory;scroll-padding:0 16px;margin:16px -16px 0;padding:0 16px 6px;scrollbar-width:none}
  .gens::-webkit-scrollbar{display:none}
  .gen{scroll-snap-align:start}
  .stage{padding:0;border:0;background:none}
  .device-frame{border:0;padding:0;border-radius:0;width:100%;height:76vh}
  .device-frame iframe{border:1px solid var(--line);border-radius:var(--r)}
  .stage.compare{flex-direction:column}
  .stage.compare .device-frame{width:100%}
  .card{padding:16px}
  dialog{width:100%;max-width:none;margin:auto 0 0;border-radius:var(--r) var(--r) 0 0;border-bottom:0}
  .dlg-h,.dlg-b,.dlg-f{padding-left:16px;padding-right:16px}
  .dlg-f .btn{flex:1}
  .rtable thead{display:none}
  .rtable,.rtable tbody,.rtable tr,.rtable td{display:block;width:100%}
  .rtable tr{border-bottom:1px solid var(--line);padding:10px 12px}
  .rtable td{border:0;padding:2px 0;display:flex;gap:10px}
  .rtable td::before{content:attr(data-label);flex:0 0 76px;color:var(--muted);font-size:.72rem;text-transform:uppercase;
    letter-spacing:.06em;padding-top:2px}
  .page-block iframe.mobile{width:100%}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

JS = """
function pkCopy(id){var el=document.getElementById(id);if(!el)return;var v=el.value||el.textContent;
  var done=function(){var b=document.getElementById(id+'-btn');if(!b)return;var t=b.getAttribute('data-label')||'Copy';
    b.setAttribute('data-copied','1');var s=b.querySelector('.lbl');if(s){s.textContent='Copied';}
    setTimeout(function(){if(s){s.textContent=t;}b.removeAttribute('data-copied');},1500);};
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(v).then(done,done);}
  else{el.select&&el.select();try{document.execCommand('copy');}catch(e){}done();}}
function pkWidth(mode){var s=document.querySelectorAll('.stage');for(var i=0;i<s.length;i++){
  s[i].classList.toggle('desktop',mode==='desktop');}
  var b=document.querySelectorAll('[data-view]');for(var j=0;j<b.length;j++){
  b[j].classList.toggle('on',b[j].getAttribute('data-view')===mode);}
  try{localStorage.setItem('pk-view',mode);}catch(e){}}
function pkHandle(input){var out=document.getElementById(input.getAttribute('data-preview'));
  var msg=document.getElementById(input.getAttribute('data-msg'));var v=input.value.trim();
  if(out){out.textContent=v||'\\u2026';}
  var bad='';if(!/^lp-/.test(v)){bad='Starts with lp-';}
  else if(!/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(v)||v.indexOf('--')>=0){bad='Lower-case letters, digits and single hyphens only';}
  else if(v.length>80){bad='80 characters at most';}
  if(msg){msg.textContent=bad;}input.setCustomValidity(bad);}
document.addEventListener('click',function(ev){
  var o=ev.target.closest('[data-dialog]');
  if(o){var d=document.getElementById(o.getAttribute('data-dialog'));
    if(d&&d.showModal){ev.preventDefault();d.showModal();var f=d.querySelector('input[type=text]');if(f){f.focus();f.select();}}}
  var c=ev.target.closest('[data-close]');if(c){ev.preventDefault();c.closest('dialog').close();}
  var v=ev.target.closest('[data-view]');if(v){pkWidth(v.getAttribute('data-view'));}
  if(ev.target.tagName==='DIALOG'){ev.target.close();}});
document.addEventListener('input',function(ev){if(ev.target.hasAttribute('data-preview')){pkHandle(ev.target);}});
document.addEventListener('change',function(ev){var t=ev.target;if(t.type==='file'&&t.closest('.drop')){
  var n=t.closest('.drop').querySelector('.file');if(n){n.textContent=t.files.length?t.files[0].name:'';}}});
document.addEventListener('DOMContentLoaded',function(){
  var s=document.querySelectorAll('svg[data-fit]');for(var i=0;i<s.length;i++){try{var b=s[i].getBBox();
    if(b.width){s[i].setAttribute('viewBox',[b.x,b.y,b.width,b.height].join(' '));}}catch(e){}}
  if(document.querySelector('.stage')){var m=null;try{m=localStorage.getItem('pk-view');}catch(e){}
    if(window.innerWidth<760){m='mobile';}if(m){pkWidth(m);}}
  var h=location.hash&&document.getElementById(location.hash.slice(1));
  if(h&&h.tagName==='DIALOG'&&h.showModal){h.showModal();}
  var drops=document.querySelectorAll('.drop');for(var k=0;k<drops.length;k++){(function(d){
    d.addEventListener('dragover',function(){d.classList.add('over');});
    d.addEventListener('dragleave',function(){d.classList.remove('over');});
    d.addEventListener('drop',function(){d.classList.remove('over');});})(drops[k]);}
});
"""

_ICONS = {
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "upload": '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "phone": '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>',
    "desktop": '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8"/><path d="M12 16v4"/>',
    "back": '<path d="M15 6l-6 6 6 6"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    "video": '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="M16 10l5-3v10l-5-3"/>',
    "text": '<path d="M5 6h14"/><path d="M5 11h14"/><path d="M5 16h9"/>',
    "live": '<circle cx="12" cy="12" r="2.5"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/>',
    "hide": '<path d="M3 3l18 18"/><path d="M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 8.5 4.5 9 6-.3.8-1.2 2.3-2.7 3.6"/><path d="M6.6 6.7C4.6 8 3.4 10 3 12c.5 1.5 4 6 9 6 1.6 0 3-.4 4.2-1.1"/><path d="M9.9 10a3 3 0 0 0 4.1 4.1"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "send": '<path d="M5 12h13"/><path d="M13 6l6 6-6 6"/>',
}


def icon(name, cls=""):
    return (f'<svg class="i {cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            f'{_ICONS.get(name, "")}</svg>')


def chip(text, kind="", *, dot=False):
    return f'<span class="chip {e(kind)}{" dot" if dot else ""}">{e(str(text))}</span>'


def copy_field(elem_id, value, *, label=None, live=False, open_link=True):
    """A read-only value with a Copy button (and an open-in-new-tab link)."""
    head = f'<div class="copy-label">{e(label)}</div>' if label else ""
    link = (f'<a class="iconbtn" href="{e(value)}" target="_blank" rel="noopener" title="Open">{icon("external")}'
            '<span class="sr">Open</span></a>') if open_link and str(value).startswith("http") else ""
    return (
        f'{head}<div class="copy{" live" if live else ""}">'
        f'<input type="text" readonly id="{elem_id}" value="{e(value)}" aria-label="{e(label or "Link")}">'
        f'<button type="button" class="btn ghost sm" id="{elem_id}-btn" data-label="Copy" onclick="pkCopy(\'{elem_id}\')">'
        f'{icon("copy")}<span class="lbl">Copy</span></button>{link}</div>'
    )


# ---------------------------------------------------------------------------
# the brand mark, from the tenant's brand folder
# ---------------------------------------------------------------------------

_LOGO_CACHE = {}


def logo_svg(brand_dir, label=""):
    """The tenant's logo paths (brand/logo-basalt.svg or logo.svg) in ink,
    as an inline SVG -- only the path data is kept, nothing executable.
    The page's script crops the viewBox to the drawing (data-fit)."""
    key = (str(brand_dir), label)
    if key in _LOGO_CACHE:
        return _LOGO_CACHE[key]
    svg = ""
    for name in ("logo-basalt.svg", "logo.svg"):
        path = Path(brand_dir) / name
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        box = re.search(r'viewBox="([\d.\s-]+)"', text)
        paths = re.findall(r'<path[^>]*\sd="([^"]+)"', text)
        if box and paths:
            svg = (f'<svg data-fit viewBox="{e(box.group(1))}" role="img" aria-label="{e(label)}">'
                   + "".join(f'<path fill="currentColor" d="{e(d)}"/>' for d in paths) + "</svg>")
            break
    _LOGO_CACHE[key] = svg
    return svg


# ---------------------------------------------------------------------------
# the shell
# ---------------------------------------------------------------------------

NAV = (
    ("ads_home", "Ads", ("ads_home", "generation", "gen_publish", "gen_replace", "gen_post_live", "gen_update_live",
                         "gen_unpublish")),
    ("upload_ad", "Upload", ("upload_ad",)),
    ("job_list", "Jobs", ("job_list", "job_detail")),
    ("run_list", "Runs", ("run_list", "run_detail")),
    ("image_library", "Images", ("image_library", "image_library_product")),
    ("audit_log", "Audit", ("audit_log",)),
)


def _active_jobs():
    tenant = current_app.config.get("SITE_TENANT")
    if tenant is None:
        return 0
    from . import jobs

    try:
        return sum(1 for j in jobs.list_jobs(tenant, limit=50) if j["state"] in ("queued", "running"))
    except Exception:  # noqa: BLE001 -- a counter must never break a page
        return 0


def shell(title, body, *, refresh=None, extra_css=""):
    email = getattr(g, "reviewer_email", "") or ""
    endpoint = request.endpoint or ""
    busy = _active_jobs()
    links = []
    for ep, label, group in NAV:
        badge = f' <span class="badge" title="{busy} job(s) queued or running">{busy}</span>' if ep == "job_list" and busy else ""
        on = ' class="on" aria-current="page"' if endpoint in group else ""
        links.append(f'<a href="{url_for(ep)}"{on}>{label}{badge}</a>')
    brand_dir = current_app.config.get("SITE_BRAND_DIR")
    name = current_app.config.get("SITE_NAME") or ""
    mark = logo_svg(brand_dir, name) if brand_dir else ""
    mark = mark or f'<span class="wordmark">{e(name.upper())}</span>'
    tag = e(current_app.config.get("SITE_TAG") or "Listicles")
    initial = e((email[:1] or "?"))
    meta_refresh = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f'<meta name="theme-color" content="{THEME_COLOR}">{icon_links()}{meta_refresh}<title>{e(title)}{" · " + e(name) if name else ""}</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{FONTS_HREF}">'
        f"<style>{CSS}{extra_css}</style><script>{JS}</script></head><body>"
        f'<header class="topbar"><div class="topbar-in"><a class="brand" href="{url_for("ads_home")}">{mark}'
        f'<span class="tag">{tag}</span></a><nav class="nav" aria-label="Main">{"".join(links)}</nav>'
        f'<div class="who" title="{e(email)}"><span class="email">{e(email)}</span>'
        f'<span class="avatar" aria-hidden="true">{initial}</span></div></div></header>'
        f"<main>{body}</main></body></html>"
    )
