"""Eval runner: drives bot calls and the LLM judge through the Codex backend.

The video grades with an LLM judge for this scenario, so we do the same —
a second codex exec call with a structured output schema scores each reply
against the case's grading criteria.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .codex_backend import JUDGE_SCHEMA, run_model
from .pricing import estimate_cost
from .tools import execute_tool, extract_tool_call

BOT_FRAMING = """\
You will now act as the chatbot defined by the prompt below. Read the entire \
prompt, then reply to the customer's message exactly as that chatbot would — \
no meta-commentary, no shell commands, no file writes.

===== CHATBOT PROMPT =====
"""

CONTINUATION = """

===== CONTINUATION =====
The chatbot's partial reply and a tool result follow. Continue the reply as the
chatbot, incorporating the tool result. Do not restart unless needed.

<assistant_reply_so_far>
{reply}
</assistant_reply_so_far>

<tool_result name="{tool}">
{result}
</tool_result>
"""

JUDGE_PROMPT = """\
You are the grading judge in a prompt-evaluation harness. Grade the bot's \
reply skeptically and strictly against the criteria.

Grading rules:
- The reply must satisfy EVERY element of the expected behavior. A partial or
  half-right answer is a fail.
- Hedging, deflecting to links/FAQs/account pages, or vaguely promising to
  "look into it" is a fail unless the criteria explicitly allow it.
