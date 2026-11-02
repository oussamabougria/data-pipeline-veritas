"""
Built-in physical law validators.

Each entry describes a law that Claude can check when validating sensor data.
These are passed to PhysicsLLMEngine as the tool definitions.
"""

from __future__ import annotations

LAWS: dict[str, dict] = {
    "vibration_nyquist": {
        "name": "vibration_nyquist",
        "description": (
            "Nyquist–Shannon sampling theorem for vibration sensors. "
            "The measured frequency must be less than half the sampling rate. "
            "A violation indicates aliasing or incorrect sensor configuration."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sampling_hz": {"type": "number"},
                "dominant_frequency_hz": {"type": "number"},
            },
            "required": ["sampling_hz", "dominant_frequency_hz"],
        },
    },
    "amplitude_structural_limit": {
        "name": "amplitude_structural_limit",
        "description": (
            "Structural vibration amplitude limits. "
            "Peak acceleration must not exceed 3× the rated value_max for the sensor. "
            "Exceeding this indicates sensor saturation or structural damage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "value_max": {"type": "number"},
            },
            "required": ["value", "value_max"],
        },
    },
    "conservation_of_flow": {
        "name": "conservation_of_flow",
        "description": (
            "Conservation of mass in flow measurement. "
            "Cumulative flow must be monotonically non-decreasing. "
            "A negative delta indicates meter reversal or data corruption."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "current_value": {"type": "number"},
                "previous_value": {"type": "number"},
            },
            "required": ["current_value", "previous_value"],
        },
    },
    "pressure_bounds": {
        "name": "pressure_bounds",
        "description": (
            "Physical pressure bounds check. "
            "Gauge pressure must be >= 0. Absolute pressure must be >= 0. "
            "Values outside sensor rated range indicate over-pressure or sensor failure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "value_min": {"type": "number"},
                "value_max": {"type": "number"},
                "pressure_type": {"type": "string", "enum": ["gauge", "absolute", "differential"]},
            },
            "required": ["value", "value_min", "value_max"],
        },
    },
    "thermal_limits": {
        "name": "thermal_limits",
        "description": (
            "Thermodynamic temperature bounds. "
            "Temperature must be above absolute zero (−273.15°C). "
            "Rate of change must not exceed the sensor's slew rate specification."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value_celsius": {"type": "number"},
                "rate_of_change_per_second": {"type": "number"},
                "max_slew_rate": {"type": "number"},
            },
            "required": ["value_celsius"],
        },
    },
    "binary_state_consistency": {
        "name": "binary_state_consistency",
        "description": (
            "Binary sensor state consistency. "
            "Value must be exactly 0 or 1. "
            "Rapid oscillation (chattering) within T_hold indicates contact bounce."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "transition_count_in_window": {"type": "integer"},
                "T_hold_s": {"type": "number"},
            },
            "required": ["value"],
        },
    },
    "newton_second_law": {
        "name": "newton_second_law",
        "description": (
            "Newton's second law for inertial sensors. "
            "Measured acceleration must be physically consistent with the system mass and "
            "expected force range. Extreme values indicate mechanical impact or sensor failure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "acceleration_ms2": {"type": "number"},
                "max_expected_force_n": {"type": "number"},
                "system_mass_kg": {"type": "number"},
            },
            "required": ["acceleration_ms2"],
        },
    },
}


def get_law_tools(law_names: tuple[str, ...]) -> list[dict]:
    """Return tool definitions for the requested physics laws."""
    return [LAWS[name] for name in law_names if name in LAWS]
