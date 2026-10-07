# PEAK web fonts -- licences (cycle 95, 2026-10-07)

The files in this folder are uploaded to the storefront's Shopify Files at
the next `harness publish` (once; `cdn-manifest.json` remembers the CDN
urls). `harness publish --dry-run` lists what the next publish uploads. The
source of truth for every licensed file is the owner's Google Drive (shared
fonts folder `1qZiZhifw6mqNjPyOX8tNlQkckPfCzMfV`); the EULA PDFs stay in
Drive and are never committed.

| Family | File | Role | Licence | Drive source |
|---|---|---|---|---|
| Epika (Superior Type) | `Epika-Regular.woff2` (400) | serif voice: pull quotes, review quotes, the founder line, statement lines (`--ps-serif`) | Superior Type **Webfont EULA** 2026 1.0 (Drive file `1rd1KdBErjJsF8ar3coOV8GofbyL6PyQg`); desktop EULA `1VNUG7KQtJV_qcynEjXH_6V7i-F320W_-` | folder `Epika/Regular/Static` `1CH69WvFRK4-hzo1zkFFwZQnsrBBQTBAA` (woff2 `1SM73n8wPU2glprXEg3msYIL9htNcPPDs`) |
| Acid Grotesk (FFF) | `FFF-AcidGrotesk-Regular.woff2` (400) | second display face (`--ps-display-acid`) | the licence in the Drive `Acid Grotesk` folder (replaces the unlicensed evaluation file used in cycle 79) | folder `Acid Grotesk` `1LkMYVrbbN72DlEwnr-YajA55mSnVL31u`, subfolder `WOFF WOFF2` `1nOaNd6R9JSp2MPFiJQNeadkobugILn9c` (woff2 `1AWCHUicz5DavHQeJyu27PSfzstuvhCn_`) |
| Montserrat | `Montserrat-Variable.woff2` (100-900) | headings (`--ps-heading`, Poppins next) | SIL Open Font License 1.1, `OFL-Montserrat.txt` (no Reserved Font Name) | Google Fonts `Montserrat[wght].ttf`, converted to woff2 with fontTools |
| Peak Grotesk Wide | `../fonts-display/pk-peak-grotesk-wide.woff2` (400) | display headline, upper case (`--ps-display-wide`) | PEAK's own face, served by the live theme (theme 64 assets); see docs/FIXLOG.md cycle 79 | the live theme's CDN `.../t/64/assets/pk-peak-grotesk-wide.woff2` |

## Superior Type webfont EULA -- terms that bind this repo

- Web use only via `@font-face`, up to the monthly pageviews on the
  purchase invoice, on domains of the same company.
- Never modify, rename, subset (except for load time), decompile or
  **convert** the files: Epika ships exactly as delivered. There is no
  Epika Light webfont: `Epika-Light.otf` is a desktop file, and the desktop
  EULA does not permit `@font-face` embedding, so it is not here.
- Never upload the files to a publicly accessible server, cloud service or
  git repository. This repository's origin is PEAK's own server; never
  push it to a public host.
- Not for automated document composition systems installed on a server.
  Open question for the owner: the generator only emits `@font-face`
  rules, but the library thumbnails are rendered by headless Chrome on the
  server.
