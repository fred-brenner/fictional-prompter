"""Backend that runs prompts through the red_halo local GGUF model.

Instead of `codex exec`, this spawns `app/local_worker.py` under the red_halo
llm venv's python (which has llama-cpp-python + red-halo-llm installed). The
model loads once and stays resident; calls are serialized since llama.cpp
generates one completion at a time.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import time
from pathlib import Path

from .codex_backend import CodexResult

_RED_HALO = Path("/Users/frede/PycharmProjects/red_halo/llm")
_PROJECT_ROOT = Path(__file__).parent.parent
# fictional-prompter's own worker env (newest llama-cpp — needed for qwen35);
# the red_halo venv's 0.3.34 predates the qwen35 architecture entirely
VENV_PYTHON = Path(
    os.environ.get("PROMPTER_LOCAL_PYTHON", _PROJECT_ROOT / ".venv/bin/python")
)
MODEL_DIR = Path(os.environ.get("PROMPTER_LOCAL_DIR", _RED_HALO / "models"))

LOCAL_MODELS = {
    "local-gemma-4-19b": {
        "label": "Gemma 4 19B — local GGUF (red_halo)",
        "file": "gemma-4-19B-A4B-it-The-DECKARD-Heretic-Uncensored-Thinking.i1-IQ3_S.gguf",
        "engine": "worker",
        "n_gpu_layers": -1,
    },
    "local-gemma-4-19b-iq1": {
        "label": "Gemma 4 19B IQ1 — local GGUF (red_halo)",
        "file": "gemma-4-19B-A4B-it-The-DECKARD-Heretic-Uncensored-Thinking.i1-IQ1_S.gguf",
        "engine": "worker",
        "n_gpu_layers": -1,
    },
    "local-qwen3.5-9b": {
        "label": "Qwen 3.5 9B — local GGUF (red_halo)",
        "file": "Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-IQ4_XS.gguf",
        # qwen35 needs upstream llama.cpp for Metal (delta-net kernels);
        # llama-cpp-python 0.3.36 vendors an older copy that segfaults
        "engine": "server",
        "n_gpu_layers": -1,
    },
}

WORKER = Path(__file__).parent / "local_worker.py"
LOG_DIR = Path(__file__).parent.parent / "logs"
LLAMA_SERVER = _PROJECT_ROOT / "vendor/llama.cpp/build/bin/llama-server"
LLAMA_SERVER_PORT = 8421


def local_model_path(slug: str) -> Path | None:
    spec = LOCAL_MODELS.get(slug)
    return MODEL_DIR / spec["file"] if spec else None

# llama.cpp has no --output-schema, so describe the schema in prose instead.
_SCHEMA_SUFFIX = (
    "\n\nRespond with ONLY a JSON object matching this JSON schema "
    "(no prose, no markdown fences):\n"
)

_LOAD_TIMEOUT = 300.0
_REQUEST_TIMEOUT = 900.0


def is_local_model(model: str) -> bool:
    return model in LOCAL_MODELS or model.startswith("local-")


def _strip_think(text: str) -> str:
    """Remove thinking blocks (think-tags or Gemma channel format)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|channel>thought.*?<channel\|>", "", text, flags=re.DOTALL)
    # unclosed leading block (output truncated mid-thought)
    text = re.sub(r"^\s*<think>.*$", "", text, flags=re.DOTALL)
    text = re.sub(r"^\s*<\|channel>.*$", "", text, flags=re.DOTALL)
    # stray leftover control tokens like bare channel/markers
    text = re.sub(r"<[^>\n]*\|[^>\n]*>", "", text)
    return text.strip()


