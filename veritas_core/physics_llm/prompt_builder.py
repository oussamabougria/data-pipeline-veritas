"""Build the system prompt and user message for physics LLM validation."""

from __future__ import annotations

import textwrap
from decimal import Decimal

_SYSTEM = textwrap.dedent("""
You are a senior industrial physicist and metrologist validating sensor readings
for an ISO-certified manufacturing system.

You will be given a sensor observation with its confidence score and the physical
laws that apply to this sensor type. Your task is to:
1. Call each relevant physics validation tool with the sensor values.
2. Based on tool results, determine whether the observation is physically plausible.
3. Return a concise validation summary.

Rules:
- NEVER suggest corrected values. Your role is validation only.
- If a tool call fails, skip that law and note it in your summary.
- Be conservative: physical implausibility should lower the confidence score.
- Return your verdict as a JSON object at the end of your response:
  {"physics_ok": true|false, "confidence_delta": -0.05|0.0, "note": "..."}
""").strip()


def build_validation_prompt(
    sensor_id: str,
    archetype: str,
    value: Decimal,
    previous_value: Decimal,
    unit: str,
    confidence: Decimal,
    age_seconds: float,
    metadata: dict,
    llm_prompt_pack: str | None,
) -> tuple[str, str]:
    """
    Return (system_prompt, user_message) for the physics validation request.
    """
    user_msg = textwrap.dedent(f"""
    Sensor: {sensor_id}
    Archetype: {archetype}
    Current value: {value} {unit}
    Previous value: {previous_value} {unit}
    Data age: {age_seconds:.1f} seconds
    Current confidence score: {confidence}
    Additional context: {metadata}
    {f'Domain notes: {llm_prompt_pack}' if llm_prompt_pack else ''}

    Please validate this observation against the physical laws provided as tools.
    Return your JSON verdict at the end.
    """).strip()

    return _SYSTEM, user_msg
