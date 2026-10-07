"""A minimal serve-mode harness: one model, three tools, a loop (portal/SERVE.md, "The serve protocol").

    python serve.py --port 8765

The evaluator connects over a websocket and sends the task. The model's tools become protocol requests: `bash` is
`exec`, and `read_file` / `write_file` are their namesakes; the evaluator runs them in the task's container and sends
the results back. The final answer goes back as `done`.

The model is whatever the environment names:
    MODEL_API=anthropic  MODEL=claude-opus-5-5  ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY  (the portal model, or Anthropic)
    MODEL_API=openai     MODEL=deepseek-chat    OPENAI_BASE_URL, OPENAI_API_KEY        (any chat-completions endpoint:
                                                                                         OpenAI, DeepSeek, litellm, vLLM)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import json
import os
import sys
import time

from websockets.asyncio.server import serve

SYSTEM = """You are an engineering agent. The task's files are in a Linux container you control through tools:
`bash` runs a command there (in the working directory {workdir}), `read_file` and `write_file` move text in and out.
The container has no internet access. Write deliverables exactly where the task asks for them. When the work is
done, reply with a concise summary of what you delivered and where, and any assumptions you made."""

TOOLS = [
    {"name": "bash", "description": "Run a bash command in the task's container and return its exit code and output.",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string", "description": "The command."},
         "timeout_sec": {"type": "integer", "description": "Seconds before it is stopped (default 120)."}},
         "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file from the task's container.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write a text file in the task's container (parents are created).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}},
]
MAX_RESULT_CHARS = 20_000            # of a tool result sent back to the model
MAX_TURNS = 200
STOP_MARGIN_SEC = 90                 # stop calling the model this long before the agent's time is up


class Session:
    """One task: the requests in flight, matched to their results by id."""

    def __init__(self, ws):
        self.ws = ws
        self.ids = itertools.count(1)
        self.waiting: dict[str, asyncio.Future] = {}
        self.cancelled = asyncio.Event()

    async def read(self) -> None:
        async for frame in self.ws:
            msg = json.loads(frame)
            if msg.get("type") == "result" and msg.get("id") in self.waiting:
                self.waiting.pop(msg["id"]).set_result(msg)
            elif msg.get("type") == "cancel":
                self.cancelled.set()
        self.cancelled.set()

    async def request(self, kind: str, **fields) -> dict:
        rid = str(next(self.ids))
        future = asyncio.get_running_loop().create_future()
        self.waiting[rid] = future
        await self.ws.send(json.dumps({"type": kind, "id": rid, **fields}))
        cancel = asyncio.ensure_future(self.cancelled.wait())
        done, _ = await asyncio.wait({future, cancel}, return_when=asyncio.FIRST_COMPLETED)
        cancel.cancel()
        if future not in done:
            raise asyncio.CancelledError("the evaluator cancelled the task")
        return future.result()

    async def race(self, coro):
        """`coro`'s result, or CancelledError when the evaluator cancels first (a model call can be long)."""
        work = asyncio.ensure_future(coro)
        cancel = asyncio.ensure_future(self.cancelled.wait())
        done, _ = await asyncio.wait({work, cancel}, return_when=asyncio.FIRST_COMPLETED)
        cancel.cancel()
        if work not in done:
            work.cancel()
            raise asyncio.CancelledError("the evaluator cancelled the task")
        return work.result()

    async def tool(self, name: str, args: dict) -> str:
        if name == "bash":
            r = await self.request("exec", command=str(args.get("command", "")),
                                   timeout_sec=int(args.get("timeout_sec") or 120))
            if r.get("ok") is False:
                return f"error: {r.get('error')}"
            note = " (timed out)" if r.get("timed_out") else ""
            text = f"[exit code {r['exit_code']}{note}]\n{r['output'] or '(no output)'}"
        elif name == "read_file":
            r = await self.request("read_file", path=str(args.get("path", "")))
            if not r.get("ok"):
                return f"error: {r.get('error')}"
            text = base64.b64decode(r["content_b64"]).decode("utf-8", errors="replace")
        elif name == "write_file":
            content = str(args.get("content", "")).encode()
            r = await self.request("write_file", path=str(args.get("path", "")),
                                   content_b64=base64.b64encode(content).decode())
            text = f"wrote {r['size']} bytes" if r.get("ok") else f"error: {r.get('error')}"
        else:
            return f"error: unknown tool {name}"
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS // 2] + "\n[... cut ...]\n" + text[-MAX_RESULT_CHARS // 2:]
        return text

    async def note(self, kind: str, **fields) -> None:
        await self.ws.send(json.dumps({"type": kind, **fields}))


RETRY_WAITS = (5, 15, 30, 60)      # seconds between attempts at one model call


