"""The tenant pages: where an organization address turns into a page.

This module is the only place a tenant address is answered, and every answer
re-asks the services what the current person may do (AUTH-005: the address
selects the centre, the server verifies the membership; SEC-001: hidden links
are not authorization, a request that comes without any link follows the same
rules).

The error model (docs/DECISIONS.md):
- unknown centre / no active membership here / suspended membership / not
  signed in at a tenant address -> the identical project problem page at 404:
  the address never reveals whether an unknown centre exists (SEC-003/004),
- member here but the role is not permitted for this page -> this centre's
  denied page at 403,
- a non-read method at a read-only address -> the problem page at 405.

Data rule: every list a page shows is built here from the centre's own
records; no view reads another centre, and each row's title text comes from
this centre's glossary (pages.py injects the same ui for the page body).
"""

from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model

from apps.pages import center_ui, page
from apps.accounts.services import working_center

from .models import Members, Organization
from .permissions import role_has_permission
from .roles import ROLE_CHOICES
from .services import (
    SETTING_FIELDS,
    add_person,
    membership,
    require,
    restore_person,
    set_role,
    suspend_person,
    update_settings,
)

READ_METHODS = ("GET", "HEAD", "OPTIONS")

# Settings a page shows, and the glossary key that titles each one.
SETTING_UI = {
    "name": "settings_name",
    "primary_currency": "settings_currency",
    "display_timezone": "settings_timezone",
    "preferred_locale": "settings_locale",
    "capacity_policy": "settings_capacity",
    "schedule_conflict_policy": "settings_schedule_conflict",
}

# COH-005: the capacity policy's allowed values, in the center's own words.
CAPACITY_UI = {
    "prevent": "capacity_prevent",
    "warn": "capacity_warn",
}

# SCH-005/SCH-006: the same choice for room and instructor double-booking.
SCHEDULE_UI = {
    "prevent": "schedule_prevent",
    "warn": "schedule_warn",
}

# Which settings are a fixed vocabulary instead of free text.
POLICY_UI = {
    "capacity_policy": CAPACITY_UI,
    "schedule_conflict_policy": SCHEDULE_UI,
}

# The change-settings form: all text, so the model's own validators can
# attribute each rejected value to the field it came from.
MEMBERS_FIELD_UI = {
    "members_action": "members_action",
    "members_target": "members_target",
    "members_role": "members_role",
}
MEMBERS_ACTIONS = ("add", "suspend", "restore", "set_role")
MEMBERS_ACTION_UI = {
    "add": "members_action_add",
    "suspend": "members_action_suspend",
    "restore": "members_action_restore",
    "set_role": "members_action_set_role",
}
# What a service says was not accepted, and the field the page marks for it.
MEMBERS_PROBLEM_UI = {
    "role": ("members_role", "role_out_of_range"),
    "exists": ("members_target", "members_exists"),
    "member": ("members_target", "member_not_here"),
}


def authenticated(request):
    """Is there a signed-in person on this request? One source for that."""
    return request.user is not None and request.user.is_authenticated


def here(request, slug):
    """The centre at this address, and the member this request has there.

    None means: no such centre, not signed in, or no active membership here
    (SEC-003/004: an unknown centre and an unaffiliated person get the same
    answer, so this address never tells whether an unseen centre exists).
    """
    if not authenticated(request):
        return None
    center = Organization.objects.filter(slug=slug).first()
    if center is None:
        return None
    member = Members.objects.filter(
        user=request.user, organization=center, status="active"
    ).first()
    if member is None:
        return None
    return center, member


def settings_rows(center, ui):
    """The centre's settings, as rows the settings page shows."""
    rows = []
    for name in SETTING_FIELDS:
        value = getattr(center, name)
        if name in POLICY_UI:
            value = ui[POLICY_UI[name][value]]
        rows.append({"id": name, "title": ui[SETTING_UI[name]], "value": value})
    return rows


def settings_fields(center, ui, values):
    """The change-settings page's rows: what is here, plus what was sent."""
    rows = []
    for name in SETTING_FIELDS:
        value = values.get(name) or getattr(center, name)
        if name in POLICY_UI and value in POLICY_UI[name]:
            value = ui[POLICY_UI[name][value]]
        rows.append(
            {
                "id": name,
                "title": ui[SETTING_UI[name]],
                "value": value,
                "problem": "",
            }
        )
    return rows


def settings_form(center, ui, values):
    """The change-settings form: one field per changeable setting."""
    action = f"/o/{center.slug}/center/settings/change/"
    fields = []
    for name in SETTING_FIELDS:
        field = {
            "id": name,
            "title": ui[SETTING_UI[name]],
            "type": "text",
            "value": values.get(name, ""),
        }
        if name in POLICY_UI:
            field["type"] = "select"
            field["current"] = values.get(name) or getattr(center, name)
            field["options"] = [
                {"value": code, "label": ui[key]}
                for code, key in POLICY_UI[name].items()
            ]
        fields.append(field)
    return {
        "id": action.rstrip("/"),
        "method": "post",
        "action": action,
        "fields": fields,
    }


def role_label(ui, code):
    """A role's name in this centre's language."""
    return ui["role_" + code]


def state_label(ui, status):
    """Whether a person can use this centre, in this centre's language."""
    return ui["state_" + status]


def viewing_text(request, ui, center):
    """Where the current person is working, as the dashboard tells it."""
    slug = working_center(request)
    if not slug:
        return ui["viewing_none"]
    if slug == center.slug:
        return ui["viewing_here"]
    return ui["viewing_other"]


def dashboard(request, slug):
    """The centre's dashboard: what this centre is, and what I may read here."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    ui = center_ui(center)
    data = {
        "address": request.path,
        "role": role_label(ui, member.role),
        "viewing": viewing_text(request, ui, center),
    }
    if role_has_permission(member.role, "organization.settings.view"):
        data["settings"] = settings_rows(center, ui)
    return page(request, "dashboard", data, center=center)


def settings_page(request, slug):
    """A centre's settings: only the centre's own people may read them."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "organization.settings.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(request, "settings", {"settings": settings_rows(center, ui)}, center=center)


def settings_change(request, slug):
    """The page settings are changed on, and where a rejection is shown."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method in READ_METHODS:
        if not role_has_permission(member.role, "organization.settings.view"):
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        data = {
            "fields": settings_fields(center, ui, {}),
            "form": settings_form(center, ui, {}),
        }
        return page(request, "settings_change", data, center=center)
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "organization.settings.change"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    values = {name: request.POST.get(name, "") for name in SETTING_FIELDS}
    try:
        saved, problems = update_settings(request.user, center, values)
    except PermissionDenied:
        return page(request, "denied", {}, center=center, status=403)
    data = {
        "fields": settings_fields(center, ui, values),
        "form": settings_form(center, ui, values),
    }
    for name, message in problems:
        for field in data["fields"]:
            if field["id"] == name:
                field["problem"] = ui["field_invalid"].format(message)
    if saved:
        data["note"] = ui["settings_saved"]
        data["note_kind"] = "saved"
    elif problems:
        data["note"] = ui["settings_rejected"]
        data["note_kind"] = "rejected"
    else:
        data["note"] = ui["nothing_changed"]
        data["note_kind"] = "unchanged"
    return page(request, "settings_change", data, center=center)


def members_rows(center, ui):
    """Every person who belongs to this centre, suspended people included."""
    User = get_user_model()
    return [
        {
            "number": record.pk,
            "name": record.user.display_name or record.user.username,
            "role": role_label(ui, record.role),
            "state": state_label(ui, record.status),
        }
        for record in Members.objects.filter(organization=center).order_by(
            "user__username"
        )
    ]


def members_form_at(action, ui, sent):
    """The change-membership form: this centre's actions, accounts, and roles."""
    User = get_user_model()
    roles = [{"value": code, "label": ui["role_" + code]} for code, _ in ROLE_CHOICES]
    people = [
        {"value": person.username, "label": person.display_name or person.username}
        for person in User.objects.order_by("username")
    ]
    return {
        "id": action.rstrip("/"),
        "method": "post",
        "action": action,
        "fields": [
            {
                "id": "members_action",
                "title": ui[MEMBERS_FIELD_UI["members_action"]],
                "type": "select",
                "current": sent.get("members_action", ""),
                "options": [
                    {"value": code, "label": ui[MEMBERS_ACTION_UI[code]]}
                    for code in MEMBERS_ACTIONS
                ],
            },
            {
                "id": "members_target",
                "title": ui[MEMBERS_FIELD_UI["members_target"]],
                "type": "select",
                "current": sent.get("members_target", ""),
                "options": people,
            },
            {
                "id": "members_role",
                "title": ui[MEMBERS_FIELD_UI["members_role"]],
                "type": "select",
                "current": sent.get("members_role", ""),
                "options": roles,
            },
        ],
    }


