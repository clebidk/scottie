"""Export publisher: writes a self-contained folder per page instead of
calling any storefront API. The default for a tenant with no publisher
credentials yet (tenant.yaml `publisher: export`), and simply a working,
offline adapter to exercise `harness publish`'s approval/packet gate against
in tests without ever touching a real Shopify store.
"""
from pathlib import Path

from .base import Publisher


class ExportPublisher(Publisher):
    def __init__(self, *, out_dir, template_suffix=None):
        self.out_dir = Path(out_dir)
        # Cycle 84: tenant.yaml shopify_publish.template_suffix; only named
        # in the README, since nothing here talks to a storefront.
        self.template_suffix = template_suffix or None

    def dry_run(self, page):
        return {
            "ok": True,
            "reason": "",
            "out_dir": str(self.out_dir),
            "template_suffix": self.template_suffix,
            "body_bytes": len(page.get("body_html", "")) if isinstance(page, dict) else None,
            "asset_count": len(page.get("assets", []) or []) if isinstance(page, dict) else None,
        }

    def upload_assets(self, manifest):
        """Copies each asset's bytes next to index.html under this export
        folder; the "uploaded" URL is a relative local path, since there is
        no CDN involved."""
        assets_dir = self.out_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        mapping = {}
        for item in manifest:
            dest = assets_dir / item["cdn_filename"]
            dest.write_bytes(item["bytes"])
            mapping[item["local_path"]] = f"assets/{item['cdn_filename']}"
        return mapping

    def publish(self, page, *, unpublished=True):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "index.html").write_text(page.get("full_html") or page["body_html"])
        (self.out_dir / "shopify-body.html").write_text(page["body_html"])
        readme = (
            f"# Manual upload: {page.get('title', '')}\n\n"
            "This page was exported, not published live -- no storefront credentials "
            "were used (or the tenant's publisher is `export`). To publish by hand:\n\n"
            "1. Shopify admin -> Online Store -> Pages -> Add page.\n"
            f"2. Title: {page.get('title', '')}\n"
            "3. Paste shopify-body.html's contents into the page body via the API/HTML "
            "editor, not the rich-text editor -- it can strip the <style> block.\n"
            "4. Upload every file under assets/ to Shopify Files, then replace each "
            "assets/... src in the pasted body with its Shopify CDN URL.\n"
            "5. Leave the page unpublished (draft) unless the packet is stamped "
            "'ship' and a reviewer has approved.\n"
            "6. Theme template: some themes' default page template strips the body "
            "<style> block, and the page then shows its CSS as text. "
            + (
                f"Set the page's theme template to `{self.template_suffix}` "
                f"(templates/page.{self.template_suffix}.liquid, pushed to every theme the store "
                "runs with deploy/shopify/push_template.py).\n"
                if self.template_suffix else
                "If the store has deploy/shopify/templates/page.pk-harness.liquid in its themes "
                "(deploy/shopify/push_template.py), set the page's theme template to `pk-harness`.\n"
            )
            + "   See docs/PUBLISHING.md, \"Storefront template (cycle 84)\".\n"
        )
        (self.out_dir / "README.md").write_text(readme)
        return {"id": None, "url": None, "export_dir": str(self.out_dir)}
