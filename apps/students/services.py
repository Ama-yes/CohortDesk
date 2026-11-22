"""The business actions of one center's student and application records.

One place decides what a signed-in person may do wherever a page, an action,
or a lookup needs the decision (SEC-004): every action here re-asks the
central authorization layer (apps.organizations.services.require) before
anything is read or written, so no page or later caller can skip permission.
Nothing here is a page: a page resolves the identifiers typed on it before
calling these functions, and shows what was not accepted on the page the
person is already on.

Statuses are never free text: the tables below are the only place a status
is ever moved, which is this product's answer to STU-007 and ADM-003's
"controlled transitions" (docs/DECISIONS.md records them for the product
owner to confirm). Archiving sets the status through the same rule and
never removes a record (STU-008/ADM-007 and RULE 6.2: records stay
findable after archive). The duplicate check (STU-009) never merges
records by itself - it says what may be a duplicate and saves nothing.
A student record never needs its own login (STU-004): center staff create
records, and ADM-005's "enroll an existing student directly" is create
with the active entry status.

Contract (one shape for all actions): (record_or_none, problems, notes)
- record: the created/updated record, or None when nothing was saved;
- problems: list of (field, english validator message) rows from
  full_clean, the same shape as the tenant settings action returns
  (DECISIONS D20; the English wording is an open question for later
  translation);
- notes: short business codes the page answers with its own words (the
  members action pattern). Nothing is saved unless everything submitted
  passed this model's own rules.
"""
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.organizations.services import require
from .models import Student, Application
from .statuses import (
    STATUS_ACTIVE,
    STATUS_APPLICANT,
    STATUS_ARCHIVED,
    STATUS_GRADUATED,
    STATUS_SUSPENDED,
    STATUS_WITHDRAWN,
    APP_STATUS_NEW,
    APP_STATUS_UNDER_REVIEW,
    APP_STATUS_ACCEPTED,
    APP_STATUS_REJECTED,
    APP_STATUS_WITHDRAWN,
    APP_STATUS_ARCHIVED,
)

# The only transitions a center may take (STU-007: "controlled transitions";
# docs/DECISIONS.md records the design answer, the product owner confirms).
STUDENT_TRANSITIONS = {
    STATUS_APPLICANT: (STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_WITHDRAWN,
                       STATUS_ARCHIVED),
    STATUS_ACTIVE: (STATUS_SUSPENDED, STATUS_GRADUATED, STATUS_WITHDRAWN,
                    STATUS_ARCHIVED),
    STATUS_SUSPENDED: (STATUS_ACTIVE, STATUS_GRADUATED, STATUS_WITHDRAWN,
                       STATUS_ARCHIVED),
    STATUS_GRADUATED: (STATUS_WITHDRAWN, STATUS_ARCHIVED),
    STATUS_WITHDRAWN: (STATUS_ARCHIVED,),
    STATUS_ARCHIVED: (),
}
# ADM-005/ADM-004: records enter as applicant, or directly as active when
# staff enroll someone without an application. Archived is not an entry
# status: archiving is an action on an existing record, not a way in.
ENTRY_STATUSES = (STATUS_APPLICANT, STATUS_ACTIVE)
# The profile fields the change-record page may edit: never student_number
# (the identifier of a record, STU-002 - changing it would be a new
# record), never status (only the transition rule moves it, STU-007),
# never registered_at (a record is registered when it is created).
EDITABLE_STUDENT_FIELDS = ("name", "contact", "birth_date", "address", "note")

APPLICATION_TRANSITIONS = {
    APP_STATUS_NEW: (APP_STATUS_UNDER_REVIEW, APP_STATUS_ACCEPTED,
                     APP_STATUS_REJECTED, APP_STATUS_WITHDRAWN,
                     APP_STATUS_ARCHIVED),
    APP_STATUS_UNDER_REVIEW: (APP_STATUS_ACCEPTED, APP_STATUS_REJECTED,
                              APP_STATUS_WITHDRAWN, APP_STATUS_ARCHIVED),
    APP_STATUS_ACCEPTED: (),
    APP_STATUS_REJECTED: (APP_STATUS_ARCHIVED,),
    APP_STATUS_WITHDRAWN: (APP_STATUS_ARCHIVED,),
    APP_STATUS_ARCHIVED: (),
}
# accepted is terminal: the next step of an accepted application is
# convert_application (ADM-004), not another status.

