"""Role constants (MGMT-001/002). Roles are per-membership, not global."""

ROLE_OWNER = "owner"
ROLE_ADMINISTRATOR = "administrator"
ROLE_RECEPTIONIST = "receptionist"
ROLE_ACADEMIC_COORDINATOR = "academic_coordinator"
ROLE_INSTRUCTOR = "instructor"
ROLE_FINANCE_OFFICER = "finance_officer"

ROLES = (
    ROLE_OWNER,
    ROLE_ADMINISTRATOR,
    ROLE_RECEPTIONIST,
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_INSTRUCTOR,
    ROLE_FINANCE_OFFICER,
)

ROLE_CHOICES = [(role, role.replace("_", " ").title()) for role in ROLES]
