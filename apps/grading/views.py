"""The grading pages of one tenant centre (GRD-001..GRD-007's lines).

The hub summarises the centre's own assessments and raw results, and the pages
below it work them: the assessments list and each assessment's definition with
its audit rows, the results list and the two manage pages that record and
change them, each assessment's results sheet with the honest missing and
pending marks, and the cohort report of raw figures with no final grade and no
weighted figure anywhere (docs/STATUS.md's deferred list). Every row is read
through this centre and nothing else (RULE 6.1), the instructor's rows are
narrowed to their assigned cohorts server-side (GRD-004's mirror of ATT-004),
and the CSV answers carry the same permission, scope and rows as their pages
(SEC-001: a hidden link is not authorization)."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone

from apps.csv_safety import csv_writer
from apps.organizations.permissions import role_has_permission
from apps.organizations.views import READ_METHODS, here
from apps.pages import center_ui, page

from apps.grading.models import Assessment, GradeAuditEvent, Result
from apps.grading.reporting import grading_rows, open_enrollments, result_counts
from apps.grading.services import (
    _assessment_here,
    _grade_scope,
    create_assessment,
    edit_assessment,
    record_result,
    update_result,
)

# The rows of the assessments manage page and their names, in the order the
# form shows them (the form shares one definition with the rows it shows).
ASSESSMENT_FIELDS = (
    "assessment_action",
    "assessment_cohort",
    "assessment_number",
    "assessment_field",
    "assessment_title",
    "assessment_type",
    "assessment_date",
    "assessment_module",
    "assessment_max",
    "assessment_weight",
    "assessment_instructions",
)
ASSESSMENT_FIELD_UI = {
    "assessment_action": "grading_field_action",
    "assessment_cohort": "grading_field_cohort",
    "assessment_number": "grading_field_assessment_number",
    "assessment_field": "grading_field_edit_field",
    "assessment_title": "grading_field_title",
    "assessment_type": "grading_field_type",
    "assessment_date": "grading_field_date",
    "assessment_module": "grading_field_module",
    "assessment_max": "grading_field_max",
    "assessment_weight": "grading_field_weight",
    "assessment_instructions": "grading_field_instructions",
}
ASSESSMENT_ACTIONS = ("create", "edit")
ASSESSMENT_ACTION_UI = {"create": "grading_action_create", "edit": "grading_action_edit"}
# The edit select's options: the model field it changes, and the row that
# holds the new value.
EDIT_ROW_OF_FIELD = {
    "title": "assessment_title",
    "assessment_type": "assessment_type",
    "date": "assessment_date",
    "max_score": "assessment_max",
    "instructions": "assessment_instructions",
    "weight": "assessment_weight",
}
EDIT_FIELD_UI = {
    "title": "grading_field_title",
    "assessment_type": "grading_field_type",
    "date": "grading_field_date",
    "max_score": "grading_field_max",
    "instructions": "grading_field_instructions",
    "weight": "grading_field_weight",
}
# What each refusal code means for the rows the person typed.
ASSESSMENT_PROBLEM_UI = {
    "cohort_not_found": ("assessment_cohort", "grading_cohort_not_found"),
    "cohort_cancelled": ("assessment_cohort", "grading_cohort_cancelled"),
    "module_not_found": ("assessment_module", "grading_module_not_found"),
    "assessment_not_found": ("assessment_number", "grading_assessment_not_found"),
    "not_assigned": ("assessment_number", "grading_not_assigned"),
    "field_not_editable": ("assessment_field", "grading_not_editable"),
    "max_below_results": ("assessment_max", "grading_max_below_results"),
}
ASSESSMENT_MODEL_FIELDS = {
    "cohort": "assessment_cohort",
    "module": "assessment_module",
    "title": "assessment_title",
    "assessment_type": "assessment_type",
    "date": "assessment_date",
    "max_score": "assessment_max",
    "weight": "assessment_weight",
    "instructions": "assessment_instructions",
}

# The rows of the results manage page and what each refusal means there.
RESULT_FIELDS = (
    "result_action",
    "result_assessment",
    "result_enrollment",
    "result_number",
    "result_score",
    "result_reason",
)
RESULT_FIELD_UI = {
    "result_action": "grading_field_action",
    "result_assessment": "grading_field_assessment",
    "result_enrollment": "grading_field_enrollment",
    "result_number": "grading_field_result_number",
    "result_score": "grading_field_score",
    "result_reason": "grading_field_reason",
}
RESULT_ACTIONS = ("record", "update")
RESULT_ACTION_UI = {"record": "grading_action_record", "update": "grading_action_update"}
# GradeAuditEvent.reason's stored width; a longer typed value is refused on
# the page instead of reaching full_clean() inside the audit transaction.
RESULT_REASON_MAX = 200
RESULT_PROBLEM_UI = {
    "assessment_not_found": ("result_assessment", "grading_assessment_not_found"),
    "not_assigned": ("result_assessment", "grading_not_assigned"),
    "enrollment_not_found": ("result_enrollment", "grading_enrollment_not_found"),
    "cohort_mismatch": ("result_enrollment", "grading_cohort_mismatch"),
    "student_archived": ("result_enrollment", "grading_student_archived"),
    "enrollment_closed": ("result_enrollment", "grading_enrollment_closed"),
    "already_recorded": ("result_enrollment", "grading_already_recorded"),
    "result_not_found": ("result_number", "grading_result_not_found"),
    "score_required": ("result_score", "grading_score_required"),
    "score_above_max": ("result_score", "grading_score_above_max"),
}
RESULT_MODEL_FIELDS = {
    "assessment": "result_assessment",
    "enrollment": "result_enrollment",
    "student": "result_number",
    "score": "result_score",
}

UNCHANGED_UI = {"unchanged": "grading_unchanged"}


def date_text(value):
    """A date in the ISO form the form asks for."""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def moment_text(center, value):
    """A recorded moment, shown in this centre's own timezone (ORG-002)."""
    zone = center.display_timezone or "UTC"
    try:
        return value.astimezone(_zone(zone)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value.strftime("%Y-%m-%d %H:%M")


def _zone(name):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except LookupError:
        return ZoneInfo("UTC")


def score_text(value):
    """A score shown to two fixed places, never a float (§13)."""
    if value is None:
        return ""
    return "{:.2f}".format(value)


def parse_decimal(text):
    """(Decimal or None, accepted) - a page parses before a service is called."""
    text = str(text).strip()
    if not text:
        return None, False
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, False
    if not value.is_finite():
        return None, False
    return value, True


def parse_optional_decimal(text):
    """Like parse_decimal, but an empty text is accepted as nothing given."""
    if not str(text).strip():
        return None, True
    return parse_decimal(text)


def parse_date(text):
    """(date or None, accepted) - the ISO form the form asks for."""
    text = str(text).strip()
    if not text:
        return None, False
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), True
    except ValueError:
        return None, False


