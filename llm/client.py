# pqi/llm/client.py
"""
Unified LLM client — the ONLY place in the codebase that talks to Ollama or Anthropic.

Dispatches to the correct backend based on the LLM_BACKEND env var:
  "ollama"     → POST http://localhost:11434/api/chat
  "anthropic"  → Anthropic Messages API via the anthropic SDK

All callers import and await chat() — never import anthropic or httpx elsewhere.
"""

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
    """Send a chat request to the Anthropic Messages API and return the response text."""
    import anthropic  # local import — SDK may not be installed in ollama-only setups

    if not config.ANTHROPIC_API_KEY:
        raise ValueError(
            "ANTHROPIC_API_KEY (or CLAUDE_API_KEY) is not set. "
            "Add it to your .env file before using the anthropic backend."
        )

    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=1024,
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
