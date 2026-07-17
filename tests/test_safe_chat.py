"""Test that with_null_choices_retry retries on MiniMax-style null choices error."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from ai_code_review.safe_chat import with_null_choices_retry


class _FakeChat(BaseChatModel):
    """Minimal chat model that fails N times with the null-choices TypeError, then succeeds."""

    fail_count: int = 0
    attempts: int = 0
    succeed_after: int = 1

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise TypeError(
                "Received response with null value for 'choices'. Full response keys: "
                "['id', 'choices', 'input_sensitive', 'output_sensitive']"
            )
        return AIMessage(content=f"ok on attempt {self.attempts}")

    def invoke(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise TypeError(
                "Received response with null value for 'choices'. Full response keys: "
                "['id', 'choices', 'input_sensitive', 'output_sensitive']"
            )
        return AIMessage(content=f"ok on attempt {self.attempts}")


def test_async_retry_succeeds_after_one_transient_failure():
    async def run():
        m = _FakeChat(fail_count=1, succeed_after=1)
        wrapped = with_null_choices_retry(m, max_retries=1, delay_seconds=0)
        result = await wrapped.ainvoke("hello")
        assert result.content == "ok on attempt 2"
        assert m.attempts == 2

    asyncio.run(run())


def test_async_gives_up_after_max_retries():
    async def run():
        m = _FakeChat(fail_count=10, succeed_after=99)
        wrapped = with_null_choices_retry(m, max_retries=1, delay_seconds=0)

        with pytest.raises(TypeError, match="null value for 'choices'"):
            await wrapped.ainvoke("hello")
        assert m.attempts == 2

    asyncio.run(run())


def test_non_null_choices_typeerror_propagates_immediately():
    async def run():
        class _BadChat(BaseChatModel):
            attempts: int = 0

            @property
            def _llm_type(self) -> str:
                return "fake"

            def _generate(self, messages, stop=None, **kwargs):
                raise NotImplementedError

            async def _agenerate(self, messages, stop=None, **kwargs):
                raise NotImplementedError

            async def ainvoke(self, *args, **kwargs):
                self.attempts += 1
                raise TypeError("unsupported arg: stop_sequence")

        m = _BadChat()
        wrapped = with_null_choices_retry(m, max_retries=5, delay_seconds=0)

        with pytest.raises(TypeError, match="unsupported arg"):
            await wrapped.ainvoke("hello")
        assert m.attempts == 1

    asyncio.run(run())


def test_sync_path_also_retries():
    m = _FakeChat(fail_count=1, succeed_after=1)
    wrapped = with_null_choices_retry(m, max_retries=1, delay_seconds=0)
    result = wrapped.invoke("hello")
    assert result.content == "ok on attempt 2"
    assert m.attempts == 2
