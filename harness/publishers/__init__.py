"""Publisher adapters: harness/publishers/base.py's interface, implemented by
shopify.py (the real storefront) and export.py (a self-contained folder for
manual upload -- the fallback for a tenant with no credentials yet).
tenant.yaml's `publisher` key picks which one `harness publish` uses.
"""
