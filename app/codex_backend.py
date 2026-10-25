"""Backend that drives `codex exec` as the model provider.

Each call spawns a non-interactive Codex CLI session in a read-only sandbox
and captures the agent's last message plus the reported token usage. This lets
the whole workbench run on an existing Codex subscription instead of an API
key.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "low"

# Models the ChatGPT-account codex backend accepts (from ~/.codex/models_cache
# + probing). Older API slugs (gpt-5, gpt-5.4-mini, o3, ...) are rejected.
MODELS = [
    {"slug": "gpt-6-luna", "label": "GPT-6 Luna — small/fast (default)"},
    {"slug": "gpt-5.6-luna", "label": "GPT-5.6 Luna — prev-gen small"},
    {"slug": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
    {"slug": "gpt-5.6-sol", "label": "GPT-5.6 Sol"},
    {"slug": "gpt-6-sol", "label": "GPT-6 Sol"},
    {"slug": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
    {"slug": "gpt-6-astra", "label": "GPT-6 Astra — largest"},
    {"slug": "gpt-reserve", "label": "GPT Reserve — hidden catalog model"},
    {"slug": "codex-auto-review", "label": "Codex Auto Review — review-tuned"},
]

# Judge schema: structured final response so grading is machine-readable.
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "rationale"],
    "additionalProperties": False,
}


@dataclass
class CodexResult:
    text: str
    tokens: int | None = None
    usage: dict | None = None  # input/cached/output token breakdown
    latency_ms: int = 0
    error: str | None = None
    raw_tail: str = field(default="", repr=False)


def _parse_events(raw: str) -> tuple[dict | None, str | None]:
    """Sum usage across `turn.completed` events in --json stdout.

    Returns (usage_totals, last_turn_error). A codex exec call can span
    multiple turns, so usage accumulates.
    """
    usage: dict | None = None
    error: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type") or ""
        if etype == "turn.completed":
            u = ev.get("usage") or {}
            if usage is None:
                usage = {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                }
            for k in usage:
                usage[k] += u.get(k, 0)
        elif etype == "turn.failed":
            error = (ev.get("error") or {}).get("message") or "turn failed"
        elif etype == "error":
            error = ev.get("message") or error
    return usage, error


async def run_codex(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    output_schema: dict | None = None,
    timeout: float = 300.0,
) -> CodexResult:
    """Run `codex exec` once and return the agent's last message.

    `output_schema`, when given, is written to a temp file and passed via
    --output-schema so the final message is guaranteed JSON.
    """
    with tempfile.TemporaryDirectory(prefix="playbook-") as tmp:
        tmp_path = Path(tmp)
        out_file = tmp_path / "last_message.txt"
        cmd = [
            "codex",
            "exec",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-s",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--json",
            "-o",
            str(out_file),
        ]
        if output_schema is not None:
            schema_file = tmp_path / "schema.json"
            schema_file.write_text(json.dumps(output_schema))
            cmd += ["--output-schema", str(schema_file)]
        cmd += ["-"]  # read the prompt from stdin

        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=tmp,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, UnboundLocalError):
                pass
            return CodexResult(
                text="", latency_ms=int((time.monotonic() - started) * 1000),
                error=f"codex exec timed out after {timeout:.0f}s",
            )
        except FileNotFoundError:
            return CodexResult(text="", error="`codex` CLI not found on PATH")

        latency_ms = int((time.monotonic() - started) * 1000)
        raw = stdout.decode(errors="replace")
        usage, turn_error = _parse_events(raw)
        tokens = (
            usage["input_tokens"] + usage["output_tokens"] if usage else None
        )
        text = out_file.read_text().strip() if out_file.exists() else ""

        error = None
        if proc.returncode != 0 and not text:
            error = turn_error or f"codex exec exited {proc.returncode}"
        elif not text:
            error = turn_error or "codex produced no message"
        return CodexResult(
            text=text,
            tokens=tokens,
            usage=usage,
            latency_ms=latency_ms,
            error=error,
            raw_tail=raw[-2000:],
        )
