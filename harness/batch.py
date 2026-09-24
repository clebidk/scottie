"""Message Batches API support (fix cycle 17 item 5): `harness run --batch`
submits the three cartridge writers' INITIAL (attempt 1) calls as one batch,
at 50% off every token type, polls for completion, then hands each result to
the normal cli.write_and_gate_page repair loop. Repairs stay fully
synchronous -- each one depends on that specific cartridge's own gate
result, which doesn't exist until the initial write is graded -- so only the
initial write is ever batched. A cartridge whose batch result didn't parse or
validate simply isn't in collect_batch_results' returned dict; the caller
(pipeline.py) falls back to a normal synchronous attempt 1 for it, exactly as
if --batch had never been passed for that one cartridge.
"""
import time

from .errors import HarnessError

from .jsonutil import extract_json
from .write import build_initial_write_request, validate_schema


class BatchTimeout(HarnessError):
    """A Message Batch never reached "ended" inside the timeout."""


# 20-minute cap per the fix cycle 17 spec. In practice harness/budget.py's
# own wall-clock budget (300s by default) is almost always the tighter limit
# for a real run; this cap mainly guards a test or CI invocation against ever
# hanging on a batch that never reaches "ended".
DEFAULT_TIMEOUT_S = 1200
DEFAULT_POLL_INTERVAL_S = 10


def build_batch_requests(*, cartridge_names, cartridges_dir, ad_brief, facts_pack, model, tenant,
                          ad_not_repeated=None, listicle_style=None, listicle_headline=None):
    """(requests, schemas_by_cartridge). requests is ready to pass to
    client.messages.batches.create(requests=requests); schemas_by_cartridge
    is needed to validate_schema() each result the same way write_page does.
    Each request is byte-for-byte the same request a synchronous attempt 1
    would send (write.build_initial_write_request) -- batching never changes
    what's asked for, only how the call is billed and scheduled."""
    # Imported here, not at module load, so importing harness.batch never
    # requires the `anthropic` package to be installed (matches
    # anthropic_client.make_client's own lazy import).
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    from .repair import cartridge_write_constraints

    requests = []
    schemas = {}
    for cartridge_name in cartridge_names:
        _, word_range, allowed_cta_texts = cartridge_write_constraints(
            cartridge_name, cartridges_dir, facts_pack, ad_brief, tenant
        )
        schema, kwargs = build_initial_write_request(
            cartridge_name=cartridge_name,
            cartridges_dir=cartridges_dir,
            ad_brief=ad_brief,
            facts_pack=facts_pack,
            model=model,
            word_range=word_range,
            allowed_cta_texts=allowed_cta_texts,
            listicle_style=listicle_style,
            listicle_headline=listicle_headline,
            ad_not_repeated=ad_not_repeated,
            tenant=tenant,
        )
        schemas[cartridge_name] = schema
        requests.append(Request(custom_id=cartridge_name, params=MessageCreateParamsNonStreaming(**kwargs)))
    return requests, schemas


def poll_batch(client, batch_id, *, log=None, timeout_s=DEFAULT_TIMEOUT_S,
               poll_interval_s=DEFAULT_POLL_INTERVAL_S, sleep=time.sleep, clock=time.monotonic):
    """Blocks until `batch_id`'s processing_status is "ended"; raises
    BatchTimeout after timeout_s. `sleep`/`clock` are injectable so a test
    never actually sleeps -- a fake client whose batches.retrieve() reports
    "ended" on the first call returns from here after exactly one call, with
    zero real waiting."""
    start = clock()
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if log:
            log.event("write_pages.batch", f"batch {batch_id} status={batch.processing_status}")
        if batch.processing_status == "ended":
            return batch
        if clock() - start > timeout_s:
            raise BatchTimeout(f"batch {batch_id} did not finish within {timeout_s}s")
        sleep(poll_interval_s)


def collect_batch_results(client, batch_id, schemas):
    """{cartridge_name: (page, usage, error)}. `page`/`usage` are None and
    `error` is a short reason whenever the batch result didn't succeed,
    wasn't valid JSON, or failed validate_schema against that cartridge's own
    schema -- the caller treats a None page as "batch didn't produce a usable
    initial write for this cartridge" and falls back to a synchronous call."""
    results = {}
    for result in client.messages.batches.results(batch_id):
        cartridge_name = result.custom_id
        if result.result.type != "succeeded":
            results[cartridge_name] = (None, None, f"batch result: {result.result.type}")
            continue
        message = result.result.message
        usage = message.usage
        text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
        try:
            page = extract_json(text)
            errors = validate_schema(page, schemas[cartridge_name])
            if errors:
                raise ValueError("; ".join(errors))
        except Exception as e:
            results[cartridge_name] = (None, usage, str(e))
            continue
        results[cartridge_name] = (page, usage, None)
    return results