class _LocalWorker:
    """Owns the worker subprocess and serializes requests over stdio."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.slug: str | None = None
        self.lock = asyncio.Lock()
        self._req_id = 0
        self._log = None

    async def _ensure_locked(self, slug: str) -> asyncio.subprocess.Process:
        alive = self.proc is not None and self.proc.returncode is None
        if alive and self.slug == slug:
            return self.proc

        # dead, or a different local model was requested — swap the resident
        # model rather than holding multiple GGUFs in memory at once
        self._kill_locked()

        spec = LOCAL_MODELS[slug]
        model_path = MODEL_DIR / spec["file"]
        if not VENV_PYTHON.exists():
            raise RuntimeError(f"worker python not found: {VENV_PYTHON}")
        if not model_path.exists():
            raise RuntimeError(f"local model file not found: {model_path}")

        LOG_DIR.mkdir(exist_ok=True)
        self._log = open(LOG_DIR / "local_worker.log", "ab", buffering=0)
        self.proc = await asyncio.create_subprocess_exec(
            str(VENV_PYTHON),
            "-u",
            str(WORKER),
            str(model_path),
            "32768",  # n_ctx — eval prompts + thinking need real headroom
            str(spec.get("n_gpu_layers", -1)),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._log,
        )
        try:
            line = await asyncio.wait_for(
                self.proc.stdout.readline(), timeout=_LOAD_TIMEOUT
            )
        except (asyncio.TimeoutError, Exception) as exc:
            self._kill_locked()
            raise RuntimeError(
                f"local model failed to load: {exc!r} (see logs/local_worker.log)"
            ) from exc
        try:
            hello = json.loads(line.decode())
        except json.JSONDecodeError as exc:
            self._kill_locked()
            raise RuntimeError(f"local worker bad handshake: {line!r}") from exc
        if not hello.get("ready"):
            self._kill_locked()
            raise RuntimeError(f"local model failed to load: {hello.get('error')}")
        self.slug = slug
        return self.proc

    def _kill_locked(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        self.proc = None
        self.slug = None
        if self._log is not None:
            self._log.close()
            self._log = None

    async def generate(
        self, prompt: str, slug: str, max_tokens: int = 6144, effort: str = "low"
    ) -> dict:
        # llama_cpp has no per-request reasoning control — effort is the
        # max_tokens budget only on this engine
        del effort
        async with self.lock:
            proc = await self._ensure_locked(slug)
            self._req_id += 1
            rid = self._req_id
            req = {"id": rid, "prompt": prompt, "max_tokens": max_tokens}
            try:
                proc.stdin.write(json.dumps(req).encode() + b"\n")
                await proc.stdin.drain()
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=_REQUEST_TIMEOUT
                )
            except Exception as exc:
                self._kill_locked()
                raise RuntimeError(f"local worker request failed: {exc!r}") from exc
            if not line:
                self._kill_locked()
                raise RuntimeError("local worker exited mid-request")
            resp = json.loads(line.decode())
            if resp.get("error"):
                raise RuntimeError(resp["error"])
            return resp

    def shutdown(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
        self.proc = None
        self.slug = None
        if self._log is not None:
            self._log.close()
            self._log = None


_WORKER = _LocalWorker()


class _LlamaServer:
    """Owns a llama-server process; OpenAI-compatible HTTP on a fixed port."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.slug: str | None = None
        self.lock = asyncio.Lock()
        self._log = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{LLAMA_SERVER_PORT}"

    def _get(self, path: str, timeout: float = 5.0):
        import urllib.request

        req = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except Exception as exc:
            return None, {"error": str(exc)}

    def _post(self, path: str, payload: dict, timeout: float = 600.0):
        import urllib.request

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    async def _ensure_locked(self, slug: str) -> None:
        alive = self.proc is not None and self.proc.returncode is None
        if alive and self.slug == slug:
            return
        self._kill_locked()

        spec = LOCAL_MODELS[slug]
        model_path = MODEL_DIR / spec["file"]
        if not LLAMA_SERVER.exists():
            raise RuntimeError(f"llama-server not built: {LLAMA_SERVER}")
        if not model_path.exists():
            raise RuntimeError(f"local model file not found: {model_path}")

        LOG_DIR.mkdir(exist_ok=True)
        self._log = open(LOG_DIR / "llama_server.log", "ab", buffering=0)
        self.proc = await asyncio.create_subprocess_exec(
            str(LLAMA_SERVER),
            "--model", str(model_path),
            "--host", "127.0.0.1",
            "--port", str(LLAMA_SERVER_PORT),
            "--ctx-size", "32768",
            "--n-gpu-layers", str(spec.get("n_gpu_layers", -1)),
            "--jinja",
            stdout=self._log,
            stderr=asyncio.subprocess.STDOUT,
        )
        # /health returns 503 while the model loads
        deadline = time.monotonic() + _LOAD_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc.returncode is not None:
                break
            status, _ = await asyncio.to_thread(self._get, "/health")
            if status == 200:
                self.slug = slug
                return
            await asyncio.sleep(2)
        self._kill_locked()
        raise RuntimeError(
            "llama-server failed to become healthy (see logs/llama_server.log)"
        )

    def _kill_locked(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
        self.proc = None
        self.slug = None
        if self._log is not None:
            self._log.close()
            self._log = None

    async def generate(
        self, prompt: str, slug: str, max_tokens: int = 6144, effort: str = "low"
    ) -> dict:
        async with self.lock:
            await self._ensure_locked(slug)
            try:
                resp = await asyncio.to_thread(
                    self._post,
                    "/v1/chat/completions",
                    {
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.7,
                        "top_p": 0.95,
                        "stream": False,
                        "reasoning_effort": effort,
                        "reasoning_budget_tokens": EFFORT_REASONING_BUDGET.get(
                            effort, -1
                        ),
                    },
                )
            except Exception as exc:
                self._kill_locked()
                raise RuntimeError(f"llama-server request failed: {exc!r}") from exc
            usage = resp.get("usage") or {}
            return {
                "text": resp["choices"][0]["message"]["content"] or "",
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "cached_input_tokens": 0,
                    "output_tokens": usage.get("completion_tokens", 0),
                },
            }

    def shutdown(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
        self.proc = None
        self.slug = None
        if self._log is not None:
            self._log.close()
            self._log = None


_SERVER = _LlamaServer()

# Serializes ALL local generation across both engines so model swaps and
# requests can't interleave (each engine's own lock nests inside this).
_GEN_LOCK = asyncio.Lock()

# Idle auto-unload: release the resident model after 10 min of no local use.
_IDLE_TIMEOUT = 600.0
_idle_timer: asyncio.TimerHandle | None = None


def _cancel_idle_timer() -> None:
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _rearm_idle_timer() -> None:
    global _idle_timer
    _cancel_idle_timer()
    _idle_timer = asyncio.get_running_loop().call_later(
        _IDLE_TIMEOUT, _release_idle
    )


def _release_idle() -> None:
    asyncio.get_running_loop().create_task(_release_idle_async())


async def _release_idle_async() -> None:
    # waits on _GEN_LOCK, so an in-flight generation is never killed
    async with _GEN_LOCK:
        _WORKER.shutdown()
        _SERVER.shutdown()


# For thinking-style local models "effort" means two things: an overall
# max_tokens cap, plus (on the llama-server engine) a real reasoning budget —
# enforced server-side even if the template ignores reasoning_effort.
EFFORT_MAX_TOKENS = {"low": 4096, "medium": 8192, "high": 16384}
EFFORT_REASONING_BUDGET = {"low": 1024, "medium": 4096, "high": -1}
SCHEMA_MIN_TOKENS = 8192  # structured calls must survive the thinking + JSON


async def run_local(
    prompt: str,
    *,
    model: str,
    effort: str = "low",
    output_schema: dict | None = None,
    timeout: float = _REQUEST_TIMEOUT,
) -> CodexResult:
    if model not in LOCAL_MODELS:
        return CodexResult(text="", error=f"unknown local model: {model}")
    max_tokens = EFFORT_MAX_TOKENS.get(effort, 8192)
    if output_schema is not None:
        prompt = prompt + _SCHEMA_SUFFIX + json.dumps(output_schema)
        max_tokens = max(max_tokens, SCHEMA_MIN_TOKENS)
    started = time.monotonic()
    engine = LOCAL_MODELS[model].get("engine", "worker")
    backend = _SERVER if engine == "server" else _WORKER
    try:
        async with _GEN_LOCK:
            _cancel_idle_timer()
            # one resident model at a time, across both engines
            if engine == "server" and _WORKER.slug is not None:
                _WORKER.shutdown()
            elif engine != "server" and _SERVER.slug is not None:
                _SERVER.shutdown()
            resp = await asyncio.wait_for(
                backend.generate(prompt, model, max_tokens, effort),
                timeout=timeout,
            )
            _rearm_idle_timer()
    except Exception as exc:  # noqa: BLE001
        return CodexResult(
            text="",
            latency_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
        )
    usage = resp.get("usage")
    text = _strip_think(resp.get("text", ""))
    error = None
    if not text:
        error = (
            "local model produced no answer — thinking consumed the "
            f"{max_tokens}-token budget (raise effort)"
        )
    return CodexResult(
        text=text,
        tokens=(usage or {}).get("input_tokens", 0)
        + (usage or {}).get("output_tokens", 0),
        usage=usage,
        latency_ms=int((time.monotonic() - started) * 1000),
        error=error,
        raw_tail=resp.get("text", "")[-2000:],
    )


def shutdown_worker() -> None:
    _WORKER.shutdown()
    _SERVER.shutdown()


atexit.register(shutdown_worker)
