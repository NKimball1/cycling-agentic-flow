"""Provider-agnostic LLM boundary.

coach.py talks to an `LLMClient` interface, not to any specific vendor SDK.
Today the only implementation is `ClaudeClient` (the Anthropic tool-use loop),
but the seam means adding OpenAI / Gemini / a local model later is "write
another adapter" — with zero changes to coach.py, sync.py, analyze.py, or
anything else. It's the same swap pattern the data sources (.fit vs Strava)
already use: define a contract, implement per backend, choose via config.

Tools are described NEUTRALLY as {name, description, parameters(JSON Schema)};
each adapter translates that into its provider's function-calling dialect. That
translation — not text generation — is the real work in any future adapter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Protocol

import anthropic

import config

# A tool executor runs a requested tool and returns a JSON-serializable result.
ToolExecutor = Callable[[str, dict], dict]


@dataclass
class LLMResult:
    """What an LLM call produced: the text, plus telemetry (tokens, model).

    Returning this instead of a bare string is what makes observability possible
    — the caller can log token cost and which model ran, without the seam
    leaking any vendor-specific response object. Any future adapter fills the
    same fields from its own provider's usage data.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LLMClient(Protocol):
    """The contract every provider adapter implements."""

    def run_tool_loop(
        self,
        system: str,
        user_message: str,
        tools: list[dict],
        tool_executor: ToolExecutor,
        verbose: bool = True,
        tools_are_terminal: bool = False,
    ) -> LLMResult:
        """Send the request, execute any tool calls via `tool_executor`, and
        return the model's text plus token telemetry. `tools` are neutral specs
        (see module docstring); the adapter translates them for its provider.

        If `tools_are_terminal` is True, the tools are fire-and-forget side
        effects: once executed, the loop stops without a follow-up model turn.
        That avoids a wasted round-trip and any post-tool acknowledgment text."""
        ...

    def complete(self, system: str, user_message: str) -> LLMResult:
        """A plain text completion — no tools. Used by the eval judge, and handy
        for any single-shot generation. Same telemetry as run_tool_loop."""
        ...


class ClaudeClient:
    """Anthropic implementation of LLMClient (the current default)."""

    def __init__(self, model: str | None = None, max_tokens: int = 8000):
        self.model = model or config.CLAUDE_MODEL
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    @staticmethod
    def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
        # Neutral {name, description, parameters} -> Anthropic's {..., input_schema}.
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]

    def run_tool_loop(
        self,
        system: str,
        user_message: str,
        tools: list[dict],
        tool_executor: ToolExecutor,
        verbose: bool = True,
        tools_are_terminal: bool = False,
    ) -> LLMResult:
        anthropic_tools = self._to_anthropic_tools(tools)
        messages = [{"role": "user", "content": user_message}]
        text_parts: list[str] = []
        total_in = total_out = 0  # accumulate token usage across turns

        # Minimal tool-use loop: model writes text and/or requests tools; we run
        # the tools and feed results back until it stops asking. (Cap as backstop.)
        for _ in range(6):
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=anthropic_tools,
                messages=messages,
            )
            if response.usage:
                total_in += response.usage.input_tokens or 0
                total_out += response.usage.output_tokens or 0

            if response.stop_reason == "refusal":
                return LLMResult(
                    "The model declined to respond to this request.",
                    total_in, total_out, self.model,
                )

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)

            if response.stop_reason != "tool_use":
                break  # end_turn — done

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = tool_executor(block.name, dict(block.input))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )

            # Terminal tools are side effects only — they've now run, so we don't
            # need the model's follow-up turn (which would just be an ack).
            if tools_are_terminal:
                break

            messages.append({"role": "user", "content": tool_results})

        return LLMResult("".join(text_parts).strip(), total_in, total_out, self.model)

    def complete(self, system: str, user_message: str) -> LLMResult:
        """Plain text completion, no tools (used by the eval judge)."""
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        return LLMResult(
            text,
            usage.input_tokens if usage else 0,
            usage.output_tokens if usage else 0,
            self.model,
        )


def get_llm_client(model: str | None = None) -> LLMClient:
    """Return the configured provider's client, optionally pinned to a specific
    model (used by the eval harness to benchmark models and to fix the judge).
    None → the provider's default model."""
    provider = config.LLM_PROVIDER.lower()
    if provider in ("anthropic", "claude"):
        return ClaudeClient(model=model)  # None falls back to config.CLAUDE_MODEL
    raise ValueError(
        f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}'. Only 'anthropic' is "
        "implemented — add an adapter class in llm.py to support another provider."
    )
