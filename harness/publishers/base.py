"""Publisher adapter interface.

A publisher takes one cartridge's rendered output -- a `page` dict of
{"title", "body_html", ...} built from `harness/page_body.py`'s
`build_shopify_body`, plus an asset manifest with bytes attached -- and
either gets it in front of a real storefront or exports it for manual
upload. Nothing in `harness/cli.py`'s `cmd_publish` picks which adapter runs;
`tenant.yaml`'s `publisher: shopify|export` key does.
"""


class Publisher:
    def dry_run(self, page):
        """Validate credentials and the page body without creating anything.
        Returns a report dict with at least {"ok": bool, "reason": str}.
        Never raises for a missing credential -- that's a reported failure,
        not a crash."""
        raise NotImplementedError

    def upload_assets(self, manifest):
        """`manifest`: a list of {"local_path", "alt", "cdn_filename",
        "bytes"} (see harness/page_body.py's build_asset_manifest, with bytes
        read in by the caller). Returns {local_path: uploaded_url_or_path}."""
        raise NotImplementedError

    def publish(self, page, *, unpublished=True):
        """Creates or updates the page. `unpublished=True` (the default)
        creates a draft; only `--live` on `harness publish` ever passes
        False. Returns {"id": ..., "url": ...}."""
        raise NotImplementedError
