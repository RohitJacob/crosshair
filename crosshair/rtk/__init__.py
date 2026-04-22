"""crosshair.rtk — Python port of the common rtk output filters.

Public surface:

- ``rewrite_command(cmd)`` — string-to-string rewrite (for the preToolUse hook)
- ``run_filter(argv)`` — execute a filter by name, returns ``FilterResult``
- ``registry`` — data-only table of patterns → filter functions
- ``FilterResult`` — captured stdout/stderr/exit_code + savings
"""

from crosshair.rtk.filters.base import FilterContext, FilterResult
from crosshair.rtk.registry import RTK_COMMANDS, dispatch, find_rule, list_filters
from crosshair.rtk.rewrite import rewrite_command
from crosshair.rtk.runner import run_filter

__all__ = [
    "FilterContext",
    "FilterResult",
    "RTK_COMMANDS",
    "dispatch",
    "find_rule",
    "list_filters",
    "rewrite_command",
    "run_filter",
]
