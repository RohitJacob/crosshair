"""Claude Code hook adapters.

Claude Code's hook events, JSON field names, and output contract
(``hookSpecificOutput`` + exit codes) differ from Cursor's. Each module here
translates one Claude Code event into the shared, host-agnostic handlers in
``crosshair.hooks`` and translates the result back into Claude Code's
contract, so the router/safepoint/rtk logic itself stays a single
implementation shared by both hosts.

Every adapter implements ``run(raw_input, config, logger, store) -> (output,
exit_code, stderr_text)``:

- ``output`` is the dict to print as JSON on stdout.
- ``exit_code`` is 0 (allow / observe) or 2 (block — only ``before-submit``
  ever uses this, and only when a real current model is known).
- ``stderr_text`` is the blocking reason, written to stderr when exit_code is
  2, since that is the channel Claude Code guarantees for exit-2 reasons.
"""

from __future__ import annotations

from crosshair.claude_code import (
    after_file_edit,
    pre_compact,
    pre_tool_use,
    post_tool_use,
    session_start,
    stop,
    user_prompt_submit,
)

HANDLERS = {
    "session-start": session_start.run,
    "before-submit": user_prompt_submit.run,
    "pre-tool-use": pre_tool_use.run,
    "post-tool": post_tool_use.run,
    "after-file-edit": after_file_edit.run,
    "pre-compact": pre_compact.run,
    "stop": stop.run,
}


def get_handler(name: str):
    return HANDLERS.get(name)
