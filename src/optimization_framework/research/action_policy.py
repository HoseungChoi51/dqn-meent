"""Execution intent for legacy research recommendations."""


def probe_execution_issue(action, *, autonomous=False):
    """Prose about a study is not a serialized single-trial procedure."""
    if action.get("probe_scope") == "plan":
        return "This probe is a conditional or multi-trial plan. Design concrete experiments before launching it."
    if autonomous and action.get("probe_scope") != "single_trial":
        return "Autonomous probes require explicit single_trial scope; this legacy recommendation needs a concrete current experiment."
    if not action.get("task_id"):
        return "This proposal needs a concrete development task."
    if not action.get("hypothesis_id") and not action.get("algorithm"):
        return "This probe must select a saved hypothesis or an explicit algorithm. No random baseline is inferred."
    if not isinstance(action.get("budget_calls"), int) or isinstance(action.get("budget_calls"), bool) or action["budget_calls"] <= 0:
        return "A single-trial probe needs an explicit positive evaluation-request limit."
    return None
