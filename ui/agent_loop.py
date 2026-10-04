"""Streaming Messages API tool loop (Agent Studio stand-in). Tools come from the MCP spec,
calls dispatch in-process through IntegrationsHandlers.

stream() events: text{delta} · tool_start{id,name,round} · tool_input{delta} ·
tool_result{id,name,input,ok,summary,output,ms,ts} · notice{message} ·
done{model,rounds,usage,cost_usd,stop_reason,transcript} · error{message}.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from anthropic import APIError, AsyncAnthropic, BadRequestError

from api.integrations.config.settings import get_settings
from api.integrations.routers.integrations_handlers import IntegrationsHandlers
from api.integrations.routers.mcp_server import SERVER_INSTRUCTIONS
from api.integrations.routers.mcp_spec import collect_spec, spec_to_claude_tools

MAX_TOOL_ROUNDS = 8
MAX_TOKENS = 8192
JSON_RETRIES = 2
TOOL_RESULT_MAX_CHARS = 60000

# USD per million tokens: (input, output, cache read). Used for the on-screen estimate only.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
    "claude-fable-5-1": (10.0, 50.0, 0.25),
}

SYSTEM_PROMPT = (
    "You are a merchant operations assistant inside Razorpay Agent Studio. You answer questions "
    "about support tickets (Freshdesk) and inventory / sales orders (Zoho Inventory) using the "
    "tools provided.\n"
    "Rules:\n"
    "- Only state facts that came from tool results. Never invent ids, quantities or amounts.\n"
    "- Cite ticket ids, SKUs or order numbers so the merchant can verify. Never make up example "
    "ids or order numbers, even in a greeting; describe the kind of question instead.\n"
    "- Prefer one well-chosen tool call over many; paginate only when has_more is true and the "
    "question needs the rest.\n"
    "- If a tool returns ok=false, explain error.type in plain words (reconnect, wait, bad input) "
    "and retry at most once.\n"
    "- You are read-only. If asked to change data (create, update, delete, send), say you cannot "
    "and suggest what the merchant can do instead.\n"
    "- Keep answers short. Use a markdown table for multiple records, bullets for a few facts.\n"
    "- Count rows before stating a count; the number in the sentence must match the table.\n"
    "- Your earlier tool calls and their results are in this conversation. Reuse them; do not "
    "claim you did not check something you already fetched.\n\n"
    + SERVER_INSTRUCTIONS
)


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    ok: bool
    ms: int
    ts: float


@dataclass
class ChatResult:
    answer: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    rounds: int = 0
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0


def estimate_cost(model: str, usage: dict[str, int]) -> float:
    key = next((k for k in PRICES if model.startswith(k)), None)
    if not key:
        return 0.0
    inp, out, cache = PRICES[key]
    return round(
        usage.get("input_tokens", 0) / 1e6 * inp
        + usage.get("output_tokens", 0) / 1e6 * out
        + usage.get("cache_read_input_tokens", 0) / 1e6 * cache,
        6,
    )


def summarize_output(output: dict[str, Any]) -> str:
    """One line for the tool timeline."""
    if not output.get("ok"):
        err = output.get("error") or {}
        return f"{err.get('type', 'error')}: {err.get('message', '')}"[:160]
    if "items" in output:
        n = len(output["items"] or [])
        more = " (more pages)" if output.get("has_more") else ""
        total = f" of {output['total_count']}" if output.get("total_count") is not None else ""
        return f"{n} record{'s' if n != 1 else ''}{total}{more}"
    data = output.get("data")
    if isinstance(data, dict):
        for key in ("organization_name", "account_name", "name", "subject", "salesorder_number", "id"):
            if data.get(key):
                return str(data[key])[:120]
    return "ok"


class AgentLoop:
    def __init__(self, handlers: IntegrationsHandlers, *, client: AsyncAnthropic | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.handlers = handlers
        self.model = model or settings.anthropic_model
        self._client = client
        self._tools: list[dict[str, Any]] | None = None
        self._api_key = settings.anthropic_api_key
        # Server-side refusal fallback is an account-level beta. If the API rejects it once, it will
        # reject it every time, so the flag is turned off for the lifetime of this process on purpose.
        self._use_fallbacks = True

    def __repr__(self) -> str:  # never expose the API key through logging or debuggers
        return f"AgentLoop(model={self.model!r}, api_key={'set' if self._api_key else 'unset'})"

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set; chat is disabled")
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def tools(self) -> list[dict[str, Any]]:
        if self._tools is None:
            tools = spec_to_claude_tools(await collect_spec(self.handlers))
            for t in tools:
                t["eager_input_streaming"] = True  # arguments stream as generated; handlers validate inputs
            self._tools = tools
        return self._tools

    def _stream_kwargs(self, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "tools": tools,
            "messages": messages,
            "output_config": {"effort": "medium"},
        }
        if self._use_fallbacks:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def _stream_round(self, tools, messages, round_no: int) -> AsyncIterator[dict[str, Any]]:
        """One model turn. Yields UI events, then a final {"event": "_response", "response": msg}.
        Re-issues the turn on unparseable tool JSON (bounded) and once without the fallback beta."""
        json_retries = 0
        while True:
            try:
                async with self.client.beta.messages.stream(**self._stream_kwargs(tools, messages)) as stream:
                    async for event in stream:
                        if event.type == "text":
                            yield {"event": "text", "delta": event.text}
                        elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                            yield {"event": "tool_start", "id": event.content_block.id,
                                   "name": event.content_block.name, "round": round_no}
                        elif event.type == "input_json":
                            yield {"event": "tool_input", "delta": event.partial_json}
                    yield {"event": "_response", "response": await stream.get_final_message()}
                    return
            except ValueError:
                json_retries += 1
                if json_retries > JSON_RETRIES:
                    yield {"event": "error", "message": "model produced unparseable tool input twice, giving up"}
                    return
            except BadRequestError as exc:
                if self._use_fallbacks and "fallback" in str(exc).lower():
                    self._use_fallbacks = False
                    continue
                raise

    async def _run_tools(self, tool_uses, messages) -> AsyncIterator[dict[str, Any]]:
        """Execute the model's tool calls, yield tool_result events, append the tool_result turn."""
        results = []
        for block in tool_uses:
            args = dict(block.input) if isinstance(block.input, dict) else {}
            started = time.perf_counter()
            output = await self._dispatch(block.name, args)
            ms = int((time.perf_counter() - started) * 1000)
            yield {"event": "tool_result", "id": block.id, "name": block.name, "input": args,
                   "ok": bool(output.get("ok")), "summary": summarize_output(output), "output": output,
                   "ms": ms, "ts": time.time()}
            results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": not output.get("ok"),
                            "content": json.dumps(output)[:TOOL_RESULT_MAX_CHARS]})
        messages.append({"role": "user", "content": results})

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for route, task in self.handlers.tasks():
            if f"{route}_{task}" == name:
                return await self.handlers.handle_task(route, task, arguments)
        return {"ok": False, "error": {"type": "validation_error", "message": f"unknown tool {name}"}}

    async def stream(self, question: str, history: list[dict[str, Any]] | None = None) -> AsyncIterator[dict[str, Any]]:
        history = list(history or [])
        messages: list[dict[str, Any]] = history + [{"role": "user", "content": question}]
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        try:
            tools = await self.tools()
        except Exception as exc:  # spec build failure: config problem
            yield {"event": "error", "message": f"could not load tools: {exc}"}
            return

        try:
            for round_no in range(1, MAX_TOOL_ROUNDS + 1):
                yield {"event": "round", "n": round_no}
                response = None
                async for ev in self._stream_round(tools, messages, round_no):
                    if ev["event"] == "_response":
                        response = ev["response"]
                    else:
                        yield ev
                if response is None:  # _stream_round already emitted the error
                    return

                for key in usage:
                    usage[key] += getattr(response.usage, key, 0) or 0

                # `fallback` blocks record a server-side model switch; the API rejects them on replay.
                replay = [b.model_dump() for b in response.content if b.type != "fallback"]
                if len(replay) != len(response.content):
                    yield {"event": "notice", "message": f"safety fallback: this turn was answered by {response.model}"}
                if replay:
                    messages.append({"role": "assistant", "content": replay})

                if response.stop_reason == "pause_turn":
                    continue
                if response.stop_reason == "refusal":
                    details = getattr(response, "stop_details", None)
                    yield {"event": "error", "message": f"refused: {getattr(details, 'explanation', None) or 'the model declined this request'}"}
                    return
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if response.stop_reason == "max_tokens" and tool_uses:
                    yield {"event": "error", "message": "tool input was cut off by max_tokens; ask a narrower question"}
                    return
                if not tool_uses:
                    yield {"event": "done", "model": response.model, "rounds": round_no, "usage": usage,
                           "cost_usd": estimate_cost(self.model, usage), "stop_reason": response.stop_reason,
                           "transcript": messages[len(history):]}
                    return

                async for ev in self._run_tools(tool_uses, messages):
                    yield ev

            yield {"event": "error", "message": f"stopped after {MAX_TOOL_ROUNDS} tool rounds without a final answer"}
        except APIError as exc:
            yield {"event": "error", "message": f"Anthropic API error: {exc.__class__.__name__}: {getattr(exc, 'message', exc)}"}
        except RuntimeError as exc:
            yield {"event": "error", "message": str(exc)}

    async def run(self, question: str, history: list[dict[str, Any]] | None = None) -> ChatResult:
        """Non-streaming convenience: drain stream() into a ChatResult."""
        result = ChatResult(answer="")
        parts: list[str] = []
        async for ev in self.stream(question, history):
            kind = ev["event"]
            if kind == "text":
                parts.append(ev["delta"])
            elif kind == "round" and parts and not parts[-1].endswith("\n"):
                parts.append("\n\n")  # text written before a tool call stays, separated from the final answer
            elif kind == "tool_result":
                result.tool_calls.append(ToolCall(ev["id"], ev["name"], ev["input"], ev["output"], ev["ok"], ev["ms"], ev["ts"]))
            elif kind == "done":
                result.rounds, result.usage, result.cost_usd = ev["rounds"], ev["usage"], ev["cost_usd"]
            elif kind == "error":
                result.error = ev["message"]
        result.answer = "".join(parts).strip() or ("" if result.error else "(no answer)")
        return result
