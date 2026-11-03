"""The only LLM client: Vultr Serverless Inference (OpenAI-compatible)."""

import ast
import re
from collections.abc import Callable
from typing import Protocol

import httpx2
import openai
from openai import AsyncOpenAI

from app import prompts
from app.config import Settings

TIMEOUT_S = 60
MAX_TOKENS = 1500
FENCED_PYTHON = re.compile(r"```python\n(.*?)```", re.S)

Validator = Callable[[ast.Module], str | None]


class LLMError(Exception):
    pass


class LLM(Protocol):
    async def generate_tests(self, code: str, intent: str | None) -> str: ...

    async def fix_code(self, code: str, tests: str, failure: str) -> str: ...

    async def explain(self, summary: str) -> str: ...


def _check_test_file(tree: ast.Module) -> str | None:
    imports_target = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "target"
        and node.level == 0
        and [alias.name for alias in node.names] == ["*"]
        for node in tree.body
    )
    if not imports_target:
        return "the test file must contain the line `from target import *` at top level."
    has_property = any(
        _is_given(decorator)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for decorator in node.decorator_list
    )
    if not has_property:
        return "the test file must contain at least one test decorated with Hypothesis `@given`."
    return None


def _is_given(decorator: ast.expr) -> bool:
    func = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (isinstance(func, ast.Name) and func.id == "given") or (
        isinstance(func, ast.Attribute) and func.attr == "given"
    )


def _extract(reply: str, validate: Validator | None) -> tuple[str | None, str | None]:
    """Return (code, None) for a valid artifact, else (None, error message for the model)."""
    match = FENCED_PYTHON.search(reply)
    if match is None:
        return None, "it contains no ```python fenced block."
    code = match.group(1)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return None, f"the code block does not parse: SyntaxError: {exc.msg} (line {exc.lineno})."
    if validate is not None and (error := validate(tree)) is not None:
        return None, error
    return code, None


class VultrLLM(LLM):
    def __init__(self, settings: Settings, http_client: httpx2.AsyncClient | None = None) -> None:
        # max_retries=0: a timeout fails after TIMEOUT_S instead of silently tripling the wait.
        self._client = AsyncOpenAI(
            base_url=settings.vultr_base_url,
            api_key=settings.vultr_api_key,
            timeout=TIMEOUT_S,
            max_retries=0,
            http_client=http_client,
        )
        self._model = settings.vultr_model

    async def _chat(self, messages: list[dict[str, str]]) -> str:
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0,
                max_tokens=MAX_TOKENS,
            )
        except openai.APIError as exc:
            raise LLMError("model unavailable") from exc
        if not resp.choices:
            return ""
        return resp.choices[0].message.content or ""

    async def _code(self, system: str, user: str, validate: Validator | None = None) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        reply = await self._chat(messages)
        code, error = _extract(reply, validate)
        if error is None:
            return code
        # At temperature 0 a plain retry repeats the same answer, so feed the error back.
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": prompts.retry_user(error)},
        ]
        code, error = _extract(await self._chat(messages), validate)
        if error is None:
            return code
        raise LLMError("model returned invalid output")

    async def generate_tests(self, code: str, intent: str | None) -> str:
        return await self._code(prompts.TESTS_SYSTEM, prompts.tests_user(code, intent), _check_test_file)

    async def fix_code(self, code: str, tests: str, failure: str) -> str:
        return await self._code(prompts.FIX_SYSTEM, prompts.fix_user(code, tests, failure))

    async def explain(self, summary: str) -> str:
        messages = [
            {"role": "system", "content": prompts.EXPLAIN_SYSTEM},
            {"role": "user", "content": prompts.explain_user(summary)},
        ]
        return (await self._chat(messages)).strip()
