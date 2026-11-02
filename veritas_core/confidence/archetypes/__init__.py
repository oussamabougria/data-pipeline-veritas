from .inertial import InertialArchetype
from .cumulative import CumulativeArchetype
from .cyclical import CyclicalArchetype
from .binary import BinaryArchetype
from .base import BaseArchetype

ARCHETYPE_MAP: dict[str, BaseArchetype] = {
    "INERTIAL": InertialArchetype(),
    "CUMULATIVE": CumulativeArchetype(),
    "CYCLICAL": CyclicalArchetype(),
    "BINARY": BinaryArchetype(),
}

__all__ = [
    "BaseArchetype",
    "InertialArchetype",
    "CumulativeArchetype",
    "CyclicalArchetype",
    "BinaryArchetype",
    "ARCHETYPE_MAP",
]
