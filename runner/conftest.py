"""Sandbox-wide pytest plugin: deterministic, stateless Hypothesis settings."""

from hypothesis import settings

settings.register_profile(
    "proveit",
    database=None,
    deadline=None,
    max_examples=200,
    derandomize=True,
)
settings.load_profile("proveit")