_STAMP_FIELDS = ("organization", "created_at", "registered_at",
                 "status_changed_at", "archived_at")
_STUDENT_PROFILE_FIELDS = ("student_number", "name", "contact", "birth_date",
                          "address", "status", "note")


def _field_problems(error):
    """Attribute a rejection to the fields that caused it.

    The model's clean_fields raises per field, so a full_clean failure names
    each field it came from (the tenant settings flow, DECISIONS D20). A
    field-less error is reported with an empty field name and only shows as
    the page's "nothing was accepted" note. Django's own validators hand over
    words that translate late, so each message is rendered here; a page only
    ever shows it as text.
    """
    if hasattr(error, "error_dict"):
        return [
            (
                field,
                "; ".join(
                    str(part)
                    for nested in nested_errors
                    for part in nested.messages
                ),
            )
            for field, nested_errors in error.error_dict.items()
        ]
    return [("", "; ".join(str(part) for part in error.messages))]


def _student_here(organization, number):
    """This center's record with that student number, or None (RULE 6.1)."""
    normalized = str(number).strip().upper()
    return Student.objects.filter(
        organization=organization, student_number=normalized
    ).first()


def _application_here(organization, application_number):
    """This center's application with that number, or None (RULE 6.1).

    The applications page shows each record's pk as its number on the page;
    a value that is not a whole number names no application.
    """
    text = str(application_number).strip()
    if not text.isdigit():
        return None
    return Application.objects.filter(
        organization=organization, pk=int(text)
    ).first()


def duplicate_found(organization, name, contact):
    """STU-009: possible duplicate = same name plus same contact here.

    The matching signal is name plus contact (the SRS's example signal;
    configurable signals are a later refinement - docs/OPEN_QUESTIONS.md).
    Only records with a contact are candidates: two people sharing a name
    with no contact to compare are never guessed to be one person. A
    possible duplicate is reported and never merged or saved (STU-009).
    """
    if not contact:
        return False
    for existing in Student.objects.filter(organization=organization):
        if existing.name == name and existing.contact == contact:
            return True
    return False


