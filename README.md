# Prompt Playbook

An eval workbench for iterating on production prompts, modeled on the tool demoed in
[*The prompting playbook*](https://www.youtube.com/watch?v=G2B0YWuJUgI) (Code with Claude).

You maintain a customer-support-bot prompt for the fictional telco **Meridian Mobile**,
run it against a 10-case eval suite (control / edge / boundary), and fix the failure
modes one by one — the same arc as the talk:

| Preset | What changes |
| ------ | ------------ |
| `v0` | The messy inherited prompt — wrong persona, website copy-paste, wall-of-text instructions, stale defensive patches |
| `v1` | Hygiene pass — XML structure, AI role, junk removed (flawed instructions kept) |
| `v2` | Output contract — `<agent_response>` tags + a harness stop sequence |
| `v3` | Tool use — `calculate_proration` replaces mental math |
| `v4` | Balanced trade-offs — two-sided escalation cost; account context is source of truth for grandfathered plans |

The eval suite covers the three case types from the talk:

- **control** (1) — a question that should always pass (Basic plan data limit)
- **edge** (5) — proration math both directions, EU roaming, current-plan
  hotspot, and a plan-fit recommendation requiring account-context reasoning
- **boundary** (4) — an ambiguous charge that must escalate, a plan-switch
  request, a legacy-plan customer whose real allowance gets withheld, and an
  off-topic request to decline

The judge is deliberately strict: partial answers, hedging, deflection to links,
and anything the criteria forbids are all fails — when in doubt, fail.

Grading is done by an LLM judge, like the talk's first scenario.

## Scheduler lab (second tab)

The video's second scenario: an agent builds a week-long retail rota — 10
employees, 3 shifts/day (2-1-2 headcount), 6 hard rules (availability,
one-shift-per-day, ≤5 shifts, **max 2 consecutive days**, everyone ≥1 shift,
senior coverage). Instead of the LLM judge, a deterministic Python grader
counts violations per generated schedule.

Five preset approaches mirror the talk's comparison:

- **Small model · baseline prompt** (`gpt-5.6-luna` @ low)
- **Large model · baseline prompt** (`gpt-6.1-sol` @ low)
- **Large model · high effort** — the "adaptive thinking" analog
- **Small model · better prompt** — plan-order + verify-your-work guidance
- **Agentic loop** — generate → evaluate (LLM auditor) → repair, up to 2
  repair rounds; **soft constraints** typed in the UI are injected into the
  evaluator prompt at runtime, without touching the Python grader

Each approach runs N trials; every trial reports violations, the schedule
grid, loop trace, tokens, est. cost and latency.

## Backends: Codex CLI + local GGUF, no API key

Codex model calls (bot under test + judge) go through your local **Codex CLI**
subscription via `codex exec` — no API key needed. Defaults to `gpt-6-luna` at
low effort; both are configurable in the header.

The model dropdown also offers two **local GGUF models** from the `red_halo`
project, both on Metal GPU: `local-gemma-4-19b` (19B, via llama-cpp-python
stdio worker) and `local-qwen3.5-9b` (9B, via `llama-server` on :8421 — its
`qwen35` arch needs the upstream llama.cpp built in `vendor/`, as
llama-cpp-python 0.3.36's vendored copy segfaults Metal). `app/local_backend.py`
owns both processes; one model stays resident at a time across both engines.
The llama-server path splits `reasoning_content` out of replies natively. For
these thinking-style models the effort selector maps to generation budget
(low/medium/high → 4k/8k/16k max_tokens). Est. costs use hosted reference
rates for the model class (~$0.20/0.60 per 1M for ~20B, ~$0.10/0.30 for ~9B),
not real API prices.

Tool calling is emulated at the prompt level: the prompt tells the bot to emit
`<tool_call>{...}</tool_call>`, the harness runs the Python function, and feeds
the `<tool_result>` back. Stop sequences are applied by truncating the output.

## Run it

```bash
python3 run.py        # serves on http://127.0.0.1:8420
```

Requirements: `fastapi`, `uvicorn` (`pip install -r requirements.txt`), and the
`codex` CLI on your PATH, logged in. For the local models, a `.venv` with
`llama-cpp-python` must exist (build with `CMAKE_ARGS="-DGGML_METAL=on
-DCMAKE_OSX_ARCHITECTURES=arm64" pip install llama-cpp-python`), plus
`vendor/llama.cpp/build/bin/llama-server` for the qwen model; paths
overridable via `PROMPTER_LOCAL_PYTHON` and `PROMPTER_LOCAL_DIR`.

## Layout

```
run.py               launcher
app/
  main.py            FastAPI routes
  codex_backend.py   `codex exec` wrapper + model dispatcher (tokens, latency, judge schema)
  local_backend.py   local GGUF backend — owns the worker process (see below)
  local_worker.py    stdio JSON-lines worker, run under the red_halo venv python
  evals.py           run orchestration: tool loop, stop seqs, judge, run store
  prompts.py         preset prompts v0–v4
  testcases.py       the 5 Meridian Mobile eval cases
  tools.py           calculate_proration + tool-call protocol
  scheduling.py      rota instance, deterministic grader, loop prompts, trial runner
  pricing.py         OpenAI list-price table for cost estimates
  static/            single-page UI (workbench + scheduler tabs)
```
