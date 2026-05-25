# pqi/llm/client.py
"""
Unified LLM client — the ONLY place in the codebase that talks to Ollama or Anthropic.

Dispatches to the correct backend based on the LLM_BACKEND env var:
  "ollama"     → POST http://localhost:11434/api/chat
  "anthropic"  → Anthropic Messages API via the anthropic SDK

All callers import and await chat() — never import anthropic or httpx elsewhere.

Anthropic calls are wrapped with a centralized exponential-backoff retry
loop (see _chat_anthropic). The SDK's built-in auto-retry is disabled
(max_retries=0 on the AsyncAnthropic client) so this layer owns the
policy end-to-end. Retryable errors: 5xx (incl. 529 OverloadedError),
429 RateLimitError, transient connection / timeout errors.
"""

import asyncio
import logging

import httpx

from pqi import config

logger = logging.getLogger(__name__)


async def _chat_ollama(system: str, user: str, model: str, json_mode: bool = False) -> str:
    """Send a chat request to a local Ollama instance and return the response text."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{config.OLLAMA_BASE_URL}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


async def _chat_anthropic(system: str, user: str, model: str) -> str:
    """Send a chat request to the Anthropic Messages API and return the response text.

    Wraps the underlying messages.create() call with an exponential-backoff
    retry loop. Retryable errors (transient on Anthropic's side or in
    transit) get up to ANTHROPIC_MAX_RETRIES additional attempts; after
    that the final exception is re-raised so the caller's own retry
    layer (e.g. council._run_single_agent) can decide what to do.

    Non-retryable errors (4xx other than 429, validation errors) are
    re-raised immediately — no point burning quota on them.
    """
    import anthropic  # local import — SDK may not be installed in ollama-only setups

    if not config.ANTHROPIC_API_KEY:
        raise ValueError(
            "ANTHROPIC_API_KEY (or CLAUDE_API_KEY) is not set. "
            "Add it to your .env file before using the anthropic backend."
        )

    # Disable the SDK's built-in retry (default: 2). This module owns the
    # policy; double-retry would silently double the worst-case latency
    # and obscure the backoff schedule we expose via env.
    client = anthropic.AsyncAnthropic(
        api_key=config.ANTHROPIC_API_KEY,
        max_retries=0,
    )

    # Wrap the system prompt as a cached ephemeral block when enabled.
    # The cache is keyed by the exact prefix bytes — repeated calls within the
    # 5-minute TTL whose system prompt matches are billed at ~10% of normal
    # input. Below the model's minimum cacheable prefix the cache_control
    # field is a silent no-op (no error, just no caching).
    if config.ANTHROPIC_PROMPT_CACHE_ENABLED:
        system_param: str | list = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_param = system

    # Retryable error set: 5xx (incl. 529 OverloadedError = anthropic load
    # shedding), 429 (RateLimitError), connection drops, read timeouts.
    # InternalServerError / APIStatusError are catch-alls for anything ≥500.
    retryable = (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )

    max_retries = config.ANTHROPIC_MAX_RETRIES
    base = config.ANTHROPIC_RETRY_BASE_DELAY
    cap = config.ANTHROPIC_RETRY_MAX_DELAY
    total_attempts = 1 + max_retries

    for attempt in range(total_attempts):
        try:
            response = await client.messages.create(
                model=model,
                system=system_param,
                messages=[{"role": "user", "content": user}],
                max_tokens=config.ANTHROPIC_MAX_TOKENS,
            )
            break
        except retryable as exc:
            if attempt >= max_retries:
                logger.error(
                    "anthropic call failed after %d attempts: %s",
                    total_attempts,
                    exc,
                )
                raise
            delay = min(base * (2**attempt), cap)
            logger.warning(
                "anthropic call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1,
                total_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
        except anthropic.APIStatusError as exc:
            # Generic catch — only retry on 5xx; surface 4xx (≠429) immediately.
            status = getattr(exc, "status_code", None)
            if status is not None and status >= 500 and attempt < max_retries:
                delay = min(base * (2**attempt), cap)
                logger.warning(
                    "anthropic %s (attempt %d/%d): %s — retrying in %.1fs",
                    status,
                    attempt + 1,
                    total_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise

    # Surface cache stats so we can verify the cache is engaging without
    # waiting on the dashboard CSV. cache_read_input_tokens > 0 means we hit
    # a warm cache; cache_creation_input_tokens > 0 means we wrote one.
    usage = response.usage
    logger.debug(
        "anthropic usage: input=%d output=%d cache_read=%d cache_create=%d",
        usage.input_tokens,
        usage.output_tokens,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )

    return response.content[0].text


async def chat(
    system: str,
    user: str,
    model_override: str | None = None,
    json_mode: bool = False,
) -> str:
    """
    Public interface for all LLM calls in the codebase.

    Selects the backend from config.LLM_BACKEND and routes to the correct
    private helper. Accepts an optional model_override for one-off model changes.

    Args:
        system:        The system prompt (persona) for the LLM.
        user:          The user message (briefing document or query).
        model_override: If provided, use this model instead of the config default.
        json_mode:     If True, ask the backend to return valid JSON only.
                       For Ollama this sets {"format": "json"} in the request.
                       For Anthropic the model reliably follows JSON instructions
                       in the prompt, so this flag is a no-op there.

    Returns:
        The raw text response from the LLM (unparsed).

    Raises:
        ValueError: If LLM_BACKEND is not a recognised value.
    """
    backend = config.LLM_BACKEND

    if backend == "ollama":
        model = model_override or config.OLLAMA_MODEL
        logger.debug("LLM call → backend=ollama model=%s json_mode=%s", model, json_mode)
        return await _chat_ollama(system, user, model, json_mode=json_mode)

    if backend == "anthropic":
        model = model_override or config.ANTHROPIC_MODEL
        logger.debug("LLM call → backend=anthropic model=%s", model)
        return await _chat_anthropic(system, user, model)

    raise ValueError(
        f"Unknown LLM_BACKEND='{backend}'. Must be one of: {sorted(config.VALID_BACKENDS)}"
    )


if __name__ == "__main__":
    import asyncio
    import os

    logging.basicConfig(level=logging.DEBUG)

    async def smoke_test() -> None:
        print("=== pqi/llm/client.py smoke test ===\n")

        # --- Ollama test (always runs) ---
        print(f"[ollama] backend={config.LLM_BACKEND}, model={config.OLLAMA_MODEL}")
        print("[ollama] Sending: 'Say the word YES and nothing else'")
        response = await chat(
            system="You are a helpful assistant. Follow instructions exactly.",
            user="Say the word YES and nothing else",
        )
        assert isinstance(response, str) and len(response) > 0, (
            f"Expected non-empty string, got: {response!r}"
        )
        print(f"[ollama] Response: {response!r}")
        print("[ollama] PASS — non-empty string received\n")

        # --- Anthropic test (only when configured) ---
        anthropic_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
        if anthropic_key and config.LLM_BACKEND == "anthropic":
            print(f"[anthropic] backend=anthropic, model={config.ANTHROPIC_MODEL}")
            print("[anthropic] Sending: 'Say the word YES and nothing else'")
            resp_ant = await chat(
                system="You are a helpful assistant. Follow instructions exactly.",
                user="Say the word YES and nothing else",
            )
            assert isinstance(resp_ant, str) and len(resp_ant) > 0, (
                f"Expected non-empty string, got: {resp_ant!r}"
            )
            print(f"[anthropic] Response: {resp_ant!r}")
            print("[anthropic] PASS — non-empty string received\n")
        else:
            print(
                "[anthropic] SKIPPED — LLM_BACKEND is not 'anthropic' "
                "or ANTHROPIC_API_KEY/CLAUDE_API_KEY is not set."
            )

        print("\n✓ client.py smoke test complete.")

    asyncio.run(smoke_test())
