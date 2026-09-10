"""Real Anthropic client factory. Kept separate from every module that calls the
API so tests can inject a fake client instead of importing this at all.
"""
from dotenv import load_dotenv


def make_client():
    load_dotenv()  # loads ANTHROPIC_API_KEY from .env; never printed/logged
    import anthropic

    return anthropic.Anthropic()
