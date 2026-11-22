"""The vocabularies a student record and an application may have.

Like roles.py is for roles, one module owns these so the model layer
(status choices) and the service layer (transition rules) read the same
source. STU-007: applicant (where applicable), active, suspended,
graduated, withdrawn and archived. ADM-003: new, under review, accepted,
rejected and withdrawn (plus archived, because RULE 6.2 prefers archive
over hard deletion for every record referenced by history).

The transitions themselves are business rules and live in services.py:
only legal moves are reachable there, and the tables are the only place
a status is ever changed.
"""

STATUS_APPLICANT = "applicant"
STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
STATUS_GRADUATED = "graduated"
STATUS_WITHDRAWN = "withdrawn"
STATUS_ARCHIVED = "archived"

STUDENT_STATUSES = (
    STATUS_APPLICANT,
    STATUS_ACTIVE,
    STATUS_SUSPENDED,
    STATUS_GRADUATED,
    STATUS_WITHDRAWN,
    STATUS_ARCHIVED,
)

APP_STATUS_NEW = "new"
APP_STATUS_UNDER_REVIEW = "under_review"
APP_STATUS_ACCEPTED = "accepted"
APP_STATUS_REJECTED = "rejected"
APP_STATUS_WITHDRAWN = "withdrawn"
APP_STATUS_ARCHIVED = "archived"

APPLICATION_STATUSES = (
    APP_STATUS_NEW,
    APP_STATUS_UNDER_REVIEW,
    APP_STATUS_ACCEPTED,
    APP_STATUS_REJECTED,
    APP_STATUS_WITHDRAWN,
    APP_STATUS_ARCHIVED,
)

STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in STUDENT_STATUSES
]
APPLICATION_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in APPLICATION_STATUSES
]
