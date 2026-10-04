# Instructions for a coding agent: build, check and submit a harness

You are helping a participant enter the harness competition at $PORTAL_URL. Every entry runs the same
model, `anthropic/claude-opus-5-5`, on hidden engineering tasks. The participant competes on the **harness**: the prompts,
tools, loop and context management around the model. Your job is to turn their idea into a harness that the
portal can build and run, prove it works with the kit, and submit it when they say so.

Work in this order and do not skip a check:

1. Start from the demo harness, https://github.com/EvanZhuang/harness-competition-demo, or from the participant's repository.
2. Write the harness and its **recipe** (section 2) so that it meets the rules in sections 3 to 5.
3. `kit check`, `kit build`, `kit run --smoke-only`, then one practice task (section 6). Fix until all pass.
4. Commit, push, and pin the recipe to the pushed commit's full sha.
5. Ask the participant before submitting: they have **3 scored submissions** in total. Then
   `kit submit --yes` and follow it with `kit status --wait` (section 7).

## 1. What gets submitted

A GitHub repository at one commit, plus a recipe: a short YAML file saying how to install the harness and how
to run it on a task. The portal clones the commit, runs the recipe's install step in a build container, packs
`/opt/harness`, and unpacks that bundle into each task's container, where the recipe's command runs.

