"""The staff and instructor pages at a tenant address.

Same error model as every other tenant address (apps/organizations/views.py):
- unknown centre / not signed in / no active membership here -> the project
  problem page at 404 (SEC-003/004),
- a member here but the role is not permitted for this page -> this centre's
  denied page at 403,
- a non-read method at a read-only address -> the project problem page at 405,
- a staff number this centre has no record for -> this centre's own not-found
  page at 404, which says nothing about any other centre.

Data rule: every row is built here from this centre's own queryset (RULE 6.1).
Every identifier typed on a page is resolved by the service layer inside this
centre, so a tampered staff/course/cohort number answers as not found here and
never reaches another centre's record.

Page and form share one definition (the students/academics pattern): the
STAFF_FORM_FIELDS spec drives both the rows a manage page shows and the form it
collects, so a person is never asked for a value nowhere is shown.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.academics.reporting import attendance_totals, session_report_rows
from apps.academics.models import Attendance
from apps.academics.views import attendance_total_rows, moment_text
from apps.organizations.permissions import role_has_permission
from apps.organizations.views import READ_METHODS, here
from apps.accounts.models import CohortUser
from apps.pages import center_ui, page

from .models import Staff
from .services import (
    EDITABLE_STAFF_FIELDS,
    _staff_here,
    assign_staff_to_cohort,
    assign_staff_to_course,
    create_staff,
    edit_staff,
    link_staff_account,
    remove_staff_from_cohort,
    remove_staff_from_course,
    set_staff_status,
    unlink_staff_account,
)
from .statuses import STAFF_STATUSES, STATUS_ACTIVE, STATUS_ARCHIVED

STAFF_ACTIONS = (
    "create",
    "edit",
    "set_status",
    "assign_course",
    "remove_course",
    "assign_cohort",
    "remove_cohort",
    "link",
    "unlink",
)
STAFF_ACTION_UI = {
    "create": "staff_action_create",
    "edit": "staff_action_edit",
    "set_status": "staff_action_set_status",
    "assign_course": "staff_action_assign_course",
    "remove_course": "staff_action_remove_course",
    "assign_cohort": "staff_action_assign_cohort",
    "remove_cohort": "staff_action_remove_cohort",
    "link": "staff_action_link",
    "unlink": "staff_action_unlink",
}

# One ordered definition drives both the page rows and the form fields.
STAFF_FORM_FIELDS = (
    (
        "staff_action",
        "staff_action",
        tuple((action, STAFF_ACTION_UI[action]) for action in STAFF_ACTIONS),
    ),
    ("staff_number", "staff_number", None),
    ("staff_account", "staff_account", None),
    (
        "staff_field",
        "staff_field",
        tuple((name, "field_staff_" + name) for name in EDITABLE_STAFF_FIELDS),
    ),
    ("staff_value", "staff_value", None),
    ("name", "field_staff_name", None),
    ("email", "field_staff_email", None),
    ("phone", "field_staff_phone", None),
    ("job_title", "field_staff_job_title", None),
    ("specializations", "field_staff_specializations", None),
    ("availability", "field_staff_availability", None),
    ("qualifications", "field_staff_qualifications", None),
    (
        "status",
        "field_staff_status",
        tuple((code, "staff_status_" + code) for code in STAFF_STATUSES),
    ),
    ("target_number", "field_assignment_target", None),
)

# What a service says went wrong, the row the page marks, and the words used.
STAFF_PROBLEM_UI = {
    "not_found": ("staff_number", "staff_not_found"),
    "staff_not_found": ("staff_number", "staff_not_found"),
    "course_not_found": ("target_number", "course_not_found"),
    "cohort_not_found": ("target_number", "cohort_not_found"),
    "field_not_editable": ("staff_field", "field_not_editable"),
    "transition": ("status", "transition_not_allowed"),
    "entry": ("status", "entry_status_not_allowed"),
    "staff_not_active": ("staff_number", "staff_not_active"),
    "already_assigned": ("target_number", "already_assigned"),
    "not_assigned": ("target_number", "not_assigned"),
    "number_used": ("staff_number", "staff_number_used"),
    "not_linked": ("staff_number", "staff_not_linked"),
    "account_not_found": ("staff_account", "staff_account_not_here"),
    "already_linked": ("staff_account", "staff_already_linked"),
    "account_taken": ("staff_account", "staff_taken"),
    "staff_not_linkable": ("staff_account", "staff_not_linkable"),
}

STAFF_PROFILE_UI = (
    ("staff_number", "staff_number"),
    ("name", "field_staff_name"),
    ("email", "field_staff_email"),
    ("phone", "field_staff_phone"),
    ("job_title", "field_staff_job_title"),
    ("specializations", "field_staff_specializations"),
    ("employment_status", "field_staff_status"),
    ("availability", "field_staff_availability"),
    ("qualifications", "field_staff_qualifications"),
)


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


def form_at(action, ui, sent, spec):
    """The manage page's form: the same fields the rows show."""
    return {
        "id": action.rstrip("/"),
        "method": "post",
        "action": action,
        "fields": form_fields(ui, sent, spec),
    }


def manage_rows(ui, sent, problems, spec):
    """The page rows for a spec (sent values and rejections)."""
    return [
        {
            "id": name,
            "title": ui[key],
            "value": sent.get(name, ""),
            "problem": problems.get(name, ""),
        }
        for name, key, _options in spec
    ]


def search_form(center, ui, query):
    """The staff list's search form, at this centre's own address."""
    action = f"/o/{center.slug}/center/staff/"
    return {
        "id": "staff_search",
        "method": "get",
        "action": action,
        "submit": ui["staff_search"],
        "fields": [
            {
                "id": "q",
                "title": ui["staff_search"],
                "type": "text",
                "value": query,
            }
        ],
    }


def staff_rows(center, ui, query=""):
    """This centre's staff, optionally narrowed by STAFF-001's search."""
    queryset = Staff.objects.filter(organization=center)
    text = str(query).strip()
    if text:
        queryset = queryset.filter(
            Q(staff_number__icontains=text)
            | Q(name__icontains=text)
            | Q(email__icontains=text)
            | Q(phone__icontains=text)
            | Q(job_title__icontains=text)
            | Q(specializations__icontains=text)
        )
    return [
        {
            "number": record.staff_number,
            "name": record.name,
            "job_title": record.job_title or ui["not_recorded"],
            "status": ui["staff_status_" + record.employment_status],
        }
        for record in queryset.order_by("staff_number")
    ]


def staff_profile(ui, record):
    """One staff record's own parts, as the staff page shows them."""
    rows = []
    for field, key in STAFF_PROFILE_UI:
        if field == "employment_status":
            shown = ui["staff_status_" + record.employment_status]
        else:
            shown = getattr(record, field) or ui["not_recorded"]
        rows.append({"id": field, "title": ui[key], "value": shown})
    return rows


def assigned_courses(record):
    """The courses this instructor teaches (STAFF-003/STAFF-004)."""
    return [
        {
            "number": assignment.course.pk,
            "title": assignment.course.title,
        }
        for assignment in record.course_assignments.select_related(
            "course"
        ).order_by("course__title")
    ]


def assigned_cohorts(record):
    """The cohorts this instructor teaches (STAFF-003/STAFF-004)."""
    return [
        {
            "number": assignment.cohort.pk,
            "name": assignment.cohort.name,
            "course": assignment.cohort.course.title,
        }
        for assignment in record.cohort_assignments.select_related(
            "cohort", "cohort__course"
        ).order_by("cohort__name")
    ]


def staff_session_rows(center, ui, record):
    """The sessions of the cohorts this record teaches (STAFF-004/ATT-004).

    The server-side scope is this record's own cohort assignments, never a
    number typed on the page: an instructor sees the sessions of the cohorts
    they teach and nothing else, whatever the address claims (RULE 6.1).
    """
    sessions = session_report_rows(
        center, cohorts=staff_cohort_ids(center, record)
    )
    return [
        {
            "number": session.pk,
            "cohort": session.cohort.name,
            "cohort_number": session.cohort.pk,
            "course": session.cohort.course.title,
            "start": moment_text(center, session.start_at),
            "end": moment_text(center, session.end_at),
            "note": session.note,
            "present": str(session.present),
            "absent": str(session.absent),
            "late": str(session.late),
            "excused": str(session.excused),
            "records": str(session.attendance_records),
        }
        for session in sessions
    ]


def staff_cohort_ids(center, record):
    """The ids of this centre's cohorts this instructor's record teaches."""
    return list(
        record.cohort_assignments.select_related("cohort")
        .filter(cohort__organization=center)
        .values_list("cohort_id", flat=True)
    )


def staff_sessions_total(center, ui, record):
    """The attendance figures over exactly the session rows above (ATT-007)."""
    sessions = session_report_rows(
        center, cohorts=staff_cohort_ids(center, record)
    )
    return attendance_total_rows(
        ui,
        attendance_totals(
            Attendance.objects.filter(session__in=sessions), sessions
        ),
    )


def staff_not_found(request, center, ui):
    """This centre's own not-found page for a staff number."""
    return page(
        request,
        "not_found",
        {
            "message": ui["staff_not_found"],
            "back": {
                "href": f"/o/{center.slug}/center/staff/",
                "kind": "staff",
                "title": ui["staff_heading"],
            },
        },
        center=center,
        status=404,
    )


def staff_self_view(request, slug, number=""):
    """An instructor's own record at this centre, or the prompt to link."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "staff.self.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = Staff.objects.filter(organization=center, user=request.user).first()
    if record is None:
        return page(
            request,
            "staff_self",
            {
                "message": ui["staff_self_none"],
                "number": "",
                "profile": [],
                "archived": "",
                "courses": [],
                "cohorts": [],
                "sessions": [],
                "sessions_total": [],
            },
            center=center,
        )
    if record.employment_status == STATUS_ARCHIVED:
        return page(
            request,
            "staff_self",
            {
                "message": "",
                "number": "",
                "record_line": "",
                "profile": [],
                "archived": ui["staff_self_archived"],
                "courses": [],
                "cohorts": [],
                "sessions": [],
                "sessions_total": [],
            },
            center=center,
        )
    return page(
        request,
        "staff_self",
        {
            "message": "",
            "number": record.staff_number,
            "record_line": ui["staff_self_record"].format(
                number=record.staff_number, name=record.name
            ),
            "profile": staff_profile(ui, record),
            "archived": "",
            "courses": assigned_courses(record),
            "cohorts": assigned_cohorts(record),
            # STAFF-004's sessions: the sessions of the cohorts this
            # instructor's record teaches, shown only to those with access to
            # attendance (the staff pages themselves stay open to all who
            # view staff).
            "sessions": (
                staff_session_rows(center, ui, record)
                if role_has_permission(member.role, "attendance.view")
                else []
            ),
            "sessions_total": (
                staff_sessions_total(center, ui, record)
                if role_has_permission(member.role, "attendance.view")
                else []
            ),
        },
        center=center,
    )


def staff_page(request, slug):
    """This centre's staff and instructors, with STAFF-001's search."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "staff.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    query = request.GET.get("q", "")
    return page(
        request,
        "staff",
        {
            "staff": staff_rows(center, ui, query),
            "query": str(query).strip(),
            "form": search_form(center, ui, str(query).strip()),
        },
        center=center,
    )


def staff_member(request, slug, number):
    """One staff record of this centre, with assigned courses and cohorts."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "staff.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    record = _staff_here(center, number)
    if record is None:
        return staff_not_found(request, center, ui)
    linked = record.user is not None
    return page(
        request,
        "staff_member",
        {
            "number": record.staff_number,
            "profile": staff_profile(ui, record),
            "link_present": ui["staff_account_yes" if linked else "staff_account_no"],
            "courses": assigned_courses(record),
            "cohorts": assigned_cohorts(record),
            # STAFF-004's sessions for this record (empty for staff that teach
            # no cohort, and for those without access to attendance).
            "sessions": (
                staff_session_rows(center, ui, record)
                if role_has_permission(member.role, "attendance.view")
                else []
            ),
            "sessions_total": (
                staff_sessions_total(center, ui, record)
                if role_has_permission(member.role, "attendance.view")
                else []
            ),
        },
        center=center,
    )


def staff_manage(request, slug):
    """The page staff are recorded, changed, statused, and assigned on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(member.role, "staff.manage")
    action_at = f"/o/{center.slug}/center/staff/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {"staff_number": "", "staff_account": ""}
        return page(
            request,
            "staff_manage",
            {
                "fields": manage_rows(ui, sent, {}, STAFF_FORM_FIELDS),
                "form": form_at(action_at, ui, sent, STAFF_FORM_FIELDS),
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
        for name, _key, _options in STAFF_FORM_FIELDS
    }
    problems = {}
    action = sent["staff_action"]
    if action == "":
        problems["staff_action"] = ui["field_required"]
        note, kind = ui["staff_rejected"], "rejected"
    elif action not in STAFF_ACTIONS:
        problems["staff_action"] = ui["field_invalid"].format(action)
        note, kind = ui["staff_rejected"], "rejected"
    else:
        try:
            saved, field_problems, codes = run_staff_action(
                request, center, action, sent
            )
        except PermissionDenied:
            return page(request, "denied", {}, center=center, status=403)
        for name, message in field_problems:
            target = staff_problem_field(action, name)
            problems[target] = ui["field_invalid"].format(message)
        note, kind = staff_outcome(ui, saved, problems, codes)
    return page(
        request,
        "staff_manage",
        {
            "fields": manage_rows(ui, sent, problems, STAFF_FORM_FIELDS),
            "form": form_at(action_at, ui, sent, STAFF_FORM_FIELDS),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )


def run_staff_action(request, center, action, sent):
    """Call the service the action names with the values the page collected."""
    if action == "create":
        return create_staff(
            request.user,
            center,
            sent["staff_number"],
            sent["name"],
            sent["email"],
            sent["phone"],
            sent["job_title"],
            sent["specializations"],
            sent["status"] or STATUS_ACTIVE,
            sent["availability"],
            sent["qualifications"],
        )
    if action == "edit":
        return edit_staff(
            request.user,
            center,
            sent["staff_number"],
            sent["staff_field"],
            sent["staff_value"],
        )
    if action == "set_status":
        return set_staff_status(
            request.user, center, sent["staff_number"], sent["status"]
        )
    if action == "assign_course":
        return assign_staff_to_course(
            request.user, center, sent["staff_number"], sent["target_number"]
        )
    if action == "remove_course":
        return remove_staff_from_course(
            request.user, center, sent["staff_number"], sent["target_number"]
        )
    if action == "assign_cohort":
        return assign_staff_to_cohort(
            request.user, center, sent["staff_number"], sent["target_number"]
        )
    if action == "link":
        account = None
        typed = sent["staff_account"].strip()
        if typed.isdigit():
            account = CohortUser.objects.filter(pk=int(typed)).first()
        return link_staff_account(
            request.user, center, sent["staff_number"], account
        )
    if action == "unlink":
        return unlink_staff_account(request.user, center, sent["staff_number"])
    return remove_staff_from_cohort(
        request.user, center, sent["staff_number"], sent["target_number"]
    )


def staff_problem_field(action, name):
    """Where the page marks a validator's rejection: the row that was typed."""
    if action == "edit" and name in EDITABLE_STAFF_FIELDS:
        return "staff_value"
    if name == "staff":
        return "staff_number"
    if name in ("course", "cohort"):
        return "target_number"
    return name


def staff_outcome(ui, saved, problems, codes):
    for code in codes:
        entry = STAFF_PROBLEM_UI.get(code)
        if entry is None:
            continue
        field_id, key = entry
        problems[field_id] = ui[key]
    if saved:
        return ui["staff_saved"], "saved"
    if problems:
        return ui["staff_rejected"], "rejected"
    return ui["nothing_changed"], "unchanged"
