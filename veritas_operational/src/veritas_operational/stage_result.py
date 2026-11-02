from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StageResult:
    data: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
