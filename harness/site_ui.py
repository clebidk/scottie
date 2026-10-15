"""Cycle 80: the review site's look -- one shell, one stylesheet, a few
components -- shared by the listicle site (harness/site.py) and the older
reviewer pages (harness/serve.py: runs, run detail, image library).

Owner direction (2026-10-06): the layout and components of the team
dashboard (a fixed left sidebar with grouped navigation, a top bar with a
search pill, flat 8px cards), recoloured with the brand's official palette
and no dark sidebar and no Solar Flare: sidebar Fossil Dust #C0C8C3 with
Basalt text and a Stone active row, main ground Stone #EFE3D2, cards a
lighter Stone #F7F1E8, Cedar-tinted hairlines, Basalt #181918 text and
primary buttons, Red #702B33 only for live / attention. Owner change
2026-10-07 (cycle 86): the page, cards and fields are white (#FFFFFF), cards
keep their Cedar hairline border; Stone stays only as the sidebar's active
row, and the light wash (#E9DFCD) behind previews and table heads. Acid Grotesk (the
webfont the generator already loads) with a Helvetica Neue fallback, Geist
Mono for numbers. Vanilla CSS and a few lines of JS; no build step.
"""
import html
import re
from pathlib import Path

from flask import current_app, g, request, url_for

e = html.escape

TOKENS = {
    "basalt": "#181918", "fossil": "#C0C8C3", "stone": "#EFE3D2", "card": "#F7F1E8", "red": "#702B33",
    "cedar": "#483215", "muted": "#5E5548",
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


FONTS_HREF = "https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500;600&display=swap"
# The generator's own Acid Grotesk webfont (cycle 79, the storefront theme's CDN).
ACID_GROTESK_URL = "https://cdn.shopify.com/s/files/1/0825/8312/6317/t/64/assets/pk-acid-grotesk-trial.otf"

CSS = """
@font-face{font-family:"Acid Grotesk";src:url("@@ACID@@") format("opentype");font-weight:400 800;font-display:swap}
:root{--basalt:#181918;--fossil:#C0C8C3;--stone:#EFE3D2;--cedar:#483215;
  --ink:#181918;--text:#181918;--muted:#5E5548;--faint:#857B6E;--bg:#FFFFFF;--card:#FFFFFF;--field:#FFFFFF;
  --line:rgba(72,50,21,.16);--line-2:rgba(72,50,21,.30);--hover:rgba(72,50,21,.06);--wash:#E9DFCD;
  --red:#702B33;--red-wash:rgba(112,43,51,.09);--red-line:rgba(112,43,51,.34);--r:8px;
  --side:#C0C8C3;--side-ink:#181918;--side-muted:#3D4240;--side-line:rgba(24,25,24,.14);--side-active:#EFE3D2;
  --ui:"Acid Grotesk","Helvetica Neue",Helvetica,Arial,sans-serif;
  --head:"Acid Grotesk","Helvetica Neue",Helvetica,Arial,sans-serif;
  --mono:"Geist Mono",ui-monospace,"SF Mono",Menlo,monospace}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 var(--ui);overflow-wrap:anywhere;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
a{color:inherit;text-decoration-color:var(--line-2);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
h1,h2,h3{font-family:var(--head);color:var(--ink);font-weight:700;letter-spacing:-.015em;line-height:1.25;margin:0}
h1{font-size:1.55rem} h2{font-size:1.08rem} h3{font-size:.95rem}
p{margin:0 0 .8em}
code{font:.84em var(--mono);background:var(--wash);border:1px solid var(--line);padding:0 4px;border-radius:var(--r)}
img,video,iframe{max-width:100%}
.muted{color:var(--muted)} .small{font-size:.82rem} .nowrap{white-space:nowrap} .num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
svg.i{width:16px;height:16px;flex:none;stroke:currentColor;fill:none;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}

/* app shell: sidebar + main (the team dashboard's layout) */
.app{display:grid;grid-template-columns:236px minmax(0,1fr);min-height:100vh}
.side{position:sticky;top:0;height:100vh;overflow-y:auto;background:var(--side);color:var(--side-ink);
  border-right:1px solid var(--side-line);display:flex;flex-direction:column;padding:20px 0 16px;scrollbar-width:none}
.side::-webkit-scrollbar{display:none}
.brand{display:flex;flex-direction:column;gap:4px;padding:2px 20px 18px;text-decoration:none;color:var(--side-ink)}
.brand svg{height:16px;width:auto;display:block;align-self:flex-start}
.brand .wordmark{font-weight:700;letter-spacing:.08em}
.brand small{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;
  color:var(--side-muted)}
.navsec{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.14em;text-transform:uppercase;
  color:var(--side-muted);padding:14px 20px 6px}
.nav{display:flex;flex-direction:column}
.nav a{display:flex;align-items:center;gap:10px;padding:8px 20px;color:var(--side-ink);text-decoration:none;
  font-weight:500;font-size:.87rem;border-left:2px solid transparent;white-space:nowrap}
.nav a svg.i{width:15px;height:15px;opacity:.7}
.nav a:hover{background:rgba(239,227,210,.45)}
.nav a.on{background:var(--side-active);border-left-color:var(--side-ink);font-weight:600}
.nav a.on svg.i{opacity:1}
.nav .badge{margin-left:auto;min-width:20px;height:18px;padding:0 6px;border-radius:9px;background:var(--red);color:#fff;
  font:600 .68rem/18px var(--mono);text-align:center}
.side .spacer{flex:1;min-height:14px}
.side .me{display:flex;align-items:center;gap:9px;padding:14px 20px 0;margin-top:6px;border-top:1px solid var(--side-line)}
.side .me .nm{font-size:.82rem;font-weight:600;line-height:1.2}
.side .me .em{font-size:.7rem;color:var(--side-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px}
.side .foot{padding:10px 20px 0;font-family:var(--mono);font-size:10px;letter-spacing:.06em;color:var(--side-muted)}
.avatar{width:30px;height:30px;border-radius:50%;background:var(--basalt);color:var(--stone);display:grid;place-items:center;
  font-size:.74rem;font-weight:700;text-transform:uppercase;flex:none}
.main{min-width:0;display:flex;flex-direction:column}
.topbar{display:flex;align-items:center;gap:16px;padding:16px 32px 14px;position:sticky;top:0;z-index:20;
  background:rgba(255,255,255,.94);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
.qsearch{position:relative;flex:1;max-width:520px;margin:0}
.qsearch input[type=search]{height:40px;border-radius:999px;padding:0 54px 0 38px;background:var(--field);border-color:var(--line)}
.qsearch svg.i{position:absolute;left:14px;top:12px;color:var(--muted)}
.qsearch .kbd{position:absolute;right:12px;top:10px;font:500 .68rem/18px var(--mono);color:var(--muted);
  border:1px solid var(--line-2);border-radius:5px;padding:0 5px;background:var(--card)}
.profile{display:flex;align-items:center;gap:9px;margin-left:auto}
.profile .nm{font-size:.84rem;font-weight:600;line-height:1.15}
.profile .em{font-size:.72rem;color:var(--muted)}
main.content{width:100%;max-width:1320px;padding:12px 32px 96px}

/* page header */
.page-head{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px 24px;margin-bottom:24px}
.page-head .sub{margin:6px 0 0;color:var(--muted);font-size:.9rem}
.page-head .actions{display:flex;gap:8px;flex-wrap:wrap}
.crumb{display:inline-flex;align-items:center;gap:6px;font-size:.84rem;color:var(--muted);text-decoration:none;margin-bottom:12px}
.crumb:hover{color:var(--ink)}
.eyebrow{font:500 .68rem var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}

/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:38px;padding:0 18px;border-radius:999px;
  border:1px solid var(--ink);background:var(--ink);color:#fff;font:500 .88rem/1 var(--ui);text-decoration:none;
  cursor:pointer;white-space:nowrap;transition:background-color .15s,border-color .15s,color .15s}
.btn:hover{background:#2E302E;border-color:#2E302E}
.btn:focus-visible,.iconbtn:focus-visible,.seg a:focus-visible,.nav a:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.btn.ghost{background:var(--field);color:var(--ink);border-color:var(--line-2)}
.btn.ghost:hover{border-color:var(--ink);background:var(--field)}
.btn.danger{background:var(--field);color:var(--red);border-color:var(--red-line)}
.btn.danger:hover{border-color:var(--red);background:var(--red-wash)}
.btn.live{background:var(--red);border-color:var(--red)} .btn.live:hover{background:#5C232A;border-color:#5C232A}
.btn.sm{height:30px;padding:0 13px;font-size:.8rem;gap:6px}
.btn.block{width:100%}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.iconbtn{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:var(--r);border:0;background:none;
  color:var(--muted);cursor:pointer;flex:none}
.iconbtn:hover{background:var(--hover);color:var(--ink)}
.linkbtn{background:none;border:0;padding:0;font:inherit;color:var(--muted);text-decoration:underline;
  text-decoration-color:var(--line-2);text-underline-offset:3px;cursor:pointer}

/* chips */
.chip{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 9px;border-radius:999px;
  border:1px solid var(--line-2);font-size:.74rem;font-weight:500;color:var(--muted);background:var(--field);white-space:nowrap;line-height:1}
.chip.dot::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.chip.live{color:var(--red);border-color:var(--red-line);background:var(--red-wash)}
.chip.approved,.chip.pass,.chip.done{color:var(--ink);border-color:var(--line-2)}
.chip.draft,.chip.hidden,.chip.queued{color:var(--muted)}
.chip.attn,.chip.fail,.chip.failed,.chip.rejected{color:var(--red);border-color:var(--red-line)}
.chip.running{color:var(--ink);border-color:var(--line-2)}
.chip.running::before{content:"";width:8px;height:8px;border-radius:50%;border:1.5px solid currentColor;
  border-right-color:transparent;animation:spin .8s linear infinite;flex:none}
.chip.solid{background:var(--ink);color:var(--stone);border-color:var(--ink)}
/* cycle 83: the page is the destination of a Meta ad (muted: no ACTIVE ad) */
.adlink{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:999px;
  border:1px solid var(--red-line);color:var(--red);background:var(--red-wash);cursor:help;flex:none}
.adlink svg.i{width:13px;height:13px}
.adlink.off{border-color:var(--line-2);color:var(--faint);background:var(--field)}
@keyframes spin{to{transform:rotate(360deg)}}

/* cards, sections */
.card{border:1px solid var(--line);border-radius:var(--r);background:var(--card);padding:20px}
.card + .card{margin-top:16px}
.two > .card,.stack > .card{margin-top:0}
.card-h{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
.card-h h2,.card-h h3{display:flex;align-items:center;gap:8px}
.stack{display:flex;flex-direction:column;gap:16px}
.row{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center}
.hr{border:0;border-top:1px solid var(--line);margin:16px 0}
.notice{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);border-radius:var(--r);padding:12px 14px;
  font-size:.88rem;margin:0 0 16px;background:var(--card)}
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
  border-radius:var(--r);padding:0 12px;font:inherit;font-size:.92rem;background:var(--field);color:var(--text);transition:border-color .15s}
textarea{height:auto;min-height:132px;padding:10px 12px;line-height:1.5;resize:vertical}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--ink);box-shadow:0 0 0 3px rgba(72,50,21,.12)}
input::placeholder,textarea::placeholder{color:var(--faint)}
.hint{font-size:.8rem;color:var(--muted);margin:6px 0 0}
.field-pre{display:flex;align-items:stretch;border:1px solid var(--line-2);border-radius:var(--r);background:var(--field);overflow:hidden}
.field-pre:focus-within{border-color:var(--ink);box-shadow:0 0 0 3px rgba(72,50,21,.12)}
.field-pre span{display:flex;align-items:center;padding:0 0 0 12px;color:var(--muted);font-size:.86rem;white-space:nowrap}
.field-pre input{border:0;box-shadow:none!important;padding-left:2px}
.drop{position:relative;border:1px dashed var(--line-2);border-radius:var(--r);padding:28px 16px;text-align:center;
  color:var(--muted);background:var(--wash);transition:border-color .15s,background-color .15s}
.drop:hover,.drop.over{border-color:var(--ink);background:var(--field)}
.drop input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.drop b{color:var(--ink);font-weight:500}
.drop .file{margin-top:8px;color:var(--ink);font-size:.86rem;font-weight:500}

/* copy field */
.copy{display:flex;align-items:center;gap:4px;border:1px solid var(--line);border-radius:var(--r);height:36px;
  padding:0 3px 0 10px;background:var(--field);min-width:0}
.copy input{border:0;height:32px;padding:0;font-size:.82rem;background:none;color:var(--ink);min-width:0;flex:1;box-shadow:none!important}
.copy a.iconbtn{text-decoration:none}
.copy-label{font-size:.74rem;color:var(--muted);margin:0 0 4px;display:flex;align-items:center;gap:6px}
.copy.live{border-color:var(--red-line)}

/* toolbar */
.toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:20px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:3px;gap:2px;background:var(--card);max-width:100%;overflow-x:auto}
.seg a,.seg button{border:0;background:none;border-radius:999px;padding:0 14px;height:30px;display:inline-flex;align-items:center;
  gap:6px;font:500 .84rem var(--ui);color:var(--muted);text-decoration:none;cursor:pointer;white-space:nowrap}
.seg a:hover,.seg button:hover{color:var(--ink)}
.seg .on{background:var(--ink);color:var(--stone)}
.seg .on:hover{color:var(--stone)}
.seg .n{opacity:.7;font-family:var(--mono);font-size:.76rem;font-variant-numeric:tabular-nums}
.search{display:flex;gap:8px;flex:1;max-width:380px;min-width:220px}
.search .field{position:relative;flex:1}
.search svg{position:absolute;left:11px;top:12px;color:var(--faint)}
.search input{padding-left:34px}
.statline{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--muted);font-size:.84rem;margin:0 0 20px}
.statline b{color:var(--ink);font-weight:600}
.busy{display:inline-flex;align-items:center;gap:8px}

/* ad cards */
.ad{border:1px solid var(--line);border-radius:var(--r);padding:20px;margin:0 0 16px;background:var(--card);transition:border-color .15s}
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
/* desktop: two ad cards side by side (the phone keeps one column) */
@media (min-width:1100px){.ads{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
  .ads > .ad{margin:0}}

/* generation card */
.gen{border:1px solid var(--line);border-radius:var(--r);display:flex;flex-direction:column;background:var(--field);min-width:0;
  transition:border-color .15s,box-shadow .2s}
.gen:hover{border-color:var(--line-2)}
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
.facts b{color:var(--ink);font:500 .76rem var(--mono)}
.facts.quality{margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.gen-actions{display:flex;gap:8px;margin-top:auto;padding-top:4px}
.gen-actions .btn{flex:1}
.abstats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
.abstats div{padding:6px 8px;border-left:1px solid var(--line)} .abstats div:first-child{border-left:0}
.abstats span{display:block;font-size:.66rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.abstats b{font:600 .84rem var(--mono);color:var(--ink)}

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
.stage.desktop{padding:0;background:var(--card)}
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
.timeline .t{color:var(--muted);font:.72rem var(--mono)}
.versions{display:flex;flex-wrap:wrap;gap:6px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--r)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:.86rem}
th{font:500 .66rem var(--mono);text-transform:uppercase;letter-spacing:.1em;color:var(--muted);background:var(--wash);white-space:nowrap}
tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--hover)}
table.stats td,table.stats th{padding:8px 10px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.num{font-family:var(--mono)}
tr.shipped td{background:var(--red-wash)}
tr.shipped td:first-child > b{color:var(--red);font-size:.72rem;font-weight:600;margin-left:6px;text-transform:uppercase;
  letter-spacing:.05em}
.score{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:.8rem}
.score i{display:block;width:30px;height:4px;border-radius:2px;background:var(--line);overflow:hidden;position:relative}
.score i b{position:absolute;left:0;top:0;bottom:0;background:var(--ink)}
pre.detail{white-space:pre-wrap;margin:0;font:.76rem/1.45 var(--mono);color:var(--muted)}
.pager{display:flex;gap:10px;align-items:center;justify-content:center;flex-wrap:wrap;margin-top:24px}

/* dialog */
dialog{border:1px solid var(--line);border-radius:var(--r);padding:0;width:calc(100% - 32px);max-width:540px;color:var(--text);
  background:var(--card)}
dialog:focus,dialog:focus-visible{outline:none}
dialog::backdrop{background:rgba(24,25,24,.42)}
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
.toggle-btn{height:30px;padding:0 12px;border-radius:999px;border:1px solid var(--line-2);background:var(--field);font:500 .8rem var(--ui);
  color:var(--ink);cursor:pointer;margin:0 6px 10px 0}
.toggle-btn:hover{border-color:var(--ink)}
form.feedback{margin-top:12px}
form.feedback label{display:inline-block;min-width:60px;margin:8px 6px 8px 0}
form.feedback select{width:auto;height:34px;margin-right:12px}
.help{color:var(--muted);font-size:.82rem}
.actions{display:flex;flex-wrap:wrap;gap:8px}
.actions button,form > button[type=submit]:not(.btn){height:36px;padding:0 16px;border-radius:999px;border:1px solid var(--ink);
  background:var(--ink);color:#fff;font:500 .84rem var(--ui);cursor:pointer}
.actions button[value=changes]{background:var(--field);color:var(--ink);border-color:var(--line-2)}
.actions button[value=reject]{background:var(--field);color:var(--red);border-color:var(--red-line)}
.history{font-size:.8rem;color:var(--muted)}
.source-ad{background:var(--wash);border:1px solid var(--line);padding:14px;border-radius:var(--r);white-space:pre-wrap;font-size:.9rem}
.legacy h2{margin:28px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.image-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px;margin:16px 0}
.image-card{border:1px solid var(--line);border-radius:var(--r);padding:10px;font-size:.8rem;background:var(--card)}
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

/* cycle 86: the Library (every page version as a reference) */
.lib-note{margin:0 0 14px}
.lib-nav{display:flex;flex-wrap:wrap;gap:6px}
.lib-group{margin-top:28px}
.lib-group .card-h{margin-bottom:4px}
.lib-group .card-h h2{font-size:1.05rem}
.lib-purpose{margin:0 0 14px;max-width:760px}
.lib-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.lib-card .phone img.lib-poster{opacity:1;object-position:top}
.lib-card .gen-actions{flex-wrap:wrap}
.lib-text{display:flex;align-items:center;justify-content:center;padding:22px;text-align:center}
.lib-text b{font-size:.95rem;line-height:1.4;color:var(--ink)}
.lib-summary{margin:0;line-height:1.45}
.lib-why{margin:0;color:var(--red)}
.lib-card [hidden]{display:none!important}
.lib-card .shot.lib-shot{height:380px}
.lib-card .lib-shot .phone{width:200px;height:433px;margin-left:-100px}
.lib-noshot{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:18px;
  text-align:center;font-size:.85rem;line-height:1.4;color:var(--ink)}
.lib-card .lib-summary{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.lib-current{margin:0}
.lib-pic{display:flex;flex-direction:column;gap:6px}
.lib-pic .lib-current,.lib-from{margin:0;padding:0 14px;text-align:center}
.lib-toggle{align-self:flex-start;font-size:.8rem;font-weight:600;color:var(--ink);text-decoration:underline;
  text-underline-offset:3px;cursor:pointer}
.lib-toggle[aria-expanded="true"]{text-decoration:none}
.lib-more{display:flex;flex-direction:column;gap:8px}
.lib-strip{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}
.lib-var{flex:none;width:66px;display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:.68rem;
  line-height:1.25;text-decoration:none}
.lib-var-shot{display:block;width:66px;height:96px;border:1px solid var(--line);border-radius:6px;overflow:hidden;
  background:var(--wash)}
.lib-var-shot img{display:block;width:100%;height:100%;object-fit:cover;object-position:top}
.lib-var.on{color:var(--ink)}
.lib-var.on .lib-var-shot{border-color:var(--ink);box-shadow:0 0 0 1px var(--ink)}
.lib-offs{display:flex;flex-wrap:wrap;gap:6px}
.lib-off{height:auto;min-height:22px;padding:4px 9px;white-space:normal;line-height:1.3;cursor:help}
/* cycle 102: an imported look's source, as a chip with a thumbnail that grows on hover */
.lib-source{position:relative;text-decoration:none;padding-left:3px}
.lib-source img{height:16px;width:12px;object-fit:cover;object-position:top;border-radius:999px}
.lib-source-pop{display:none;position:absolute;z-index:5;left:0;top:26px;width:180px;border:1px solid var(--line-2);
  border-radius:8px;background:var(--bg);overflow:hidden}
.lib-source-pop img{display:block;width:100%;height:auto;border-radius:0}
.lib-source:hover .lib-source-pop,.lib-source:focus .lib-source-pop{display:block}
/* cycle 102: the import job -- its steps, then the source beside the render */
.li-steps{list-style:none;margin:0 0 12px;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.li-steps li{font-size:.74rem;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:3px 9px}
.li-steps li.done{color:var(--ink)}
.li-steps li.now{color:var(--red);border-color:var(--red-line);font-weight:600}
.li-compare{display:flex;gap:12px;margin:0 0 12px}
.li-compare figure{margin:0;flex:1;min-width:0;max-width:220px}
.li-compare img{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:8px}
.li-compare figcaption{font-size:.72rem;color:var(--muted);margin-top:4px}
.li-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
/* cycle 102d: "Revise this look" and a revise's before/after */
.li-revise{margin:12px 0 0}
.li-revise summary{cursor:pointer;font-size:.84rem;font-weight:500;color:var(--ink)}
.li-revise form{margin-top:8px}
.li-compare-1280 figure{max-width:none}
.li-actions form{margin:0}
.notice.pin{display:block;margin:0 0 16px}

/* cycle 96: the Photos page */
.ph-tabs{margin:0 0 18px}
.ph-jobs ul{margin:4px 0 0;padding-left:18px}
.ph-jobs .ph-step{font-weight:600}
.ph-top{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:16px;align-items:start;margin-bottom:28px}
.ph-top > .card{margin:0}
.ph-cov-grid{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:20px}
.ph-cov .card-h{flex-wrap:wrap;gap:4px 12px}
.ph-cov .card-h h2{flex:none}
.ph-cov h3{font-size:.95rem;margin:0 0 8px}
.ph-cov h3 + ol,.ph-cov h3 + ul{margin-top:0}
.ph-mat{width:100%;border-collapse:collapse;font-size:.8rem}
.ph-mat th,.ph-mat td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap;background:none}
.ph-mat th:first-child{text-align:left;font:500 .78rem var(--ui);text-transform:none;letter-spacing:0;color:var(--ink)}
.ph-mat thead th{font:500 .68rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);background:var(--wash)}
.ph-mat td.z{color:var(--faint)}
.ph-mat td.tot{font-weight:600}
.ph-flagline{margin:8px 0 0}
.ph-models{flex-wrap:wrap;gap:6px}
.ph-models .chip b{color:var(--ink);font-weight:600}
.ph-shoot{padding-left:20px;margin:0 0 16px;font-size:.86rem}
.ph-shoot li{margin:0 0 6px}
.ph-rules{list-style:none;padding:0;margin:0;font-size:.84rem}
.ph-rules li{display:flex;gap:8px;align-items:flex-start;margin:0 0 6px}
.ph-rules svg.i{flex:none;width:16px;height:16px;margin-top:2px}
.ph-up .drop{padding:22px 14px}
.ph-drive{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.ph-drive .hint{margin:0}
.ph-lib > .card-h{margin-bottom:8px}
.ph-filters{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:0 0 10px}
.ph-filters select{height:36px;font-size:.84rem}
.ph-vocab{margin:-8px 0 16px}
.ph-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;align-items:start}
.ph-card{border:1px solid var(--line);border-radius:var(--r);background:var(--card);overflow:hidden;min-width:0}
.ph-card.off .ph-img img{opacity:.45}
.ph-img{position:relative;background:var(--wash);cursor:crosshair;line-height:0}
.ph-img img{display:block;width:100%;height:auto}
.ph-img.saving{opacity:.6}
.ph-dot{position:absolute;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;border:2px solid #fff;
  box-shadow:0 0 0 1px rgba(0,0,0,.55);background:rgba(112,43,51,.85);pointer-events:none}
.ph-b{padding:12px 14px 14px;display:flex;flex-direction:column;gap:8px;min-width:0}
.ph-b > *{min-width:0;max-width:100%}
.ph-b .chips{display:flex;flex-wrap:wrap;gap:5px}
.ph-id{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:baseline;font-size:.72rem;color:var(--muted)}
.ph-id code{font-size:.7rem}
.ph-tags{display:flex;flex-wrap:wrap;gap:4px}
.ph-tags span{font-size:.74rem;border-radius:4px;background:var(--wash);padding:1px 7px;color:var(--ink)}
.ph-tags span.none{background:none;padding:0;color:var(--faint)}
.ph-desc{margin:0;font-size:.82rem;line-height:1.4}
.ph-src{margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ph-used a{color:var(--ink)}
.ph-edit summary{cursor:pointer;font-weight:500;font-size:.84rem;color:var(--ink)}
.ph-edit label{margin:10px 0 4px;font-size:.78rem}
.ph-edit input[type=text],.ph-edit select{height:34px;font-size:.84rem;padding:0 8px}
.ph-edit input[type=number]{width:84px;height:34px;border:1px solid var(--line-2);border-radius:var(--r);padding:0 8px;font:inherit;font-size:.84rem}
.ph-sel{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-bottom:10px}
.ph-focal{margin-top:4px}
.ph-acts{display:flex;flex-wrap:wrap;gap:6px}
.ph-pager{margin-top:20px}
@media (max-width:1100px){.ph-top{grid-template-columns:minmax(0,1fr)}}
@media (max-width:760px){
  .ph-cov-grid{grid-template-columns:minmax(0,1fr)}
  .ph-top .ph-up{order:-1}
  .ph-filters{grid-template-columns:repeat(2,minmax(0,1fr))}
  .ph-grid{grid-template-columns:minmax(0,1fr)}
  .ph-sel{grid-template-columns:minmax(0,1fr)}
}

/* phones */
@media (max-width:1040px){
  .gv{grid-template-columns:minmax(0,1fr);grid-template-rows:none;grid-template-areas:"top" "main" "rest"}
  .two{grid-template-columns:minmax(0,1fr)}
}
/* phones and small tablets: the sidebar becomes a top bar with a sideways-scrolling row of nav pills */
@media (max-width:900px){
  .app{grid-template-columns:minmax(0,1fr)}
  .side{position:sticky;top:0;z-index:30;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;
    padding:12px 16px 0;border-right:0;border-bottom:1px solid var(--side-line)}
  .brand{flex-direction:row;align-items:center;gap:10px;padding:4px 0 10px;flex:1}
  .navsec,.side .spacer,.side .foot{display:none}
  .side .me{border-top:0;margin:0;padding:0 0 10px}
  .side .me > div{display:none}
  .navs{order:3;flex-basis:100%;display:flex;gap:4px;overflow-x:auto;margin:0 -16px;padding:0 16px 10px;scrollbar-width:none}
  .navs::-webkit-scrollbar{display:none}
  .nav{flex-direction:row;gap:4px}
  .nav a{border-left:0;border-radius:999px;padding:6px 12px;font-size:.84rem}
  .nav a.on{background:var(--side-active);border-left:0}
  .nav .badge{margin-left:2px}
  .topbar{position:static;padding:14px 16px 6px;background:none;-webkit-backdrop-filter:none;backdrop-filter:none}
  .qsearch{max-width:none}
  .qsearch .kbd,.profile{display:none}
}
@media (max-width:760px){
  body{font-size:15px}
  main.content{padding:8px 16px 72px}
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
  .lib-cards{grid-template-columns:minmax(0,1fr)}
  .page-head .actions form,.page-head .actions form .btn{width:100%}
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
""".replace("@@ACID@@", ACID_GROTESK_URL)

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
function pkLibVariant(v){var c=v.closest('.lib-card');if(!c)return;
  var q=function(s){return c.querySelector(s);},t=v.getAttribute('data-thumb'),r=v.getAttribute('data-review'),
    g=v.getAttribute('data-generate'),img=q('.lib-poster'),txt=q('.lib-noshot'),gen=q('[data-lib-generate]');
  if(t){img.src=t;img.hidden=false;txt.hidden=true;}else{img.hidden=true;txt.textContent=v.getAttribute('data-text');txt.hidden=false;}
  var links=c.querySelectorAll('a.lib-shot,[data-lib-review]');for(var i=0;i<links.length;i++){links[i].href=r;}
  if(gen){gen.hidden=!g;if(g){gen.href=g;}}
  var cur=q('[data-lib-current]');if(cur){cur.textContent=v.getAttribute('data-label');cur.parentNode.hidden=false;}
  var from=q('[data-lib-from]');if(from){from.hidden=true;}
  var all=c.querySelectorAll('[data-lib-variant]');for(var j=0;j<all.length;j++){all[j].classList.toggle('on',all[j]===v);}}
document.addEventListener('click',function(ev){
  var o=ev.target.closest('[data-dialog]');
  if(o){var d=document.getElementById(o.getAttribute('data-dialog'));
    if(d&&d.showModal){ev.preventDefault();d.showModal();var f=d.querySelector('input[type=text]');if(f){f.focus();f.select();}}}
  var c=ev.target.closest('[data-close]');if(c){ev.preventDefault();c.closest('dialog').close();}
  var v=ev.target.closest('[data-view]');if(v){pkWidth(v.getAttribute('data-view'));}
  var lv=ev.target.closest('[data-lib-variant]');if(lv&&!ev.metaKey&&!ev.ctrlKey){ev.preventDefault();pkLibVariant(lv);}
  var lt=ev.target.closest('[data-lib-toggle]');if(lt&&!ev.metaKey&&!ev.ctrlKey){
    var lc=lt.closest('.lib-card'),m=lc&&lc.querySelector('.lib-more');
    if(m){ev.preventDefault();m.hidden=lt.classList.contains('lib-shot')?false:!m.hidden;
      var ts=lc.querySelectorAll('.lib-toggle');for(var k=0;k<ts.length;k++){ts[k].setAttribute('aria-expanded',String(!m.hidden));}}}
  if(ev.target.tagName==='DIALOG'){ev.target.close();}});
document.addEventListener('keydown',function(ev){if((ev.metaKey||ev.ctrlKey)&&(ev.key==='k'||ev.key==='K')){
  var q=document.getElementById('q');if(q){ev.preventDefault();q.focus();q.select();}}});
document.addEventListener('input',function(ev){if(ev.target.hasAttribute('data-preview')){pkHandle(ev.target);}});
document.addEventListener('change',function(ev){var t=ev.target;if(t.type==='file'&&t.closest('.drop')){
  var n=t.closest('.drop').querySelector('.file');
  if(n){n.textContent=t.files.length>1?t.files.length+' files: '+t.files[0].name+', \u2026':t.files.length?t.files[0].name:'';}}});
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
    "link": '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
    "live": '<circle cx="12" cy="12" r="2.5"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/>',
    "hide": '<path d="M3 3l18 18"/><path d="M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 8.5 4.5 9 6-.3.8-1.2 2.3-2.7 3.6"/><path d="M6.6 6.7C4.6 8 3.4 10 3 12c.5 1.5 4 6 9 6 1.6 0 3-.4 4.2-1.1"/><path d="M9.9 10a3 3 0 0 0 4.1 4.1"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "send": '<path d="M5 12h13"/><path d="M13 6l6 6-6 6"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "layers": '<path d="M12 4l8 4-8 4-8-4 8-4z"/><path d="M4 12l8 4 8-4"/><path d="M4 16l8 4 8-4"/>',
    "chart": '<path d="M4 20h16"/><path d="M7 16v-5"/><path d="M12 16V7"/><path d="M17 16v-9"/>',
    "list": '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
}


