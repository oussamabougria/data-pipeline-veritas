"""
veritas_core.logging.vlog
=========================
Rule 1: Every Python file must use ``_vlog = get_vlog(__name__)``.
The name ``log`` is banned — it collides with Airflow internals.
"""

import logging
from typing import Optional


def get_vlog(name: str, level: Optional[int] = None) -> logging.Logger:
    """
    Return a standard Logger for *name*.

    Usage in every module::

        from veritas_core.logging.vlog import get_vlog
        _vlog = get_vlog(__name__)
    """
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger
