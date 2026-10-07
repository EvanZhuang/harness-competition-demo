# Instructions for a coding agent: build, check and submit a harness

You are helping a participant enter the harness competition at $PORTAL_URL. An entry is a **harness that
runs as a server**, `serve.py`, and works hidden engineering tasks through the portal's protocol. It calls the
model the participant chooses: their own API (OpenAI, Anthropic, DeepSeek, ...), a model they serve themselves
(behind litellm, on Beam, ...), or the portal's own model, `anthropic/claude-opus-5-5`. The leaderboard ranks harness and model
together. Your job is to turn the participant's idea into a harness the portal can build and run, prove it works
with the kit, and submit it when they say so.

Work in this order and do not skip a check:

1. Start from the demo harness, https://github.com/EvanZhuang/harness-competition-demo, whose `serve.py` is the reference harness (the kit has it too,
   under `portal/examples/serve-demo/`), or from the participant's repository.
2. Write the harness and its **recipe** (section 2) so that it meets the rules in sections 3 to 6.
3. Save the secrets the harness needs on the portal (section 6). `kit check`, `kit build`, `kit run --smoke-only`,
   then one practice task (section 7). Fix until all pass.
4. Commit, push, and pin the recipe to the pushed commit's full sha.
5. Ask the participant before submitting: they have **3 scored submissions** in total. Then
   `kit submit --yes` and follow it with `kit status --wait` (section 8).

## 1. What gets submitted

A GitHub repository at one commit, plus a recipe: a short YAML file saying how to install the harness, how to
start its server, which model it calls and which hosts it reaches. The portal clones the commit, runs the recipe's
install step in a build container and packs `/opt/harness`. For every task it then starts the server from that
bundle in **a container of its own**, next to the task's container, and connects to it. The server calls its
model and acts on the task only through the serve protocol (section 5): it asks the portal to run commands and to
read and write files in the task container.

- The repository is public, or private and owned by the participant with the competition's GitHub App
  installed on it (read-only access to that one repository's contents). Private submodules are not supported.
- Two ways in: `kit submit` (or the API, section 8) sends the recipe file, `source` included. The web form
  instead lists the participant's repositories and reads `recipe.yaml` at the root of the commit they pick,
  ignoring any `source` in it. Keeping the recipe there as `recipe.yaml`, with `source` pinned for the kit,
  serves both.
- The server's container reaches **only the hosts the recipe lists** (plus the portal's model endpoint if the
  recipe uses it). The task container has no network at all, so commands the harness runs there cannot reach the
  internet either.
- **Every task's prompt and files, the hidden tasks' too, go to the model endpoint the harness calls.** Use an
  endpoint whose logging the participant is comfortable with, and do not keep task content.

## 2. The recipe

```yaml
name: my-harness                                # 2-39 characters: a-z, 0-9, -
source:
  repo: https://github.com/OWNER/REPO           # https, no credentials
  ref: "<the full 40-character commit sha>"     # in quotes; never a branch or tag
run:
  install: "<shell, runs once at build time, with internet>"     # optional
serve:
  command: "/opt/harness/venv/bin/python {src}/serve.py --port {port}"   # required; must pass {port}
  egress: ["api.deepseek.com"]                  # every host the server calls: host or host:port (443 by default)
model: "deepseek-chat"                          # required: the model it calls, as the leaderboard shows it
secrets: [DEEPSEEK_API_KEY]                     # optional: names of secrets saved on the portal (section 6)
env:                                            # optional; values must be quoted strings
  MODEL_BASE_URL: "https://api.deepseek.com"
  MODEL_NAME: "deepseek-chat"
```

Placeholders, filled in by the portal:

| placeholder | value | allowed in |
|---|---|---|
| `{src}` | `/opt/harness/src`, your checkout | install, serve.command, env |
| `{port}` | the port your server must listen on (also in `$PORT`) | serve.command, env |
| `{proxy_url}` | the portal's model endpoint (`anthropic/claude-opus-5-5`); append `/v1` for OpenAI-compatible clients | env |
| `{proxy_key}` | your submission's own key to that endpoint | env, and only as the whole value |

Rules the portal enforces at submit time (`kit check` applies the same rules offline):

- `serve.command` passes `{port}`. A recipe has no `run.command` beside it (that is the legacy mode, last section).
- `serve.egress` lists at most 8 DNS host names (`api.openai.com`, `my-model.beam.cloud:8443`): no `https://`, no
  path, no wildcard, no IP address. A host that resolves to a private or internal address is refused when the
  server tries it. Leave the list empty (`[]`) if the harness calls only the portal's model.
- `model` names what the harness calls, 1-80 printable characters. It is what the leaderboard shows; keep it true.
- `secrets` lists at most 8 names (`[A-Z_][A-Z0-9_]*`), each saved on the portal before submitting, none also an
  `env` key. API keys go here, never in `env`.
- `env` and `secrets` may not set `PORT`, `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` or `NO_PROXY`: the portal sets
  them. `env` values contain no `${...}` and are quoted strings (an unquoted `true`, `1.10` or `0755` is refused,
  because YAML would read it as a boolean or a number).
- `{proxy_key}` appears only as an entire `env` value. `{model}` is not a placeholder here: put the model's name in
  `env` yourself.
- No keys outside this schema; at most 16 KiB.

## 3. The build container (`run.install`)

- A clean `debian:bookworm` container, as root, **with internet**, and no task data. 30 minutes at most.
- Your checkout is at `/opt/harness/src` (root-owned, git metadata included until packing). The working
  directory is that checkout. `$HOME` exists and is scratch.
- Preinstalled: `uv` at `/opt/harness/bin/uv`, Node 22 at `/opt/harness/node`, `git`, `curl`, compilers.
  `UV_PYTHON_INSTALL_DIR`, `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` point under `/opt/harness`, and `npm -g`
  installs into `/opt/harness/node`.
- **Everything the server needs must end up under `/opt/harness`**: that directory, packed, is the bundle
  (at most 2 GiB compressed). A typical Python harness:
  `uv venv --python 3.12 /opt/harness/venv && uv pip install --python /opt/harness/venv/bin/python -r {src}/requirements.txt`.
- Pin your dependencies. The server runs on the same image, so what works here works there.

## 4. The serve container (`serve.command`)

- One per task, from the builder image with your bundle at `/opt/harness`. It runs `serve.command` as an
  unprivileged user: `/opt/harness` is read-only, `/tmp` and `$HOME` are writable scratch. 2 CPUs, 4 GB, 512
  processes.
- **Listen on `0.0.0.0:$PORT` for a websocket within 120 seconds**, or the task fails as your harness's failure.
  The portal opens one connection per task; serve each connection on its own.
- Environment: your recipe's `env` (placeholders filled), your `secrets`, `PORT`, and `HTTPS_PROXY`, `HTTP_PROXY`
  and `ALL_PROXY`, pointing at the egress proxy through which every outbound connection goes. Python's `requests`
  and `httpx`, and the `openai` and `anthropic` SDKs, honour them; Node's built-in `fetch` does not (use undici's
  `EnvHttpProxyAgent`). There is no direct DNS: the proxy resolves names.
- Only the hosts in `serve.egress` (and the portal's endpoint, when `env` uses `{proxy_url}`) are reachable.
- stdout and stderr are kept as the server's log (bounded). Write progress as you go.

## 5. The serve protocol

One websocket per task, JSON text frames of at most 16 MiB, one object each. The portal speaks first:

```json
{"type": "task", "protocol": 1, "session": "...", "instruction": "<the task>", "workdir": "/app",
 "limits": {"agent_timeout_sec": 3600, "exec_timeout_sec": 600, "max_output_bytes": 100000,
            "max_write_bytes": 10000000},
 "mcp_servers": [{"name": "...", "url": "http://...", "transport": "streamable-http"}]}
```

Your server then sends requests, each with an `id` it chooses (several may be in flight), and gets one
`{"type": "result", "id": ...}` back per request:

| request | result |
|---|---|
| `{"type": "exec", "id", "command": "<bash>", "timeout_sec": 120}` | `exit_code`, `output` (stdout and stderr, head and tail kept), `truncated`, `timed_out` |
| `{"type": "read_file", "id", "path": "/abs/path"}` | `ok`, `content_b64`, `size`, `truncated` (or `ok: false`, `error`) |
| `{"type": "write_file", "id", "path": "/abs/path", "content_b64": "...", "append": false}` | `ok`, `size`; parent directories are created |
| `{"type": "http", "id", "method", "url", "headers", "body_b64"}` | `status`, `headers`, `body_b64`; only to the task's `mcp_servers`, made from inside the task container |

And without an answer:

- `{"type": "log", "text": "..."}` adds a line to the task's log; `{"type": "usage", "model": "...",
  "input_tokens": N, "output_tokens": N}` reports what a model call used.
- `{"type": "done", "final_message": "..."}` ends the task. **The final message is graded**: end with a short
  summary of what you delivered and where.
- `{"type": "error", "message": "..."}` gives up.

The portal may send `{"type": "cancel", "reason": "timeout"}`; stop then, because the socket closes 10 seconds
later. What is already in the task container is graded.

What the requests do:

- `exec` runs `bash -c` in the task container, in `workdir`, as the task's user. Nothing persists between calls
  but the filesystem (a `cd` does not carry over). Each call stops at `min(timeout_sec, exec_timeout_sec, the time
  left)`.
- Files are bytes, base64-encoded both ways; paths are absolute. Write large files in several `append` calls.
- Some tasks expose their apps as MCP servers (`mcp_servers`, often empty); reach them with `http`, or with
  `exec` and a client inside the task container.
- A malformed request gets `ok: false` and an `error`, and the task continues. More than 20 malformed frames, or
  the socket closing before `done` or `error`, ends the task as your harness's failure.

A minimal server in Python (`pip install websockets`):

```python
import asyncio, json, os
import websockets

