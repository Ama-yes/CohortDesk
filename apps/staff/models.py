"""Staff records and instructor assignments for one center.

Every instance hangs directly from one Organization (RULE 6.1), and every
identifier is resolved through the caller's own organization before use. The
features are the staff half of SRS Milestone 2:

- Staff is the center's employee/instructor record (STAFF-001/STAFF-002). It is
  independent of a login identity (STAFF-006): a staff record needs no account,
  and creating one grants no access to the product. The optional user link (STAFF-
  007, decided here): Staff.user is a blank, nullable FK that one explicit staff
  action sets per center; the model keeps only the shape rules (at most one
  record per account per center; the account must already be an active
  member of this center). Linking grants nothing, changes no membership role,
  and never creates or moves anyone (docs/DECISIONS.md).
- CourseInstructor and CohortInstructor record that one staff member teaches
  one course or one cohort (STAFF-003), with the database refusing a duplicate
  assignment. A staff profile lists these assignments, which is the
  assigned-cohort half of STAFF-004; scheduled sessions do not exist yet.

Validation follows the project's established model hook: every rejection is
raised as {field: message} so a page can mark the row the person typed, and the
hook finishes with Django's own declared-field validation.
"""
import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.academics.models import Cohort, Course
from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization

from .statuses import STAFF_STATUS_CHOICES

STAFF_NUMBER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,19}$")


class Staff(models.Model):
    """One employee or instructor of one center (STAFF-001/STAFF-002)."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="staff",
    )
    staff_number = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    email = models.EmailField(max_length=254, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    job_title = models.CharField(max_length=80, blank=True)
    specializations = models.CharField(max_length=200, blank=True)
    employment_status = models.CharField(
        max_length=16,
        choices=STAFF_STATUS_CHOICES,
        default="active",
    )
    availability = models.CharField(max_length=300, blank=True)
    qualifications = models.TextField(blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(
        CohortUser,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_links",
    )

    def clean_fields(self, exclude=None):
        """STAFF-002 rules, one place; field-keyed for a page's rows."""
        excluded = set(exclude or ())
        if "staff_number" not in excluded:
            self.staff_number = str(self.staff_number).strip().upper()
            if not STAFF_NUMBER_PATTERN.match(self.staff_number):
                raise ValidationError(
                    {
                        "staff_number": (
                            "Staff number must match {} (up to 20 characters "
                            "of letters, digits, dash or underscore, starting "
                            "with a letter or digit)."
                        ).format(STAFF_NUMBER_PATTERN.pattern)
                    }
                )
        for name in (
            "name",
            "email",
            "phone",
            "job_title",
            "specializations",
            "availability",
            "qualifications",
        ):
            if name not in excluded:
                setattr(self, name, str(getattr(self, name)).strip())
        if "name" not in excluded and not self.name:
            raise ValidationError({"name": "A staff member must have a name."})
        if "employment_status" not in excluded:
            self.employment_status = str(self.employment_status).strip().lower()
        if "user" not in excluded and self.user_id is not None:
            # The account must already be an active member of this center;
            # linking can never move a membership or change a role (SEC-004).
            if not Members.objects.filter(
                organization_id=self.organization_id,
                user=self.user,
                status="active",
            ).exists():
                raise ValidationError(
                    {
                        "user": (
                            "That account is not an active member of this "
                            "center."
                        )
                    }
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        ordering = ["staff_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "staff_number"],
                name="staff_organization_staff_number",
            ),
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="staff_user_organization_once",
                violation_error_message=(
                    "Only one staff record here may be linked to an account."
                ),
            ),
        ]


class CourseInstructor(models.Model):
    """One instructor assigned to one course (STAFF-003)."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="course_instructors",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        related_name="instructor_assignments",
    )
    staff = models.ForeignKey(
        Staff,
        on_delete=models.PROTECT,
        related_name="course_assignments",
    )
    assigned_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The three records must all belong to the same center (RULE 6.1)."""
        excluded = set(exclude or ())
        if "course" not in excluded and self.course_id is not None:
            if self.course.organization_id != self.organization_id:
                raise ValidationError(
                    {"course": "That course belongs to another center."}
                )
        if "staff" not in excluded and self.staff_id is not None:
            if self.staff.organization_id != self.organization_id:
                raise ValidationError(
                    {"staff": "That staff member belongs to another center."}
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["course", "staff"],
                name="staff_course_instructor_unique",
            ),
        ]


class CohortInstructor(models.Model):
    """One instructor assigned to one cohort (STAFF-003)."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="cohort_instructors",
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="instructor_assignments",
    )
    staff = models.ForeignKey(
        Staff,
        on_delete=models.PROTECT,
        related_name="cohort_assignments",
    )
    assigned_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """The three records must all belong to the same center (RULE 6.1)."""
        excluded = set(exclude or ())
        if "cohort" not in excluded and self.cohort_id is not None:
            if self.cohort.organization_id != self.organization_id:
                raise ValidationError(
                    {"cohort": "That cohort belongs to another center."}
                )
        if "staff" not in excluded and self.staff_id is not None:
            if self.staff.organization_id != self.organization_id:
                raise ValidationError(
                    {"staff": "That staff member belongs to another center."}
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "staff"],
                name="staff_cohort_instructor_unique",
            ),
        ]