def transient(exc: Exception) -> bool:
    """A failure worth another attempt: a timeout, a dropped connection, a rate limit or a server error."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return "Timeout" in type(exc).__name__ or "Connection" in type(exc).__name__


async def with_retries(call):
    """`call()`, tried again after a transient failure; the model's reply is appended only once it succeeds, so a
    second attempt sends the same conversation."""
    for wait in (*RETRY_WAITS, None):
        try:
            return await call()
        except Exception as exc:                      # noqa: BLE001 - classified below
            if wait is None or not transient(exc):
                raise
            print(f"model call failed ({type(exc).__name__}: {exc}); again in {wait}s", file=sys.stderr, flush=True)
            await asyncio.sleep(wait)


class Anthropic:
    """Anthropic Messages (the portal model through {proxy_url}/{proxy_key}, or Anthropic itself)."""

    def __init__(self, model: str, system: str, instruction: str):
        import anthropic
        self.client = anthropic.AsyncAnthropic()      # ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY from the environment
        self.model, self.system = model, system
        self.messages: list = [{"role": "user", "content": instruction}]
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                      for t in TOOLS]

    async def step(self):
        # Streamed, with no sampling or thinking settings: Claude Opus 5.5 rejects them.
        async with self.client.messages.stream(model=self.model, max_tokens=32000, system=self.system,
                                               tools=self.tools, messages=self.messages) as stream:
            r = await stream.get_final_message()
        self.messages.append({"role": "assistant", "content": r.content})
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        calls = [(b.id, b.name, b.input) for b in r.content if b.type == "tool_use"]
        usage = {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens,
                 "cache_read_tokens": getattr(r.usage, "cache_read_input_tokens", 0) or 0}
        return text, calls if r.stop_reason == "tool_use" else [], usage

    def results(self, outputs: list[tuple[str, str]]) -> None:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": out} for cid, out in outputs]})


class OpenAI:
    """Chat completions: OpenAI, DeepSeek, a litellm or vLLM server, or any compatible endpoint."""

    def __init__(self, model: str, system: str, instruction: str):
        import openai
        self.client = openai.AsyncOpenAI()            # OPENAI_BASE_URL / OPENAI_API_KEY from the environment
        self.model = model
        self.messages: list = [{"role": "system", "content": system}, {"role": "user", "content": instruction}]
        self.tools = [{"type": "function", "function": t} for t in TOOLS]

    async def step(self):
        r = await self.client.chat.completions.create(model=self.model, messages=self.messages, tools=self.tools,
                                                      tool_choice="auto")
        m = r.choices[0].message
        self.messages.append(m.model_dump(exclude_none=True))
        calls = [(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in m.tool_calls or []]
        usage = {"input_tokens": getattr(r.usage, "prompt_tokens", 0) or 0,
                 "output_tokens": getattr(r.usage, "completion_tokens", 0) or 0, "cache_read_tokens": 0}
        return (m.content or "").strip(), calls, usage

    def results(self, outputs: list[tuple[str, str]]) -> None:
        self.messages += [{"role": "tool", "tool_call_id": cid, "content": out} for cid, out in outputs]


async def solve(session: Session, task: dict) -> str:
    model = os.environ.get("MODEL", "claude-opus-5-5")
    backend_cls = OpenAI if os.environ.get("MODEL_API", "anthropic") == "openai" else Anthropic
    backend = backend_cls(model, SYSTEM.format(workdir=task["workdir"]), task["instruction"])
    budget = (task.get("limits") or {}).get("agent_timeout_sec")
    deadline = time.monotonic() + budget - STOP_MARGIN_SEC if budget else None
    final = ""
    for turn in range(1, MAX_TURNS + 1):
        text, calls, usage = await session.race(with_retries(backend.step))
        await session.note("usage", model=model, **usage)
        final = text or final
        if not calls:
            break
        outputs = []
        for cid, name, args in calls:
            await session.note("log", text=f"[turn {turn}] {name} {json.dumps(args)[:300]}")
            outputs.append((cid, await session.tool(name, args)))
        backend.results(outputs)
        if deadline is not None and time.monotonic() > deadline:
            await session.note("log", text="time budget reached; stopping")
            break
    return final


async def handle(ws) -> None:
    task = json.loads(await ws.recv())
    session = Session(ws)
    reader = asyncio.ensure_future(session.read())
    try:
        final = await solve(session, task)
        await ws.send(json.dumps({"type": "done", "final_message": final}))
    except asyncio.CancelledError:
        await ws.send(json.dumps({"type": "done", "final_message": "(stopped: the agent's time ran out)"}))
    except Exception as exc:                          # noqa: BLE001 - report it rather than vanish
        await ws.send(json.dumps({"type": "error", "message": f"{type(exc).__name__}: {exc}"}))
    finally:
        reader.cancel()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    a = ap.parse_args()
    async with serve(handle, "0.0.0.0", a.port, max_size=16 << 20, ping_interval=None):
        print(f"serve-demo listening on {a.port}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
