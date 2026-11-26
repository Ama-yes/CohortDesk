"""One place that decides what a signed-in person may do, wherever a page,
an action, or a lookup needs to know it.

Nothing here is a page: this is what makes permission part of the product no
matter how a page reaches the decision. Browser-supplied organization ids are
never trusted: every protected call resolves the caller's active membership
first, then checks the permission code (SEC-001/SEC-003/SEC-004). Unknown
permission codes fail closed via PermissionNotDefined.

The business actions return (saved, problems) instead of raising, so a page
can show exactly what was not accepted on the page the person is on (RULE 6.3,
AUTH-005/006/007). Services never import another app (RULE 6.1): pages do the
username-to-account resolving before calling these.
"""
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Organization, Members
from .permissions import role_has_permission
from .roles import ROLES

# Settings a center may change from its own settings page. The slug is never in
# this set: it is the address, and changing it would move every page.
SETTING_FIELDS = ("name", "primary_currency", "display_timezone", "preferred_locale",
                  "capacity_policy", "schedule_conflict_policy")


def membership(user, organization):
    """Resolve user access to organization. PermissionDenied otherwise.

    Suspended memberships are denied even with a valid session (SESSION-001).
    """
    member = Members.objects.filter(user=user, organization=organization).first()
    if member is None:
        raise PermissionDenied("No membership for this organization.")
    if member.status != "active":
        raise PermissionDenied("Membership is not active.")
    return member


def require(user, organization, code):
    """Require permission `code` for user within organization."""
    member = membership(user, organization)
    if not role_has_permission(member.role, code):
        raise PermissionDenied("Role is not permitted for this operation.")
    return member


def centers(user):
    """Organizations the user has an active membership in, in a stable order."""
    return [
        member.organization
        for member in Members.objects.filter(user=user, status="active").order_by("pk")
    ]


def update_settings(user, organization, values):
    """Change a center's settings or reject them. Returns (saved, problems).

    Values are validated through full_clean with the other settings fields
    excluded, one field at a time, so each rejected value can be attributed to
    the field it came from (models.clean_fields honors exclude, DECISIONS D20).
    Nothing is saved unless every submitted value is valid. problems is a list
    of (field, english validator message); the English wording is a recorded
    open question for later translation.
    """
    require(user, organization, "organization.settings.change")
    changed = {
        name: values[name]
        for name in SETTING_FIELDS
        if values.get(name, "") != ""
    }
    if not changed:
        return False, []
    original = {name: getattr(organization, name) for name in SETTING_FIELDS}
    for name, value in changed.items():
        setattr(organization, name, value)
    problems = []
    for name in changed:
        exclude = [other for other in SETTING_FIELDS if other != name]
        try:
            organization.full_clean(exclude=exclude)
        except ValidationError as error:
            # one field at a time was validated (the others excluded), so the
            # message belongs to `name` even when Django files it under "__all__"
            problems.append((name, "; ".join(error.messages)))
    if problems:
        for name in SETTING_FIELDS:
            setattr(organization, name, original[name])
        return False, problems
    organization.full_clean()
    organization.save(update_fields=list(changed))
    return True, []


def add_person(user, organization, person, role):
    """Add an account to this center with `role` (AUTH-006). Returns (saved, problems).

    problems names the field the page should mark: "role" is not one of the
    center's roles, "exists" means the account already belongs here.
    """
    require(user, organization, "organization.members.invite")
    if role not in ROLES:
        return False, ["role"]
    if Members.objects.filter(user=person, organization=organization).exists():
        return False, ["exists"]
    # The form offers this centre's own people plus accounts of no centre
    # (D105); a hidden option is not authorization, so the same rule holds
    # here: an account attached to another centre is not this centre's to
    # take. The multi-centre invitation question is open item 74.
    if Members.objects.filter(user=person).exclude(
        organization=organization
    ).exists():
        return False, ["member"]
    Members.objects.create(user=person, organization=organization, role=role)
    return True, []


def suspend_person(user, organization, person):
    """Suspend a member's access at this center (AUTH-007). Returns (saved, problems)."""
    require(user, organization, "organization.members.manage")
    record = Members.objects.filter(user=person, organization=organization).first()
    if record is None:
        return False, ["member"]
    if record.status == "suspended":
        return False, []
    record.status = "suspended"
    record.suspended_at = timezone.now()
    record.save(update_fields=["status", "suspended_at"])
    return True, []


def restore_person(user, organization, person):
    """Restore a suspended member's access at this center. Returns (saved, problems)."""
    require(user, organization, "organization.members.manage")
    record = Members.objects.filter(user=person, organization=organization).first()
    if record is None:
        return False, ["member"]
    if record.status == "active":
        return False, []
    record.status = "active"
    record.suspended_at = None
    record.save(update_fields=["status", "suspended_at"])
    return True, []


def set_role(user, organization, person, role):
    """Assign a permitted role to a member (AUTH-006). Returns (saved, problems)."""
    require(user, organization, "organization.members.manage")
    if role not in ROLES:
        return False, ["role"]
    record = Members.objects.filter(user=person, organization=organization).first()
    if record is None:
        return False, ["member"]
    if record.role == role:
        return False, []
    record.role = role
    record.save(update_fields=["role"])
    return True, []
