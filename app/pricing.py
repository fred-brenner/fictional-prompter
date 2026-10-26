"""OpenAI list-price table for cost estimates (USD per 1M tokens).

Standard short-context rates from the OpenAI API pricing page. Estimates only —
subscription/plan usage isn't billed this way, this answers "what would this run
have cost at API rates". Unknown models return None and show as '—' in the UI.
"""

# model slug -> {input, cached input, output} USD per 1M tokens
PRICES = {
    "gpt-6-luna": {"input": 0.10, "cached": 0.01, "output": 0.50},
    "gpt-5.6-luna": {"input": 0.20, "cached": 0.02, "output": 1.20},
    "gpt-6.1-sol": {"input": 2.00, "cached": 0.10, "output": 10.00},
    "gpt-6-sol": {"input": 2.00, "cached": 0.20, "output": 10.00},
    "gpt-6-astra": {"input": 10.00, "cached": 1.00, "output": 50.00},
    "gpt-5.6-sol": {"input": 4.00, "cached": 0.40, "output": 20.00},
    "gpt-5.6-terra": {"input": 2.00, "cached": 0.20, "output": 12.00},
    "gpt-5.5": {"input": 5.00, "cached": 0.50, "output": 30.00},
    "gpt-5.4": {"input": 2.50, "cached": 0.25, "output": 15.00},
    # Not API prices — reference rates for open models hosted at inference
    # providers (Together/Fireworks/OpenRouter ~$0.2/$0.6 for ~20B, ~$0.1/$0.3
    # for ~9B class).
    "local-gemma-4-19b": {"input": 0.20, "cached": 0.04, "output": 0.60},
    "local-qwen3.5-9b": {"input": 0.10, "cached": 0.02, "output": 0.30},
    # 1-bit quant of the 19B — priced like a smaller effective model
    "local-gemma-4-19b-iq1": {"input": 0.10, "cached": 0.02, "output": 0.30},
}


def estimate_cost(model: str, usage: dict | None) -> float | None:
    """Estimate USD for one call. `usage` has input/cached/output token counts.

    OpenAI accounting treats cached_input_tokens as a subset of input_tokens,
    and output_tokens as including reasoning tokens.
    """
    rates = PRICES.get(model)
    if not rates or not usage:
        return None
    uncached = max(usage.get("input_tokens", 0) - usage.get("cached_input_tokens", 0), 0)
    return (
        uncached * rates["input"]
        + usage.get("cached_input_tokens", 0) * rates["cached"]
        + usage.get("output_tokens", 0) * rates["output"]
    ) / 1_000_000
