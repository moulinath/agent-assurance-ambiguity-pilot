"""
Pluggable LLM client interface.

Independence levels L2/L3 require validators drawn from a genuinely different
model family/substrate than the implementer (see design doc sec. 4). This
module defines a common `LLMClient` interface so the harness can mix
providers without caring which one is behind a given role.

Only ANTHROPIC_API_KEY is assumed reachable from a locked-down execution
environment. To actually realize L2/L3 independence you will need to run
this on infrastructure that can reach at least one other provider (OpenAI,
an open-weight model host, etc.) and fill in that client below -- the
scaffolding (OpenAIClient) is provided but stubbed.

A MockLLMClient is included so the whole harness (spec presentation ->
clarifying-question loop -> code generation -> oracle execution -> validator
review -> decision aggregation) can be dry-run and unit-tested with zero API
calls and zero cost, before spending real tokens on the pilot.
"""

from __future__ import annotations
import os
import re
import json
from dataclasses import dataclass
from typing import List, Dict, Optional, Callable


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "system"
    content: str


class LLMClient:
    """Common interface every provider wrapper implements."""

    name: str = "base"

    def chat(self, messages: List[ChatMessage], system: Optional[str] = None) -> str:
        raise NotImplementedError


class AnthropicClient(LLMClient):
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: Optional[str] = None):
        self.model = model
        self.name = f"anthropic:{model}"
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Export it in your own environment before "
                "running real (non-mock) pilot calls."
            )
        try:
            import anthropic  # pip install anthropic
        except ImportError as e:
            raise RuntimeError("pip install anthropic") from e
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def chat(self, messages: List[ChatMessage], system: Optional[str] = None) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=system or "",
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        return "".join(block.text for block in resp.content if block.type == "text")


class OpenAIClient(LLMClient):
    """
    OpenAI Chat Completions client. Used either as the L2/L3 validator
    (--cross-provider) or for every role (--provider openai).

    NOT YET RUN AGAINST THE LIVE API from this project's sandbox (no key was
    available there). It is wired to the standard `chat.completions.create`
    call and was exercised only against a local fake of the `openai` package,
    which checks the plumbing, not real model behavior or API quirks. Treat
    the first real call as the real test, and expect to adjust if a given
    model rejects a parameter or returns an unexpected shape.
    """

    def __init__(self, model: str = "gpt-5.1", api_key: Optional[str] = None):
        self.model = model
        self.name = f"openai:{model}"
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set.")
        try:
            import openai  # pip install openai
        except ImportError as e:
            raise RuntimeError("pip install openai") from e
        self._client = openai.OpenAI(api_key=self.api_key)

    def chat(self, messages: List[ChatMessage], system: Optional[str] = None) -> str:
        full_messages = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": m.role, "content": m.content} for m in messages]
        resp = self._client.chat.completions.create(model=self.model, messages=full_messages)
        return resp.choices[0].message.content


class GeminiClient(LLMClient):
    """
    Google Gemini through the NATIVE Gemini API, using the `google-genai` package
    (pip install google-genai). Reads GEMINI_API_KEY (or GOOGLE_API_KEY). Used as the
    substrate-independent validator (a different model family from a gpt-5.1
    implementer), i.e. a real L2.

    Why native and not the OpenAI-compatible endpoint: AI Studio now issues "AQ."-prefix
    keys, which were reported to be rejected by the OpenAI-compatible endpoint but work on
    the native one.

    The model id is a flag: run `python3 gemini_check.py` to list ids your key can use.
    Retries with backoff on rate limit (429) and transient server errors (500/503/504).
    NOT yet run against the live API from this project.
    """

    def __init__(self, model: str, api_key: Optional[str] = None, max_retries: int = 6):
        self.model = model
        self.name = f"gemini:{model}"
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not set.")
        try:
            from google import genai  # pip install google-genai
            from google.genai import types, errors
        except ImportError as e:
            raise RuntimeError("pip install google-genai") from e
        self._types, self._errors = types, errors
        self._client = genai.Client(api_key=self.api_key)
        self.max_retries = max_retries

    def chat(self, messages: List[ChatMessage], system: Optional[str] = None) -> str:
        import time
        t = self._types
        contents = [t.Content(role=("model" if m.role == "assistant" else "user"),
                              parts=[t.Part(text=m.content)]) for m in messages]
        config = t.GenerateContentConfig(system_instruction=system) if system else None
        delay = 4.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.models.generate_content(
                    model=self.model, contents=contents, config=config)
                return resp.text or ""
            except self._errors.APIError as e:
                code = getattr(e, "code", None)
                if code not in (429, 500, 503, 504) or attempt == self.max_retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 60)


