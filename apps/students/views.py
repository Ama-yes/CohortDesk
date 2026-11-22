"""The student and application pages: where a centre's records are answered.

Same error model as every other tenant address (apps/organizations/views.py):
- unknown centre / not signed in / no active membership here -> the project
  problem page at 404: the address never reveals whether an unseen centre
  exists (SEC-003/004),
- a member here but the role is not permitted for this page -> this centre's
  denied page at 403,
- a non-read method at a read-only address -> the problem page at 405,
- a student number this centre has no record for, asked at this centre -> this
  centre's own not-found page at 404. The same number at another centre names a
  different person (STU-002 / apps/students/test_access.py), so this centre
  answers only about its own records and never says whether the number exists
  anywhere else.

Data rule: every row is built here from this centre's own queryset (RULE 6.1).
The lookup rules themselves (a number's normal form) are the service layer's,
so a page and a service never disagree about what an identifier names (SEC-004).

Page and form share one definition: STUDENTS_FORM_FIELDS and
APPLICATIONS_FORM_FIELDS drive both the rows a manage page shows and the form
it collects, so a person is never asked for a value nowhere is shown, or shown
a field never asked for.
"""

from datetime import datetime

from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied

from apps.pages import center_ui, page
from apps.organizations.views import READ_METHODS, here
from apps.organizations.permissions import role_has_permission

from .models import Application, Student
from .services import (
    EDITABLE_STUDENT_FIELDS,
    _student_here,
    archive_student,
    convert_application,
    create_student,
    edit_student,
    record_application,
    set_application_status,
    set_student_status,
)
from .statuses import (
    STATUS_APPLICANT,
    APPLICATION_STATUSES,
    STUDENT_STATUSES,
)

# The actions a manage page offers, and the glossary key that names each. A
# page can only name an action its glossary has words for (the members page
# pattern): nothing is ever shown untranslated.
STUDENTS_ACTIONS = ("create", "set_status", "edit", "archive")
STUDENTS_ACTION_UI = {
    "create": "students_action_create",
    "set_status": "students_action_set_status",
    "edit": "students_action_edit",
    "archive": "students_action_archive",
}
APPLICATIONS_ACTIONS = ("record", "set_status", "convert")
APPLICATIONS_ACTION_UI = {
    "record": "applications_action_create",
    "set_status": "applications_action_set_status",
    "convert": "applications_action_convert",
}

# One ordered definition drives both the page rows and the form fields:
# (field id, glossary key, allowed values as (value, glossary key) pairs, or
# None for free text). Select fields are like the members page: the model's
# own vocabulary is the options list, so nobody types a status a service would
# only guess at.
STUDENTS_FORM_FIELDS = (
    (
        "students_action",
        "students_action",
        tuple(
            (action, STUDENTS_ACTION_UI[action]) for action in STUDENTS_ACTIONS
        ),
    ),
    ("student_number", "field_student_number", None),
    (
        "students_field",
        "students_field",
        tuple((name, "field_" + name) for name in EDITABLE_STUDENT_FIELDS),
    ),
    ("students_value", "students_value", None),
    ("name", "field_name", None),
    ("contact", "field_contact", None),
    (
        "status",
        "field_status",
        tuple((code, "status_" + code) for code in STUDENT_STATUSES),
    ),
    ("note", "field_note", None),
)
APPLICATIONS_FORM_FIELDS = (
    (
        "applications_action",
        "applications_action",
        tuple(
            (action, APPLICATIONS_ACTION_UI[action])
            for action in APPLICATIONS_ACTIONS
        ),
    ),
    ("applications_number", "applications_number", None),
    ("name", "field_name", None),
    ("contact", "field_contact", None),
    ("source", "field_source", None),
    ("desired_course", "field_desired_course", None),
    ("student_number", "field_student_number", None),
    (
        "status",
        "field_status",
        tuple(
            (code, "application_status_" + code)
            for code in APPLICATION_STATUSES
        ),
    ),
    ("note", "field_note", None),
)

