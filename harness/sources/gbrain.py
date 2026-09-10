"""Knowledge-base grounding -- not implemented.

A FactsSource backed by a knowledge base, read through the retrieval allowlist
in docs/SPEC.md section 3: page types product/concept/campaign/spec/policy/kb/
reference/book-analysis only, and never customer/order/email/support_ticket/
conversation/chat_log/person. That allowlist is the point of this adapter: a
generated page is public, and the failure mode it guards against is a private
record reaching one.

Grounding today runs entirely on harness/ground.py's LocalFactsSource, which
reads the tenant's own claims store. Wire this in once a tenant's knowledge map
exists and defines how to query it for a given product and ad_brief.
"""


class GBrainSource:
    """FactsSource backed by g Brain, through the retrieval allowlist in
    SPEC.md section 3 (page types product/concept/campaign/spec/policy/kb/
    reference/book-analysis; never customer/order/email/support_ticket/
    conversation/slack_log/person).

    Not implemented in V1: docs/knowledge-map.md is being written concurrently
    by another agent and g Brain retrieval isn't wired up yet. Wire this in
    once that document exists and defines how to query g Brain for a given
    product/ad_brief.
    """

    def facts_for(self, product_slug, ad_brief):
        raise NotImplementedError(
            "GBrainSource is a stub; wire it in after docs/knowledge-map.md lands "
            "(see class docstring). Use LocalFactsSource until then."
        )
