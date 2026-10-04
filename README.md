# Harness competition: demo harness

A complete, minimal entry for the harness competition: Claude Opus 5.5 with one `run_shell` tool, in a
streaming loop. Every entry runs the same model on hidden engineering tasks, so the harness is what competes:
the prompt, the tools, the loop, context management, sub-agents. Copy this, make it better, submit it.

| file | what |
|---|---|
| `agent.py` | the whole agent, about 100 lines: read it first |
| `requirements.txt` | the pinned SDK |
| `recipe.yaml` | how the portal installs it (`run.install`) and runs it on a task (`run.command`) |
| `AGENTS.md` | everything a coding agent needs to build, check and submit a harness |

## Make it yours

1. **Use this template** (or copy the files) into your own GitHub repository.
2. **Improve the harness.** Some directions: a better system prompt, file-editing and search tools, planning,
   verification steps before the final answer, context compaction for long tasks, sub-agents.
3. **Test it with the kit** from the competition repository (Docker and Harbor needed; see `AGENTS.md`):
   ```bash
   python -m portal.kit check recipe.yaml
   python -m portal.kit run recipe.yaml --smoke-only
   python -m portal.kit run recipe.yaml --tasks <a practice task>
   ```
4. **Pin and submit.** Push, set `ref` in `recipe.yaml` to `git rev-parse HEAD` (in quotes) and `repo` to your
   repository, then paste the recipe on the portal's Submit page, or
   `python -m portal.kit submit recipe.yaml --yes` with an agent token from the portal.

**With a coding agent:** tell it *"Read AGENTS.md, then build, check and submit my harness."*

## The rules in one breath

Install everything under `/opt/harness` at build time; tasks run offline except for the model endpoint. Name
the model with `{model_id}` and reach it through `{proxy_url}` / `{proxy_key}`. Stream every call. Write the
deliverables exactly where the task asks and end with a summary. Details: `AGENTS.md`.

## How this one did

Submitted through the portal's agent API as it stands at commit `6a2d0b2`: **0.507 on the public split**
(15 of 15 tasks measured), within a few points of the baseline harnesses. Before that, through the portal's
own pipeline: smoke test 1.00 and practice task `engibench_l3__041` 1.000.