def _field_problem(ui, problems, codes, table):
    """Mark the fields a service refused (the rows the person typed)."""
    for code in codes:
        target = table.get(code)
        if target is None:
            continue
        field_id, key = target
        problems[field_id] = ui[key]


def _model_problems(ui, problems, field_problems, model_fields):
    """Mark the rows full_clean named (DECISIONS D20: the English wording of
    a validator is shown as text only, and its translation is open)."""
    for name, messages in field_problems:
        row = model_fields.get(name, name)
        words = []
        for message in messages:
            words.append(str(getattr(message, "message", message)))
        problems[row] = ui["field_invalid"].format("; ".join(words))


def student_shown(student):
    """A student named the way every page shows them (§7's rows)."""
    return f"{student.student_number} ({student.name})"


def cohort_shown(cohort):
    """A cohort named by its own number and name (RULE 6.1: this centre's)."""
    return f"{cohort.pk} ({cohort.name})"


def _not_found(request, center, ui, kind):
    """This centre's own not-found page, saying nothing about any other."""
    return page(
        request,
        "not_found",
        {
            "message": ui[kind + "_not_found"],
            "back": {
                "href": f"/o/{center.slug}/center/grading/",
                "kind": "grading",
                "title": ui["grading_heading"],
            },
        },
        center=center,
        status=404,
    )