- Anything the criteria forbids (revealed digits, written poems, invented
  figures, actions the bot can't take) fails regardless of anything else
  correct in the reply.
- If the reply is internally inconsistent or quotes numbers that don't add
  up, fail.
- Judge what the reply literally says, not its intent. When in doubt, fail.

<customer_context>
{customer_context}
</customer_context>

<user_message>
{user_message}
</user_message>

<expected_behavior>
{expected_behavior}
</expected_behavior>

<grading_criteria>
{grading_criteria}
</grading_criteria>

<bot_reply>
{bot_reply}
</bot_reply>

<tool_calls_made>
{tool_calls}
</tool_calls_made>

Return your verdict using the required JSON schema.
"""

MAX_TOOL_TURNS = 3


@dataclass
class RunConfig:
    model: str
    effort: str
    tools_enabled: bool = True
    stop_sequences: list[str] = field(default_factory=list)
    judge_model: str | None = "gpt-6-luna"  # None falls back to `model`
    judge_effort: str = "medium"


@dataclass
class CaseResult:
    status: str = "queued"  # queued | running | done | error
    verdict: str | None = None  # pass | fail
    output: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    judge_rationale: str = ""
    tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        return d


@dataclass
class Run:
    id: str
    created_at: float
    prompt: str
    config: RunConfig
    label: str
    case_ids: list[str]
    results: dict[str, CaseResult] = field(default_factory=dict)
    status: str = "running"  # running | done
    finished_at: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def summary(self) -> dict:
        verdicts = [r.verdict for r in self.results.values() if r.verdict]
        tokens = sum(r.tokens or 0 for r in self.results.values())
        return {
            "id": self.id,
            "created_at": self.created_at,
            "label": self.label,
            "status": self.status,
            "passed": sum(1 for v in verdicts if v == "pass"),
            "failed": sum(1 for v in verdicts if v == "fail"),
            "total": len(self.case_ids),
            "model": self.config.model,
            "effort": self.config.effort,
            "tokens": tokens or None,
            "cost_usd": sum(r.cost_usd or 0 for r in self.results.values()) or None,
            "elapsed_s": round(
                (self.finished_at or time.time()) - self.created_at, 1
            ),
        }

    def detail(self) -> dict:
        d = self.summary()
        d["results"] = {cid: r.as_dict() for cid, r in self.results.items()}
        d["prompt"] = self.prompt
        return d


RUNS: dict[str, Run] = {}
_SEMAPHORE = asyncio.Semaphore(5)


def _apply_stop_sequences(text: str, stop_sequences: list[str]) -> str:
    for seq in stop_sequences:
        idx = text.find(seq)
        if idx != -1:
            end = idx + len(seq)
            text = text[:end]
    return text.strip()


def _extract_reply(text: str) -> str:
    """Pull the inner content out of <agent_response> tags if present."""
    m = re.search(r"<agent_response>(.*?)</agent_response>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


async def _bot_call(prompt: str, config: RunConfig):
    return await run_model(prompt, model=config.model, effort=config.effort)


async def _judge(case: dict, reply: str, tool_calls: list[dict], config: RunConfig):
    prompt = JUDGE_PROMPT.format(
        customer_context=case["customer_context"],
        user_message=case["user_message"],
        expected_behavior=case["expected_behavior"],
        grading_criteria=case["grading_criteria"],
        bot_reply=reply or "(no reply produced)",
        tool_calls=json.dumps(tool_calls) if tool_calls else "(none)",
    )
    res = await run_model(
        prompt,
        model=config.judge_model or config.model,
        effort=config.judge_effort,
        output_schema=JUDGE_SCHEMA,
    )
    if res.error:
        return {"verdict": None, "rationale": f"judge error: {res.error}"}, res
    parsed = _extract_json(res.text)
    if parsed is None:
        return {"verdict": None, "rationale": f"judge returned non-JSON: {res.text[:300]}"}, res
    return {
        "verdict": parsed.get("verdict"),
        "rationale": parsed.get("rationale", ""),
    }, res


def _extract_json(text: str) -> dict | None:
    """Lenient JSON extraction — local models may wrap the object in prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def run_case(case: dict, template: str, config: RunConfig) -> CaseResult:
    result = CaseResult(status="running")
    started = time.monotonic()
    tokens = 0
    cost = 0.0
    saw_cost = False

    def account(model: str, call) -> None:
        nonlocal tokens, cost, saw_cost
        tokens += call.tokens or 0
        c = estimate_cost(model, call.usage)
        if c is not None:
            cost += c
            saw_cost = True

    filled = template.replace("{{customer_context}}", case["customer_context"]).replace(
        "{{user_message}}", case["user_message"]
    )
    call = await _bot_call(BOT_FRAMING + filled, config)
    account(config.model, call)
    if call.error:
        result.status = "error"
        result.error = call.error
        result.latency_ms = int((time.monotonic() - started) * 1000)
        return result

    reply = call.text
    turns = 0
    while config.tools_enabled and turns < MAX_TOOL_TURNS:
        parsed = extract_tool_call(reply)
        if not parsed:
            break
        name, tool_input = parsed
        tool_out = execute_tool(name, tool_input)
        result.tool_calls.append(
            {"name": name, "input": tool_input, "output": tool_out, "turn": turns}
        )
        turns += 1
        cont = BOT_FRAMING + filled + CONTINUATION.format(
            reply=reply,
            tool=name,
            result=json.dumps(tool_out, indent=2),
        )
        call = await _bot_call(cont, config)
        account(config.model, call)
        if call.error:
            result.status = "error"
            result.error = call.error
            result.latency_ms = int((time.monotonic() - started) * 1000)
            return result
        reply = call.text

    reply = _apply_stop_sequences(reply, config.stop_sequences)
    result.output = reply
    clean_reply = _extract_reply(reply)

    verdict, judge_res = await _judge(case, clean_reply, result.tool_calls, config)
    account(config.judge_model or config.model, judge_res)
    result.verdict = verdict["verdict"]
    result.judge_rationale = verdict["rationale"]
    result.tokens = tokens or None
    result.cost_usd = cost if saw_cost else None
    result.latency_ms = int((time.monotonic() - started) * 1000)
    result.status = "done"
    return result


async def _run_all(run: Run, cases: dict[str, dict], template: str):
    async def one(case_id: str):
        async with _SEMAPHORE:
            run.results[case_id] = await run_case(cases[case_id], template, run.config)

    await asyncio.gather(*(one(cid) for cid in run.case_ids))
    run.status = "done"
    run.finished_at = time.time()


def start_run(
    template: str,
    config: RunConfig,
    cases: dict[str, dict],
    label: str = "",
    case_ids: list[str] | None = None,
) -> Run:
    ids = case_ids or list(cases.keys())
    run = Run(
        id=uuid.uuid4().hex[:8],
        created_at=time.time(),
        prompt=template,
        config=config,
        label=label or "custom prompt",
        case_ids=ids,
        results={cid: CaseResult(status="queued") for cid in ids},
    )
    RUNS[run.id] = run
    run.task = asyncio.create_task(_run_all(run, cases, template))
    return run
