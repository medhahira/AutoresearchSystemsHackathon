from __future__ import annotations

import asyncio
import json
import os
from typing import TypeVar

from pydantic import BaseModel


OutputModel = TypeVar("OutputModel", bound=BaseModel)


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_LLM_TIMEOUT_S = 120


def get_default_model() -> str:
    return os.getenv("LEGAL_ARENA_MODEL", DEFAULT_MODEL)


def ensure_openai_configured() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required to run Legal Arena LLM calls.")


def get_llm_timeout_s() -> int:
    raw = os.getenv("LEGAL_ARENA_LLM_TIMEOUT_S", str(DEFAULT_LLM_TIMEOUT_S))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_LLM_TIMEOUT_S


async def structured_completion(
    *,
    output_type: type[OutputModel],
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> OutputModel:
    ensure_openai_configured()

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("Install the openai package to run LLM calls: pip install openai") from exc

    timeout_s = get_llm_timeout_s()
    async with AsyncOpenAI() as client:
        completion = await asyncio.wait_for(
            client.beta.chat.completions.parse(
                model=model or get_default_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=output_type,
            ),
            timeout=timeout_s,
        )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raw = completion.choices[0].message.content or "{}"
        return output_type.model_validate_json(raw)
    return parsed


def json_for_prompt(value: BaseModel | list[BaseModel] | dict | list | str) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return json.dumps(
            [item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in value],
            indent=2,
        )
    return json.dumps(value, indent=2)