def _audit_rows(ui, center, assessment=None):
    """This centre's audited grade actions, oldest kept first (RULE 6.5).

    What happened, where and who did it, with the records it happened to and,
    for a score change, the score before and after. An assessment's page sees
    the rows its own definition leads to; nothing here names another centre's
    records (FIN-015's line: this centre's own rows only).
    """
    events = GradeAuditEvent.objects.filter(organization=center)
    if assessment is not None:
        events = events.filter(
            Q(assessment=assessment) | Q(result__assessment=assessment)
        )
    rows = []
    for event in events.order_by("at"):
        references = []
        for name, label in (
            ("result", "result"),
            ("enrollment", "enrollment"),
            ("student", "student"),
        ):
            record = getattr(event, name)
            if record is not None:
                references.append(ui["grading_audit_ref_" + label] + " #" + str(record.pk))
        rows.append(
            {
                "id": str(event.pk),
                "action": ui["grading_audit_action_" + event.action],
                "records": ", ".join(references) or ui["not_recorded"],
                "actor": event.actor.display_name or event.actor.username,
                "at": moment_text(center, event.at),
                "from": score_text(event.previous_score) or ui["not_recorded"],
                "to": score_text(event.new_score) or ui["not_recorded"],
                "reason": event.reason or ui["not_recorded"],
            }
        )
    return rows


def assessment_rows(ui, center, record):
    """One assessment's definition, as the detail page shows it (GRD-002)."""
    return [
        {
            "id": "number",
            "title": ui["grading_assessment_row_number"],
            "value": str(record.pk),
        },
        {
            "id": "title",
            "title": ui["grading_assessment_row_title"],
            "value": record.title,
        },
        {
            "id": "type",
            "title": ui["grading_assessment_row_type"],
            "value": record.assessment_type,
        },
        {
            "id": "cohort",
            "title": ui["grading_assessment_row_cohort"],
            "value": cohort_shown(record.cohort),
        },
        {
            "id": "course",
            "title": ui["grading_assessment_row_course"],
            "value": record.cohort.course.title,
        },
        {
            "id": "module",
            "title": ui["grading_assessment_row_module"],
            "value": (
                f"{record.module.pk} ({record.module.title})"
                if record.module is not None
                else ui["not_recorded"]
            ),
        },
        {
            "id": "date",
            "title": ui["grading_assessment_row_date"],
            "value": date_text(record.date),
        },
        {
            "id": "max",
            "title": ui["grading_assessment_row_max"],
            "value": score_text(record.max_score),
        },
        {
            "id": "weight",
            "title": ui["grading_assessment_row_weight"],
            "value": (
                score_text(record.weight) if record.weight is not None else ui["not_recorded"]
            ),
        },
        {
            "id": "instructions",
            "title": ui["grading_assessment_row_instructions"],
            "value": record.instructions or ui["not_recorded"],
        },
        {
            "id": "by",
            "title": ui["grading_assessment_row_by"],
            "value": record.created_by.display_name or record.created_by.username,
        },
        {
            "id": "at",
            "title": ui["grading_assessment_row_at"],
            "value": moment_text(center, record.created_at),
        },
    ]


def grading_page(request, slug):
    """The centre's grading hub: its own counts, and the way down."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.assessment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    scope = _grade_scope(request.user, center)
    assessments = Assessment.objects.filter(organization=center)
    results = Result.objects.filter(organization=center)
    if scope is not None:
        assessments = assessments.filter(cohort_id__in=scope)
        results = results.filter(assessment__cohort_id__in=scope)
    assessment_count = assessments.count()
    recorded = results.exclude(score=None).count()
    pending = results.filter(score=None).count()
    missing = 0
    for record in assessments:
        missing += result_counts(record)["missing"]
    return page(
        request,
        "grading",
        {
            "heading": ui["grading_heading"],
            "hint": ui["grading_hint"],
            "summary": [
                {
                    "id": "assessments",
                    "title": ui["grading_summary_assessments"],
                    "value": str(assessment_count),
                },
                {
                    "id": "results",
                    "title": ui["grading_summary_results"],
                    "value": str(recorded),
                },
                {
                    "id": "pending",
                    "title": ui["grading_summary_pending"],
                    "value": str(pending),
                },
                {
                    "id": "missing",
                    "title": ui["grading_summary_missing"],
                    "value": str(missing),
                },
            ],
        },
        center=center,
    )


def assessments_page(request, slug):
    """This centre's assessments, each with its own figures (GRD-001/002).

    The instructor's rows are the assessments of the cohorts assigned to
    their staff record and nothing else - the same server-side scope the
    results and the report answer with, never a hidden link (SEC-001).
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.assessment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    scope = _grade_scope(request.user, center)
    rows = []
    for record in grading_rows(center, scope=scope):
        assessment = record["assessment"]
        rows.append(
            {
                "number": str(assessment.pk),
                "title": assessment.title,
                "type": assessment.assessment_type,
                "cohort": cohort_shown(assessment.cohort),
                "course": assessment.cohort.course.title,
                "date": date_text(assessment.date),
                "max": score_text(assessment.max_score),
            }
        )
    return page(
        request,
        "grading_assessments",
        {
            "heading": ui["grading_assessments_heading"],
            "hint": ui["grading_assessments_hint"],
            "rows": rows,
            "none": ui["grading_assessments_none"],
            "can_manage": role_has_permission(member.role, "grading.assessment.manage"),
        },
        center=center,
    )


def assessments_manage(request, slug):
    """The page assessments are defined on and worked over (GRD-001/GRD-002).

    One page takes both actions - defining one assessment of one of this
    centre's cohorts, and changing one editable field of one the centre owns -
    so a person works a definition down without chasing two addresses (the
    manage-page pattern every other area uses). The page parses dates and
    numbers first, and the service owns what the centre allows: the cohort,
    the module of the cohort's own course, and the score shape are the
    service's; lowering a maximum below an already recorded score is refused
    (6.3: nothing half-taken, no recorded score is ever orphaned).
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "grading.assessment.manage")
    action_at = f"/o/{center.slug}/center/grading/assessments/manage/"
    selects = {
        "assessment_action": tuple(
            (value, ASSESSMENT_ACTION_UI[value]) for value in ASSESSMENT_ACTIONS
        ),
        "assessment_field": tuple(
            (value, EDIT_FIELD_UI[value]) for value in EDIT_ROW_OF_FIELD
        ),
    }
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "grading_assessments_manage",
            {
                "fields": rows_from_spec(ui, sent, {}, ASSESSMENT_FIELDS, ASSESSMENT_FIELD_UI),
                "form": form_at(
                    "grading_assessments",
                    action_at, ui, sent, ASSESSMENT_FIELDS, ASSESSMENT_FIELD_UI, selects
                ),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {name: request.POST.get(name, "") for name in ASSESSMENT_FIELDS}
    problems = {}
    action = sent["assessment_action"].strip()
    note = ""
    kind = ""
    unchanged = False
    if action == "" or action not in ASSESSMENT_ACTIONS:
        if action != "":
            problems["assessment_action"] = ui["field_invalid"].format(action)
        saved, field_problems, codes = None, [], []
    else:
        bad_input = False
        date_value = None
        max_value = None
        weight_value = None
        edit_field = sent["assessment_field"].strip()
        edit_value = None
        if action == "create":
            date_value, when_ok = parse_date(sent["assessment_date"])
            if not when_ok:
                bad_input = True
                problems["assessment_date"] = ui["field_invalid"].format(
                    sent["assessment_date"]
                )
            max_value, max_ok = parse_decimal(sent["assessment_max"])
            if not max_ok:
                bad_input = True
                if sent["assessment_max"]:
                    problems["assessment_max"] = ui["field_invalid"].format(
                        sent["assessment_max"]
                    )
                else:
                    problems["assessment_max"] = ui["field_required"]
            weight_value, weight_ok = parse_optional_decimal(sent["assessment_weight"])
            if not weight_ok:
                bad_input = True
                problems["assessment_weight"] = ui["field_invalid"].format(
                    sent["assessment_weight"]
                )
        if action == "edit":
            if edit_field and edit_field not in EDIT_ROW_OF_FIELD:
                problems["assessment_field"] = ui["field_invalid"].format(edit_field)
                bad_input = True
            elif edit_field:
                row = EDIT_ROW_OF_FIELD[edit_field]
                raw = sent[row]
                if edit_field == "date":
                    edit_value, when_ok = parse_date(raw)
                    if not when_ok:
                        bad_input = True
                        problems[row] = ui["field_invalid"].format(raw)
                elif edit_field in ("max_score", "weight"):
                    if edit_field == "max_score":
                        edit_value, max_ok = parse_decimal(raw)
                        if not max_ok:
                            bad_input = True
                            problems[row] = (
                                ui["field_invalid"].format(raw)
                                if raw
                                else ui["field_required"]
                            )
                    else:
                        edit_value, weight_ok = parse_optional_decimal(raw)
                        if not weight_ok:
                            bad_input = True
                            problems[row] = ui["field_invalid"].format(raw)
                else:
                    edit_value = raw
        if bad_input:
            saved, field_problems, codes = None, [], []
        else:
            try:
                if action == "create":
                    saved, field_problems, codes = create_assessment(
                        request.user,
                        center,
                        sent["assessment_cohort"],
                        sent["assessment_title"],
                        sent["assessment_type"],
                        date_value,
                        max_value,
                        module_number=sent["assessment_module"],
                        weight=weight_value,
                        instructions=sent["assessment_instructions"],
                    )
                elif action == "edit":
                    saved, field_problems, codes = edit_assessment(
                        request.user,
                        center,
                        sent["assessment_number"],
                        edit_field,
                        edit_value,
                    )
                else:
                    saved, field_problems, codes = None, [], []
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
        for code in codes:
            if code in UNCHANGED_UI:
                note, kind = ui[UNCHANGED_UI[code]], "unchanged"
                unchanged = True
                break
        if not unchanged:
            _field_problem(ui, problems, codes, ASSESSMENT_PROBLEM_UI)
            _model_problems(ui, problems, field_problems, ASSESSMENT_MODEL_FIELDS)
        if not unchanged:
            if saved is not None:
                note, kind = ui["grading_saved"], "saved"
            elif problems:
                note, kind = ui["grading_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "grading_assessments_manage",
        {
            "fields": rows_from_spec(
                ui, sent, problems, ASSESSMENT_FIELDS, ASSESSMENT_FIELD_UI
            ),
            "form": form_at(
                "grading_assessments",
                action_at, ui, sent, ASSESSMENT_FIELDS, ASSESSMENT_FIELD_UI, selects
            ),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def rows_from_spec(ui, sent, problems, fields, field_ui):
    """One manage page's rows: what the page is, and what was not accepted."""
    return [
        {
            "id": name,
            "title": ui[field_ui[name]],
            "value": sent.get(name, ""),
            "problem": problems.get(name, ""),
        }
        for name in fields
    ]


def form_at(form_id, action, ui, sent, fields, field_ui, selects):
    """The manage page's form, built from the same rows it shows."""
    fields_out = []
    for name in fields:
        field = {
            "id": name,
            "title": ui[field_ui[name]],
            "type": "text",
            "value": sent.get(name, ""),
        }
        if name in selects:
            field["type"] = "select"
            field["current"] = sent.get(name, "")
            field["options"] = [
                {"value": code, "label": ui[key]} for code, key in selects[name]
            ]
        fields_out.append(field)
    return {"id": form_id, "method": "post", "action": action, "fields": fields_out}


def assessment_detail(request, slug, number):
    """One assessment at this centre, in full, and only this centre's (GRD-002,
    GRD-015's line: the same number at another centre is another record)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.assessment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _assessment_here(center, number)
    if record is None:
        return _not_found(request, center, ui, "grading_assessment")
    scope = _grade_scope(request.user, center)
    if scope is not None and record.cohort_id not in scope:
        return _not_found(request, center, ui, "grading_assessment")
    return page(
        request,
        "grading_assessment",
        {
            "heading": ui["grading_assessment_heading"].format(record.pk),
            "number": str(record.pk),
            "rows": assessment_rows(ui, center, record),
            "audit": _audit_rows(ui, center, assessment=record),
            "audit_none": ui["grading_audit_none"],
            "back": {
                "href": f"/o/{center.slug}/center/grading/assessments/",
                "kind": "assessments",
                "title": ui["grading_assessments_heading"],
            },
        },
        center=center,
    )


def results_page(request, slug):
    """This centre's raw results, each with what it is a result of (GRD-003).

    The instructor sees the results of their assigned cohorts only; every
    other permitted role sees this centre's own. The scores are shown to
    fixed places, and a pending record is shown as pending, never as a zero.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.result.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    scope = _grade_scope(request.user, center)
    results = Result.objects.filter(organization=center)
    if scope is not None:
        results = results.filter(assessment__cohort_id__in=scope)
    rows = []
    for record in results.order_by("assessment", "pk"):
        rows.append(
            {
                "number": str(record.pk),
                "assessment": f"{record.assessment.pk} ({record.assessment.title})",
                "assessment_number": str(record.assessment.pk),
                "student": student_shown(record.student),
                "score": score_text(record.score) or ui["grading_state_pending"],
                "max": score_text(record.assessment.max_score),
                "at": moment_text(center, record.updated_at or record.recorded_at),
            }
        )
    return page(
        request,
        "grading_results",
        {
            "heading": ui["grading_results_heading"],
            "hint": ui["grading_results_hint"],
            "rows": rows,
            "none": ui["grading_results_none"],
            "can_record": role_has_permission(member.role, "grading.result.record"),
        },
        center=center,
    )


