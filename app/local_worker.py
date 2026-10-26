"""Stdio worker that hosts a local GGUF model via llama-cpp-python.

Run under whatever python has llama_cpp installed:

    python local_worker.py MODEL_PATH [N_CTX] [N_GPU_LAYERS]

Protocol (newline-delimited JSON):
  -> stdout, once loaded:   {"ready": true, "model": "..."}
  <- stdin, per request:    {"id": N, "prompt": "...", "max_tokens": M,
                             "temperature": T, "top_p": P}
  -> stdout, per request:   {"id": N, "text": "...", "usage": {...}}
                        or  {"id": N, "error": "..."}

Only one request is in flight at a time; callers serialize anyway.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    model_path = Path(sys.argv[1])
    n_ctx = int(sys.argv[2]) if len(sys.argv) > 2 else 16384
    n_gpu_layers = int(sys.argv[3]) if len(sys.argv) > 3 else -1

    try:
        from llama_cpp import Llama
    except ImportError:
        _emit({"ready": False, "error": "llama-cpp-python is not installed"})
        return 2

    try:
        llama = Llama(
            model_path=str(model_path),
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )
    except Exception as exc:  # noqa: BLE001 — report ANY load failure upward
        _emit({"ready": False, "error": str(exc)})
        return 2

    _emit({"ready": True, "model": model_path.name})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = llama.create_chat_completion(
                messages=[{"role": "user", "content": req["prompt"]}],
                temperature=float(req.get("temperature", 0.7)),
                top_p=float(req.get("top_p", 0.95)),
                max_tokens=int(req.get("max_tokens", 6144)),
            )
            usage = resp.get("usage") or {}
            _emit(
                {
                    "id": req.get("id"),
                    "text": resp["choices"][0]["message"]["content"],
                    "usage": {
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "cached_input_tokens": 0,
                        "output_tokens": usage.get("completion_tokens", 0),
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 — keep the worker alive
            try:
                rid = json.loads(line).get("id")
            except Exception:  # noqa: BLE001
                rid = None
            _emit({"id": rid, "error": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
