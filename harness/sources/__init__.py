"""Input adapters: everything the harness reads from outside its own repo.

Each module here owns one outside system and nothing else, so a new tenant on a
different stack replaces an adapter instead of editing a pipeline stage.

    drive.py             a file, by id, from Google Drive (no OAuth)
    shopify_products.py  the storefront's public product feed
    judgeme.py           review statistics from a storefront review widget
    gbrain.py            a knowledge base, behind a retrieval allowlist (stub)
"""
