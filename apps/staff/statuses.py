"""The vocabularies a staff record may have (STAFF-002/STAFF-005).

Like apps/students/statuses.py and apps/academics/statuses.py, one module owns
these values so the model's field choices and the service's transition table
read the same source. The transitions themselves are business rules and live
in services.py.
"""

STATUS_ACTIVE = "active"
STATUS_ON_LEAVE = "on_leave"
STATUS_INACTIVE = "inactive"
STATUS_ARCHIVED = "archived"

STAFF_STATUSES = (STATUS_ACTIVE, STATUS_ON_LEAVE, STATUS_INACTIVE, STATUS_ARCHIVED)

STAFF_STATUS_CHOICES = [
    (value, value.replace("_", " ").title()) for value in STAFF_STATUSES
]