def icon(name, cls=""):
    return (f'<svg class="i {cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            f'{_ICONS.get(name, "")}</svg>')


def chip(text, kind="", *, dot=False):
    return f'<span class="chip {e(kind)}{" dot" if dot else ""}">{e(str(text))}</span>'


# Cycle 110: a page's quality numbers (state.json <page>.quality: the landing
# page's from its gates, the listicle's from listicle_persuasion), "—" for
# each one a run did not record.
QUALITY_FIELDS = (
    ("hook_match", "Hook", "First screen vs the ad's hook: share of the hook's words (1.00 = quotes the speaker)"),
    ("grade_level", "Grade", "Reading grade (Flesch-Kincaid); 7.5 or lower passes"),
    ("cta_count", "CTAs", "Links to the CTA url on the page"),
    ("proof_count", "Proof", "Verified proof elements shown: rating, units line, review quotes"),
    ("lcp_ms", "LCP", "Largest contentful paint, ms"),
    ("cls", "CLS", "Cumulative layout shift"),
    ("first_screen_ok", "First screen", "Hook match, proof and a CTA in the first screen"),
)
DASH = "&mdash;"


def quality_value(key, value):
    """One quality number as text (HTML-safe); DASH when absent."""
    if value is None or value == "":
        return DASH
    if isinstance(value, bool):
        return "&#10003;" if value else "&#10007;"
    if key == "hook_match" and isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    if key == "grade_level" and isinstance(value, (int, float)):
        return f"{float(value):.1f}"
    if key == "cls" and isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    if key == "lcp_ms" and isinstance(value, (int, float)):
        return f"{int(round(value))} ms"
    return e(str(value))


def quality_facts(quality):
    """The quality numbers as a card's fact row: every field, DASH for each
    one the run did not record."""
    quality = quality if isinstance(quality, dict) else {}
    bits = "".join(f'<span title="{e(title)}">{e(label)} <b>{quality_value(key, quality.get(key))}</b></span>'
                   for key, label, title in QUALITY_FIELDS)
    return f'<div class="facts quality" aria-label="Quality">{bits}</div>'


def quality_compact(quality):
    """Hook / grade / first screen in one short cell (the results table)."""
    quality = quality if isinstance(quality, dict) else {}
    if not quality:
        return DASH
    return " &middot; ".join(f'{label} {quality_value(key, quality.get(key))}' for key, label, _t in QUALITY_FIELDS
                             if key in ("hook_match", "grade_level", "first_screen_ok"))


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

# (section, [(endpoint, label, icon, endpoints that light it up)]) -- the
# dashboard's grouped sidebar.
NAV = (
    ("Review", (
        ("ads_home", "Ads", "grid", ("ads_home", "generation", "gen_publish", "gen_replace", "gen_post_live",
                                     "gen_update_live", "gen_unpublish")),
        # Cycle 86: every page version as a reference (harness/library.py).
        ("library_home", "Library", "layers", ("library_home",)),
        # Cycle 107: which generations perform best (per test and pooled).
        ("results_home", "Results", "chart", ("results_home",)),
        ("upload_ad", "Upload an ad", "upload", ("upload_ad",)),
    )),
    ("Queue", (
        ("job_list", "Jobs", "clock", ("job_list", "job_detail")),
        ("audit_log", "Audit log", "list", ("audit_log",)),
    )),
    ("Library", (
        ("run_list", "Runs", "text", ("run_list", "run_detail")),
        # Cycle 96: one Photos page; the older per-product image review is its second tab.
        ("photos_home", "Photos", "image", ("photos_home", "image_library", "image_library_product")),
    )),
)


def photo_tabs(active):
    """Cycle 96: the Photos page's two tabs -- the library photos
    (harness/site_photos.py) and the older per-product image review
    (serve.py /images: alt text and exclusions)."""
    tabs = (("photos_home", "Library photos"), ("image_library", "Product images (alt text)"))
    links = "".join(f'<a href="{url_for(ep)}"' + (' class="on" aria-current="page"' if ep == active else "")
                    + f">{label}</a>" for ep, label in tabs)
    return f'<nav class="seg ph-tabs" aria-label="Photos">{links}</nav>'


def _active_jobs():
    tenant = current_app.config.get("SITE_TENANT")
    if tenant is None:
        return 0
    from . import jobs

    try:
        return sum(1 for j in jobs.list_jobs(tenant, limit=50) if j["state"] in ("queued", "running"))
    except Exception:  # noqa: BLE001 -- a counter must never break a page
        return 0


def _reviewer_name(email):
    tenant = current_app.config.get("SITE_TENANT")
    if tenant is None or not email:
        return ""
    from . import runstate

    return (runstate.find_reviewer(tenant, email) or {}).get("name") or ""


def shell(title, body, *, refresh=None, extra_css=""):
    email = getattr(g, "reviewer_email", "") or ""
    endpoint = request.endpoint or ""
    busy = _active_jobs()
    sections = []
    for label, items in NAV:
        links = []
        for ep, text, ic, group in items:
            badge = (f'<span class="badge" title="{busy} job(s) queued or running">{busy}</span>'
                     if ep == "job_list" and busy else "")
            on = ' class="on" aria-current="page"' if endpoint in group else ""
            links.append(f'<a href="{url_for(ep)}"{on}>{icon(ic)}{text}{badge}</a>')
        sections.append(f'<div class="navsec">{label}</div><nav class="nav" aria-label="{label}">{"".join(links)}</nav>')
    brand_dir = current_app.config.get("SITE_BRAND_DIR")
    name = current_app.config.get("SITE_NAME") or ""
    mark = logo_svg(brand_dir, name) if brand_dir else ""
    mark = mark or f'<span class="wordmark">{e(name.upper())}</span>'
    tag = e(current_app.config.get("SITE_TAG") or "Listicles")
    person = _reviewer_name(email) or email.split("@")[0]
    initial = e((person[:1] or "?"))
    q = e(request.args.get("q") or "") if endpoint == "ads_home" else ""
    keep_filter = (f'<input type="hidden" name="f" value="{e(request.args["f"])}">'
                   if endpoint == "ads_home" and request.args.get("f") in ("review", "live") else "")
    meta_refresh = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f'<meta name="theme-color" content="{THEME_COLOR}">{icon_links()}{meta_refresh}'
        f'<title>{e(title)}{" · " + e(name) if name else ""}</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{FONTS_HREF}">'
        f"<style>{CSS}{extra_css}</style><script>{JS}</script></head><body><div class=\"app\">"
        f'<aside class="side"><a class="brand" href="{url_for("ads_home")}">{mark}<small>{tag} workspace</small></a>'
        f'<div class="navs">{"".join(sections)}</div><div class="spacer"></div>'
        f'<div class="me"><span class="avatar" aria-hidden="true">{initial}</span>'
        f'<div><div class="nm">{e(person)}</div><div class="em">{e(email)}</div></div></div>'
        f'<div class="foot">{e(request.host)}</div></aside>'
        f'<div class="main"><div class="topbar"><form class="qsearch" method="get" action="{url_for("ads_home")}" '
        f'role="search">{icon("search")}{keep_filter}<input type="search" name="q" value="{q}" id="q" '
        'placeholder="Search ads by name" aria-label="Search ads by name"><span class="kbd" aria-hidden="true">'
        '&#8984;K</span></form>'
        f'<div class="profile"><span class="avatar" aria-hidden="true">{initial}</span>'
        f'<div><div class="nm">{e(person)}</div><div class="em">{e(email)}</div></div></div></div>'
        f'<main class="content">{body}</main></div></div></body></html>'
    )