# What a service says went wrong, and the row the page marks for it, in this
# centre's words (the members page pattern).
STUDENTS_PROBLEM_UI = {
    "entry": ("status", "entry_status_only"),
    "duplicate": ("name", "possible_duplicate"),
    "number_used": ("student_number", "number_used"),
    "not_found": ("student_number", "not_found_record"),
    "field_not_editable": ("students_field", "field_not_editable"),
    "transition": ("status", "transition_not_allowed"),
}
APPLICATIONS_PROBLEM_UI = {
    "not_found": ("applications_number", "not_found_record"),
    "transition": ("status", "transition_not_allowed"),
    "not_accepted": ("applications_number", "not_accepted"),
    "needs_number": ("student_number", "needs_number"),
    "duplicate": ("name", "possible_duplicate"),
    "number_used": ("student_number", "number_used"),
}
# Already in the state asked for: not a rejection, nothing changed (STU-008:
# an archived record stays findable; ADM-004: converting twice changes the
# record zero times).
STUDENTS_UNCHANGED_UI = {"already_archived": "already_archived"}
APPLICATIONS_UNCHANGED_UI = {"converted_already": "converted_already"}


def problem_field_for(action, name):
    """Where the page marks a validator's rejection: the row the person typed.

    For the edit action the value a person types goes in students_value (the
    students_field row selects which part of the record it changes), so a
    rejection naming a profile field belongs to the value row, not to a row
    of its own. Every other action names its fields as they are.
    """
    if action == "edit" and name in EDITABLE_STUDENT_FIELDS:
        return "students_value"
    return name


# The parts of a record a profile page shows, and the key that titles each
# (ADM-001: a complete profile; an empty part says it was not recorded).
PROFILE_UI = {
    "student_number": "field_student_number",
    "name": "field_name",
    "contact": "field_contact",
    "birth_date": "field_birth_date",
    "address": "field_address",
    "status": "field_status",
    "note": "field_note",
    "registered_at": "field_registered_at",
    "status_changed_at": "field_status_changed_at",
    "archived_at": "field_archived_at",
}


def text_value(center, value):
    """A recorded moment, shown in this centre's own timezone (ORG-002).

    The zone was validated when the centre was configured (apps/organizations
    models clean_fields checks it with ZoneInfo), and a bad value can only
    come from an out-of-band edit: UTC then answers rather than an error
    hiding every date on the page.
    """
    try:
        zone = ZoneInfo(center.display_timezone)
    except LookupError:
        zone = ZoneInfo("UTC")
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def student_rows(center, ui):
    """This centre's student records, archived ones included (STU-008)."""
    return [
        {
            "number": record.student_number,
            "name": record.name,
            "status": ui["status_" + record.status],
        }
        for record in Student.objects.filter(organization=center).order_by(
            "student_number"
        )
    ]


def profile_rows(center, ui, record):
    """One record's own parts, as the profile page shows them."""
    rows = []
    for field, key in PROFILE_UI.items():
        value = getattr(record, field)
        if value is None or value == "":
            shown = ui["not_recorded"]
        elif field == "birth_date":
            shown = value.strftime("%Y-%m-%d")
        elif field in ("registered_at", "status_changed_at", "archived_at"):
            shown = text_value(center, value)
        elif field == "status":
            shown = ui["status_" + value]
        else:
            shown = value
        rows.append({"id": field, "title": ui[key], "value": shown})
    return rows


def application_links(ui, record):
    """The application at this centre that made this record (ADM-004 trail)."""
    return [
        {
            "id": application.pk,
            "name": application.name,
            "course": application.desired_course or ui["not_recorded"],
            "status": ui["application_status_" + application.status],
        }
        for application in record.converted_applications.all()
    ]


def student_fields(ui, sent, problems):
    """The change-student page's rows: what was sent, what was not accepted."""
    return [
        {
            "id": name,
            "title": ui[key],
            "value": sent.get(name, ""),
            "problem": problems.get(name, ""),
        }
        for name, key, _options in STUDENTS_FORM_FIELDS
    ]


def student_form_at(action, ui, sent):
    """The change-student form: the same fields the page rows show."""
    return {
        "id": action.rstrip("/"),
        "method": "post",
        "action": action,
        "fields": form_fields(ui, sent, STUDENTS_FORM_FIELDS),
    }


def form_fields(ui, sent, spec):
    """Form fields built from a page's field spec (one source for both)."""
    fields = []
    for name, key, options in spec:
        field = {"id": name, "title": ui[key]}
        if options is None:
            field["type"] = "text"
            field["value"] = sent.get(name, "")
        else:
            field["type"] = "select"
            field["current"] = sent.get(name, "")
            field["options"] = [
                {"value": value, "label": ui[label]} for value, label in options
            ]
        fields.append(field)
    return fields


