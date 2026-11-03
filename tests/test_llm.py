import asyncio
import json

import httpx2
import pytest

from app.config import Settings
from app.llm import LLMError, VultrLLM

USER_CODE = "def add(a, b):\n    return a + b\n"

VALID_TESTS = (
    "from hypothesis import given, strategies as st\n"
    "from target import *\n"
    "\n"
    "@given(st.integers(), st.integers())\n"
    "def test_add_commutes(a, b):\n"
    "    assert add(a, b) == add(b, a)\n"
)


def _settings() -> Settings:
    return Settings(vultr_api_key="x", vultr_model="m", _env_file=None)


def _completion(content: str | None) -> dict:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _fenced(code: str) -> str:
    return f"Here you go:\n```python\n{code}```\nDone."


class FakeVultr:
    """Serves the given replies in order and records every request body."""

    def __init__(self, *replies: str | None) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_completion(self.replies.pop(0)))


def _llm(handler) -> VultrLLM:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return VultrLLM(_settings(), http_client=client)


def test_extracts_fenced_block():
    fake = FakeVultr(_fenced(VALID_TESTS))
    code = asyncio.run(_llm(fake).generate_tests(USER_CODE, "adds numbers"))
    assert code == VALID_TESTS
    assert len(fake.requests) == 1


def test_retry_sends_error_feedback():
    first = "I cannot use a code block, sorry."
    fake = FakeVultr(first, _fenced(VALID_TESTS))
    code = asyncio.run(_llm(fake).generate_tests(USER_CODE, None))
    assert code == VALID_TESTS
    assert len(fake.requests) == 2
    messages = fake.requests[1]["messages"]
    assert messages[:-2] == fake.requests[0]["messages"]
    assert messages[-2] == {"role": "assistant", "content": first}
    assert messages[-1]["role"] == "user"
    assert "no ```python fenced block" in messages[-1]["content"]


def test_raises_after_two_invalid():
    fake = FakeVultr("no code here", "still no code")
    with pytest.raises(LLMError, match="^model returned invalid output$"):
        asyncio.run(_llm(fake).generate_tests(USER_CODE, None))
    assert len(fake.requests) == 2


def test_syntax_error_in_block_triggers_retry():
    fake = FakeVultr(_fenced("def broken(:\n    pass\n"), _fenced(VALID_TESTS))
    code = asyncio.run(_llm(fake).generate_tests(USER_CODE, None))
    assert code == VALID_TESTS
    feedback = fake.requests[1]["messages"][-1]["content"]
    assert "SyntaxError" in feedback
    assert "line 1" in feedback


def test_wrong_import_triggers_retry():
    wrong = VALID_TESTS.replace("from target import *", "from solution import add")
    fake = FakeVultr(_fenced(wrong), _fenced(VALID_TESTS))
    code = asyncio.run(_llm(fake).generate_tests(USER_CODE, None))
    assert code == VALID_TESTS
    assert "from target import *" in fake.requests[1]["messages"][-1]["content"]


def test_missing_given_triggers_retry():
    no_property = "from target import *\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    fake = FakeVultr(_fenced(no_property), _fenced(VALID_TESTS))
    code = asyncio.run(_llm(fake).generate_tests(USER_CODE, None))
    assert code == VALID_TESTS
    assert "@given" in fake.requests[1]["messages"][-1]["content"]


def test_empty_content_counts_as_invalid():
    fake = FakeVultr(None, None)
    with pytest.raises(LLMError, match="^model returned invalid output$"):
        asyncio.run(_llm(fake).generate_tests(USER_CODE, None))


def test_timeout_raises_model_unavailable():
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMError, match="^model unavailable$"):
        asyncio.run(_llm(handler).generate_tests(USER_CODE, None))
    assert len(calls) == 1


def test_http_error_raises_model_unavailable():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"error": {"message": "overloaded"}})

    with pytest.raises(LLMError, match="^model unavailable$"):
        asyncio.run(_llm(handler).explain("summary"))


def test_sends_temperature_zero_and_model():
    fake = FakeVultr(_fenced(VALID_TESTS))
    asyncio.run(_llm(fake).generate_tests(USER_CODE, "adds numbers"))
    body = fake.requests[0]
    assert body["model"] == "m"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 1500
    sent = "\n".join(m["content"] for m in body["messages"])
    assert USER_CODE in sent
    assert "adds numbers" in sent


def test_requests_go_to_vultr_base_url():
    urls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        urls.append(str(request.url))
        return httpx2.Response(200, json=_completion("plain text"))

    asyncio.run(_llm(handler).explain("summary"))
    assert urls == ["https://api.vultrinference.com/v1/chat/completions"]


def test_fix_code_returns_parsed_block():
    fixed = "def add(a, b):\n    return b + a\n"
    fake = FakeVultr(_fenced(fixed))
    code = asyncio.run(_llm(fake).fix_code(USER_CODE, VALID_TESTS, "AssertionError"))
    assert code == fixed
    sent = "\n".join(m["content"] for m in fake.requests[0]["messages"])
    assert VALID_TESTS in sent
    assert "AssertionError" in sent


def test_explain_returns_plain_text():
    fake = FakeVultr("  The function drops the remainder.  ")
    text = asyncio.run(_llm(fake).explain("3 of 5 properties failed"))
    assert text == "The function drops the remainder."
    sent = "\n".join(m["content"] for m in fake.requests[0]["messages"])
    assert "3 of 5 properties failed" in sent
