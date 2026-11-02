"""
LLMSkillSuggester — uses Claude API to suggest skill parameters for new sensors.

Flow:
  1. Engineer provides sensor description, manufacturer, model, process context.
  2. This class calls Claude API with a physics-expert system prompt.
  3. Claude returns a suggested skill YAML with parameters and reasoning.
  4. The suggestion is returned to the caller — NEVER auto-applied.
  5. Engineer reviews, adjusts, and approves in the dashboard.
  6. Approved skill is stored as version 1.0.0 with source='llm_suggested'.

Rule (CLAUDE.md §4.7): The LLM suggests. It never writes corrected values.
On Claude API error: log it, return None, apply no penalty.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass

import anthropic

_vlog = logging.getLogger(__name__)

_SYSTEM_PROMPT = textwrap.dedent("""
You are a senior industrial instrumentation engineer and physicist specializing in
sensor data quality for aerospace, oil & gas, and automotive manufacturing.

You are given a description of a physical sensor deployed in an industrial process.
Your task is to suggest the optimal parameters for the Veritas C(v) confidence formula:

    C(v) = τ(T) × δ(n) × α(σ)

The formula has four sensor archetypes:
  INERTIAL:   τ = exp(-lambda × T),  δ = sigmoid(n/n_ref),  α = 1/(1 + k × σ²)
  CUMULATIVE: τ = max(0, 1 - T/T_max), δ = min(1, n/n_min), α = 1.0 if monotonic else 0.0
  CYCLICAL:   τ = |cos(π×T/T_period)|^β, δ = harmonic_mean, α = 1 - σ/amplitude
  BINARY:     τ = 1 if T < T_hold else 0, δ = 1 if n≥1 else 0, α = 1 if stable else 0

Your output must be a valid YAML skill file. Include your physical reasoning as YAML comments.
Be conservative: parameters should be tuned for safety, not optimistic performance.
Always suggest the most specific physics_laws that apply to this sensor type.
""").strip()


@dataclass
class LLMSkillSuggestion:
    skill_yaml: str        # Complete YAML content
    reasoning: str         # Why these parameters were chosen
    archetype: str         # INERTIAL / CUMULATIVE / CYCLICAL / BINARY
    confidence: float      # LLM's self-assessed confidence in the suggestion (0-1)
    model_used: str        # Claude model that generated this


class LLMSkillSuggester:
    """
    Uses Claude API to suggest skill parameters for new sensors with no history.

    The suggestion is NEVER auto-applied. Human approval is always required.
    """

    MODEL = "claude-sonnet-4-6"   # Sonnet is sufficient for structured YAML output

    def __init__(self, api_key: str | None = None) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)

    async def suggest(
        self,
        sensor_description: str,
        manufacturer: str,
        model_number: str,
        process_context: str,
        iso_standards: list[str] | None = None,
        skill_id: str | None = None,
    ) -> LLMSkillSuggestion | None:
        """
        Request a skill parameter suggestion from Claude.

        Returns LLMSkillSuggestion, or None on API error (logged, not raised).
        """
        iso_list = iso_standards or ["ISO9001"]
        generated_skill_id = skill_id or f"veritas-llm-suggested-{manufacturer.lower().replace(' ', '-')}-v1"

        user_prompt = textwrap.dedent(f"""
        Please suggest skill parameters for the following sensor:

        **Sensor description:** {sensor_description}
        **Manufacturer:** {manufacturer}
        **Model number:** {model_number}
        **Process context:** {process_context}
        **Required ISO standards:** {', '.join(iso_list)}
        **Skill ID to use:** {generated_skill_id}

        Produce a complete Veritas skill YAML file with:
        1. The correct archetype (INERTIAL / CUMULATIVE / CYCLICAL / BINARY)
        2. All physical parameters with your reasoning as inline YAML comments
        3. Appropriate physics_laws list
        4. Conservative quarantine_threshold (err on the side of caution)

        After the YAML, add a section starting with "REASONING:" explaining your parameter choices.
        Then add "CONFIDENCE: X.XX" (0-1) for your self-assessed confidence.
        """).strip()

        try:
            response = self._client.messages.create(
                model=self.MODEL,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            _vlog.error(
                "Claude API error in LLMSkillSuggester.suggest: %s (sensor=%r)",
                exc,
                sensor_description[:80],
            )
            return None

        raw_text = next(
            (b.text for b in response.content if b.type == "text"), ""
        )

        return self._parse_response(raw_text, response.model)

    def _parse_response(self, raw_text: str, model_used: str) -> LLMSkillSuggestion:
        """Extract YAML, reasoning, and confidence from Claude's response."""
        yaml_part = raw_text
        reasoning_part = ""
        confidence = 0.7  # default if not parsed

        if "REASONING:" in raw_text:
            parts = raw_text.split("REASONING:", 1)
            yaml_part = parts[0].strip()
            tail = parts[1]
            if "CONFIDENCE:" in tail:
                reason_conf = tail.split("CONFIDENCE:", 1)
                reasoning_part = reason_conf[0].strip()
                try:
                    confidence = float(reason_conf[1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
            else:
                reasoning_part = tail.strip()

        # Strip markdown code fences if present
        if "```yaml" in yaml_part:
            yaml_part = yaml_part.split("```yaml", 1)[1].split("```")[0].strip()
        elif "```" in yaml_part:
            yaml_part = yaml_part.split("```", 1)[1].split("```")[0].strip()

        # Detect archetype from YAML content
        archetype = "INERTIAL"
        for arc in ("INERTIAL", "CUMULATIVE", "CYCLICAL", "BINARY"):
            if arc in yaml_part:
                archetype = arc
                break

        return LLMSkillSuggestion(
            skill_yaml=yaml_part,
            reasoning=reasoning_part,
            archetype=archetype,
            confidence=max(0.0, min(1.0, confidence)),
            model_used=model_used,
        )
