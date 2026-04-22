"""beforeSubmitPrompt hook.

Does two things on every user prompt:

1. Router decision — classifies the prompt and either blocks with a model
   recommendation, adds a soft nudge, or allows it through.
2. Safepoint decision — scores conversation fatigue signals and, at the
   strongest level, attaches a handoff summary so the user can paste it into a
   new chat.

Writes back to the conversation state so the after-response / post-tool hooks
can accumulate metrics on top of what we saw here.
"""

from __future__ import annotations

from typing import Any

from crosshair.config import Config
from crosshair.logs import EventLogger
from crosshair.router.classifier import classify, decide_action, resolve_model_category
from crosshair.router.messages import build_block_message, nudge_message
from crosshair.safepoint.detector import evaluate, render_advice
from crosshair.safepoint.handoff import build_handoff_summary
from crosshair.state import ConversationState, StateStore
from crosshair.util import approx_tokens, extract_file_paths, now_iso, tokenize


def run(
    input_data: dict[str, Any],
    config: Config,
    logger: EventLogger,
    store: StateStore,
) -> dict[str, Any]:
    prompt = input_data.get("prompt", "") or ""
    model = (input_data.get("model") or "").lower()
    conversation_id = input_data.get("conversation_id", "") or "unknown"
    generation_id = input_data.get("generation_id", "")
    workspace_roots = input_data.get("workspace_roots") or []
    workspace = workspace_roots[0] if workspace_roots else ""

    state = store.load(conversation_id)
    if not state.workspace:
        state.workspace = workspace
    if not state.first_prompt:
        state.first_prompt = store.truncate_prompt(prompt)

    result = classify(prompt, config.router)
    current_category = resolve_model_category(model, config.router)

    router_out = _router_decision(
        prompt=prompt,
        model=model,
        current_category=current_category,
        classifier=result,
        config=config,
        logger=logger,
        conversation_id=conversation_id,
        generation_id=generation_id,
    )

    safepoint_out = _safepoint_decision(
        prompt=prompt,
        state=state,
        config=config,
        logger=logger,
        conversation_id=conversation_id,
    )

    _update_state_with_prompt(state, prompt, model, config)
    store.save(state)

    return _combine(router_out, safepoint_out)


def _router_decision(
    *,
    prompt: str,
    model: str,
    current_category: str | None,
    classifier,
    config: Config,
    logger: EventLogger,
    conversation_id: str,
    generation_id: str,
) -> dict[str, Any]:
    if classifier.override:
        logger.log(
            "router",
            action="override",
            conversation_id=conversation_id,
            generation_id=generation_id,
            model=model,
            prompt_snippet=logger.snippet(prompt),
            word_count=classifier.word_count,
        )
        return {"continue": True, "_router_action": "override"}

    if not classifier.matched:
        logger.log(
            "router",
            action="allow",
            conversation_id=conversation_id,
            generation_id=generation_id,
            model=model,
            prompt_snippet=logger.snippet(prompt),
            word_count=classifier.word_count,
        )
        return {"continue": True, "_router_action": "allow"}

    action, _ = decide_action(current_category, classifier.target, config.router)

    if action in ("block_downgrade", "block_upgrade"):
        message = build_block_message(
            action=action,
            current_model=model,
            current_category=current_category,
            recommendation=classifier.target,
            rule_name=classifier.rule,
            router_cfg=config.router,
        )
        logger.log(
            "router",
            action=action,
            conversation_id=conversation_id,
            generation_id=generation_id,
            model=model,
            recommendation=classifier.target,
            rule=classifier.rule,
            prompt_snippet=logger.snippet(prompt),
            word_count=classifier.word_count,
        )
        return {
            "continue": False,
            "user_message": message,
            "_router_action": action,
            "_router_message": message,
        }

    if action == "nudge":
        message = nudge_message(model, classifier.target, classifier.rule)
        logger.log(
            "router",
            action="nudge",
            conversation_id=conversation_id,
            generation_id=generation_id,
            model=model,
            recommendation=classifier.target,
            rule=classifier.rule,
            prompt_snippet=logger.snippet(prompt),
            word_count=classifier.word_count,
        )
        return {"continue": True, "user_message": message, "_router_action": "nudge"}

    logger.log(
        "router",
        action="allow",
        conversation_id=conversation_id,
        generation_id=generation_id,
        model=model,
        recommendation=classifier.target,
        rule=classifier.rule,
        prompt_snippet=logger.snippet(prompt),
        word_count=classifier.word_count,
    )
    return {"continue": True, "_router_action": "allow"}


