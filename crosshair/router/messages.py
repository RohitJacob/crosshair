"""User-facing message templates for router block/nudge actions."""

from __future__ import annotations

from typing import Any

_DEFAULT_DOWNGRADE = (
    "This looks like {target_category} work. {target} handles it at a fraction "
    "of the cost of {from_model}. Switch and resend. Prefix with ! to override."
)
_DEFAULT_UPGRADE = (
    "This likely needs {target}-class reasoning. Switch up from {from_model} "
    "for better results. Prefix with ! to override."
)


def _render(template: str, **ctx: Any) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace(f"{{{{{k}}}}}", str(v))
        out = out.replace(f"{{{k}}}", str(v))
    return out


def build_block_message(
    action: str,
    current_model: str,
    current_category: str | None,
    recommendation: str,
    rule_name: str | None,
    router_cfg: dict[str, Any],
) -> str:
    category_label = (rule_name or recommendation or "this task").replace("-", " ")
    if action == "block_downgrade":
        tmpl = (router_cfg.get("block_downgrade", {}) or {}).get("template", _DEFAULT_DOWNGRADE)
    elif action == "block_upgrade":
        tmpl = (router_cfg.get("block_upgrade", {}) or {}).get("template", _DEFAULT_UPGRADE)
    else:
        tmpl = _DEFAULT_UPGRADE
    return _render(
        tmpl,
        target=recommendation,
        target_category=category_label,
        from_model=current_model or current_category or "current model",
    )


def nudge_message(current_model: str, recommendation: str, rule_name: str | None) -> str:
    label = (rule_name or recommendation).replace("-", " ")
    return (
        f"Heads up: this looks like {label}; {recommendation} would likely be faster or "
        f"cheaper than {current_model}. Continuing on current model."
    )
