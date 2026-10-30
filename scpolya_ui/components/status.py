"""Status dots and labels shared by product pages."""

from __future__ import annotations

import re
from collections.abc import Callable

SUCCESS_STATUSES = frozenset(
    {
        "succeeded",
        "ready",
        "completed",
        "complete",
        "complete_with_warnings",
        "complete_with_untestable_length_units",
        "descriptive_only",
    }
)
DANGER_STATUSES = frozenset({"failed", "error", "missing", "exited", "unverified", "unreadable", "refused"})
STOPPED_STATUSES = frozenset({"stopped", "cancelled", "prepared_eligibility_only", "not_produced"})
ACTIVE_STATUSES = frozenset({"starting", "running", "stopping", "queued"})
PLANNED_STATUSES = frozenset({"planned", "pending", "draft", "ready_for_external_approval"})


def status_tone(status: str) -> str:
    normalized = str(status or "").casefold()
    if normalized in SUCCESS_STATUSES:
        return "good"
    if normalized in DANGER_STATUSES:
        return "crit"
    if normalized in STOPPED_STATUSES:
        return "idle"
    if normalized in ACTIVE_STATUSES:
        return "run"
    if normalized in PLANNED_STATUSES:
        return "plan"
    return "warn"


def status_text(translate: Callable[..., str], status: str) -> str:
    raw = str(status or "unknown")
    key = f"status.{raw.casefold()}"
    label = translate(key)
    return raw if label == f"[[{key}]]" else label


# Pipeline names built from a template; the named parts are translated separately.
_ARTIFACT_TEMPLATES = (
    (re.compile(r"^Explicit per-unit (?P<strand>plus|minus) compatibility BAMs and indexes$"), "artifact.template.unit_strand_bams"),
    (re.compile(r"^Retained unstranded (?P<branch>expression|polyA) cohort merge chunks$"), "artifact.template.branch_chunks"),
    (re.compile(r"^Retained (?P<strand>plus|minus) compatibility cohort merge chunks$"), "artifact.template.strand_chunks"),
    (re.compile(r"^Merged unstranded (?P<branch>expression|polyA) cohort BAM and index$"), "artifact.template.branch_cohort_bam"),
    (re.compile(r"^Explicit cohort (?P<strand>plus|minus) compatibility BAMs and indexes$"), "artifact.template.cohort_strand_bams"),
    (re.compile(r"^Reference: (?P<name>.+)$"), "artifact.template.reference"),
    (re.compile(r"^annotated final output (?P<name>.+)$"), "artifact.template.annotated_final"),
    (re.compile(r"^final output (?P<name>.+)$"), "artifact.template.final"),
    (re.compile(r"^peak sidecar (?P<name>.+)$"), "artifact.template.peak_sidecar"),
    (re.compile(r"^count matrix (?P<name>.+)$"), "artifact.template.count_matrix"),
)


def artifact_label(translate: Callable[..., str], label: str) -> str:
    """Artifact names come from the pipeline in English; known ones have a catalog entry."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(label).casefold()).strip("_")
    key = f"artifact.label.{slug}"
    text = translate(key)
    if text != f"[[{key}]]":
        return text
    for pattern, template_key in _ARTIFACT_TEMPLATES:
        match = pattern.match(str(label))
        if match is None:
            continue
        params = match.groupdict()
        for part in ("strand", "branch"):
            if part in params:
                part_key = f"artifact.part.{params[part]}"
                part_text = translate(part_key)
                params[part] = params[part] if part_text == f"[[{part_key}]]" else part_text
        if template_key == "artifact.template.reference":
            # Reference files are named after their key with underscores as spaces.
            name_key = "artifact.reference." + re.sub(r"[^a-z0-9]+", "_", params["name"].casefold()).strip("_")
            name_text = translate(name_key)
            if name_text != f"[[{name_key}]]":
                params["name"] = name_text
        text = translate(template_key, **params)
        if not text.startswith("[["):
            return text
    return label


def run_kind_text(translate: Callable[..., str], kind: str) -> str:
    """Run kind (manual_rerun, cli, …) in the user's language; unknown kinds stay as recorded."""
    key = f"runs.kind.{kind}"
    text = translate(key)
    return kind if text == f"[[{key}]]" else text


def localized_params(translate: Callable[..., str], params: dict) -> dict:
    """Message parameters with any ``status`` value shown in the user's language."""
    if "status" not in params:
        return dict(params)
    return {**params, "status": status_text(translate, str(params["status"]))}


def render_status(translate: Callable[..., str], status: str, *, classes: str = ""):
    """Render a dot + localized label; the raw value stays available as a tooltip."""
    from nicegui import ui

    tone = status_tone(status)
    with ui.element("span").classes(f"sp-status sp-status-{tone} {classes}".strip()) as element:
        ui.element("i")
        ui.label(status_text(translate, status))
    return element