def _safepoint_decision(
    *,
    prompt: str,
    state: ConversationState,
    config: Config,
    logger: EventLogger,
    conversation_id: str,
) -> dict[str, Any]:
    decision = evaluate(prompt, state, config.safepoint)

    if not decision.should_advise:
        state.safepoint_level_last = 0
        state.safepoint_last_score = decision.score
        state.safepoint_signals_last = decision.signal_names
        return {}

    state.safepoint_level_last = decision.level
    state.safepoint_last_score = decision.score
    state.safepoint_last_ts = now_iso()
    state.safepoint_signals_last = decision.signal_names

    advice = render_advice(decision)
    attachments = ""
    if decision.level >= 3 and (config.safepoint.get("handoff", {}) or {}).get("enabled", True):
        attachments = "\n\n" + build_handoff_summary(state, config.safepoint)

    logger.log(
        "safepoint",
        conversation_id=conversation_id,
        level=decision.level,
        label=decision.label,
        score=decision.score,
        signals=decision.signal_names,
        reasons=decision.reasons,
        estimated_tokens=state.metrics.get("estimated_tokens", 0),
    )

    message = advice + attachments
    return {
        "user_message": message,
        "_safepoint_level": decision.level,
        "_safepoint_message": message,
    }


def _update_state_with_prompt(
    state: ConversationState,
    prompt: str,
    model: str,
    config: Config,
) -> None:
    safepoint_cfg = config.safepoint or {}
    stopwords = safepoint_cfg.get("stopwords", []) or []
    tokens = approx_tokens(prompt)
    state.metrics["estimated_tokens"] = int(state.metrics.get("estimated_tokens", 0)) + tokens
    state.metrics["user_turns"] = int(state.metrics.get("user_turns", 0)) + 1
    state.last_prompt = (prompt or "")[: 400]
    state.last_prompt_ts = now_iso()

    if model and (not state.model_history or state.model_history[-1] != model):
        state.model_history.append(model)
        if len(state.model_history) > 10:
            state.model_history = state.model_history[-10:]

    kw = tokenize(prompt or "", stopwords)[:60]
    state.recent_keywords.append(kw)
    if len(state.recent_keywords) > max(5, int(safepoint_cfg.get("topic_history_size", 3)) * 2):
        state.recent_keywords = state.recent_keywords[-8:]

    state.recent_prompts.append((prompt or "")[:160])
    if len(state.recent_prompts) > 8:
        state.recent_prompts = state.recent_prompts[-8:]

    for path in extract_file_paths(prompt)[:10]:
        if path not in state.files_touched:
            state.files_touched.append(path)

    markers = safepoint_cfg.get("completion_markers", []) or []
    lowered = (prompt or "").lower()
    for m in markers:
        if m.lower() in lowered and m not in state.completion_markers:
            state.completion_markers.append(m)
            if len(state.completion_markers) > 10:
                state.completion_markers = state.completion_markers[-10:]


def _combine(router_out: dict[str, Any], safepoint_out: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["continue"] = router_out.get("continue", True)

    messages: list[str] = []
    router_msg = router_out.get("user_message") or router_out.get("_router_message")
    safe_msg = safepoint_out.get("user_message") or safepoint_out.get("_safepoint_message")
    if router_msg:
        messages.append(router_msg)
    if safe_msg:
        messages.append(safe_msg)
    if messages:
        out["user_message"] = "\n\n".join(messages)
    return out
