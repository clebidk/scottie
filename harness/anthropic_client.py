"""Real Anthropic client factory. Kept separate from every module that calls the
API so tests can inject a fake client instead of importing this at all.
"""
from dotenv import load_dotenv


def make_client():
    load_dotenv()  # loads ANTHROPIC_API_KEY from .env; never printed/logged
    import anthropic

    return anthropic.Anthropic()


# Fix cycle 17 (model tiering): every bounded extraction/classification call
# in this harness (ad_brief, still-image transcription, the writer, the
# claims semantic matcher) disables thinking so the model's token budget goes
# to visible output, not hidden reasoning -- but Haiku 4.5 doesn't accept a
# `thinking` param at all (any value, including "disabled", is rejected).
# Callers do `client.messages.create(model=model, ..., **thinking_kwargs(model))`
# so a Haiku call sends no `thinking` key and every other model keeps sending
# `{"type": "disabled"}` exactly as before this cycle.
def thinking_kwargs(model):
    if "haiku" in model:
        return {}
    return {"thinking": {"type": "disabled"}}


# Cycle 92: the design gate's judge runs on Claude Opus 5.5, where thinking
# cannot be disabled (`{"type": "disabled"}` is a 400 at every effort), so
# thinking_kwargs above must not be used for it. Thinking stays on (adaptive,
# the default when the parameter is omitted) and its depth is set by
# output_config.effort. Haiku takes neither parameter.
def adaptive_kwargs(model, effort=None):
    if "haiku" in model or not effort:
        return {}
    return {"output_config": {"effort": effort}}