def members_fields(ui, sent, problems):
    """The change-membership page's rows, and what was not accepted."""
    return [
        {
            "id": name,
            "title": ui[MEMBERS_FIELD_UI[name]],
            "value": sent.get(name, ""),
            "problem": problems.get(name, ""),
        }
        for name in MEMBERS_FIELD_UI
    ]


def members_page(request, slug):
    """The people of this centre, with the access each of them has here."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    if request.method not in READ_METHODS:
        return page(request, "problem", {}, status=405)
    if not role_has_permission(member.role, "organization.members.view"):
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    return page(request, "members", {"members": members_rows(center, ui)}, center=center)


def members_manage(request, slug):
    """The page people are added, suspended, restored, or re-roled on."""
    resolved = here(request, slug)
    if resolved is None:
        return page(request, "problem", {}, status=404)
    center, member = resolved
    can_manage = role_has_permission(
        member.role, "organization.members.manage"
    ) or role_has_permission(member.role, "organization.members.invite")
    action_at = f"/o/{center.slug}/center/members/manage/"
    if request.method in READ_METHODS:
        if not can_manage:
            return page(request, "denied", {}, center=center, status=403)
        ui = center_ui(center)
        sent = {}
        return page(
            request,
            "members_manage",
            {
                "fields": members_fields(ui, sent, {}),
                "form": members_form_at(action_at, ui, sent),
            },
            center=center,
        )
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    if not can_manage:
        return page(request, "denied", {}, center=center, status=403)
    ui = center_ui(center)
    sent = {name: request.POST.get(name, "") for name in MEMBERS_FIELD_UI}
    problems = {}
    action = sent["members_action"]
    if action == "":
        problems["members_action"] = ui["field_required"]
        note, kind = ui["members_rejected"], "rejected"
    elif action not in MEMBERS_ACTIONS:
        problems["members_action"] = ui["field_invalid"].format(action)
        note, kind = ui["members_rejected"], "rejected"
    else:
        User = get_user_model()
        target = None
        if sent["members_target"]:
            target = User.objects.filter(username=sent["members_target"]).first()
        if target is None:
            problems["members_target"] = ui["account_not_found"]
            note, kind = ui["members_rejected"], "rejected"
        else:
            try:
                if action == "add":
                    saved, codes = add_person(request.user, center, target, sent["members_role"])
                elif action == "suspend":
                    saved, codes = suspend_person(request.user, center, target)
                elif action == "restore":
                    saved, codes = restore_person(request.user, center, target)
                else:
                    saved, codes = set_role(request.user, center, target, sent["members_role"])
            except PermissionDenied:
                return page(request, "denied", {}, center=center, status=403)
            for code in codes:
                field_id, key = MEMBERS_PROBLEM_UI[code]
                problems[field_id] = ui[key]
            if saved:
                note, kind = ui["members_saved"], "saved"
            elif codes:
                note, kind = ui["members_rejected"], "rejected"
            else:
                note, kind = ui["nothing_changed"], "unchanged"
    return page(
        request,
        "members_manage",
        {
            "fields": members_fields(ui, sent, problems),
            "form": members_form_at(action_at, ui, sent),
            "note": note,
            "note_kind": kind,
        },
        center=center,
    )