async def session(ws):
    task = json.loads(await ws.recv())                      # {"type": "task", ...}
    n = 0

    async def call(req):                                    # one request at a time keeps it simple
        nonlocal n
        n += 1
        await ws.send(json.dumps({**req, "id": str(n)}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "cancel":
                raise asyncio.CancelledError
            if msg.get("type") == "result" and msg.get("id") == str(n):
                return msg

    out = await call({"type": "exec", "command": "ls -la", "timeout_sec": 60})
    # ... your loop: ask the model, run its tool calls with call(...), feed back the results ...
    await ws.send(json.dumps({"type": "done", "final_message": "Wrote /app/output/report.md: ..."}))

async def main():
    async with websockets.serve(session, "0.0.0.0", int(os.environ["PORT"]), max_size=16 * 2**20):
        await asyncio.Future()

asyncio.run(main())
```

## 6. Calling the model, and secrets

**Your own model.** List its API host under `serve.egress`, put its key in a secret, and say what it is under
`model`. Save the secret on $PORTAL_URL/competition/me (section *Secrets*), or with `python -m portal.kit secret set NAME`
(the value is read from standard input), then name it under `secrets`; the server reads it from its environment.
A value is stored encrypted and never shown again. Examples: OpenAI (`api.openai.com`), Anthropic
(`api.anthropic.com`), DeepSeek (`api.deepseek.com`, OpenAI-compatible), a model you serve behind litellm or on
Beam (its host, and port if not 443). The portal does not pay for, or rate-limit, your model.

**The portal's model.** `env: {ANTHROPIC_BASE_URL: "{proxy_url}", ANTHROPIC_API_KEY: "{proxy_key}"}` (the official
SDK reads both), the model name `claude-opus-5-5`, and `model: "anthropic/claude-opus-5-5"` in the recipe. Its rules:

- `claude-opus-5-5` rejects `temperature`, `top_p` or `top_k`; `thinking` disabled or a `budget_tokens` (leave
  `thinking` out); and a `tool_choice` of `any` or `tool` (use `auto`).
- **Stream every call.** A connection silent for about 15 seconds is dropped.
- Send each assistant reply back unchanged, and give `max_tokens` room (32,000 is a good default).

Whatever the model: stream long calls, retry transient errors with backoff, handle a refusal without crashing,
and keep a time budget below `limits.agent_timeout_sec`.

## 7. Doing well on a task, and checking it locally

What the graders read:

- **The files the instruction asks for, exactly where and as it says** (paths, names, formats). This is
  most of every score.
- **Your final message** (`done.final_message`), which some graders read. End with a short summary of what you
  delivered and where.

Habits that score: read the whole instruction first; inspect the provided files; work in small verified
steps; write the deliverable early and improve it; keep a time budget below the limit.

The kit builds and runs your harness exactly as the portal does. It lives in the kit repository,
https://github.com/EvanZhuang/harness-competition-kit (private: an admin gives participants access), which also holds the practice tasks under
`eval/tasks/` (the list is `portal/practice_tasks.yaml`). It needs Docker and Harbor
(`uv tool install harbor==0.20.0`). Run it from the kit repository's root with Harbor's interpreter and
`PYTHONPATH=eval:.`:

```bash
python -m portal.kit check my-recipe.yaml                     # the portal's rules, offline
python -m portal.kit build my-recipe.yaml                     # the bundle, built as the portal builds it
python -m portal.kit dev --url ws://127.0.0.1:8765 --tasks <practice>   # tasks against a serve.py you started
python -m portal.kit run my-recipe.yaml --smoke-only          # your server in its container + a trivial task
python -m portal.kit run my-recipe.yaml --tasks <practice>    # a real task, scored like the portal
```

`dev` is the fast loop: start `python serve.py --port 8765` yourself (with a debugger, if you like) and let the
kit drive tasks through it. `run` uses the recipe's secrets from your environment (or `--env-file`), with the same
egress rules as the portal. Rubric-graded practice tasks need `JUDGE_BACKEND` (`anthropic` by default).

Pass criteria before submitting: `check` prints no problems, `build` succeeds, the smoke test scores
**1.00**, and at least one practice task is measured with a deliverable where its instruction asked for it.

## 8. Submitting and following a submission

1. Commit and push. Set `source.ref` to `git rev-parse HEAD` of the pushed commit, in quotes.
2. The participant creates an **agent token** on $PORTAL_URL/competition/me and gives it to you as
   `PORTAL_TOKEN`. The portal sits behind Google sign-in: run `gcloud auth login` with the participant's
   google.com account once; the kit sends `gcloud auth print-identity-token` with each request.
3. Every secret the recipe names is saved on the portal (`kit secret list` shows the names).
4. `python -m portal.kit submit my-recipe.yaml` checks the recipe on the portal and shows the quota left and any
   missing secret, without submitting. **With the participant's go-ahead**, `python -m portal.kit submit
   my-recipe.yaml --yes` submits it and prints its number.
5. `python -m portal.kit status <number> --wait` follows it: `queued`, `validating`, `building`, `smoke`,
   `running`, `grading`, then `scored` or `failed`. On a failure it prints the reason and the build or
   smoke log; fix, push, re-pin, and submit again.

Quota: a submission counts once it starts running. One that fails before running (invalid, build failed,
smoke failed), or fails on the portal's side, gives its slot back. A scored run takes one to two hours.

The same calls without the kit (`$ID_TOKEN` from `gcloud auth print-identity-token`):

```bash
H=(-H "Authorization: Bearer $ID_TOKEN" -H "X-Portal-Token: $PORTAL_TOKEN")
curl "${H[@]}" $PORTAL_URL/api/v1/me                                         # account, quota, submissions
curl "${H[@]}" -X PUT --data-binary @key.txt $PORTAL_URL/api/v1/secrets/OPENAI_API_KEY   # save a secret
curl "${H[@]}" $PORTAL_URL/api/v1/secrets                                    # their names
curl "${H[@]}" -X POST --data-binary @my-recipe.yaml $PORTAL_URL/api/v1/check    # rules only
curl "${H[@]}" -X POST --data-binary @my-recipe.yaml $PORTAL_URL/api/v1/submissions
curl "${H[@]}" $PORTAL_URL/api/v1/submissions/<number>                         # status, logs, scores
```

## 9. When something fails

| symptom | fix |
|---|---|
| recipe refused: *put the sha in quotes* | `ref: "<sha>"` |
| refused: *set these secrets first* | save them on /me or with `kit secret set NAME`, under exactly the names the recipe lists |
| build failed: *could not clone* | check the URL and that the commit is pushed; a private repository needs the GitHub App, under the participant's own account |
| build failed otherwise | read the build log; `kit build` reproduces it |
| smoke failed: the server never listened | listen on `0.0.0.0:$PORT` (not 127.0.0.1) within 120 s; install into `/opt/harness` and call it by full path |
| smoke failed: connection refused or 403 from your model's host | add the host (and port) to `serve.egress`; make the client use `HTTPS_PROXY` |
| smoke failed: 401 from your model | the secret's name in `secrets` and in your code differ, or the saved value is wrong |
| *socket closed*, `harness:AgentTimeoutError` | send `done` before the time runs out; stream model calls; give the loop a time budget |
| a low score although it ran | check the instruction's paths and file names; end with a summary |

## 10. Fair play

No hard-coded answers and no detecting particular tasks. Do not try to reach hosts the recipe does not list, read
the graders, or tamper with the environment. Do not keep or share the tasks' content. The top submissions' code is
reviewed before results are final.

## Legacy recipes (`run.command`)

Admins' reference entries may use the earlier mode: `run.command` runs inside each task's container, offline but
for the portal's endpoint, with `$HARBOR_INSTRUCTION` (the task), `$HARBOR_MCP_CONFIG` (its MCP servers as
`{"mcpServers": {...}}`), `$HARBOR_MCP_SERVERS`, and the competition's model through `{model}`/`{model_id}`,
`{proxy_url}` and `{proxy_key}`; its output's tail is the final message. `competition.yaml: submission_mode` says
whether participants may submit it.
