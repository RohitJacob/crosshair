"""Built-in filters.

Each module exposes filter functions that take an argv list and a
``FilterContext`` and return a ``FilterResult``. They are wired into
``crosshair.rtk.registry.RTK_COMMANDS``.
"""

from crosshair.rtk.filters.base import FilterContext, FilterResult, passthrough

__all__ = ["FilterContext", "FilterResult", "passthrough"]
