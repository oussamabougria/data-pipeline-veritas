"""
Canonical INERTIAL default skill — calibrated parameters for vibration/IMU sensors.

Publish to DB with:
    skill_id="veritas-inertial-default-v1", format="python"
"""

from __future__ import annotations

from decimal import Decimal

from veritas_core.skills.base import BaseSkill


class InertialDefaultSkill(BaseSkill):
    skill_id = "veritas-inertial-default-v1"
    version = "1.0.0"
    archetype = "INERTIAL"
    tags = ["inertial", "vibration", "imu", "accelerometer", "gyroscope"]

    decay_lambda: Decimal = Decimal("0.001026")
    n_ref: int = 14
    sigma_ref: Decimal = Decimal("0.74")
    k_stability: Decimal = Decimal("0.64")
    quarantine_threshold: Decimal = Decimal("0.70")