def create_student(user, organization, number, name, contact,
                   status=STATUS_APPLICANT, note=""):
    """STU-001/STU-002/STU-003/STU-004/STU-009/ADM-005: create one record.

    Center staff create it (no student logs in anywhere, STU-004). The entry
    status is applicant, or active when staff enroll someone directly
    (ADM-005). Returns (record, problems, notes); nothing is saved unless
    every submitted value passed this model's own rules, and a possible
    duplicate is reported and never merged (STU-009).
    """
    require(user, organization, "students.manage")
    record = Student(
        organization=organization,
        student_number=str(number),
        name=str(name),
        contact=str(contact),
        status=str(status),
        note=str(note),
        registered_at=timezone.now(),
        status_changed_at=timezone.now(),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    if record.status not in ENTRY_STATUSES:
        return None, [], ["entry"]
    if duplicate_found(organization, record.name, record.contact):
        return None, [], ["duplicate"]
    try:
        record.save()
    except Exception:
        return None, [], ["number_used"]
    return record, [], []


def edit_student(user, organization, number, field, value):
    """STU-001/STU-003: edit one profile field of one existing record.

    value is a real date for birth_date or None (a page parses the text
    before calling), and text for the text fields; the model's own rules
    accept or reject it, attributed to the field it came from.
    """
    require(user, organization, "students.manage")
    record = _student_here(organization, number)
    if record is None:
        return None, [], ["not_found"]
    if field not in EDITABLE_STUDENT_FIELDS:
        return None, [], ["field_not_editable"]
    original = getattr(record, field)
    setattr(record, field, value)
    exclude = list(_STAMP_FIELDS) + [
        name for name in _STUDENT_PROFILE_FIELDS if name != field
    ]
    problems = []
    try:
        record.full_clean(exclude=exclude)
    except ValidationError as error:
        problems = _field_problems(error)
    if problems:
        setattr(record, field, original)
        return None, problems, []
    record.full_clean()
    record.save(update_fields=[field])
    return record, [], []


def set_student_status(user, organization, number, new_status):
    """STU-007: move a record's status through this center's transition table."""
    require(user, organization, "students.manage")
    record = _student_here(organization, number)
    if record is None:
        return None, [], ["not_found"]
    target = str(new_status).strip().lower()
    allowed = STUDENT_TRANSITIONS.get(record.status, ())
    if target not in allowed:
        return None, [], ["transition"]
    record.status = target
    record.status_changed_at = timezone.now()
    if target == STATUS_ARCHIVED and record.archived_at is None:
        record.archived_at = record.status_changed_at
    record.full_clean()
    record.save(update_fields=["status", "status_changed_at", "archived_at"])
    return record, [], []


def archive_student(user, organization, number):
    """STU-008/RULE 6.2: archive a record - it stays findable, never deleted."""
    require(user, organization, "students.archive")
    record = _student_here(organization, number)
    if record is None:
        return None, [], ["not_found"]
    if record.status == STATUS_ARCHIVED:
        return record, [], ["already_archived"]
    if STATUS_ARCHIVED not in STUDENT_TRANSITIONS.get(record.status, ()):
        return None, [], ["transition"]
    record.status = STATUS_ARCHIVED
    record.status_changed_at = timezone.now()
    if record.archived_at is None:
        record.archived_at = record.status_changed_at
    record.full_clean()
    record.save(update_fields=["status", "status_changed_at", "archived_at"])
    return record, [], []


def record_application(user, organization, name, course="", contact="",
                       source="", note="", student_number=""):
    """ADM-001/ADM-002: record an application at this center."""
    require(user, organization, "students.manage")
    application = Application(
        organization=organization,
        student_number=str(student_number),
        name=str(name),
        contact=str(contact),
        source=str(source),
        desired_course=str(course),
        note=str(note),
    )
    try:
        application.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    application.save()
    return application, [], []


def set_application_status(user, organization, application_number, new_status):
    """ADM-003: move an application through this center's transition table."""
    require(user, organization, "students.manage")
    application = _application_here(organization, application_number)
    if application is None:
        return None, [], ["not_found"]
    target = str(new_status).strip().lower()
    allowed = APPLICATION_TRANSITIONS.get(application.status, ())
    if target not in allowed:
        return None, [], ["transition"]
    application.status = target
    application.status_changed_at = timezone.now()
    if target == APP_STATUS_ARCHIVED and application.archived_at is None:
        application.archived_at = application.status_changed_at
    application.full_clean()
    application.save(
        update_fields=["status", "status_changed_at", "archived_at"]
    )
    return application, [], []


def convert_application(user, organization, application_number):
    """ADM-004: an accepted application creates its student record.

    No information is re-entered: the name, contact and named student
    number are carried over from the application, and the created record
    is linked to it (converted_student), so the center can show the
    application on the profile page. The application must name a student
    number before it can be converted (STU-002: every record needs its
    center-scoped identifier; inventing one silently would merge two
    ideas - docs/DECISIONS.md records this as the product's answer).
    Returns (record, problems, notes) like every other action here.
    """
    require(user, organization, "students.application.accept")
    application = _application_here(organization, application_number)
    if application is None:
        return None, [], ["not_found"]
    if application.converted_student is not None:
        return application.converted_student, [], ["converted_already"]
    if application.status != APP_STATUS_ACCEPTED:
        return None, [], ["not_accepted"]
    if not application.student_number:
        return None, [], ["needs_number"]
    if duplicate_found(organization, application.name, application.contact):
        return None, [], ["duplicate"]
    record = Student(
        organization=organization,
        student_number=application.student_number,
        name=application.name,
        contact=application.contact,
        status=STATUS_ACTIVE,
        note=application.note,
        registered_at=timezone.now(),
        status_changed_at=timezone.now(),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    try:
        record.save()
    except Exception:
        return None, [], ["number_used"]
    application.converted_student = record
    application.save(update_fields=["converted_student"])
    return record, [], []
