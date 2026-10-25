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

## Backend: Codex CLI, not an API key

All model calls (bot under test + judge) go through your local **Codex CLI**
subscription via `codex exec` — no API key needed. Defaults to `gpt-6-luna` at
medium effort; both are configurable in the header.

Tool calling is emulated at the prompt level: the prompt tells the bot to emit
`<tool_call>{...}</tool_call>`, the harness runs the Python function, and feeds
the `<tool_result>` back. Stop sequences are applied by truncating the output.

## Run it

```bash
python3 run.py        # serves on http://127.0.0.1:8420
```

Requirements: `fastapi`, `uvicorn` (`pip install -r requirements.txt`), and the
`codex` CLI on your PATH, logged in.

## Layout

```
run.py               launcher
app/
  main.py            FastAPI routes
  codex_backend.py   `codex exec` subprocess wrapper (tokens, latency, judge schema)
  evals.py           run orchestration: tool loop, stop seqs, judge, run store
  prompts.py         preset prompts v0–v4
  testcases.py       the 5 Meridian Mobile eval cases
  tools.py           calculate_proration + tool-call protocol
  static/            single-page UI
```