- The repository is public, or private and owned by the participant with the competition's GitHub App
  installed on it (read-only access to that one repository's contents). Private submodules are not supported.
- Nothing from the repository runs on the portal's own machines, only inside these containers.

## 2. The recipe

```yaml
name: my-harness                                # 2-39 characters: a-z, 0-9, -
source:
  repo: https://github.com/OWNER/REPO           # https, no credentials
  ref: "<the full 40-character commit sha>"     # in quotes; never a branch or tag
run:
  install: "<shell, runs once at build time, with internet>"     # optional
  command: "<shell, runs in every task, offline>"                 # must contain {model} or {model_id}
env:                                            # optional; values must be quoted strings
  ANTHROPIC_BASE_URL: "{proxy_url}"
  ANTHROPIC_API_KEY: "{proxy_key}"
```

Placeholders, filled in by the portal:

| placeholder | value | allowed in |
|---|---|---|
| `{src}` | `/opt/harness/src`, your checkout | install, command, env |
| `{model}` | `anthropic/claude-opus-5-5` | command, env |
| `{model_id}` | `claude-opus-5-5` | command, env |
| `{proxy_url}` | the model endpoint's base URL; append `/v1` for OpenAI-compatible clients | env |
| `{proxy_key}` | your submission's own key to that endpoint | env, and only as the whole value |

Rules the portal enforces at submit time (`kit check` applies the same rules offline):

- `run.command` names the model through `{model}` or `{model_id}`; the model is never hard-coded.
- `{proxy_key}` appears only as an entire `env` value, never inside a longer string or in the command.
- `env` values contain no `${...}`: nothing is filled in from the evaluation host's environment.
- `env` names match `[A-Z_][A-Z0-9_]*`; every value is a quoted string (an unquoted `true`, `1.10` or
  `0755` is refused, because YAML would read it as a boolean or a number).
- No keys outside this schema; at most 16 KiB.

## 3. The build container (`run.install`)

- A clean `debian:bookworm` container, as root, **with internet**, and no task data. 30 minutes at most.
- Your checkout is at `/opt/harness/src` (root-owned, git metadata included until packing). The working
  directory is that checkout. `$HOME` exists and is scratch.
- Preinstalled: `uv` at `/opt/harness/bin/uv`, Node 22 at `/opt/harness/node`, `git`, `curl`, compilers.
  `UV_PYTHON_INSTALL_DIR`, `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` point under `/opt/harness`, and `npm -g`
  installs into `/opt/harness/node`.
- **Everything the command needs must end up under `/opt/harness`**: that directory, packed, is the bundle
  (at most 2 GiB compressed). A typical Python harness:
  `uv venv --python 3.12 /opt/harness/venv && uv pip install --python /opt/harness/venv/bin/python -r {src}/requirements.txt`.
- Pin your dependencies. Do not rely on the task image's Python or tools: they differ between tasks.

## 4. The task container (`run.command`)

- Runs in each task's own container, in the task's working directory, often **as a non-root user**.
- **No network except the model endpoint.** Package downloads, web search and other APIs are unreachable.
  Setup is offline too: the bundle is all you have.
- Environment: `$HARBOR_INSTRUCTION` (the task, as text), `$HARBOR_MCP_CONFIG` (path to a JSON file in the
  `{"mcpServers": {...}}` form; some tasks expose their apps as MCP servers, others have none),
  `$HARBOR_MCP_SERVERS` (the same servers as Harbor's JSON list), and your recipe's `env`. `PATH` starts
  with `/opt/harness/bin` and `/opt/harness/node/bin`; call your own executables by full path anyway.
- Limits: 1 hour per task (2 hours for some), 2 to 4 CPUs, 4 to 12 GB of memory. When time runs out the
  process is killed and what is already on disk is graded.

## 5. Calling the model

- Use the Anthropic Messages API at `{proxy_url}` with `{proxy_key}` (recommended: the official SDK reads
  `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`). OpenAI-compatible clients use `{proxy_url}/v1`.
- Name the model `claude-opus-5-5` (the recipe passes it through `{model_id}`).
- `claude-opus-5-5` rejects requests with:
  - `temperature`, `top_p` or `top_k`;
  - `thinking` disabled or a `budget_tokens` (leave `thinking` out; the endpoint fixes the reasoning effort
    for everyone);
  - `tool_choice` of `any` or `tool` (use `auto`).
- **Stream every call.** A connection silent for about 15 seconds is dropped.
- Send each assistant reply back unchanged (append to the history; never edit earlier turns), and give
  `max_tokens` room (32,000 is a good default).
- Handle a refusal (`stop_reason: refusal`) and transient errors (retry with backoff) without crashing.

## 6. Doing well on a task, and checking it locally

What the graders read:

- **The files the instruction asks for, exactly where and as it says** (paths, names, formats). This is
  most of every score.
- **Your output.** The last 50,000 characters of stdout are the final message some graders read. Print
  progress as you go and end with a short summary of what you delivered and where.

Habits that score: read the whole instruction first; inspect the provided files; work in small verified
steps; write the deliverable early and improve it; keep a time budget below the limit.

The kit builds and runs your harness exactly as the portal does. It needs the competition repository (ask an
admin), Docker, Harbor (`uv tool install harbor==0.20.0`), and the practice tasks under `eval/tasks/` (from
where the competition repository's `eval/README.md` says; the list is `portal/practice_tasks.yaml`). Run it from the competition
repository's root with Harbor's interpreter and `PYTHONPATH=eval:.`:

```bash
python -m portal.kit check my-recipe.yaml                     # the portal's rules, offline
python -m portal.kit build my-recipe.yaml                     # the bundle, built as the portal builds it
python -m portal.kit run my-recipe.yaml --smoke-only          # offline install + a trivial task
python -m portal.kit run my-recipe.yaml --tasks <practice>    # a real task, scored like the portal
```

`run` calls the model with the participant's own key: `ANTHROPIC_API_KEY`, and `KIT_PROXY_URL` (default
`https://api.anthropic.com`). Rubric-graded practice tasks need `JUDGE_BACKEND` (`anthropic` by default).

Pass criteria before submitting: `check` prints no problems, `build` succeeds, the smoke test scores
**1.00**, and at least one practice task is measured with a deliverable where its instruction asked for it.

## 7. Submitting and following a submission

1. Commit and push. Set `source.ref` to `git rev-parse HEAD` of the pushed commit, in quotes.
2. The participant creates an **agent token** on $PORTAL_URL/me and gives it to you as
   `PORTAL_TOKEN`. The portal sits behind Google sign-in: run `gcloud auth login` with the participant's
   google.com account once; the kit sends `gcloud auth print-identity-token` with each request.
3. `python -m portal.kit submit my-recipe.yaml` checks the recipe on the portal and shows the quota left,
   without submitting. **With the participant's go-ahead**, `python -m portal.kit submit my-recipe.yaml --yes`
   submits it and prints its number.
4. `python -m portal.kit status <number> --wait` follows it: `queued`, `validating`, `building`, `smoke`,
   `running`, `grading`, then `scored` or `failed`. On a failure it prints the reason and the build or
   smoke log; fix, push, re-pin, and submit again.

Quota: a submission counts once it starts running. One that fails before running (invalid, build failed,
smoke failed), or fails on the portal's side, gives its slot back. A scored run takes one to two hours.

The same calls without the kit (`$ID_TOKEN` from `gcloud auth print-identity-token`):

```bash
H=(-H "Authorization: Bearer $ID_TOKEN" -H "X-Portal-Token: $PORTAL_TOKEN")
curl "${H[@]}" $PORTAL_URL/api/v1/me                                         # account, quota, submissions
curl "${H[@]}" -X POST --data-binary @my-recipe.yaml $PORTAL_URL/api/v1/check    # rules only
curl "${H[@]}" -X POST --data-binary @my-recipe.yaml $PORTAL_URL/api/v1/submissions
curl "${H[@]}" $PORTAL_URL/api/v1/submissions/<number>                         # status, logs, scores
```

## 8. When something fails

| symptom | fix |
|---|---|
| recipe refused: *put the sha in quotes* | `ref: "<sha>"` |
| build failed: *could not clone* | check the URL and that the commit is pushed; a private repository needs the GitHub App, under the participant's own account |
| build failed otherwise | read the build log; `kit build` reproduces it |
| smoke failed: *No module named ...*, *command not found* | install into `/opt/harness` and call it by full path |
| smoke failed: 401, 400, connection errors | map `{proxy_url}` and `{proxy_key}` to the variables your client reads; drop the parameters section 5 lists |
| *connection dropped*, timeouts, `harness:AgentTimeoutError` | stream every call; give the loop a time budget |
| a low score although it ran | check the instruction's paths and file names; end with a summary |

## 9. Fair play

No hard-coded answers and no detecting particular tasks. Every model call goes through the endpoint. Do not
try to reach anything else, read the graders, or tamper with the environment. The top submissions' code is
reviewed before results are final.