def application_rows(center, ui):
    """Every application this centre has recorded, archived ones included."""
    return [
        {
            "number": record.pk,
            "name": record.name,
            "course": record.desired_course or ui["not_recorded"],
            "status": ui["application_status_" + record.status],
        }
        for record in Application.objects.filter(organization=center).order_by(
            "pk"
        )
    ]


def application_fields(ui, sent, problems):
    """The change-application page's rows, and what was not accepted."""
    return [
        {
            "id": name,
            "title": ui[key],
            "value": sent.get(name, ""),
            "problem": problems.get(name, ""),
        }
        for name, key, _options in APPLICATIONS_FORM_FIELDS
    ]


def application_form_at(action, ui, sent):
    """The change-application form: the same fields the page rows show."""
    return {
        "id": action.rstrip("/"),
        "method": "post",
        "action": action,
        "fields": form_fields(ui, sent, APPLICATIONS_FORM_FIELDS),
    }


def students_page(request, slug):
    """This centre's student records, whatever their status (STU-001)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "students.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request,
        "students",
        {"students": student_rows(center, ui)},
        center=center,
    )


def student_profile(request, slug, number):
    """One student record at this centre, and only this centre's (STU-002)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "students.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _student_here(center, number)
    if record is None:
        return page(
            request,
            "not_found",
            {
                "message": ui["not_found_record"],
                "back": {
                    "href": f"/o/{center.slug}/center/students/",
                    "kind": "students",
                    "title": ui["students_heading"],
                },
            },
            center=center,
            status=404,
        )
    # STU-006's attendance history (ATT-005): shown only to those with access
    # to attendance, over this centre's sessions of this student.
    from apps.academics.models import Attendance, Session
    from apps.academics.reporting import attendance_totals
    from apps.academics.views import (
        attendance_rows,
        audit_rows,
        attendance_total_rows,
    )

    can_view = role_has_permission(member.role, "attendance.view")
    can_correct = role_has_permission(member.role, "attendance.correct")
    # Phase 3G's grading results (the read-only mirror of GRD-003): shown
    # only to those with grading.result.view, narrowed server-side to the
    # instructor's assigned cohorts like every other grading page.
    from apps.grading.views import _grade_scope, student_result_rows

    can_results = role_has_permission(member.role, "grading.result.view")
    grading_results = []
    if can_results:
        grading_results = student_result_rows(
            ui,
            center,
            record,
            scope=_grade_scope(request.user, center),
        )
    attendances = Attendance.objects.filter(
        student=record, session__organization=center
    )
    session_ids = list(
        attendances.values_list("session_id", flat=True)
    )
    sessions = Session.objects.filter(
        organization=center, pk__in=session_ids
    )
    return page(
        request,
        "student",
        {
            "number": record.student_number,
            "profile": profile_rows(center, ui, record),
            "applications": application_links(ui, record),
            "attendance": (
                attendance_rows(center, ui, student=record)
                if can_view
                else []
            ),
            "attendance_totals": (
                attendance_total_rows(
                    ui,
                    attendance_totals(attendances, sessions),
                )
                if can_view
                else []
            ),
            "attendance_corrections": (
                audit_rows(center, ui, student=record)
                if can_correct
                else []
            ),
            "grading_results": grading_results,
        },
        center=center,
    )