def results_manage(request, slug):
    """The page scores are recorded and corrected on (GRD-003/GRD-006).

    One page takes both actions - recording a first score for one enrollment
    of an assessment, and changing one already recorded score - so a person
    grades a cohort down without chasing two addresses (the manage-page
    pattern every other area uses). An empty score is allowed and stays
    pending, never a zero; a correction needs a score and audits the one it
    replaces. The instructor may act here (GRD-003) but only on their
    assigned cohorts, which the service answers with its own refusal.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_record = role_has_permission(member.role, "grading.result.record")
    action_at = f"/o/{center.slug}/center/grading/results/manage/"
    selects = {
        "result_action": tuple(
            (value, RESULT_ACTION_UI[value]) for value in RESULT_ACTIONS
        ),
    }
    if request.method in READ_METHODS:
        if not can_record:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "grading_results_manage",
            {
                "fields": rows_from_spec(ui, sent, {}, RESULT_FIELDS, RESULT_FIELD_UI),
                "form": form_at(
                    "grading_results",
                    action_at, ui, sent, RESULT_FIELDS, RESULT_FIELD_UI, selects
                ),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_record:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {name: request.POST.get(name, "") for name in RESULT_FIELDS}
    problems = {}
    action = sent["result_action"].strip()
    note = ""
    kind = ""
    unchanged = False
    if action == "" or action not in RESULT_ACTIONS:
        if action != "":
            problems["result_action"] = ui["field_invalid"].format(action)
        saved, field_problems, codes = None, [], []
    else:
        bad_input = False
        score_value = None
        if action == "record":
            score_value, score_ok = parse_optional_decimal(sent["result_score"])
            if not score_ok:
                bad_input = True
                problems["result_score"] = ui["field_invalid"].format(sent["result_score"])
        else:
            score_value, score_ok = parse_decimal(sent["result_score"])
            if not score_ok:
                bad_input = True
                if sent["result_score"]:
                    problems["result_score"] = ui["field_invalid"].format(
                        sent["result_score"]
                    )
                else:
                    problems["result_score"] = ui["field_required"]
        if len(sent["result_reason"]) > RESULT_REASON_MAX:
            problems["result_reason"] = ui["field_invalid"].format(
                sent["result_reason"]
            )
            bad_input = True
        if bad_input:
            saved, field_problems, codes = None, [], []
        else:
            try:
                if action == "record":
                    saved, field_problems, codes = record_result(
                        request.user,
                        center,
                        sent["result_assessment"],
                        sent["result_enrollment"],
                        score=score_value,
                        reason=sent["result_reason"],
                    )
                elif action == "update":
                    saved, field_problems, codes = update_result(
                        request.user,
                        center,
                        sent["result_number"],
                        score_value,
                        reason=sent["result_reason"],
                    )
                else:
                    saved, field_problems, codes = None, [], []
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
        for code in codes:
            if code in UNCHANGED_UI:
                note, kind = ui[UNCHANGED_UI[code]], "unchanged"
                unchanged = True
                break
        if not unchanged:
            _field_problem(ui, problems, codes, RESULT_PROBLEM_UI)
            _model_problems(ui, problems, field_problems, RESULT_MODEL_FIELDS)
        if not unchanged:
            if saved is not None:
                note, kind = ui["grading_saved"], "saved"
            elif problems:
                note, kind = ui["grading_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "grading_results_manage",
        {
            "fields": rows_from_spec(ui, sent, problems, RESULT_FIELDS, RESULT_FIELD_UI),
            "form": form_at(
                "grading_results",
                action_at, ui, sent, RESULT_FIELDS, RESULT_FIELD_UI, selects
            ),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def assessment_results_page(request, slug, number):
    """One assessment's results sheet: every open enrollment of its cohort,
    graded or honestly marked missing and pending (GRD-005's indication).

    The population is the open enrollments the centre could grade now - the
    same rule the service records under - so a page never claims a score
    nothing could make, and a missing student is never read as a zero.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.result.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _assessment_here(center, number)
    if record is None:
        return _not_found(request, center, ui, "grading_assessment")
    scope = _grade_scope(request.user, center)
    if scope is not None and record.cohort_id not in scope:
        return _not_found(request, center, ui, "grading_assessment")
    return page(
        request,
        "grading_assessment_results",
        {
            "heading": ui["grading_assessment_results_heading"].format(record.pk),
            "number": str(record.pk),
            "rows": assessment_results_rows(ui, record),
            "none": ui["grading_assessment_results_none"],
            "back": {
                "href": f"/o/{center.slug}/center/grading/assessments/{record.pk}/",
                "kind": "assessment",
                "title": ui["grading_assessment_heading"].format(record.pk),
            },
        },
        center=center,
    )


def assessment_results_rows(ui, assessment):
    """One assessment's sheet rows: its cohort's open enrollments and states."""
    scored = {
        enrollment_id: score
        for enrollment_id, score in Result.objects.filter(
            assessment=assessment
        ).values_list("enrollment_id", "score")
    }
    rows = []
    for enrollment in open_enrollments(assessment.cohort):
        if enrollment.pk in scored:
            if scored[enrollment.pk] is None:
                state = ui["grading_state_pending"]
                score = ui["not_recorded"]
            else:
                state = ui["grading_state_recorded"]
                score = score_text(scored[enrollment.pk])
        else:
            state = ui["grading_state_missing"]
            score = ui["not_recorded"]
        rows.append(
            {
                "number": str(enrollment.pk),
                "student": student_shown(enrollment.student),
                "state": state,
                "score": score,
            }
        )
    return rows


def student_result_rows(ui, center, student, scope=None):
    """This centre's own results for one student, oldest recorded first.

    A student profile's grading section (the read-only mirror of GRD-003's
    rows): one line per result, the score to fixed places or pending, and
    nothing about another centre. The instructor's rows are their assigned
    cohorts' only - the same server-side scope every other grading page
    answers with, so a profile can never name a result the instructor may
    not see (SEC-001's line).
    """
    results = Result.objects.filter(organization=center, student=student)
    if scope is not None:
        results = results.filter(assessment__cohort_id__in=scope)
    rows = []
    for record in results.order_by("recorded_at", "pk"):
        rows.append(
            {
                "number": str(record.pk),
                "assessment": f"{record.assessment.pk} ({record.assessment.title})",
                "assessment_number": str(record.assessment.pk),
                "score": score_text(record.score) or ui["grading_state_pending"],
                "max": score_text(record.assessment.max_score),
                "at": moment_text(center, record.updated_at or record.recorded_at),
            }
        )
    return rows


def assessment_results_csv(request, slug, number):
    """The one assessment's results sheet as a file: same rows, same rules."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.result.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _assessment_here(center, number)
    if record is None:
        return _not_found(request, center, ui, "grading_assessment")
    scope = _grade_scope(request.user, center)
    if scope is not None and record.cohort_id not in scope:
        return _not_found(request, center, ui, "grading_assessment")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="assessment-{}-{}-results.csv"'.format(center.slug, record.pk)
    )
    write = csv_writer(response)
    write(
        [
            ui["grading_csv_center"],
            ui["grading_csv_assessment"],
            ui["grading_sheet_row_enrollment"],
            ui["grading_sheet_row_student"],
            ui["grading_sheet_row_state"],
            ui["grading_sheet_row_score"],
        ]
    )
    write(
        [
            center.slug,
            str(record.pk),
            "",
            "",
            "",
            "",
        ]
    )
    for row in assessment_results_rows(ui, record):
        write(
            [
                center.slug,
                str(record.pk),
                row["number"],
                row["student"],
                row["state"],
                row["score"],
            ]
        )
    return response


def reports_page(request, slug):
    """This centre's grading report: its assessments with their own counts,
    grouped per cohort, raw figures only (REP-003/REP-005/REP-006/REP-007).

    No final grade, no pass mark and no weighted figure is computed anywhere
    (docs/STATUS.md's deferred list): the counts say what is recorded, what
    is pending and what is missing, over the open enrollments the centre
    could grade now. The instructor's rows are their assigned cohorts' only.
    """
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.assessment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    scope = _grade_scope(request.user, center)
    groups = []
    for entry in grading_rows(center, scope=scope):
        assessment = entry["assessment"]
        counts = entry["counts"]
        cohort_value = cohort_shown(assessment.cohort)
        if groups and groups[-1]["pk"] == assessment.cohort.pk:
            rows = groups[-1]["rows"]
        else:
            rows = []
            groups.append({"pk": str(assessment.cohort.pk), "cohort": cohort_value, "rows": rows})
        rows.append(
            {
                "number": str(assessment.pk),
                "title": assessment.title,
                "max": score_text(assessment.max_score),
                "recorded": str(counts["recorded"]),
                "pending": str(counts["pending"]),
                "missing": str(counts["missing"]),
            }
        )
    return page(
        request,
        "grading_reports",
        {
            "heading": ui["grading_reports_heading"],
            "hint": ui["grading_reports_hint"],
            "groups": groups,
            "none": ui["grading_reports_none"],
        },
        center=center,
    )


def cohort_report_csv(request, slug):
    """The centre's grading report as a file: the same rows, the same rules."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "grading.assessment.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    scope = _grade_scope(request.user, center)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="grading-report-{}.csv"'.format(
        center.slug
    )
    write = csv_writer(response)
    write(
        [
            ui["grading_csv_center"],
            ui["grading_row_cohort"],
            ui["grading_row_number"],
            ui["grading_row_title"],
            ui["grading_row_max"],
            ui["grading_row_recorded"],
            ui["grading_row_pending"],
            ui["grading_row_missing"],
        ]
    )
    for entry in grading_rows(center, scope=scope):
        assessment = entry["assessment"]
        counts = entry["counts"]
        write(
            [
                center.slug,
                cohort_shown(assessment.cohort),
                str(assessment.pk),
                assessment.title,
                score_text(assessment.max_score),
                str(counts["recorded"]),
                str(counts["pending"]),
                str(counts["missing"]),
            ]
        )
    return response
