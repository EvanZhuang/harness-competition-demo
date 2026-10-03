"""A minimal harness for the competition: one model, one shell tool, a loop.

    python agent.py --model claude-opus-5-5 "$HARBOR_INSTRUCTION"

It reads the endpoint and key from ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY (the recipe maps them to
{proxy_url} / {proxy_key}), streams every model call, prints its progress as it goes, and prints the final
answer last: the tail of stdout is the final message the graders read.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import anthropic

SYSTEM = """You are an engineering agent working in a sandboxed Linux container. Your working directory holds
the task's files. Use the run_shell tool to inspect files, run code and write deliverables exactly where the
task asks for them. There is no internet access. When the work is done, reply with a concise summary of what
you delivered and where, and any assumptions you made."""

TOOLS = [{
    "name": "run_shell",
    "description": "Run a bash command in the task's working directory and return its exit code and output. "
                   "Use it to list and read files, run Python, and write files.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The bash command to run."}},
        "required": ["command"],
        "additionalProperties": False,
    },
}]
MAX_OUTPUT = 20_000                      # characters of tool output sent back to the model
COMMAND_TIMEOUT = 600                    # seconds per shell command


def run_shell(command: str) -> str:
    try:
        r = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
        out = (r.stdout + r.stderr) or "(no output)"
        status = f"exit code {r.returncode}"
    except subprocess.TimeoutExpired:
        out, status = "", f"timed out after {COMMAND_TIMEOUT} s"
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT // 2] + "\n[... output cut ...]\n" + out[-MAX_OUTPUT // 2:]
    return f"[{status}]\n{out}"


def log(msg: str) -> None:
    print(msg, flush=True)               # write as you go: a run cut off by the time limit keeps this


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--max-turns", type=int, default=100)
    ap.add_argument("--max-minutes", type=float, default=50.0, help="stop calling the model after this long")
    ap.add_argument("instruction")
    a = ap.parse_args()

    client = anthropic.Anthropic()       # ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY come from the environment
    messages = [{"role": "user", "content": a.instruction}]
    deadline = time.time() + a.max_minutes * 60
    final = ""
    for turn in range(1, a.max_turns + 1):
        # Stream: the sandbox drops a connection that stays silent for about 15 s, which a long prompt can be.
        # No temperature and no thinking settings: Claude Opus 5.5 rejects sampling parameters and disabled
        # thinking, and the competition pins its reasoning effort at the proxy.
        with client.messages.stream(model=a.model, max_tokens=32000, system=SYSTEM, tools=TOOLS,
                                    messages=messages) as stream:
            response = stream.get_final_message()
        messages.append({"role": "assistant", "content": response.content})   # append-only, blocks unchanged
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if text:
            final = text
        if response.stop_reason == "refusal":
            log("the model declined this request")
            break
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            break
        results = []
        for t in tool_uses:
            command = t.input.get("command", "")
            log(f"[turn {turn}] $ {command[:300]}")
            output = run_shell(command)
            log(output[:2000])
            results.append({"type": "tool_result", "tool_use_id": t.id, "content": output})
        messages.append({"role": "user", "content": results})
        if time.time() > deadline:
            log("time budget reached; stopping")
            break
    log("\n=== FINAL ANSWER ===")
    print(final or "(no final answer)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