class OpenRouterClient(LLMClient):
    """
    Any model hosted on OpenRouter (openrouter.ai), through its OpenAI-compatible endpoint
    and the `openai` package. Reads OPENROUTER_API_KEY. One key reaches many model families
    (Google, Anthropic, Meta, DeepSeek, ...), so it can supply a validator from a different
    family than the implementer. Model ids look like "google/gemini-2.5-pro"; take them from
    openrouter.ai/models (not guessed here). Retries on rate limits / transient errors.
    NOT yet run against the live API from this project.
    """
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str, api_key: Optional[str] = None, max_retries: int = 6):
        self.model = model
        self.name = f"openrouter:{model}"
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set.")
        try:
            import openai  # pip install openai
        except ImportError as e:
            raise RuntimeError("pip install openai") from e
        self._openai = openai
        self._client = openai.OpenAI(api_key=self.api_key, base_url=self.BASE_URL)
        self.max_retries = max_retries

    def chat(self, messages: List[ChatMessage], system: Optional[str] = None) -> str:
        import time
        full = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": m.role, "content": m.content} for m in messages]
        delay = 4.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.chat.completions.create(model=self.model, messages=full)
                return resp.choices[0].message.content or ""
            except (self._openai.RateLimitError, self._openai.APIConnectionError,
                    self._openai.InternalServerError) as e:
                if attempt == self.max_retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 60)


class MockLLMClient(LLMClient):
    """
    Deterministic, zero-cost stand-in for pipeline testing.

    Behavior is scripted per (task_id, condition) via `script`, a dict mapping
    task_id -> callable(spec_text, is_ambiguous, got_clarification) -> code string.
    If no script entry exists for a task, falls back to a trivial "always
    empty/pass-through" implementation, which will reliably fail most oracles
    -- useful for confirming the harness correctly detects failure, not just
    success.

    Validator behavior is scripted separately via `validator_script`:
    callable(prompt_text) -> (verdict_str, reasoning), where verdict_str is
    one of "accept" / "reject" / "hold". If omitted, the mock defaults every
    validator call to "hold" -- a deliberately conservative default, since a
    mock that silently defaults to "accept" would make chains look more
    reliable than any real (unscripted) validator behavior would justify.
    """

    def __init__(self, name: str, script: Optional[Dict] = None,
                 validator_script: Optional[Callable] = None,
                 clarifying_question_fn: Optional[Callable[[str], Optional[str]]] = None):
        self.name = name
        self.script = script or {}
        self.validator_script = validator_script
        self.clarifying_question_fn = clarifying_question_fn
        self._last_task_id = None

    def set_context(self, task_id: str):
        self._last_task_id = task_id

    def chat(self, messages: List[ChatMessage], system: Optional[str] = None) -> str:
        # Very simple router: look at the last user message to decide what's
        # being asked (implementer draft, clarifying Q&A turn, or validator review).
        last = messages[-1].content if messages else ""
        task_id = self._last_task_id

        if "You are reviewing" in (system or "") or "VALIDATOR" in (system or ""):
            if self.validator_script:
                verdict_str, reasoning = self.validator_script(last)
                if verdict_str not in ("accept", "reject", "hold"):
                    raise ValueError(
                        f"validator_script must return one of 'accept'/'reject'/'hold', got {verdict_str!r}"
                    )
                return json.dumps({"verdict": verdict_str, "reasoning": reasoning})
            return json.dumps({"verdict": "hold", "reasoning": "mock default: no validator_script provided"})

        if "You may ask one clarifying question" in (system or ""):
            if self.clarifying_question_fn:
                q = self.clarifying_question_fn(last)
                if q:
                    return json.dumps({"action": "ask", "question": q})
            # Not asking (either no question function, or it declined to ask
            # this turn) -- fall through to producing real code below, rather
            # than returning a placeholder "implement" JSON that the harness
            # would then fail to extract code from.

        # Implementer draft / final code turn.
        # Check the WHOLE conversation, not just the last message -- after a
        # clarification round, the last message is the clarification answer
        # (which won't contain "Ticket:"), so checking only `last` would
        # misdetect the ambiguity condition on later turns.
        full_text = " ".join(m.content or "" for m in messages)
        is_ambiguous = "Ticket:" in full_text  # our ambiguous_spec strings all start with "Ticket:"
        got_clarification = "clarification:" in full_text.lower()
        if task_id and task_id in self.script:
            code = self.script[task_id](last, is_ambiguous, got_clarification)
        else:
            code = "def PLACEHOLDER(*args, **kwargs):\n    raise NotImplementedError"
        return f"```python\n{code}\n```"


def extract_code_block(text: str) -> str:
    """Pull the first ```python ... ``` fenced block out of an LLM response."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()
