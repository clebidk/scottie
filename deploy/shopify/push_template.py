#!/usr/bin/env python3
"""Cycle 84: put the harness's page template into one or more store themes.

    .venv/bin/python deploy/shopify/push_template.py --tenant peak-saunas \\
        --theme-id <id> [--theme-id <id> ...] [--dry-run | --apply | --rollback]

The file is deploy/shopify/templates/page.pk-harness.liquid; its theme asset
key is templates/page.pk-harness.liquid. This script touches that one asset
and nothing else in a theme (Shopify Admin REST Asset API).

  --dry-run   (the default) GET the asset from each theme and print whether
              it is absent, identical to the repo file, or different.
  --apply     the same, then PUT the repo file to each theme where it is
              absent or different, and print the asset's updated_at.
  --rollback  DELETE the asset from each theme, but only where its content
              equals the repo file; a theme copy that differs is left alone.

Credentials: SHOPIFY_STORE and SHOPIFY_TOKEN from tenants/<tenant>/.env, read
the same way as the publisher (harness/publishers/shopify.py). The token needs
the read_themes and write_themes scopes. Theme ids are operator input (Shopify
admin -> Online Store -> Themes -> ... -> Edit code: the id is in the URL);
they are not kept in the repo.

Standard library plus the harness's Shopify client only.
"""
import argparse
import sys
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness import tenant as tenant_mod  # noqa: E402
from harness.errors import PublishFailed  # noqa: E402
from harness.publishers.shopify import ShopifyCredentialsMissing, ShopifyPublisher  # noqa: E402

ASSET_KEY = "templates/page.pk-harness.liquid"
TEMPLATE_FILE = Path(__file__).resolve().parent / ASSET_KEY


def _asset_path(theme_id, *, with_key=True):
    path = f"themes/{theme_id}/assets.json"
    if with_key:
        path += "?" + urllib.parse.urlencode({"asset[key]": ASSET_KEY})
    return path


def get_theme(client, theme_id):
    status, data = client._request("GET", f"themes/{theme_id}.json")
    if status != 200 or "theme" not in data:
        raise PublishFailed(f"theme {theme_id}: read failed: status={status} body={data}")
    return data["theme"]


def get_asset(client, theme_id):
    """The theme's copy of ASSET_KEY (with `value`), or None when absent."""
    status, data = client._request("GET", _asset_path(theme_id))
    if status == 404:
        return None
    if status != 200 or "asset" not in data:
        raise PublishFailed(f"theme {theme_id}: asset read failed: status={status} body={data}")
    return data["asset"]


def put_asset(client, theme_id, value):
    payload = {"asset": {"key": ASSET_KEY, "value": value}}
    status, data = client._request("PUT", _asset_path(theme_id, with_key=False), payload)
    if status not in (200, 201) or "asset" not in data:
        raise PublishFailed(f"theme {theme_id}: asset write failed: status={status} body={data}")
    return data["asset"]


def delete_asset(client, theme_id):
    status, data = client._request("DELETE", _asset_path(theme_id))
    if status != 200:
        raise PublishFailed(f"theme {theme_id}: asset delete failed: status={status} body={data}")


def compare(asset, local_text):
    if asset is None:
        return "absent"
    return "identical" if asset.get("value") == local_text else "different"


def run(client, theme_ids, *, mode, local_text, out=print):
    """One block of lines per theme. Returns 0, or 1 when a rollback left a
    differing copy in place."""
    rc = 0
    for theme_id in theme_ids:
        theme = get_theme(client, theme_id)
        out(f"theme {theme_id} ({theme.get('name')!r}, role {theme.get('role')}):")
        asset = get_asset(client, theme_id)
        state = compare(asset, local_text)
        out(f"  {ASSET_KEY}: {state}"
            + (f" (theme copy updated_at {asset.get('updated_at')})" if asset else ""))
        if mode == "apply":
            if state == "identical":
                out("  nothing to push")
            else:
                written = put_asset(client, theme_id, local_text)
                out(f"  pushed; updated_at {written.get('updated_at')}")
        elif mode == "rollback":
            if state == "absent":
                out("  nothing to delete")
            elif state == "different":
                out("  NOT deleted: the theme's copy differs from the repo file")
                rc = 1
            else:
                delete_asset(client, theme_id)
                out("  deleted")
    return rc


def build_parser():
    parser = argparse.ArgumentParser(
        prog="push_template.py",
        description=f"push {ASSET_KEY} to Shopify themes (cycle 84)",
    )
    parser.add_argument("--theme-id", action="append", required=True, type=int,
                        help="a theme id (repeat for each theme)")
    parser.add_argument("--tenant", help="tenant whose .env holds SHOPIFY_STORE/SHOPIFY_TOKEN; "
                                         "default: HARNESS_TENANT, else tenants/default.txt")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (the default)")
    mode.add_argument("--apply", action="store_true", help="PUT the repo file where it is absent or different")
    mode.add_argument("--rollback", action="store_true",
                      help="DELETE the asset where it equals the repo file")
    return parser


def main(argv=None, *, client=None, out=print):
    args = build_parser().parse_args(argv)
    mode = "apply" if args.apply else "rollback" if args.rollback else "dry-run"
    if client is None:
        tenant = tenant_mod.load_tenant(args.tenant)
        tenant.load_env()
        client = ShopifyPublisher()
    local_text = TEMPLATE_FILE.read_text()
    try:
        return run(client, args.theme_id, mode=mode, local_text=local_text, out=out)
    except (ShopifyCredentialsMissing, PublishFailed) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
