"""Hook entry points. Each module implements ``run(input_data, config, logger, store)``
and returns a dict that will be JSON-serialised to stdout.

Keep these modules side-effect-light: reads/writes only to state and logs,
never to user code directories.
"""

from crosshair.hooks import (
    after_file_edit,
    after_response,
    before_submit,
    post_tool,
    pre_compact,
    pre_tool_use,
    session_start,
    stop,
)

HANDLERS = {
    "session-start": session_start.run,
    "before-submit": before_submit.run,
    "after-response": after_response.run,
    "post-tool": post_tool.run,
    "after-file-edit": after_file_edit.run,
    "pre-compact": pre_compact.run,
    "pre-tool-use": pre_tool_use.run,
    "stop": stop.run,
}


def get_handler(name: str):
    return HANDLERS.get(name)
