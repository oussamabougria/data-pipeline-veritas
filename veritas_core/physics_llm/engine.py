"""
PhysicsLLMEngine — validates sensor observations against physical laws via Claude API.

Rules (CLAUDE.md §4.7):
- validate() is always async. Never blocks the Flink job.
- On Claude API error: log, return PhysicsValidationResult(degraded=True), apply -0.05.
- The LLM suggests. It never writes corrected values. Ever.
- Runs in Airflow (async enrichment DAG), NOT in the Flink stream path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

import anthropic

from .laws_library import get_law_tools
from .prompt_builder import build_validation_prompt

_vlog = logging.getLogger(__name__)

_DEGRADED_DELTA = Decimal("-0.05")
_ZERO_DELTA = Decimal("0.0000")
_MODEL = "claude-sonnet-4-6"


@dataclass
class PhysicsValidationResult:
    physics_ok: bool
    confidence_delta: Decimal      # applied to C(v) after validation
    note: str
    degraded: bool = False         # True if Claude API error occurred


def _extract_verdict(text: str) -> dict:
    """Extract the JSON verdict block from Claude's response text."""
    match = re.search(r'\{[^}]*"physics_ok"[^}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"physics_ok": True, "confidence_delta": 0.0, "note": "no verdict parsed"}


class PhysicsLLMEngine:
    def __init__(self, api_key: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def validate(
        self,
        sensor_id: str,
        archetype: str,
        value: Decimal,
        previous_value: Decimal,
        unit: str,
        confidence: Decimal,
        age_seconds: float,
        physics_laws: tuple[str, ...],
        metadata: dict | None = None,
        llm_prompt_pack: str | None = None,
    ) -> PhysicsValidationResult:
        """
        Validate a sensor observation against physics laws via Claude tool use.

        Returns PhysicsValidationResult.
        On Claude API error: logs it, returns degraded result with -0.05 delta.
        """
        tools = get_law_tools(physics_laws)
        if not tools:
            return PhysicsValidationResult(
                physics_ok=True,
                confidence_delta=_ZERO_DELTA,
                note="no applicable physics laws defined",
            )

        system, user_msg = build_validation_prompt(
            sensor_id=sensor_id,
            archetype=archetype,
            value=value,
            previous_value=previous_value,
            unit=unit,
            confidence=confidence,
            age_seconds=age_seconds,
            metadata=metadata or {},
            llm_prompt_pack=llm_prompt_pack,
        )

        messages = [{"role": "user", "content": user_msg}]

        try:
            # Agentic loop: let Claude call tool-use until it produces a verdict
            while True:
                response = await self._client.messages.create(
                    model=_MODEL,
                    max_tokens=2048,
                    system=system,
                    tools=tools,
                    messages=messages,
                )

                if response.stop_reason == "end_turn":
                    text = next(
                        (b.text for b in response.content if b.type == "text"), ""
                    )
                    verdict = _extract_verdict(text)
                    delta_raw = verdict.get("confidence_delta", 0.0)
                    delta = Decimal(str(delta_raw)).quantize(Decimal("0.0001"))
                    return PhysicsValidationResult(
                        physics_ok=bool(verdict.get("physics_ok", True)),
                        confidence_delta=delta,
                        note=str(verdict.get("note", "")),
                    )

                if response.stop_reason == "tool_use":
                    # Execute tool calls (local validators, not remote calls)
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            result = self._run_tool(block.name, block.input)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result),
                            })
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})
                    continue

                # Unexpected stop reason — return safe default
                break

        except anthropic.APIError as exc:
            _vlog.error(
                "Claude API error in PhysicsLLMEngine.validate (sensor=%s): %s",
                sensor_id, exc,
            )
            return PhysicsValidationResult(
                physics_ok=False,
                confidence_delta=_DEGRADED_DELTA,
                note=f"API error (degraded mode): {exc}",
                degraded=True,
            )

        return PhysicsValidationResult(
            physics_ok=True,
            confidence_delta=_ZERO_DELTA,
            note="validation loop exited without verdict",
        )

    def _run_tool(self, tool_name: str, tool_input: dict) -> dict:
        """
        Execute a physics law check locally.
        Returns a dict with 'passed' (bool) and 'reason' (str).
        """
        try:
            if tool_name == "vibration_nyquist":
                sampling = float(tool_input.get("sampling_hz", 0))
                freq = float(tool_input.get("dominant_frequency_hz", 0))
                ok = freq < sampling / 2
                return {"passed": ok, "reason": f"freq={freq} Hz, nyquist={sampling/2} Hz"}

            if tool_name == "amplitude_structural_limit":
                val = abs(float(tool_input.get("value", 0)))
                limit = float(tool_input.get("value_max", 9999)) * 3
                ok = val <= limit
                return {"passed": ok, "reason": f"amplitude={val}, limit={limit}"}

            if tool_name == "conservation_of_flow":
                curr = float(tool_input.get("current_value", 0))
                prev = float(tool_input.get("previous_value", 0))
                ok = curr >= prev
                return {"passed": ok, "reason": f"current={curr}, previous={prev}"}

            if tool_name == "pressure_bounds":
                val = float(tool_input.get("value", 0))
                vmin = float(tool_input.get("value_min", 0))
                vmax = float(tool_input.get("value_max", 9999))
                ok = vmin <= val <= vmax
                return {"passed": ok, "reason": f"value={val} in [{vmin}, {vmax}]"}

            if tool_name == "thermal_limits":
                temp = float(tool_input.get("value_celsius", 0))
                ok = temp > -273.15
                return {"passed": ok, "reason": f"temperature={temp}°C"}

            if tool_name == "binary_state_consistency":
                val = float(tool_input.get("value", 0))
                ok = val in (0, 1, 0.0, 1.0)
                return {"passed": ok, "reason": f"value={val}"}

            if tool_name == "newton_second_law":
                acc = abs(float(tool_input.get("acceleration_ms2", 0)))
                ok = acc < 9810  # > 1000g is physically implausible for most systems
                return {"passed": ok, "reason": f"acceleration={acc} m/s²"}

            return {"passed": True, "reason": f"unknown tool {tool_name!r}"}

        except Exception as exc:
            return {"passed": True, "reason": f"tool error: {exc}"}
