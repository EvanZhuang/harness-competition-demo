# harness-competition-demo

A minimal harness for the harness competition of Physical-Engineering’s Last Exam, in serve mode: `serve.py` is a
websocket server. For each task, the evaluator starts it in its own container, sends the task, and runs the harness's
`exec`, `read_file` and `write_file` requests in the task's container. The harness calls whatever model it is
configured for; its container reaches only the hosts `serve.egress` in `recipe.yaml` declares.

Copy this repository as the start of your own harness. [AGENTS.md](AGENTS.md) is the full contract (the recipe, the
serve protocol, secrets, the kit, the rules), written so a coding agent can follow it.

## The model

Pick it in `recipe.yaml`: plain values under `env:`, keys under `secrets:` (save them on the portal, under the same
names, before you submit).

| model | `env` | `secrets` | `serve.egress` |
|---|---|---|---|
| the portal's Claude Opus 5.5 (as committed) | `MODEL_API: anthropic`, `MODEL: claude-opus-5-5`, `ANTHROPIC_BASE_URL: "{proxy_url}"`, `ANTHROPIC_API_KEY: "{proxy_key}"` | none | `[]` |
| OpenAI | `MODEL_API: openai`, `MODEL: gpt-...` | `OPENAI_API_KEY` | `[api.openai.com]` |
| DeepSeek | `MODEL_API: openai`, `MODEL: deepseek-chat`, `OPENAI_BASE_URL: https://api.deepseek.com` | `OPENAI_API_KEY` | `[api.deepseek.com]` |
| your own litellm, vLLM or Beam endpoint | `MODEL_API: openai`, `MODEL: <its name>`, `OPENAI_BASE_URL: https://<host>/v1` | `OPENAI_API_KEY` | `[<host>]` |

Set `model:` to what you call: the leaderboard shows it.

## Develop locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
MODEL_API=openai MODEL=deepseek-chat OPENAI_BASE_URL=https://api.deepseek.com OPENAI_API_KEY=... \
    .venv/bin/python serve.py --port 8765
# in another shell, from the participant kit: run practice tasks against it
python -m portal.kit dev --url ws://127.0.0.1:8765 --tasks <a practice task>
```

`python -m portal.kit run recipe.yaml` builds the harness exactly as the portal does and runs it in its container.

## Tested

On the portal model (Claude Opus 5.5), through the full serve path on 2026-10-07: the smoke task 1.00, practice task
`batch_1__hardened__engibench_l3__041` 0.947, practice task `batch_4__fresh__civil-engineering-01__8dad83bb` 0.677.

MIT licensed.