def students_manage(request, slug):
    """The page records are made, changed, statused, or archived on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(
        member.role, "students.manage"
    ) or role_has_permission(member.role, "students.archive")
    action_at = f"/o/{center.slug}/center/students/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "students_manage",
            {
                "fields": student_fields(ui, sent, {}),
                "form": student_form_at(action_at, ui, sent),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in STUDENTS_FORM_FIELDS
    }
    problems = {}
    action = sent["students_action"]
    if action == "":
        problems["students_action"] = ui["field_required"]
        note, kind = ui["students_rejected"], "rejected"
    elif action not in STUDENTS_ACTIONS:
        problems["students_action"] = ui["field_invalid"].format(action)
        note, kind = ui["students_rejected"], "rejected"
    else:
        try:
            if action == "create":
                saved, field_problems, codes = create_student(
                    request.user,
                    center,
                    sent["student_number"],
                    sent["name"],
                    sent["contact"],
                    sent["status"] or STATUS_APPLICANT,
                    sent["note"],
                )
            elif action == "set_status":
                saved, field_problems, codes = set_student_status(
                    request.user, center, sent["student_number"], sent["status"]
                )
            elif action == "archive":
                saved, field_problems, codes = archive_student(
                    request.user, center, sent["student_number"]
                )
            else:
                # The edit action: the page parses the one typed value into
                # what the model expects (a real date for birth_date, empty
                # clearing it - the field allows null), and an unparseable
                # date is the page's own rejection, never a service guess.
                field = sent["students_field"]
                value = sent["students_value"]
                bad_date = False
                if field == "birth_date" and value:
                    try:
                        value = datetime.strptime(value, "%Y-%m-%d").date()
                    except ValueError:
                        bad_date = True
                        problems["students_value"] = ui[
                            "field_invalid"
                        ].format(sent["students_value"])
                if field == "birth_date" and not value:
                    value = None
                if bad_date:
                    saved, field_problems, codes = None, [], []
                else:
                    saved, field_problems, codes = edit_student(
                        request.user,
                        center,
                        sent["student_number"],
                        field,
                        value,
                    )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            problems[problem_field_for(action, name)] = ui[
                "field_invalid"
            ].format(message)
        unchanged = False
        for code in codes:
            if code in STUDENTS_UNCHANGED_UI:
                note, kind = ui[STUDENTS_UNCHANGED_UI[code]], "unchanged"
                unchanged = True
            else:
                field_id, key = STUDENTS_PROBLEM_UI[code]
                problems[field_id] = ui[key]
        if not unchanged:
            if saved:
                note, kind = ui["students_saved"], "saved"
            elif problems:
                note, kind = ui["students_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "students_manage",
        {
            "fields": student_fields(ui, sent, problems),
            "form": student_form_at(action_at, ui, sent),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def applications_page(request, slug):
    """This centre's applications, with what each one asked for (ADM-001)."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "students.application.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(
        request,
        "applications",
        {
            "applications": application_rows(center, ui),
            "notice": ui["desired_course_is_text"],
        },
        center=center,
    )


def applications_manage(request, slug):
    """The page applications are recorded, reviewed, and accepted on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(
        member.role, "students.manage"
    ) or role_has_permission(member.role, "students.application.accept")
    action_at = f"/o/{center.slug}/center/applications/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "applications_manage",
            {
                "fields": application_fields(ui, sent, {}),
                "form": application_form_at(action_at, ui, sent),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {
        name: request.POST.get(name, "")
        for name, _key, _options in APPLICATIONS_FORM_FIELDS
    }
    problems = {}
    action = sent["applications_action"]
    if action == "":
        problems["applications_action"] = ui["field_required"]
        note, kind = ui["applications_rejected"], "rejected"
    elif action not in APPLICATIONS_ACTIONS:
        problems["applications_action"] = ui[
            "field_invalid"
        ].format(action)
        note, kind = ui["applications_rejected"], "rejected"
    else:
        try:
            if action == "record":
                saved, field_problems, codes = record_application(
                    request.user,
                    center,
                    sent["name"],
                    sent["desired_course"],
                    sent["contact"],
                    sent["source"],
                    sent["note"],
                    sent["student_number"],
                )
            elif action == "set_status":
                saved, field_problems, codes = set_application_status(
                    request.user,
                    center,
                    sent["applications_number"],
                    sent["status"],
                )
            else:
                saved, field_problems, codes = convert_application(
                    request.user, center, sent["applications_number"]
                )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            problems[name] = ui["field_invalid"].format(message)
        unchanged = False
        for code in codes:
            if code in APPLICATIONS_UNCHANGED_UI:
                note, kind = ui[APPLICATIONS_UNCHANGED_UI[code]], "unchanged"
                unchanged = True
            else:
                field_id, key = APPLICATIONS_PROBLEM_UI[code]
                problems[field_id] = ui[key]
        if not unchanged:
            if saved:
                note, kind = ui["applications_saved"], "saved"
            elif problems:
                note, kind = ui["applications_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "applications_manage",
        {
            "fields": application_fields(ui, sent, problems),
            "form": application_form_at(action_at, ui, sent),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )
