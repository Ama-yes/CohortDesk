"""Course, Cohort, Enrollment, Session, Attendance and AuditLog of one center.

Every instance hangs directly from one Organization (RULE 6.1: every tenant
business record belongs to exactly one organization, and every read and write
is scoped to it). A cohort names a course of the same organization, and an
enrollment names a cohort and a Student record of the same organization; the
models check that invariant in clean_fields, and the services resolve every
identifier through the caller's own organization before they are used, so a
browser-supplied number can never reach another center's record.

The feature implements the requirements in SRS 5.5 and 5.6 that do not depend
on entities this product does not have yet:

- Course is a reusable offering (ACAD-001) with the descriptive fields ACAD-002
  asks for and an ordered list of modules (ACAD-003). It is archived, never
  hard-deleted, and a cohort holds a PROTECT reference to it, so a course that
  history references cannot be removed (ACAD-005).
- Cohort is a delivery instance of a course with dates, capacity and the
  lifecycle states COH-007 names.
- Enrollment is the distinct record linking one student to one cohort
  (COH-003), carrying enrollment date, status and the agreed fee (COH-004).

Deliberately absent because the entities they reference do not exist yet, and
inventing a text stand-in would misrepresent the product: the default
instructor set a new cohort copies (COH-002, STAFF-003); and reading records
into an academic period for grouping (ACAD-006's reporting grouping), which
the reports module answers when it lands. Program (ACAD-004, COH-001/COH-002's
optional program) and AcademicPeriod (ACAD-006) are the academic structure of
this slice: an optional center-owned grouping of courses, and a named, dated
period a center defines for its own years. Room (SCH-001) is this centre's own
bookable space; SchedulePattern (SCH-002/SCH-003) is the recurring weekly
definition that generates individual Session records; a Session keeps an
optional room, its own status and the pattern it came from (SCH-004/SCH-008).

Validation follows the project's established model hook: every rejection is
raised as {field: message} so a page can mark the row the person typed, and
the hook finishes with Django's own declared-field validation (DECISIONS D34).
"""
import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.accounts.models import CohortUser
from apps.organizations.models import Organization
from apps.students.models import Student

from .statuses import (
    AUDIT_ACTION_CHOICES,
    ATTENDANCE_STATUS_CHOICES,
    COHORT_STATUS_CHOICES,
    COURSE_STATUS_CHOICES,
    ENROLLMENT_STATUS_CHOICES,
    ROOM_ARCHIVED,
    ROOM_STATUS_CHOICES,
    SESSION_CANCELLED,
    SESSION_SCHEDULED,
    SESSION_STATUS_CHOICES,
    WEEKDAY_CODES,
)

CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class Program(models.Model):
    """An optional grouping of one center's courses (ACAD-004).

    A center that does not need programs simply has none, and its courses
    stay usable directly (ACAD-004 says exactly that). The SRS gives programs
    no lifecycle status, so none is invented here.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="programs",
    )
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """ACAD-004 rules, one place; field-keyed so a page can mark the row."""
        excluded = set(exclude or ())
        if "title" not in excluded:
            self.title = str(self.title).strip()
            if not self.title:
                raise ValidationError({"title": "A program must have a title."})
        if "description" not in excluded:
            self.description = str(self.description).strip()
        super().clean_fields(exclude=excluded)


class Course(models.Model):
    """A reusable course of one center (ACAD-001/ACAD-002/ACAD-005)."""
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="courses",
    )
    # Optional grouping of this center's own courses (ACAD-004). PROTECT: a
    # grouped course is never dropped because its program goes.
    program = models.ForeignKey(
        Program,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="courses",
    )
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=80, blank=True)
    level = models.CharField(max_length=60, blank=True)
    duration = models.CharField(max_length=60, blank=True)
    learning_objectives = models.TextField(blank=True)
    prerequisites = models.CharField(max_length=300, blank=True)
    default_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=COURSE_STATUS_CHOICES,
        default="draft",
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """ACAD-002 rules, one place; field-keyed so a page can mark the row."""
        excluded = set(exclude or ())
        if "title" not in excluded:
            self.title = str(self.title).strip()
            if not self.title:
                raise ValidationError({"title": "A course must have a title."})
        for name in ("description", "category", "level", "duration",
                     "learning_objectives", "prerequisites"):
            if name not in excluded:
                setattr(self, name, str(getattr(self, name)).strip())
        if "program" not in excluded and self.program_id is not None:
            if self.program.organization_id != self.organization_id:
                raise ValidationError(
                    {"program": "That program belongs to another center."}
                )
        if "default_fee" not in excluded and self.default_fee is not None:
            if self.default_fee < 0:
                raise ValidationError(
                    {"default_fee": "A default fee cannot be negative."}
                )
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(default_fee__isnull=True)
                | models.Q(default_fee__gte=0),
                name="academics_course_default_fee_not_negative",
            ),
        ]


class CourseModule(models.Model):
    """One ordered topic of a course (ACAD-003)."""
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    title = models.CharField(max_length=150)
    description = models.CharField(max_length=300, blank=True)
    position = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        excluded = set(exclude or ())
        if "title" not in excluded:
            self.title = str(self.title).strip()
            if not self.title:
                raise ValidationError({"title": "A module must have a title."})
        if "description" not in excluded:
            self.description = str(self.description).strip()
        if "position" not in excluded and self.position is not None:
            if self.position < 1:
                raise ValidationError(
                    {"position": "A module position starts at 1."}
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "position"],
                name="academics_course_module_position",
            ),
        ]


class Room(models.Model):
    """One bookable room of this center (SCH-001).

    A room is archived instead of deleted (ROOM_ARCHIVED), exactly as a course
    is (ACAD-005): history that references it keeps its PROTECT reference, but
    an archived room can no longer be chosen for a new session or pattern. The
    capacity is optional and, when supplied, at least one seat; SCH-001 names
    no enforcement rule, so none is invented.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="rooms",
    )
    name = models.CharField(max_length=120)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=16,
        choices=ROOM_STATUS_CHOICES,
        default="active",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """SCH-001 rules, field-keyed so a page can mark the row."""
        excluded = set(exclude or ())
        if "name" not in excluded:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError({"name": "A room must have a name."})
        if "location" not in excluded:
            self.location = str(self.location).strip()
        if "capacity" not in excluded and self.capacity is not None:
            if self.capacity < 1:
                raise ValidationError(
                    {"capacity": "A room capacity must be at least 1."}
                )
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="academics_room_organization_name",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__isnull=True)
                | models.Q(capacity__gte=1),
                name="academics_room_capacity_at_least_one",
            ),
        ]


class Cohort(models.Model):
    """One delivery instance of a course (COH-001/COH-002/COH-007)."""
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="cohorts",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        related_name="cohorts",
    )
    # COH-002's optional program: this centre's own, when one is given at all.
    program = models.ForeignKey(
        Program,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cohorts",
    )
    # COH-002's optional classroom: this centre's own room, when one is named.
    # It is a default only: an archived default is never silently dropped or
    # replaced, and a new session relying on it is refused (SCH-001/SCH-004).
    default_room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cohort_defaults",
    )
    name = models.CharField(max_length=120)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=COHORT_STATUS_CHOICES,
        default="planned",
    )
    note = models.CharField(max_length=300, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """COH-002 rules, including the same-organization course invariant."""
        excluded = set(exclude or ())
        if "name" not in excluded:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError({"name": "A cohort must have a name."})
        if "note" not in excluded:
            self.note = str(self.note).strip()
        if "course" not in excluded and self.course_id is not None:
            if self.course.organization_id != self.organization_id:
                raise ValidationError(
                    {"course": "That course belongs to another center."}
                )
        if "program" not in excluded and self.program_id is not None:
            if self.program.organization_id != self.organization_id:
                raise ValidationError(
                    {"program": "That program belongs to another center."}
                )
        if "default_room" not in excluded and self.default_room_id is not None:
            if self.default_room.organization_id != self.organization_id:
                raise ValidationError(
                    {"default_room": "That room belongs to another center."}
                )
        if "start_date" not in excluded and "end_date" not in excluded:
            if (
                self.start_date is not None
                and self.end_date is not None
                and self.end_date < self.start_date
            ):
                raise ValidationError(
                    {"end_date": "A cohort cannot end before it starts."}
                )
        if "capacity" not in excluded and self.capacity is not None:
            if self.capacity < 1:
                raise ValidationError(
                    {"capacity": "A cohort capacity must be at least 1."}
                )
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(start_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="academics_cohort_end_not_before_start",
            ),
        ]


class SchedulePattern(models.Model):
    """A recurring weekly definition that generates sessions (SCH-002/SCH-003).

    The pattern stores the weekdays in canonical Monday-to-Sunday order, an
    inclusive date range, local wall-clock start and end times and an optional
    room. Generation (services.create_schedule_pattern) turns it into one
    Session per matching date and is transactional; the pattern itself is
    never edited or re-generated by this slice.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="schedule_patterns",
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="schedule_patterns",
    )
    # Comma-separated weekday codes in canonical order, e.g. "mon,wed,fri".
    weekdays = models.CharField(max_length=32)
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="schedule_patterns",
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def weekday_codes(self):
        """The stored weekdays as a canonical list of codes."""
        return [code for code in str(self.weekdays).split(",") if code]

    def clean_fields(self, exclude=None):
        """SCH-002/SCH-003 rules, field-keyed so a page can mark the row."""
        excluded = set(exclude or ())
        if "cohort" not in excluded and self.cohort_id is not None:
            if self.cohort.organization_id != self.organization_id:
                raise ValidationError(
                    {"cohort": "That cohort belongs to another center."}
                )
        if "room" not in excluded and self.room_id is not None:
            if self.room.organization_id != self.organization_id:
                raise ValidationError(
                    {"room": "That room belongs to another center."}
                )
            if self.room.status == ROOM_ARCHIVED:
                raise ValidationError(
                    {"room": "An archived room cannot be scheduled."}
                )
        if "weekdays" not in excluded:
            codes = self.weekday_codes()
            canonical = [
                code for code in WEEKDAY_CODES if code in set(codes)
            ]
            if not codes or len(canonical) != len(set(codes)):
                raise ValidationError(
                    {"weekdays": "Choose at least one valid weekday."}
                )
            self.weekdays = ",".join(canonical)
        if "start_date" not in excluded and "end_date" not in excluded:
            if (
                self.start_date is not None
                and self.end_date is not None
                and self.end_date < self.start_date
            ):
                raise ValidationError(
                    {"end_date": "A pattern cannot end before it starts."}
                )
        if "start_time" not in excluded and "end_time" not in excluded:
            if (
                self.start_time is not None
                and self.end_time is not None
                and self.end_time <= self.start_time
            ):
                raise ValidationError(
                    {"end_time": "A session must end after it starts."}
                )
        if "note" not in excluded:
            self.note = str(self.note).strip()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="academics_pattern_end_not_before_start",
            ),
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="academics_pattern_end_time_after_start",
            ),
        ]


class Enrollment(models.Model):
    """One student's participation in one cohort (COH-003/COH-004)."""
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    status = models.CharField(
        max_length=16,
        choices=ENROLLMENT_STATUS_CHOICES,
        default="enrolled",
    )
    enrolled_at = models.DateTimeField(default=timezone.now)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    agreed_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    # A snapshot of the center's currency at enrollment time (SEC-002/6.7:
    # money is decimal and always carries its explicit currency).
    fee_currency = models.CharField(max_length=3, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """COH-004 rules plus the same-organization invariants (RULE 6.1)."""
        excluded = set(exclude or ())
        if "cohort" not in excluded and self.cohort_id is not None:
            if self.cohort.organization_id != self.organization_id:
                raise ValidationError(
                    {"cohort": "That cohort belongs to another center."}
                )
        if "student" not in excluded and self.student_id is not None:
            if self.student.organization_id != self.organization_id:
                raise ValidationError(
                    {"student": "That student belongs to another center."}
                )
        if "note" not in excluded:
            self.note = str(self.note).strip()
        if "agreed_fee" not in excluded and self.agreed_fee is not None:
            if self.agreed_fee < 0:
                raise ValidationError(
                    {"agreed_fee": "An agreed fee cannot be negative."}
                )
        if "fee_currency" not in excluded:
            self.fee_currency = str(self.fee_currency).strip().upper()
            if self.fee_currency and not CURRENCY_PATTERN.match(
                self.fee_currency
            ):
                raise ValidationError(
                    {"fee_currency": "Currency must be a 3-letter ISO code."}
                )
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "student"],
                name="academics_enrollment_cohort_student",
            ),
            models.CheckConstraint(
                condition=models.Q(agreed_fee__isnull=True)
                | models.Q(agreed_fee__gte=0),
                name="academics_enrollment_fee_not_negative",
            ),
        ]


class AcademicPeriod(models.Model):
    """A configurable academic year or period of one center (ACAD-006).

    ACAD-006 asks that a center can define its own academic years or periods
    for reporting and grouping; this record names and dates one. A period
    contains a record by the record's own stored dates (a cohort's start date,
    and an enrollment through its cohort), computed by apps/academics/
    reporting.py at report time, so no foreign key is duplicated here. The
    containment rule, including open sides and undated records, is documented
    in docs/DECISIONS.md D67.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="academic_periods",
    )
    name = models.CharField(max_length=120)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """ACAD-006 rules, one place; field-keyed so a page can mark the row."""
        excluded = set(exclude or ())
        if "name" not in excluded:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError(
                    {"name": "An academic period must have a name."}
                )
        if "start_date" not in excluded and "end_date" not in excluded:
            if (
                self.start_date is not None
                and self.end_date is not None
                and self.end_date < self.start_date
            ):
                raise ValidationError(
                    {
                        "end_date":
                            "An academic period cannot end before it starts."
                    }
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(start_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="academics_period_end_not_before_start",
            ),
        ]


class Session(models.Model):
    """One scheduled class occurrence of this center (SCH-003/SCH-004/SCH-008).

    The SRS data model lists Session as "Individual scheduled class
    occurrence" and SCH-004 names cohort, start/end date-time, timezone
    context, instructor(s), optional room, status and optional lesson notes.
    This class records the session itself: cohort, start/end (timestamps are
    stored consistently and shown in the center's own context, SCH-009), an
    optional room (SCH-001), its status (exactly scheduled or cancelled,
    SCH-008) and the pattern that generated it, when it came from one.
    Instructor(s) are answered by the cohort's own assignments (STAFF-004);
    nothing here invents a second instructor link.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sessions",
    )
    pattern = models.ForeignKey(
        SchedulePattern,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )
    status = models.CharField(
        max_length=16,
        choices=SESSION_STATUS_CHOICES,
        default=SESSION_SCHEDULED,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """Session rules, one place; field-keyed so a page can mark the row."""
        excluded = set(exclude or ())
        if "cohort" not in excluded and self.cohort_id is not None:
            if self.cohort.organization_id != self.organization_id:
                raise ValidationError(
                    {"cohort": "That cohort belongs to another center."}
                )
        if "room" not in excluded and self.room_id is not None:
            if self.room.organization_id != self.organization_id:
                raise ValidationError(
                    {"room": "That room belongs to another center."}
                )
        if "pattern" not in excluded and self.pattern_id is not None:
            if self.pattern.organization_id != self.organization_id:
                raise ValidationError(
                    {"pattern": "That pattern belongs to another center."}
                )
            if (
                self.cohort_id is not None
                and self.pattern.cohort_id != self.cohort_id
            ):
                raise ValidationError(
                    {"pattern": "That pattern belongs to another cohort."}
                )
        if "start_at" not in excluded and "end_at" not in excluded:
            if (
                self.start_at is not None
                and self.end_at is not None
                and self.end_at < self.start_at
            ):
                raise ValidationError(
                    {
                        "end_at":
                            "A session cannot end before it starts."
                    }
                )
        if "note" not in excluded:
            self.note = str(self.note).strip()
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        if "cancelled_at" not in excluded and self.status != SESSION_CANCELLED:
            self.cancelled_at = None
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__gte=models.F("start_at")),
                name="academics_session_end_not_before_start",
            ),
            models.UniqueConstraint(
                fields=["pattern", "start_at"],
                condition=models.Q(pattern__isnull=False),
                name="academics_session_pattern_start",
            ),
        ]


class Attendance(models.Model):
    """One student's current attendance state for one session (ATT-001..003).

    RULE 6.5 "at most one attendance state exists per student per session"
    is enforced here by a database constraint, not by a service-side check,
    so two records can never exist even through the model API. The status is
    one of the four states ATT-002 names. Eligibility (ATT-008: only an
    enrolled student of the session's cohort) is a service rule because it
    reads the enrollment table; the services keep this record and the
    enrollment consistent and refuse a record for anyone else.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.PROTECT,
        related_name="attendances",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="attendances",
    )
    status = models.CharField(
        max_length=16,
        choices=ATTENDANCE_STATUS_CHOICES,
    )
    recorded_at = models.DateTimeField(default=timezone.now)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """Same-center invariants (RULE 6.1) plus the fixed status set."""
        excluded = set(exclude or ())
        if "session" not in excluded and self.session_id is not None:
            if self.session.organization_id != self.organization_id:
                raise ValidationError(
                    {"session": "That session belongs to another center."}
                )
            if (
                self.student_id is not None
                and self.student.organization_id
                != self.organization_id
            ):
                raise ValidationError(
                    {"student": "That student belongs to another center."}
                )
        if "note" not in excluded:
            self.note = str(self.note).strip()
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student"],
                name="academics_attendance_session_student",
            ),
        ]


class AuditLog(models.Model):
    """One audited sensitive action (data model "Sensitive action history").

    So far the only audited action this product has is an attendance
    correction (RULE 6.5: corrections "must be permission-controlled and
    audited"), and the row names what changed, where and who did it. The
    actor keeps the account itself: PROTECT means deleting an account can
    never erase the history it wrote. New audited actions added later only
    add to the action vocabulary in statuses.py.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        CohortUser,
        on_delete=models.PROTECT,
        related_name="audit_logs",
    )
    action = models.CharField(
        max_length=40,
        choices=AUDIT_ACTION_CHOICES,
    )
    previous_status = models.CharField(
        max_length=16,
        choices=ATTENDANCE_STATUS_CHOICES,
    )
    new_status = models.CharField(
        max_length=16,
        choices=ATTENDANCE_STATUS_CHOICES,
    )
    at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """One center only; statuses kept in the fixed set (ATT-002)."""
        excluded = set(exclude or ())
        if "session" not in excluded and self.session_id is not None:
            if self.session.organization_id != self.organization_id:
                raise ValidationError(
                    {"session": "That session belongs to another center."}
                )
        if "student" not in excluded and self.student_id is not None:
            if self.student.organization_id != self.organization_id:
                raise ValidationError(
                    {"student": "That student belongs to another center."}
                )
        if "previous_status" not in excluded:
            self.previous_status = str(self.previous_status).strip().lower()
        if "new_status" not in excluded:
            self.new_status = str(self.new_status).strip().lower()
        super().clean_fields(exclude=excluded)
