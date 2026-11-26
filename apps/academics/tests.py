"""Model and service tests for one center's courses, cohorts, enrollments,
programs and academic periods.

The rules under test are ACAD-001..ACAD-006 and COH-001..COH-008 as this
product's services implement them: organization-scoped courses (RULE 6.1),
archive instead of deletion (ACAD-005), the cohort lifecycle COH-007 names,
an enrollment record linking one existing student to one cohort (COH-003/
COH-004), the center's capacity policy (COH-005), and status changes that only
move through the service tables (6.3), plus the phase 3B slice: the optional
program grouping (ACAD-004), the cohort-to-program link (COH-001/COH-002) and
the center's own academic periods (ACAD-006). Cross-tenant proof through the URL
layer lives in apps/academics/test_access.py. Plus the phase 3D slice: the
one-off class occurrences a cohort schedules (SCH-002/SCH-003/SCH-004) and the
attendance of one student in one session, with its four states, its eligibility
rule and its audited correction (ATT-001..ATT-008, RULE 6.5).

Every action here is called the way a page calls it (service function, not
model API), because the service layer is the one place the rules live.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)
from apps.staff.models import CohortInstructor, Staff
from apps.students.models import Student
from apps.students.statuses import STATUS_ACTIVE, STATUS_ARCHIVED

from .models import (
    AcademicPeriod,
    Attendance,
    AuditLog,
    Cohort,
    Course,
    CourseModule,
    Enrollment,
    Program,
    Session,
)
from .reporting import (
    CAPACITY_FULL,
    CAPACITY_OPEN,
    CAPACITY_OVER,
    CAPACITY_UNLIMITED,
    attendance_report_rows,
    attendance_totals,
    capacity_state,
    session_report_rows,
)
from .services import (
    ATTENDANCE_ELIGIBLE_STATUSES,
    EDITABLE_SESSION_FIELDS,
    COHORT_ENTRY_STATUSES,
    COHORT_TRANSITIONS,
    COURSE_ENTRY_STATUSES,
    COURSE_TRANSITIONS,
    EDITABLE_COHORT_FIELDS,
    ENROLLMENT_ENTRY_STATUSES,
    ENROLLMENT_TRANSITIONS,
    add_course_module,
    build_report,
    clear_program_course,
    correct_attendance,
    create_cohort,
    create_course,
    create_period,
    create_program,
    create_session,
    edit_cohort,
    edit_course,
    edit_period,
    edit_program,
    edit_session,
    enroll_student,
    record_attendance,
    set_cohort_program,
    set_cohort_status,
    set_course_status,
    set_enrollment_status,
    set_program_course,
)
from .statuses import (
    ATTENDANCE_ABSENT,
    ATTENDANCE_EXCUSED,
    ATTENDANCE_LATE,
    ATTENDANCE_PRESENT,
    ATTENDANCE_STATUSES,
    AUDIT_ATTENDANCE_CORRECTED,
    COHORT_ACTIVE,
    COHORT_CANCELLED,
    COHORT_COMPLETED,
    COHORT_PLANNED,
    COHORT_STATUSES,
    COURSE_ACTIVE,
    COURSE_ARCHIVED,
    COURSE_DRAFT,
    COURSE_STATUSES,
    ENROLLMENT_CANCELLED,
    ENROLLMENT_COMPLETED,
    ENROLLMENT_ENROLLED,
    ENROLLMENT_STATUSES,
    ENROLLMENT_WITHDRAWN,
)


class CenterFixture(TestCase):
    """Two centers, an owner each, and the staff roles under test."""

    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Alpha Center", slug="alpha-center", primary_currency="DZD"
        )
        self.beta = Organization.objects.create(
            name="Beta Center", slug="beta-center", primary_currency="EUR"
        )
        self.owner = CohortUser.objects.create_user(
            username="alpha-owner", password="x", display_name="Alpha Owner"
        )
        self.beta_owner = CohortUser.objects.create_user(
            username="beta-owner", password="x", display_name="Beta Owner"
        )
        Members.objects.create(
            user=self.owner, organization=self.alpha, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.beta_owner, organization=self.beta, role=ROLE_OWNER
        )
        self.coordinator = self.make_user("alpha-coord", ROLE_ACADEMIC_COORDINATOR)
        self.receptionist = self.make_user("alpha-desk", ROLE_RECEPTIONIST)
        self.instructor = self.make_user("alpha-teacher", ROLE_INSTRUCTOR)
        self.finance = self.make_user("alpha-money", ROLE_FINANCE_OFFICER)
        self.alpha_course = self.make_course(self.owner, self.alpha, "Spanish A1")
        self.beta_course = self.make_course(
            self.beta_owner, self.beta, "Beta Course"
        )
        self.student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-1",
            name="Alpha Learner",
            contact="alpha@example.test",
            status=STATUS_ACTIVE,
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            student_number="BC-1",
            name="Beta Learner",
            contact="beta@example.test",
            status=STATUS_ACTIVE,
        )

    def make_user(self, username, role, organization=None):
        user = CohortUser.objects.create_user(username=username, password="x")
        Members.objects.create(
            user=user, organization=organization or self.alpha, role=role
        )
        return user

    def make_course(self, user, organization, title, **kwargs):
        record, problems, notes = create_course(
            user, organization, title, **kwargs
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def make_cohort(self, course=None, name="Autumn A", user=None, **kwargs):
        record, problems, notes = create_cohort(
            user or self.owner,
            self.alpha,
            name,
            (course or self.alpha_course).pk,
            **kwargs
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def make_student(self, number, name="Another Learner", status=STATUS_ACTIVE):
        return Student.objects.create(
            organization=self.alpha,
            student_number=number,
            name=name,
            status=status,
        )


class VocabularyTests(TestCase):
    """ACAD-005/COH-007/COH-004: the vocabularies are fixed, not free text."""

    def test_course_vocabulary(self):
        self.assertEqual(
            COURSE_STATUSES, (COURSE_DRAFT, COURSE_ACTIVE, COURSE_ARCHIVED)
        )

    def test_cohort_vocabulary(self):
        self.assertEqual(
            COHORT_STATUSES,
            (COHORT_PLANNED, COHORT_ACTIVE, COHORT_COMPLETED, COHORT_CANCELLED),
        )

    def test_enrollment_vocabulary(self):
        self.assertEqual(
            ENROLLMENT_STATUSES,
            (
                ENROLLMENT_ENROLLED,
                ENROLLMENT_COMPLETED,
                ENROLLMENT_WITHDRAWN,
                ENROLLMENT_CANCELLED,
            ),
        )

    def test_archived_course_is_terminal(self):
        self.assertEqual(COURSE_TRANSITIONS[COURSE_ARCHIVED], ())
        self.assertNotIn(COURSE_ARCHIVED, COURSE_ENTRY_STATUSES)

    def test_cohort_lifecycle_ends_after_completion(self):
        self.assertEqual(
            COHORT_TRANSITIONS[COHORT_PLANNED],
            (COHORT_ACTIVE, COHORT_CANCELLED),
        )
        self.assertEqual(
            COHORT_TRANSITIONS[COHORT_ACTIVE],
            (COHORT_COMPLETED, COHORT_CANCELLED),
        )
        self.assertEqual(COHORT_TRANSITIONS[COHORT_COMPLETED], ())
        self.assertEqual(COHORT_TRANSITIONS[COHORT_CANCELLED], ())
        self.assertNotIn(COHORT_COMPLETED, COHORT_ENTRY_STATUSES)

    def test_enrollment_enters_enrolled_only(self):
        self.assertEqual(ENROLLMENT_ENTRY_STATUSES, (ENROLLMENT_ENROLLED,))
        self.assertEqual(ENROLLMENT_TRANSITIONS[ENROLLMENT_COMPLETED], ())
        self.assertEqual(ENROLLMENT_TRANSITIONS[ENROLLMENT_CANCELLED], ())
        self.assertEqual(
            ENROLLMENT_TRANSITIONS[ENROLLMENT_WITHDRAWN],
            (ENROLLMENT_ENROLLED,),
        )


class CourseTests(CenterFixture):
    """ACAD-001/ACAD-002/ACAD-005: a reusable course that is archived, not deleted."""

    def test_course_starts_as_draft_and_is_trimmed(self):
        record, problems, notes = create_course(
            self.owner,
            self.alpha,
            "  French A2  ",
            description="  Everyday French  ",
            category=" Languages ",
            level=" A2 ",
            duration=" 12 weeks ",
            learning_objectives=" Speak with confidence. ",
            prerequisites=" French A1 ",
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual(record.status, COURSE_DRAFT)
        self.assertEqual(record.title, "French A2")
        self.assertEqual(record.description, "Everyday French")
        self.assertEqual(record.category, "Languages")
        self.assertEqual(record.level, "A2")
        self.assertEqual(record.duration, "12 weeks")
        self.assertEqual(record.learning_objectives, "Speak with confidence.")
        self.assertEqual(record.prerequisites, "French A1")
        self.assertEqual(record.organization, self.alpha)

    def test_course_can_start_active(self):
        record, problems, notes = create_course(
            self.owner, self.alpha, "Open Course", status=COURSE_ACTIVE
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, COURSE_ACTIVE)

    def test_archived_is_not_an_entry_status(self):
        record, problems, notes = create_course(
            self.owner, self.alpha, "Old Course", status=COURSE_ARCHIVED
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["entry"])
        self.assertFalse(Course.objects.filter(title="Old Course").exists())

    def test_title_is_required(self):
        record, problems, notes = create_course(
            self.owner, self.alpha, "   "
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "title")

    def test_default_fee_is_decimal_and_cannot_be_negative(self):
        record, problems, notes = create_course(
            self.owner,
            self.alpha,
            "Paid Course",
            default_fee=Decimal("150.00"),
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.default_fee, Decimal("150.00"))
        record, problems, notes = create_course(
            self.owner,
            self.alpha,
            "Bad Fee",
            default_fee=Decimal("-1.00"),
        )
        self.assertIsNone(record)
        self.assertEqual(problems[0][0], "default_fee")

    def test_database_rejects_a_negative_fee(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Course.objects.create(
                    organization=self.alpha,
                    title="Database Bad Fee",
                    default_fee=Decimal("-0.01"),
                )

    def test_edit_changes_one_field_only(self):
        record, problems, notes = edit_course(
            self.owner, self.alpha, self.alpha_course.pk, "level", " B1 "
        )
        self.assertEqual((problems, notes), ([], []))
        record.refresh_from_db()
        self.assertEqual(record.level, "B1")
        self.assertEqual(record.title, "Spanish A1")

    def test_edit_rejects_an_unlistable_field(self):
        record, problems, notes = edit_course(
            self.owner, self.alpha, self.alpha_course.pk, "status", "active"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["field_not_editable"])
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.status, COURSE_DRAFT)

    def test_edit_rejects_a_blank_title_and_changes_nothing(self):
        record, problems, notes = edit_course(
            self.owner, self.alpha, self.alpha_course.pk, "title", "  "
        )
        self.assertIsNone(record)
        self.assertEqual(problems[0][0], "title")
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.title, "Spanish A1")

    def test_edit_unknown_course_is_not_found(self):
        record, problems, notes = edit_course(
            self.owner, self.alpha, 99999, "title", "Ghost"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])

    def test_archive_sets_the_status_and_the_moment(self):
        record, problems, notes = set_course_status(
            self.owner, self.alpha, self.alpha_course.pk, COURSE_ARCHIVED
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, COURSE_ARCHIVED)
        self.assertIsNotNone(record.archived_at)
        self.assertIsNotNone(record.status_changed_at)

    def test_archived_course_cannot_return_to_active(self):
        set_course_status(
            self.owner, self.alpha, self.alpha_course.pk, COURSE_ARCHIVED
        )
        record, problems, notes = set_course_status(
            self.owner, self.alpha, self.alpha_course.pk, COURSE_ACTIVE
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.status, COURSE_ARCHIVED)

    def test_modules_are_ordered_and_appended(self):
        first, problems, notes = add_course_module(
            self.owner, self.alpha, self.alpha_course.pk, "Greetings", "Hello"
        )
        self.assertEqual((problems, notes), ([], []))
        second, problems, notes = add_course_module(
            self.owner, self.alpha, self.alpha_course.pk, "Numbers"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(first.position, 1)
        self.assertEqual(second.position, 2)
        self.assertEqual(
            [module.title for module in self.alpha_course.modules.all()],
            ["Greetings", "Numbers"],
        )

    def test_module_needs_a_title(self):
        module, problems, notes = add_course_module(
            self.owner, self.alpha, self.alpha_course.pk, "   "
        )
        self.assertIsNone(module)
        self.assertEqual(problems[0][0], "title")
        self.assertEqual(CourseModule.objects.count(), 0)

    def test_database_rejects_two_modules_at_one_position(self):
        CourseModule.objects.create(
            course=self.alpha_course, title="One", position=1
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CourseModule.objects.create(
                    course=self.alpha_course, title="Two", position=1
                )

    def test_course_permissions(self):
        with self.assertRaises(PermissionDenied):
            create_course(self.receptionist, self.alpha, "Not Allowed")
        with self.assertRaises(PermissionDenied):
            create_course(self.instructor, self.alpha, "Not Allowed")
        with self.assertRaises(PermissionDenied):
            set_course_status(
                self.instructor, self.alpha, self.alpha_course.pk, COURSE_ACTIVE
            )
        record, problems, notes = create_course(
            self.coordinator, self.alpha, "Coordinator Course"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.organization, self.alpha)

    def test_another_centres_course_is_not_here(self):
        record, problems, notes = edit_course(
            self.owner, self.alpha, self.beta_course.pk, "title", "Stolen"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])
        self.beta_course.refresh_from_db()
        self.assertEqual(self.beta_course.title, "Beta Course")

    def test_model_rejects_a_cohort_course_of_another_center(self):
        record = Cohort(
            organization=self.alpha,
            course=self.beta_course,
            name="Cross Tenant",
        )
        with self.assertRaises(ValidationError) as caught:
            record.full_clean()
        self.assertIn("course", caught.exception.message_dict)


class CohortTests(CenterFixture):
    """COH-001/COH-002/COH-007: a cohort of one course, on its own lifecycle."""

    def test_cohort_is_made_from_this_centres_course(self):
        record, problems, notes = create_cohort(
            self.owner,
            self.alpha,
            "  Autumn Intake ",
            self.alpha_course.pk,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 20),
            capacity=20,
            note=" Evenings ",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.name, "Autumn Intake")
        self.assertEqual(record.course, self.alpha_course)
        self.assertEqual(record.organization, self.alpha)
        self.assertEqual(record.status, COHORT_PLANNED)
        self.assertEqual(record.capacity, 20)
        self.assertEqual(record.note, "Evenings")

    def test_cohort_can_start_active(self):
        record, problems, notes = create_cohort(
            self.owner,
            self.alpha,
            "Running Intake",
            self.alpha_course.pk,
            status=COHORT_ACTIVE,
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, COHORT_ACTIVE)

    def test_end_before_start_is_rejected(self):
        record, problems, notes = create_cohort(
            self.owner,
            self.alpha,
            "Backwards",
            self.alpha_course.pk,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 8, 1),
        )
        self.assertIsNone(record)
        self.assertEqual(problems[0][0], "end_date")
        self.assertEqual(Cohort.objects.count(), 0)

    def test_capacity_must_be_at_least_one(self):
        record, problems, notes = create_cohort(
            self.owner,
            self.alpha,
            "Zero Seats",
            self.alpha_course.pk,
            capacity=0,
        )
        self.assertIsNone(record)
        self.assertEqual(problems[0][0], "capacity")

    def test_database_rejects_end_before_start(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cohort.objects.create(
                    organization=self.alpha,
                    course=self.alpha_course,
                    name="Database Backwards",
                    start_date=date(2026, 9, 1),
                    end_date=date(2026, 8, 1),
                )

    def test_course_must_belong_to_this_center(self):
        record, problems, notes = create_cohort(
            self.owner, self.alpha, "Stolen Course", self.beta_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["course_not_found"])
        self.assertEqual(Cohort.objects.count(), 0)

    def test_archived_course_does_not_take_new_cohorts(self):
        set_course_status(
            self.owner, self.alpha, self.alpha_course.pk, COURSE_ARCHIVED
        )
        record, problems, notes = create_cohort(
            self.owner, self.alpha, "Late Intake", self.alpha_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["course_archived"])

    def test_edit_changes_one_description_field(self):
        cohort = self.make_cohort()
        record, problems, notes = edit_cohort(
            self.owner, self.alpha, cohort.pk, "capacity", 30
        )
        self.assertEqual((problems, notes), ([], []))
        record.refresh_from_db()
        self.assertEqual(record.capacity, 30)
        record, problems, notes = edit_cohort(
            self.owner,
            self.alpha,
            cohort.pk,
            "start_date",
            date(2026, 10, 1),
        )
        self.assertEqual((problems, notes), ([], []))
        record.refresh_from_db()
        self.assertEqual(record.start_date, date(2026, 10, 1))

    def test_course_is_not_editable_on_an_existing_cohort(self):
        cohort = self.make_cohort()
        record, problems, notes = edit_cohort(
            self.owner, self.alpha, cohort.pk, "course", self.beta_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["field_not_editable"])
        self.assertNotIn("course", EDITABLE_COHORT_FIELDS)

    def test_status_lifecycle_planned_active_completed(self):
        cohort = self.make_cohort()
        for target in (COHORT_ACTIVE, COHORT_COMPLETED):
            record, problems, notes = set_cohort_status(
                self.owner, self.alpha, cohort.pk, target
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertEqual(record.status, target)
        record, problems, notes = set_cohort_status(
            self.owner, self.alpha, cohort.pk, COHORT_ACTIVE
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])

    def test_cancelled_cohort_is_terminal(self):
        cohort = self.make_cohort()
        set_cohort_status(
            self.owner, self.alpha, cohort.pk, COHORT_CANCELLED
        )
        record, problems, notes = set_cohort_status(
            self.owner, self.alpha, cohort.pk, COHORT_ACTIVE
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])

    def test_completed_is_not_an_entry_status(self):
        record, problems, notes = create_cohort(
            self.owner,
            self.alpha,
            "Already Done",
            self.alpha_course.pk,
            status=COHORT_COMPLETED,
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["entry"])

    def test_cohort_permissions(self):
        with self.assertRaises(PermissionDenied):
            create_cohort(
                self.receptionist,
                self.alpha,
                "Front Desk Cohort",
                self.alpha_course.pk,
            )
        with self.assertRaises(PermissionDenied):
            set_cohort_status(
                self.receptionist, self.alpha, 1, COHORT_ACTIVE
            )
        record, problems, notes = create_cohort(
            self.coordinator,
            self.alpha,
            "Coordinator Cohort",
            self.alpha_course.pk,
        )
        self.assertEqual((problems, notes), ([], []))

    def test_another_centres_cohort_is_not_here(self):
        beta_cohort, problems, notes = create_cohort(
            self.beta_owner,
            self.beta,
            "Beta Intake",
            self.beta_course.pk,
        )
        self.assertEqual((problems, notes), ([], []))
        record, problems, notes = set_cohort_status(
            self.owner, self.alpha, beta_cohort.pk, COHORT_ACTIVE
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])
        beta_cohort.refresh_from_db()
        self.assertEqual(beta_cohort.status, COHORT_PLANNED)


class EnrollmentTests(CenterFixture):
    """COH-003/COH-004/COH-005/6.3: existing student, one cohort, one record."""

    def setUp(self):
        super().setUp()
        self.cohort = self.make_cohort()

    def enroll(self, cohort=None, student=None, **kwargs):
        return enroll_student(
            self.owner,
            self.alpha,
            (cohort or self.cohort).pk,
            (student or self.student).student_number,
            **kwargs
        )

    def test_enroll_existing_student_carries_the_course_default_fee(self):
        self.alpha_course.default_fee = Decimal("120.00")
        self.alpha_course.save(update_fields=["default_fee"])
        record, problems, notes = self.enroll()
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.organization, self.alpha)
        self.assertEqual(record.student, self.student)
        self.assertEqual(record.cohort, self.cohort)
        self.assertEqual(record.status, ENROLLMENT_ENROLLED)
        self.assertIsNotNone(record.enrolled_at)
        self.assertEqual(record.agreed_fee, Decimal("120.00"))
        self.assertEqual(record.fee_currency, "DZD")

    def test_enroll_explicit_fee_keeps_its_currency_snapshot(self):
        record, problems, notes = self.enroll(
            agreed_fee=Decimal("50.00"), fee_currency=" eur "
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.agreed_fee, Decimal("50.00"))
        self.assertEqual(record.fee_currency, "EUR")

    def test_no_fee_and_no_course_default_stores_no_money(self):
        record, problems, notes = self.enroll()
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNone(record.agreed_fee)
        self.assertEqual(record.fee_currency, "")

    def test_student_must_belong_to_this_center(self):
        record, problems, notes = self.enroll(student=self.beta_student)
        self.assertIsNone(record)
        self.assertEqual(notes, ["student_not_found"])
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_cohort_must_belong_to_this_center(self):
        beta_cohort, problems, notes = create_cohort(
            self.beta_owner,
            self.beta,
            "Beta Intake",
            self.beta_course.pk,
        )
        self.assertEqual((problems, notes), ([], []))
        record, problems, notes = enroll_student(
            self.owner,
            self.alpha,
            beta_cohort.pk,
            self.student.student_number,
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["cohort_not_found"])
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_archived_student_cannot_be_enrolled(self):
        archived = self.make_student("AC-9", status="archived")
        record, problems, notes = self.enroll(student=archived)
        self.assertIsNone(record)
        self.assertEqual(notes, ["student_archived"])

    def test_closed_cohort_takes_no_enrollment(self):
        set_cohort_status(
            self.owner, self.alpha, self.cohort.pk, COHORT_ACTIVE
        )
        set_cohort_status(
            self.owner, self.alpha, self.cohort.pk, COHORT_COMPLETED
        )
        record, problems, notes = self.enroll()
        self.assertIsNone(record)
        self.assertEqual(notes, ["cohort_closed"])

    def test_duplicate_enrollment_is_refused(self):
        self.enroll()
        record, problems, notes = self.enroll()
        self.assertIsNone(record)
        self.assertEqual(notes, ["already_enrolled"])
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_database_rejects_a_duplicate_enrollment(self):
        self.enroll()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Enrollment.objects.create(
                    organization=self.alpha,
                    cohort=self.cohort,
                    student=self.student,
                )

    def test_capacity_prevents_the_extra_enrollment(self):
        self.cohort.capacity = 1
        self.cohort.save(update_fields=["capacity"])
        second = self.make_student("AC-2", "Second Learner")
        self.enroll()
        record, problems, notes = self.enroll(student=second)
        self.assertIsNone(record)
        self.assertEqual(notes, ["capacity_full"])
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_capacity_warns_and_saves_when_the_center_says_so(self):
        self.alpha.capacity_policy = "warn"
        self.alpha.save(update_fields=["capacity_policy"])
        self.cohort.capacity = 1
        self.cohort.save(update_fields=["capacity"])
        second = self.make_student("AC-2", "Second Learner")
        self.enroll()
        record, problems, notes = self.enroll(student=second)
        self.assertIsNotNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["capacity_warning"])
        self.assertEqual(Enrollment.objects.count(), 2)

    def test_completed_enrollment_frees_the_seat(self):
        self.cohort.capacity = 1
        self.cohort.save(update_fields=["capacity"])
        first, problems, notes = self.enroll()
        set_enrollment_status(
            self.owner, self.alpha, first.pk, ENROLLMENT_COMPLETED
        )
        second = self.make_student("AC-2", "Second Learner")
        record, problems, notes = self.enroll(student=second)
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)

    def test_withdrawn_student_is_reinstated_on_the_same_record(self):
        record, problems, notes = self.enroll()
        moved, problems, notes = set_enrollment_status(
            self.owner, self.alpha, record.pk, ENROLLMENT_WITHDRAWN
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(moved.status_changed_at)
        moved, problems, notes = set_enrollment_status(
            self.owner, self.alpha, record.pk, ENROLLMENT_ENROLLED
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(moved.status, ENROLLMENT_ENROLLED)
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_reinstatement_is_capacity_checked(self):
        self.cohort.capacity = 1
        self.cohort.save(update_fields=["capacity"])
        first, problems, notes = self.enroll()
        set_enrollment_status(
            self.owner, self.alpha, first.pk, ENROLLMENT_WITHDRAWN
        )
        second = self.make_student("AC-2", "Second Learner")
        record, problems, notes = self.enroll(student=second)
        self.assertEqual((problems, notes), ([], []))
        moved, problems, notes = set_enrollment_status(
            self.owner, self.alpha, first.pk, ENROLLMENT_ENROLLED
        )
        self.assertIsNone(moved)
        self.assertEqual(notes, ["capacity_full"])
        first.refresh_from_db()
        self.assertEqual(first.status, ENROLLMENT_WITHDRAWN)

    def test_completed_and_cancelled_enrollments_are_terminal(self):
        record, problems, notes = self.enroll()
        set_enrollment_status(
            self.owner, self.alpha, record.pk, ENROLLMENT_COMPLETED
        )
        moved, problems, notes = set_enrollment_status(
            self.owner, self.alpha, record.pk, ENROLLMENT_CANCELLED
        )
        self.assertIsNone(moved)
        self.assertEqual(notes, ["transition"])
        record.refresh_from_db()
        self.assertEqual(record.status, ENROLLMENT_COMPLETED)

    def test_unknown_enrollment_is_not_found(self):
        record, problems, notes = set_enrollment_status(
            self.owner, self.alpha, 99999, ENROLLMENT_COMPLETED
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])

    def test_another_centres_enrollment_is_not_here(self):
        beta_cohort, problems, notes = create_cohort(
            self.beta_owner,
            self.beta,
            "Beta Intake",
            self.beta_course.pk,
        )
        beta_record, problems, notes = enroll_student(
            self.beta_owner,
            self.beta,
            beta_cohort.pk,
            self.beta_student.student_number,
        )
        self.assertEqual((problems, notes), ([], []))
        record, problems, notes = set_enrollment_status(
            self.owner, self.alpha, beta_record.pk, ENROLLMENT_COMPLETED
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])
        beta_record.refresh_from_db()
        self.assertEqual(beta_record.status, ENROLLMENT_ENROLLED)

    def test_negative_fee_is_rejected(self):
        record, problems, notes = self.enroll(agreed_fee=Decimal("-5.00"))
        self.assertIsNone(record)
        self.assertEqual(problems[0][0], "agreed_fee")
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_enrollment_permissions(self):
        self.assertIsNotNone(self.enroll()[0])
        with self.assertRaises(PermissionDenied):
            enroll_student(
                self.instructor,
                self.alpha,
                self.cohort.pk,
                self.student.student_number,
            )
        with self.assertRaises(PermissionDenied):
            enroll_student(
                self.finance,
                self.alpha,
                self.cohort.pk,
                self.student.student_number,
            )

    def test_front_desk_can_enroll_but_not_change_a_cohort(self):
        record, problems, notes = enroll_student(
            self.receptionist,
            self.alpha,
            self.cohort.pk,
            self.student.student_number,
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)
        with self.assertRaises(PermissionDenied):
            create_cohort(
                self.receptionist,
                self.alpha,
                "Front Desk Cohort",
                self.alpha_course.pk,
            )


class ProgramTests(CenterFixture):
    """ACAD-004: the optional grouping of this center's own courses."""

    def test_program_is_made_and_its_words_are_trimmed(self):
        record, problems, notes = create_program(
            self.owner, self.alpha, "  Literacy Basics  ", "  Reading only.  "
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.title, "Literacy Basics")
        self.assertEqual(record.description, "Reading only.")

    def test_blank_title_is_refused(self):
        record, problems, notes = create_program(self.owner, self.alpha, "   ")
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(
            problems, [("title", "A program must have a title.")]
        )

    def test_title_over_the_limit_is_refused(self):
        record, problems, notes = create_program(self.owner, self.alpha, "x" * 151)
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "title")
        self.assertEqual(self.alpha.programs.count(), 0)

    def test_desk_instructor_and_finance_may_not_manage_programs(self):
        for user in (self.receptionist, self.instructor, self.finance):
            with self.assertRaises(PermissionDenied):
                create_program(user, self.alpha, "Not Theirs Program")
        self.assertEqual(self.alpha.programs.count(), 0)

    def test_program_edit_changes_one_part(self):
        program, _, _ = create_program(
            self.owner, self.alpha, "Literacy", "Old words"
        )
        record, problems, notes = edit_program(
            self.owner, self.alpha, program.pk, "title", " Numeracy "
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.title, "Numeracy")
        self.assertEqual(record.description, "Old words")

    def test_edit_of_an_unknown_program_is_a_note(self):
        record, problems, notes = edit_program(
            self.owner, self.alpha, 9999, "title", "Nothing"
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["not_found"])

    def test_edit_of_a_part_the_page_does_not_offer_is_a_note(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        record, problems, notes = edit_program(
            self.owner, self.alpha, program.pk, "organization", "Elsewhere"
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["field_not_editable"])

    def test_edit_to_a_blank_title_is_refused(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        record, problems, notes = edit_program(
            self.owner, self.alpha, program.pk, "title", "  "
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(
            problems, [("title", "A program must have a title.")]
        )
        program.refresh_from_db()
        self.assertEqual(program.title, "Literacy")

    def test_course_is_put_into_a_program(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        record, problems, notes = set_program_course(
            self.owner, self.alpha, program.pk, self.alpha_course.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.program_id, program.pk)
        self.assertEqual(program.courses.count(), 1)

    def test_other_centres_course_is_not_found(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        record, problems, notes = set_program_course(
            self.owner, self.alpha, program.pk, self.beta_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["course_not_found"])
        self.beta_course.refresh_from_db()
        self.assertIsNone(self.beta_course.program_id)

    def test_unknown_course_is_not_found(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        record, problems, notes = set_program_course(
            self.owner, self.alpha, program.pk, 9999
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["course_not_found"])

    def test_already_grouped_is_a_note(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        set_program_course(
            self.owner, self.alpha, program.pk, self.alpha_course.pk
        )
        record, problems, notes = set_program_course(
            self.owner, self.alpha, program.pk, self.alpha_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["already_in_program"])

    def test_course_moves_from_one_program_to_another(self):
        first, _, _ = create_program(self.owner, self.alpha, "Literacy")
        second, _, _ = create_program(self.owner, self.alpha, "Grammar")
        set_program_course(
            self.owner, self.alpha, first.pk, self.alpha_course.pk
        )
        set_program_course(
            self.owner, self.alpha, second.pk, self.alpha_course.pk
        )
        self.assertEqual(first.courses.count(), 0)
        self.assertEqual(second.courses.count(), 1)

    def test_unassign_takes_the_course_out_of_the_grouping(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        set_program_course(
            self.owner, self.alpha, program.pk, self.alpha_course.pk
        )
        record, problems, notes = clear_program_course(
            self.owner, self.alpha, self.alpha_course.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)
        self.assertIsNone(record.program_id)

    def test_unassign_of_an_unassigned_course_is_a_note(self):
        record, problems, notes = clear_program_course(
            self.owner, self.alpha, self.alpha_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["not_in_program"])

    def test_model_rejects_a_course_of_another_centres_program(self):
        beta_program = Program.objects.create(
            organization=self.beta, title="Beta Program"
        )
        record = Course(
            organization=self.alpha,
            title="Cross Tenant",
            program=beta_program,
        )
        with self.assertRaises(ValidationError) as caught:
            record.full_clean()
        self.assertIn("program", caught.exception.message_dict)

    def test_protected_program_still_holds_while_a_course_uses_it(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        set_program_course(
            self.owner, self.alpha, program.pk, self.alpha_course.pk
        )
        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                program.delete()
        program.refresh_from_db()
        self.alpha_course.refresh_from_db()
        self.assertEqual(self.alpha_course.program_id, program.pk)


class CohortProgramTests(CenterFixture):
    """COH-001/COH-002: a delivery can sit in this center's program."""

    def test_cohort_can_be_made_inside_a_program(self):
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        record, problems, notes = create_cohort(
            self.owner,
            self.alpha,
            "Autumn Literacy",
            self.alpha_course.pk,
            program_number=program.pk,
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.program_id, program.pk)

    def test_other_centres_program_is_not_found_at_creation(self):
        beta_program = Program.objects.create(
            organization=self.beta, title="Beta Program"
        )
        record, problems, notes = create_cohort(
            self.owner,
            self.alpha,
            "Crossed Cohort",
            self.alpha_course.pk,
            program_number=beta_program.pk,
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["program_not_found"])
        self.assertEqual(self.alpha.cohorts.count(), 0)

    def test_existing_cohort_can_be_linked(self):
        cohort = self.make_cohort(name="Autumn A")
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        record, problems, notes = set_cohort_program(
            self.owner, self.alpha, cohort.pk, program.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.program_id, program.pk)

    def test_unknown_program_is_a_note(self):
        cohort = self.make_cohort(name="Autumn A")
        record, problems, notes = set_cohort_program(
            self.owner, self.alpha, cohort.pk, 9999
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["program_not_found"])

    def test_already_linked_is_a_note(self):
        cohort = self.make_cohort(name="Autumn A")
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        set_cohort_program(self.owner, self.alpha, cohort.pk, program.pk)
        record, problems, notes = set_cohort_program(
            self.owner, self.alpha, cohort.pk, program.pk
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["already_linked_program"])

    def test_a_blank_program_number_removes_the_link(self):
        cohort = self.make_cohort(name="Autumn A")
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        set_cohort_program(self.owner, self.alpha, cohort.pk, program.pk)
        record, problems, notes = set_cohort_program(
            self.owner, self.alpha, cohort.pk, ""
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNone(record.program_id)

    def test_removing_a_link_that_is_not_there_is_a_note(self):
        cohort = self.make_cohort(name="Autumn A")
        record, problems, notes = set_cohort_program(
            self.owner, self.alpha, cohort.pk, ""
        )
        self.assertIsNone(record)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["no_program_linked"])

    def test_finance_and_instructor_may_not_link_and_the_coordinator_may(self):
        cohort = self.make_cohort(name="Autumn A")
        program, _, _ = create_program(self.owner, self.alpha, "Literacy")
        for user in (self.finance, self.instructor):
            with self.assertRaises(PermissionDenied):
                set_cohort_program(user, self.alpha, cohort.pk, program.pk)
        second = self.make_cohort(name="Winter A")
        record, problems, notes = set_cohort_program(
            self.coordinator, self.alpha, second.pk, program.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.program_id, program.pk)

    def test_model_rejects_a_cohort_of_another_centres_program(self):
        beta_program = Program.objects.create(
            organization=self.beta, title="Beta Program"
        )
        record = Cohort(
            organization=self.alpha,
            course=self.alpha_course,
            program=beta_program,
            name="Cross Tenant",
        )
        with self.assertRaises(ValidationError) as caught:
            record.full_clean()
        self.assertIn("program", caught.exception.message_dict)


class PeriodTests(CenterFixture):
    """ACAD-006: this center's own academic years and periods."""

    def test_period_is_made_named_and_trimmed(self):
        record, problems, notes = create_period(
            self.owner,
            self.alpha,
            "  First Term 2026  ",
            date(2026, 1, 12),
            date(2026, 4, 30),
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.name, "First Term 2026")
        self.assertEqual(record.start_date, date(2026, 1, 12))
        self.assertEqual(record.end_date, date(2026, 4, 30))

    def test_period_without_dates_is_allowed(self):
        record, problems, notes = create_period(
            self.owner, self.alpha, "Whole Year"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNone(record.start_date)
        self.assertIsNone(record.end_date)

    def test_blank_name_is_refused(self):
        record, problems, notes = create_period(self.owner, self.alpha, "   ")
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(
            problems,
            [("name", "An academic period must have a name.")],
        )

    def test_end_before_start_is_refused(self):
        record, problems, notes = create_period(
            self.owner,
            self.alpha,
            "Backwards",
            date(2026, 3, 1),
            date(2026, 1, 2),
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "end_date")
        self.assertEqual(
            problems[0][1],
            "An academic period cannot end before it starts.",
        )

    def test_database_still_rejects_a_period_that_ends_before_it_starts(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicPeriod.objects.create(
                    organization=self.alpha,
                    name="Database Bad",
                    start_date=date(2026, 3, 1),
                    end_date=date(2026, 1, 2),
                )

    def test_period_edit_changes_one_part(self):
        period, _, _ = create_period(
            self.owner,
            self.alpha,
            "First Term",
            date(2026, 1, 12),
            date(2026, 4, 30),
        )
        record, problems, notes = edit_period(
            self.owner, self.alpha, period.pk, "start_date", date(2026, 1, 5)
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.start_date, date(2026, 1, 5))
        self.assertEqual(record.end_date, date(2026, 4, 30))

    def test_edit_cannot_make_the_dates_cross(self):
        period, _, _ = create_period(
            self.owner,
            self.alpha,
            "First Term",
            date(2026, 1, 12),
            date(2026, 4, 30),
        )
        record, problems, notes = edit_period(
            self.owner, self.alpha, period.pk, "start_date", date(2026, 5, 1)
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "end_date")
        period.refresh_from_db()
        self.assertEqual(period.start_date, date(2026, 1, 12))

    def test_edit_of_an_unknown_period_or_unoffered_part_is_a_note(self):
        record, problems, notes = edit_period(
            self.owner, self.alpha, 9999, "name", "Nothing"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["not_found"]))
        period, _, _ = create_period(self.owner, self.alpha, "First Term")
        record, problems, notes = edit_period(
            self.owner, self.alpha, period.pk, "created_at", "Elsewhere"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["field_not_editable"]))

    def test_edit_name_to_blank_is_refused(self):
        period, _, _ = create_period(self.owner, self.alpha, "First Term")
        record, problems, notes = edit_period(
            self.owner, self.alpha, period.pk, "name", "  "
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "name")
        period.refresh_from_db()
        self.assertEqual(period.name, "First Term")

    def test_only_owner_and_admin_may_move_periods(self):
        for user in (
            self.coordinator,
            self.receptionist,
            self.instructor,
            self.finance,
        ):
            with self.assertRaises(PermissionDenied):
                create_period(user, self.alpha, "Not Theirs Term")
        period, _, _ = create_period(
            self.owner,
            self.alpha,
            "First Term",
            date(2026, 1, 12),
            date(2026, 4, 30),
        )
        for user in (self.coordinator, self.receptionist, self.instructor,
                     self.finance):
            with self.assertRaises(PermissionDenied):
                edit_period(
                    user,
                    self.alpha,
                    period.pk,
                    "start_date",
                    date(2026, 2, 1),
                )
        self.assertEqual(self.alpha.academic_periods.count(), 1)


class ReportingTests(CenterFixture):
    """ACAD-006/REP-003/REP-004: periods group records by their own dates.

    A cohort is inside a period when its start date falls inside it, both
    ends included; an enrollment is inside the period of its own cohort
    (RULE 6.3). The calculations are exercised here without any HTML.
    """

    def setUp(self):
        super().setUp()
        self.period, problems, notes = create_period(
            self.owner,
            self.alpha,
            "Year 2026",
            date(2026, 1, 1),
            date(2026, 12, 31),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.january = self.make_cohort(
            name="January",
            start_date=date(2026, 1, 1),
            capacity=2,
        )
        self.december = self.make_cohort(
            name="December",
            start_date=date(2026, 12, 31),
            capacity=1,
        )
        self.before = self.make_cohort(
            name="Before", start_date=date(2025, 12, 31)
        )
        self.undated = self.make_cohort(name="Undated")
        self.student_b = self.make_student("AC-2", "Second Learner")
        self.student_c = self.make_student("AC-3", "Third Learner")
        self.student_d = self.make_student("AC-4", "Fourth Learner")
        self.january_enrollments = [
            self.enroll(self.january, self.student),
            self.enroll(self.january, self.student_b),
        ]
        self.december_completed = self.enroll(self.december, self.student_d)
        record, problems, notes = set_enrollment_status(
            self.owner,
            self.alpha,
            self.december_completed.pk,
            ENROLLMENT_COMPLETED,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        # The completed enrollment freed the seat, so the second one fits.
        self.december_enrolled = self.enroll(self.december, self.student_c)

    def enroll(self, cohort, student):
        record, problems, notes = enroll_student(
            self.owner, self.alpha, cohort.pk, student.student_number
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record

    def report(self, period_number=None, course_number=None,
               cohort_number=None, user=None):
        report, codes = build_report(
            user or self.owner,
            self.alpha,
            period_number,
            course_number,
            cohort_number,
        )
        self.assertEqual(codes, [])
        self.assertIsNotNone(report)
        return report

    def test_a_period_contains_both_of_its_boundary_start_dates(self):
        report = self.report(self.period.pk)
        self.assertEqual(
            [row.pk for row in report["cohorts"]],
            [self.january.pk, self.december.pk],
        )

    def test_records_outside_or_without_a_date_are_in_no_bounded_period(self):
        report = self.report(self.period.pk)
        numbers = [row.pk for row in report["cohorts"]]
        self.assertNotIn(self.before.pk, numbers)
        self.assertNotIn(self.undated.pk, numbers)

    def test_an_enrollment_is_in_the_period_of_its_own_cohort(self):
        report = self.report(self.period.pk)
        numbers = [row.pk for row in report["enrollments"]]
        self.assertIn(self.january_enrollments[0].pk, numbers)
        self.assertIn(self.december_enrolled.pk, numbers)
        # A completed enrollment is still a record of the period.
        self.assertIn(self.december_completed.pk, numbers)
        self.assertEqual(len(numbers), 4)

    def test_a_period_open_on_one_side_has_only_that_boundary(self):
        start_only, _, _ = create_period(
            self.owner, self.alpha, "From December", date(2026, 12, 1)
        )
        report = self.report(start_only.pk)
        self.assertEqual(
            [row.pk for row in report["cohorts"]], [self.december.pk]
        )
        end_only, _, _ = create_period(
            self.owner, self.alpha, "Until January", None, date(2026, 1, 31)
        )
        report = self.report(end_only.pk)
        self.assertEqual(
            [row.pk for row in report["cohorts"]],
            [self.before.pk, self.january.pk],
        )

    def test_a_period_without_dates_contains_every_record(self):
        all_dates, _, _ = create_period(self.owner, self.alpha, "All records")
        report = self.report(all_dates.pk)
        numbers = [row.pk for row in report["cohorts"]]
        for cohort in (self.january, self.december, self.before, self.undated):
            self.assertIn(cohort.pk, numbers)
        self.assertEqual(report["totals"]["cohorts"], 4)
        self.assertEqual(report["totals"]["enrollments"], 4)

    def test_no_period_filter_reports_every_record_of_this_center(self):
        report = self.report()
        self.assertEqual(report["totals"]["cohorts"], 4)
        self.assertEqual(report["totals"]["enrollments"], 4)
        self.assertEqual(report["totals"]["seats_taken"], 3)

    def test_course_and_cohort_filters_narrow_both_lists(self):
        other_course = self.make_course(self.owner, self.alpha, "French A1")
        other_cohort = self.make_cohort(
            course=other_course,
            name="French January",
            start_date=date(2026, 2, 1),
        )
        other_enrollment = self.enroll(other_cohort, self.student_d)
        report = self.report(course_number=other_course.pk)
        self.assertEqual(
            [row.pk for row in report["cohorts"]], [other_cohort.pk]
        )
        self.assertEqual(
            [row.pk for row in report["enrollments"]], [other_enrollment.pk]
        )
        report = self.report(self.period.pk, None, self.december.pk)
        self.assertEqual(
            [row.pk for row in report["cohorts"]], [self.december.pk]
        )
        self.assertEqual(
            sorted(row.pk for row in report["enrollments"]),
            sorted(
                [self.december_enrolled.pk, self.december_completed.pk]
            ),
        )

    def test_seats_count_only_enrollments_still_enrolled(self):
        report = self.report(self.period.pk)
        by_number = {row.pk: row for row in report["cohorts"]}
        self.assertEqual(by_number[self.january.pk].seats_taken, 2)
        self.assertEqual(by_number[self.december.pk].seats_taken, 1)
        self.assertEqual(report["totals"]["seats_taken"], 3)
        self.assertEqual(report["totals"]["seats_offered"], 3)

    def test_totals_describe_exactly_the_rows_reported(self):
        report = self.report(self.period.pk)
        self.assertEqual(report["totals"]["enrollments"], 4)
        self.assertEqual(report["totals"]["cohorts"], 2)
        self.assertEqual(report["totals"]["cohorts_without_limit"], 0)
        self.assertEqual(report["totals"]["cohorts_over_capacity"], 0)

    def test_an_over_capacity_cohort_is_counted_from_stored_enrollments(self):
        crowded = self.make_cohort(
            name="Crowded", start_date=date(2026, 3, 1), capacity=1
        )
        fifth = self.make_student("AC-5", "Fifth Learner")
        sixth = self.make_student("AC-6", "Sixth Learner")
        Enrollment.objects.create(
            organization=self.alpha, cohort=crowded, student=fifth
        )
        Enrollment.objects.create(
            organization=self.alpha, cohort=crowded, student=sixth
        )
        report = self.report(self.period.pk)
        by_number = {row.pk: row for row in report["cohorts"]}
        self.assertEqual(by_number[crowded.pk].seats_taken, 2)
        self.assertEqual(report["totals"]["seats_offered"], 4)
        self.assertEqual(report["totals"]["cohorts_over_capacity"], 1)

    def test_capacity_states_are_explicit(self):
        self.assertEqual(capacity_state(None, 7), (CAPACITY_UNLIMITED, None))
        self.assertEqual(capacity_state(5, 3), (CAPACITY_OPEN, 2))
        self.assertEqual(capacity_state(2, 2), (CAPACITY_FULL, 0))
        self.assertEqual(capacity_state(1, 2), (CAPACITY_OVER, -1))

    def test_a_period_with_no_matching_record_reports_nothing(self):
        empty, _, _ = create_period(
            self.owner,
            self.alpha,
            "Year 2030",
            date(2030, 1, 1),
            date(2030, 12, 31),
        )
        report = self.report(empty.pk)
        self.assertEqual(report["cohorts"], [])
        self.assertEqual(report["enrollments"], [])
        self.assertEqual(
            report["totals"],
            {
                "enrollments": 0,
                "cohorts": 0,
                "seats_taken": 0,
                "seats_offered": 0,
                "cohorts_without_limit": 0,
                "cohorts_over_capacity": 0,
            },
        )

    def test_another_centres_period_course_or_cohort_is_not_here(self):
        beta_period, _, _ = create_period(
            self.beta_owner,
            self.beta,
            "Beta term",
            date(2026, 1, 1),
            date(2026, 12, 31),
        )
        beta_cohort, _, _ = create_cohort(
            self.beta_owner,
            self.beta,
            "Beta cohort",
            self.beta_course.pk,
            date(2026, 2, 1),
        )
        for sent, code in (
            ({"period_number": beta_period.pk}, "period_not_found"),
            ({"course_number": self.beta_course.pk}, "course_not_found"),
            ({"cohort_number": beta_cohort.pk}, "cohort_not_found"),
        ):
            report, codes = build_report(self.owner, self.alpha, **sent)
            self.assertIsNone(report)
            self.assertEqual(codes, [code])

    def test_unknown_numbers_are_notes_not_records(self):
        report, codes = build_report(
            self.owner, self.alpha, "not-a-number", None, None
        )
        self.assertIsNone(report)
        self.assertEqual(codes, ["period_not_found"])

    def test_report_roles_are_owner_admin_coordinator_and_receptionist(self):
        for user in (self.owner, self.coordinator, self.receptionist):
            report, codes = build_report(user, self.alpha, self.period.pk)
            self.assertEqual(codes, [])
            self.assertIsNotNone(report)
        for user in (self.instructor, self.finance):
            with self.assertRaises(PermissionDenied):
                build_report(user, self.alpha, self.period.pk)

    def test_reporting_reads_the_dates_stored_now_not_a_snapshot(self):
        report = self.report(self.period.pk)
        self.assertIn(self.january.pk, [row.pk for row in report["cohorts"]])
        record, problems, notes = edit_cohort(
            self.owner,
            self.alpha,
            self.january.pk,
            "start_date",
            date(2027, 1, 1),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        report = self.report(self.period.pk)
        self.assertNotIn(self.january.pk, [row.pk for row in report["cohorts"]])
        self.assertNotIn(
            self.january_enrollments[0].pk,
            [row.pk for row in report["enrollments"]],
        )


class SessionTests(CenterFixture):
    """SCH-002/SCH-003/SCH-004/SCH-008: one-off class occurrences of one center.

    A session belongs to this center and to a cohort of the same center, keeps
    its start before its end (model rule and database constraint), and changes
    one part at a time. Nothing here reads HTML: the checks are on the records.
    """

    START = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    END = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        self.cohort = self.make_cohort(name="Autumn A")
        self.beta_cohort, problems, notes = create_cohort(
            self.beta_owner,
            self.beta,
            "Beta A",
            self.beta_course.pk,
            start_date=date(2026, 9, 1),
        )
        self.assertEqual((problems, notes), ([], []))
        self.session, problems, notes = create_session(
            self.owner, self.alpha, self.cohort.pk, self.START, self.END
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(self.session)

    def sessions_at(self, organization=None):
        return Session.objects.filter(organization=organization or self.alpha).count()

    def test_a_cohort_from_another_center_is_not_here(self):
        before = self.sessions_at()
        record, problems, notes = create_session(
            self.owner, self.alpha, self.beta_cohort.pk, self.START, self.END
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["cohort_not_found"]))
        self.assertEqual(self.sessions_at(), before)

    def test_editing_a_note_is_trimmed(self):
        moved, problems, notes = edit_session(
            self.owner,
            self.alpha,
            self.session.pk,
            "note",
            "  Bring the revised notes  ",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(moved)
        self.assertEqual(moved.note, "Bring the revised notes")
        saved = Session.objects.get(pk=self.session.pk)
        self.assertEqual(saved.note, "Bring the revised notes")

    def test_editable_session_fields_are_the_allowed_parts(self):
        self.assertEqual(EDITABLE_SESSION_FIELDS, ("start_at", "end_at", "note"))

    def test_editing_only_moves_the_allowed_parts(self):
        for field in ("cohort", "organization", "created_at", "status"):
            record, problems, notes = edit_session(
                self.coordinator,
                self.alpha,
                self.session.pk,
                field,
                "anything",
            )
            self.assertIsNone(record)
            self.assertEqual((problems, notes), ([], ["field_not_editable"]))

    def test_editing_the_session_start_moves_one_part_only(self):
        session = Session.objects.get(pk=self.session.pk)
        new_start = session.end_at - timedelta(hours=1)
        moved, problems, notes = edit_session(
            self.owner, self.alpha, session.pk, "start_at", new_start
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(moved.pk, self.session.pk)
        self.assertEqual(moved.created_at, self.session.created_at)
        self.assertEqual(moved.cohort, self.cohort)
        self.assertEqual(moved.organization, self.alpha)
        self.assertEqual(moved.end_at, session.end_at)
        self.assertEqual(moved.start_at, new_start)

    def test_editing_the_session_start_rejects_an_inversion(self):
        session = Session.objects.get(pk=self.session.pk)
        moved, problems, notes = edit_session(
            self.owner,
            self.alpha,
            session.pk,
            "start_at",
            session.end_at + timedelta(hours=1),
        )
        self.assertIsNone(moved)
        self.assertEqual(problems, [("end_at", "A session cannot end before it starts.")])
        self.assertEqual(notes, [])
        untouched = Session.objects.get(pk=self.session.pk)
        self.assertEqual(untouched.start_at, session.start_at)

    def test_identifiers_and_ownership_never_move_through_an_edit(self):
        before = self.sessions_at()
        for field in ("pk", "cohort", "organization"):
            record, problems, notes = edit_session(
                self.owner, self.alpha, self.session.pk, field, self.cohort.pk
            )
            self.assertIsNone(record)
            self.assertEqual((problems, notes), ([], ["field_not_editable"]))
        self.assertEqual(self.sessions_at(), before)

    def test_the_database_constraint_refuses_an_inverted_session(self):
        inverted = Session(
            organization=self.alpha,
            cohort=self.cohort,
            start_at=self.END,
            end_at=self.START,
        )
        before = self.sessions_at()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                inverted.save()
        self.assertEqual(self.sessions_at(), before)

    def test_the_model_refuses_a_session_for_another_center_directly(self):
        record = Session(
            organization=self.alpha,
            cohort=self.beta_cohort,
            start_at=self.START,
            end_at=self.END,
        )
        with self.assertRaises(ValidationError) as caught:
            record.full_clean()
        self.assertEqual(
            caught.exception.message_dict["cohort"],
            ["That cohort belongs to another center."],
        )

    def test_the_note_is_trimmed_when_the_session_is_saved(self):
        before = self.sessions_at()
        record, problems, notes = create_session(
            self.owner,
            self.alpha,
            self.cohort.pk,
            self.START,
            self.END,
            "  Bring the field notes  ",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)
        self.assertEqual(record.note, "Bring the field notes")
        self.assertEqual(self.sessions_at(), before + 1)

    def test_the_session_roles_are_owner_admin_and_coordinator(self):
        for user in (self.owner, self.coordinator):
            record, problems, notes = create_session(
                user, self.alpha, self.cohort.pk, self.START, self.END
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertIsNotNone(record)
        for user in (self.instructor, self.receptionist, self.finance):
            with self.assertRaises(PermissionDenied):
                create_session(user, self.alpha, self.cohort.pk, self.START, self.END)

    def test_the_sessions_are_the_cohorts_and_the_center_keeps_them(self):
        self.assertEqual(self.session.cohort, self.cohort)
        self.assertEqual(self.session.organization, self.alpha)
        self.assertEqual(self.sessions_at(self.beta), 0)


class AttendanceTests(CenterFixture):
    """ATT-001/ATT-002/ATT-003/ATT-008 and RULE 6.5: one current state per
    student per session, only for a learner enrolled in the session's cohort.

    Eligibility reads the enrollment table (ATT-008); the single-state rule is
    a database constraint, not a service-side check, so it holds even through
    the model API.
    """

    def setUp(self):
        super().setUp()
        self.cohort = self.make_cohort(name="Autumn A")
        self.other = self.make_cohort(name="Winter A")
        self.learners = [self.student]
        for number in ("AC-2", "AC-3", "AC-4", "AC-5", "AC-6", "AC-7", "AC-8"):
            self.learners.append(self.make_student(number, f"Learner {number[3:]}"))
        self.archived = self.make_student(
            "AC-9", "Archived Learner", status=STATUS_ARCHIVED
        )
        self.enrollments = {}
        for learner in self.learners:
            record, problems, notes = enroll_student(
                self.owner, self.alpha, self.cohort.pk, learner.student_number
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertIsNotNone(record)
            self.enrollments[learner.student_number] = record
        completed, problems, notes = set_enrollment_status(
            self.owner,
            self.alpha,
            self.enrollments["AC-8"].pk,
            ENROLLMENT_COMPLETED,
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(completed.status, ENROLLMENT_COMPLETED)
        self.session, problems, notes = create_session(
            self.owner,
            self.alpha,
            self.cohort.pk,
            datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc),
        )
        self.assertEqual((problems, notes), ([], []))
        self.other_session, problems, notes = create_session(
            self.owner,
            self.alpha,
            self.other.pk,
            datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual((problems, notes), ([], []))

    def records(self):
        return Attendance.objects.filter(organization=self.alpha).count()

    def test_a_first_attendance_record_is_stamped_when_it_is_taken(self):
        before = self.records()
        learner = self.learners[4]
        record, problems, notes = record_attendance(
            self.owner, self.alpha, self.session.pk, learner.student_number, "present"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)
        self.assertEqual(self.records(), before + 1)
        self.assertEqual(record.status, ATTENDANCE_PRESENT)
        self.assertEqual(record.note, "")
        self.assertIsNotNone(record.recorded_at)
        self.assertIsNone(record.status_changed_at)

    def test_a_foreign_or_unknown_record_is_not_here(self):
        for sent, code in (
            (self.other_session.pk, "not_enrolled"),
            (self.beta_student.student_number, "student_not_found"),
            ("ZZ-9", "student_not_found"),
        ):
            student_number = (
                self.learners[0].student_number
                if sent == self.other_session.pk
                else sent
            )
            session_number = self.other_session.pk if sent == self.other_session.pk else self.session.pk
            record, problems, notes = record_attendance(
                self.owner, self.alpha, session_number, student_number, "present"
            )
            self.assertIsNone(record)
            self.assertEqual((problems, notes), ([], [code]))
        record, problems, notes = record_attendance(
            self.owner, self.alpha, "not-a-number", self.learners[0].student_number, "present"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["session_not_found"]))

    def test_a_rejected_status_leaves_no_record_behind(self):
        before = self.records()
        record, problems, notes = record_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            self.learners[6].student_number,
            "missing",
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual([field for field, _ in problems], ["status"])
        self.assertEqual(self.records(), before)

    def test_a_status_arrives_trimmed_and_lowered(self):
        record, problems, notes = record_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            self.learners[5].student_number,
            "  Late ",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, ATTENDANCE_LATE)

    def test_the_attendance_statuses_and_eligible_states_are_fixed_vocabulary(self):
        self.assertEqual(
            ATTENDANCE_STATUSES,
            (ATTENDANCE_PRESENT, ATTENDANCE_ABSENT, ATTENDANCE_LATE, ATTENDANCE_EXCUSED),
        )
        self.assertEqual(
            ATTENDANCE_ELIGIBLE_STATUSES,
            (ENROLLMENT_ENROLLED, ENROLLMENT_COMPLETED),
        )

    def test_an_archived_student_cannot_take_a_record(self):
        record, problems, notes = record_attendance(
            self.owner, self.alpha, self.session.pk, self.archived.student_number, "present"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["student_archived"]))

    def test_completed_and_enrolled_are_the_eligible_enrollment_states(self):
        self.assertNotIn(ENROLLMENT_WITHDRAWN, ATTENDANCE_ELIGIBLE_STATUSES)
        self.assertNotIn(ENROLLMENT_CANCELLED, ATTENDANCE_ELIGIBLE_STATUSES)
        self.assertEqual(ENROLLMENT_TRANSITIONS[ENROLLMENT_COMPLETED], ())
        record, problems, notes = record_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            self.learners[7].student_number,
            "absent",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.status, ATTENDANCE_ABSENT)

    def test_one_current_state_per_student_per_session(self):
        first, problems, notes = record_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            self.learners[4].student_number,
            "present",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(first)
        self.assertEqual(first.status, ATTENDANCE_PRESENT)
        record, problems, notes = record_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            self.learners[4].student_number,
            "absent",
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["already_recorded"]))

    def test_the_database_constraint_refuses_a_second_state_for_the_pair(self):
        learner = self.learners[4]
        first, problems, notes = record_attendance(
            self.owner,
            self.alpha,
            self.session.pk,
            learner.student_number,
            "present",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(first)
        before = self.records()
        duplicate = Attendance(
            organization=self.alpha,
            session=self.session,
            student=learner,
            status=ATTENDANCE_ABSENT,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                duplicate.save()
        self.assertEqual(self.records(), before)

    def test_the_four_states_are_all_available_for_enrolled_learners(self):
        pairs = (
            (self.learners[0], ATTENDANCE_PRESENT),
            (self.learners[1], ATTENDANCE_ABSENT),
            (self.learners[2], ATTENDANCE_LATE),
            (self.learners[3], ATTENDANCE_EXCUSED),
        )
        for learner, status in pairs:
            record, problems, notes = record_attendance(
                self.owner, self.alpha, self.session.pk, learner.student_number, status
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertEqual(record.status, status)
        saved = Attendance.objects.filter(
            session=self.session,
            organization=self.alpha,
        )
        for status in ATTENDANCE_STATUSES:
            self.assertEqual(saved.filter(status=status).count(), 1)

    def test_withdrawn_and_cancelled_enrollments_are_not_eligible(self):
        before = self.records()
        for number, final in (
            ("AC-7", ENROLLMENT_WITHDRAWN),
            ("AC-6", ENROLLMENT_CANCELLED),
        ):
            enrollment = self.enrollments[number]
            moved, problems, notes = set_enrollment_status(
                self.owner, self.alpha, enrollment.pk, final
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertEqual(moved.status, final)
            record, problems, notes = record_attendance(
                self.owner, self.alpha, self.session.pk, number, "present"
            )
            self.assertIsNone(record)
            self.assertEqual((problems, notes), ([], ["not_enrolled"]))
        self.assertEqual(self.records(), before)

    def test_the_record_roles_are_instructor_owner_and_coordinator(self):
        for user in (self.receptionist, self.finance):
            with self.assertRaises(PermissionDenied):
                record_attendance(
                    user,
                    self.alpha,
                    self.session.pk,
                    self.learners[0].student_number,
                    "present",
                )
        record, problems, notes = record_attendance(
            self.instructor,
            self.alpha,
            self.session.pk,
            self.learners[0].student_number,
            "present",
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["not_assigned"]))


class CorrectionTests(CenterFixture):
    """ATT-005/ATT-010/RULE 6.5: a correction moves the one saved record in
    place, stamps the change and writes one audit row naming the previous and
    the new state, the session, the student and the actor. The audit and the
    record protect the people and places they name, so a delete cannot erase
    the history. Eligibility is not rechecked here - the audited workflow is
    the documented exception (ATT-008).
    """

    START = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    END = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        self.cohort = self.make_cohort(name="Autumn A")
        enrolled, problems, notes = enroll_student(
            self.owner, self.alpha, self.cohort.pk, self.student.student_number
        )
        self.assertEqual((problems, notes), ([], []))
        self.enrollment = enrolled
        self.session, problems, notes = create_session(
            self.owner, self.alpha, self.cohort.pk, self.START, self.END
        )
        self.assertEqual((problems, notes), ([], []))
        self.record_row, problems, notes = record_attendance(
            self.owner, self.alpha, self.session.pk, self.student.student_number, "absent"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(self.record_row)
        self.original_recorded_at = self.record_row.recorded_at

    def audits(self):
        return AuditLog.objects.filter(organization=self.alpha).count()

    def saved(self):
        return Attendance.objects.filter(
            organization=self.alpha, session=self.session, student=self.student
        ).get()

    def test_a_correction_after_the_enrollment_moved_away_still_corrects(self):
        moved, problems, notes = set_enrollment_status(
            self.owner, self.alpha, self.enrollment.pk, ENROLLMENT_WITHDRAWN
        )
        self.assertEqual((problems, notes), ([], []))
        before = self.audits()
        corrected, problems, notes = correct_attendance(
            self.coordinator, self.alpha, self.session.pk, self.student.student_number, "late"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(corrected)
        self.assertEqual(corrected.status, ATTENDANCE_LATE)
        self.assertEqual(self.audits(), before + 1)

    def test_a_correction_keeps_one_current_state_on_the_same_record(self):
        before = self.audits()
        corrected, problems, notes = correct_attendance(
            self.coordinator, self.alpha, self.session.pk, self.student.student_number, "late"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(corrected.pk, self.record_row.pk)
        saved = self.saved()
        self.assertEqual(saved.status, ATTENDANCE_LATE)
        self.assertIsNotNone(saved.status_changed_at)
        self.assertEqual(saved.recorded_at, self.original_recorded_at)
        self.assertEqual(Attendance.objects.filter(organization=self.alpha).count(), 1)
        self.assertEqual(self.audits(), before + 1)
        log = AuditLog.objects.filter(organization=self.alpha).first()
        self.assertEqual(log.action, AUDIT_ATTENDANCE_CORRECTED)
        self.assertEqual(log.actor_id, self.coordinator.pk)
        self.assertEqual(log.previous_status, ATTENDANCE_ABSENT)
        self.assertEqual(log.new_status, ATTENDANCE_LATE)
        self.assertEqual(log.session_id, self.session.pk)
        self.assertEqual(log.student_id, self.student.pk)
        self.assertIsNotNone(log.at)

    def test_a_failed_audit_save_rolls_the_correction_back(self):
        """RULE 6.5: the corrected row and its audit row commit together."""
        before = self.audits()
        with mock.patch(
            "apps.academics.services.AuditLog.save",
            side_effect=RuntimeError("audit failed"),
        ):
            with self.assertRaises(RuntimeError):
                correct_attendance(
                    self.coordinator,
                    self.alpha,
                    self.session.pk,
                    self.student.student_number,
                    "late",
                )
        self.assertEqual(self.saved().status, ATTENDANCE_ABSENT)
        self.assertEqual(self.audits(), before)

    def test_a_correction_to_a_state_that_is_not_allowed_changes_nothing(self):
        before = self.audits()
        record, problems, notes = correct_attendance(
            self.coordinator, self.alpha, self.session.pk, self.student.student_number, "missing"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual([field for field, _ in problems], ["status"])
        self.assertEqual(self.saved().status, self.record_row.status)
        self.assertEqual(self.audits(), before)

    def test_a_correction_to_the_current_state_says_no_change(self):
        before = self.audits()
        current = self.saved().status
        record, problems, notes = correct_attendance(
            self.owner, self.alpha, self.session.pk, self.student.student_number, current.upper()
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["no_change"]))
        self.assertEqual(self.saved().status, current)
        self.assertEqual(self.audits(), before)

    def test_a_missing_session_or_number_is_not_here(self):
        record, problems, notes = correct_attendance(
            self.owner, self.alpha, "not-a-number", self.student.student_number, "late"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["session_not_found"]))
        record, problems, notes = correct_attendance(
            self.owner, self.alpha, self.session.pk, "ZZ-9", "late"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["student_not_found"]))
        self.assertEqual(self.audits(), 0)

    def test_a_record_that_was_never_taken_cannot_be_corrected(self):
        self.make_student("AC-2", "Unrecorded Learner")
        enrolled, problems, notes = enroll_student(
            self.owner, self.alpha, self.cohort.pk, "AC-2"
        )
        self.assertEqual((problems, notes), ([], []))
        record, problems, notes = correct_attendance(
            self.coordinator, self.alpha, self.session.pk, "AC-2", "late"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["not_recorded"]))
        self.assertEqual(self.audits(), 0)

    def test_the_correction_roles_are_owner_admin_and_coordinator(self):
        first, problems, notes = correct_attendance(
            self.owner, self.alpha, self.session.pk, self.student.student_number, "late"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(first.status, ATTENDANCE_LATE)
        second, problems, notes = correct_attendance(
            self.coordinator, self.alpha, self.session.pk, self.student.student_number, "excused"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(second.status, ATTENDANCE_EXCUSED)
        self.assertEqual(self.audits(), 2)
        for user in (self.instructor, self.receptionist, self.finance):
            with self.assertRaises(PermissionDenied):
                correct_attendance(
                    user, self.alpha, self.session.pk, self.student.student_number, "present"
                )

    def test_the_record_and_the_audit_keep_their_people_and_places_alive(self):
        corrected, problems, notes = correct_attendance(
            self.coordinator, self.alpha, self.session.pk, self.student.student_number, "late"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(AuditLog.objects.filter(organization=self.alpha).count(), 1)
        with self.assertRaises(ProtectedError):
            self.session.delete()
        with self.assertRaises(ProtectedError):
            self.student.delete()
        with self.assertRaises(ProtectedError):
            Cohort.objects.get(pk=self.cohort.pk).delete()
        with self.assertRaises(ProtectedError):
            CohortUser.objects.get(username="alpha-coord").delete()
        self.assertTrue(Session.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(CohortUser.objects.filter(username="alpha-coord").exists())


class AttendanceScopeTests(CenterFixture):
    """ATT-004/STAFF-003/STAFF-004: an instructor only reaches the attendance
    of the cohorts a Staff record links their account to, and the scope is
    computed server-side. A Staff link without an account teaches no one, and
    an instructor with no link reaches nothing. The owner and the coordinator
    reach every cohort of this center."""

    def setUp(self):
        super().setUp()
        self.autumn = self.make_cohort(name="Autumn A")
        self.winter = self.make_cohort(name="Winter A")
        enrolled, problems, notes = enroll_student(
            self.owner, self.alpha, self.autumn.pk, self.student.student_number
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(enrolled)
        for cohort, number in (
            (self.autumn, "AC-2"),
            (self.winter, "AC-3"),
            (self.winter, "AC-4"),
        ):
            self.make_student(number)
            enrolled, problems, notes = enroll_student(
                self.owner, self.alpha, cohort.pk, number
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertIsNotNone(enrolled)
        self.assigned_session, problems, notes = create_session(
            self.owner,
            self.alpha,
            self.autumn.pk,
            datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc),
        )
        self.assertEqual((problems, notes), ([], []))
        self.other_session, problems, notes = create_session(
            self.owner,
            self.alpha,
            self.winter.pk,
            datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual((problems, notes), ([], []))
        self.teacher = Staff.objects.create(
            organization=self.alpha,
            user=self.instructor,
            staff_number="TEACH-1",
            name="Autumn Teacher",
        )
        linked = CohortInstructor.objects.create(
            organization=self.alpha, cohort=self.autumn, staff=self.teacher
        )
        self.assertIsNotNone(linked)

    def records(self):
        return Attendance.objects.filter(organization=self.alpha).count()

    def test_the_assigned_instructor_reaches_the_assigned_session(self):
        before = self.records()
        record, problems, notes = record_attendance(
            self.instructor, self.alpha, self.assigned_session.pk, "AC-1", "present"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record)
        self.assertEqual(record.status, ATTENDANCE_PRESENT)
        self.assertEqual(self.records(), before + 1)

    def test_the_other_sessions_are_outside_the_instructors_scope(self):
        before = self.records()
        record, problems, notes = record_attendance(
            self.instructor, self.alpha, self.other_session.pk, "AC-3", "present"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["not_assigned"]))
        self.assertEqual(self.records(), before)

    def test_an_instructor_without_a_cohort_link_reaches_nothing(self):
        outsider = self.make_user("alpha-subteacher", ROLE_INSTRUCTOR)
        before = self.records()
        record, problems, notes = record_attendance(
            outsider, self.alpha, self.assigned_session.pk, "AC-1", "present"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["not_assigned"]))
        self.assertEqual(self.records(), before)

    def test_a_staff_link_without_an_account_teaches_nothing(self):
        ghost = Staff.objects.create(
            organization=self.alpha, staff_number="TEACH-9", name="Ghost Teacher"
        )
        CohortInstructor.objects.create(
            organization=self.alpha, cohort=self.winter, staff=ghost
        )
        before = self.records()
        record, problems, notes = record_attendance(
            self.instructor, self.alpha, self.other_session.pk, "AC-3", "present"
        )
        self.assertIsNone(record)
        self.assertEqual((problems, notes), ([], ["not_assigned"]))
        self.assertEqual(self.records(), before)

    def test_the_owner_and_the_coordinator_reach_every_cohort(self):
        before = self.records()
        for user, session_number, number in (
            (self.owner, self.assigned_session.pk, "AC-1"),
            (self.owner, self.other_session.pk, "AC-3"),
            (self.coordinator, self.assigned_session.pk, "AC-2"),
            (self.coordinator, self.other_session.pk, "AC-4"),
        ):
            record, problems, notes = record_attendance(
                user, self.alpha, session_number, number, "present"
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertEqual(record.status, ATTENDANCE_PRESENT)
        self.assertEqual(self.records(), before + 4)


class AttendanceReportingTests(CenterFixture):
    """ATT-006..ATT-009: sessions carry their own four counts, kept apart, the
    filters (period, course, cohort, instructor scope) narrow the same rows the
    report reads, and the totals state what the rows shown actually hold.

    The numbers below are computed here, not copied from a page: six records
    over three sessions of two cohorts, one of which started before the
    period, with one learner of each state kept visible separately.
    """

    AUTUMN_START = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    AUTUMN_END = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
    AFTERNOON_START = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    AFTERNOON_END = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    LAST_YEAR_START = datetime(2025, 9, 1, 9, 0, tzinfo=timezone.utc)
    LAST_YEAR_END = datetime(2025, 9, 1, 10, 0, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        self.period, problems, notes = create_period(
            self.owner, self.alpha, "Year 2026", date(2026, 1, 1), date(2026, 12, 31)
        )
        self.assertEqual((problems, notes), ([], []))
        self.inside = self.make_cohort(
            name="Autumn A", start_date=date(2026, 9, 1), capacity=5
        )
        self.outside = self.make_cohort(
            name="Last Year A", start_date=date(2025, 9, 1), capacity=1
        )
        for cohort, number in (
            (self.inside, "AC-1"),
            (self.inside, "AC-2"),
            (self.inside, "AC-3"),
            (self.outside, "AC-4"),
        ):
            if number != "AC-1":
                self.make_student(number)
            enrolled, problems, notes = enroll_student(
                self.owner, self.alpha, cohort.pk, number
            )
            self.assertEqual((problems, notes), ([], []))
            self.assertIsNotNone(enrolled)
        self.morning, problems, notes = create_session(
            self.owner, self.alpha, self.inside.pk, self.AUTUMN_START, self.AUTUMN_END
        )
        self.assertEqual((problems, notes), ([], []))
        self.afternoon, problems, notes = create_session(
            self.owner, self.alpha, self.inside.pk, self.AFTERNOON_START, self.AFTERNOON_END
        )
        self.assertEqual((problems, notes), ([], []))
        self.last_year, problems, notes = create_session(
            self.owner, self.alpha, self.outside.pk, self.LAST_YEAR_START, self.LAST_YEAR_END
        )
        self.assertEqual((problems, notes), ([], []))
        for session, number, status in (
            (self.morning, "AC-1", ATTENDANCE_PRESENT),
            (self.morning, "AC-2", ATTENDANCE_ABSENT),
            (self.afternoon, "AC-3", ATTENDANCE_LATE),
            (self.afternoon, "AC-1", ATTENDANCE_EXCUSED),
            (self.last_year, "AC-4", ATTENDANCE_PRESENT),
        ):
            record, problems, notes = record_attendance(
                self.owner, self.alpha, session.pk, number, status
            )
            self.assertEqual((problems, notes), ([], []))

    def test_each_session_carries_its_own_four_counts(self):
        rows = list(session_report_rows(self.alpha))
        self.assertEqual(len(rows), 3)
        old, morning, afternoon = rows
        self.assertEqual(old.pk, self.last_year.pk)
        self.assertEqual(
            (old.present, old.absent, old.late, old.excused, old.attendance_records),
            (1, 0, 0, 0, 1),
        )
        self.assertEqual(morning.pk, self.morning.pk)
        self.assertEqual(
            (morning.present, morning.absent, morning.late, morning.excused, morning.attendance_records),
            (1, 1, 0, 0, 2),
        )
        self.assertEqual(afternoon.pk, self.afternoon.pk)
        self.assertEqual(
            (afternoon.present, afternoon.absent, afternoon.late, afternoon.excused, afternoon.attendance_records),
            (0, 0, 1, 1, 2),
        )

    def test_the_period_only_counts_cohorts_that_started_inside_it(self):
        sessions = session_report_rows(self.alpha, period=self.period)
        self.assertEqual(
            sorted(row.pk for row in sessions),
            sorted({self.morning.pk, self.afternoon.pk}),
        )
        attendance = attendance_report_rows(self.alpha, period=self.period)
        self.assertEqual(len(attendance), 4)
        self.assertEqual(
            attendance_totals(attendance, sessions),
            {
                "sessions": 2,
                "records": 4,
                "present": 1,
                "absent": 1,
                "late": 1,
                "excused": 1,
            },
        )

    def test_the_course_filter_keeps_only_its_cohorts_sessions(self):
        self.assertEqual(
            len(session_report_rows(self.alpha, course=self.alpha_course)), 3
        )
        self.assertEqual(
            len(attendance_report_rows(self.alpha, course=self.alpha_course)), 5
        )
        self.assertEqual(
            len(session_report_rows(self.alpha, course=self.beta_course)), 0
        )
        self.assertEqual(
            len(attendance_report_rows(self.alpha, course=self.beta_course)), 0
        )

    def test_the_cohort_filter_keeps_only_that_cohorts_sessions(self):
        self.assertEqual(
            len(session_report_rows(self.alpha, cohort=self.inside)), 2
        )
        self.assertEqual(
            len(attendance_report_rows(self.alpha, cohort=self.inside)), 4
        )

    def test_the_instructor_scope_narrows_rows_through_cohort_ids(self):
        scope = {self.inside.pk}
        self.assertEqual(
            len(session_report_rows(self.alpha, cohorts=scope)), 2
        )
        self.assertEqual(
            len(attendance_report_rows(self.alpha, cohorts=scope)), 4
        )
        only_old = {self.outside.pk}
        self.assertEqual(
            len(session_report_rows(self.alpha, cohorts=only_old)), 1
        )
        self.assertEqual(
            len(attendance_report_rows(self.alpha, cohorts=only_old)), 1
        )

    def test_the_four_states_stay_counted_apart(self):
        self.assertEqual(
            attendance_totals(
                list(attendance_report_rows(self.alpha)),
                list(session_report_rows(self.alpha)),
            ),
            {
                "sessions": 3,
                "records": 5,
                "present": 2,
                "absent": 1,
                "late": 1,
                "excused": 1,
            },
        )

    def test_the_report_reads_the_same_rows_as_the_helpers(self):
        for period_number, expected_sessions, expected_records in (
            (None, 3, 5),
            (self.period.pk, 2, 4),
        ):
            report, codes = build_report(
                self.coordinator, self.alpha, period_number
            )
            self.assertEqual(codes, [])
            self.assertIsNotNone(report)
            self.assertEqual(len(report["sessions"]), expected_sessions)
            self.assertEqual(len(report["attendance"]), expected_records)
        report, codes = build_report(self.coordinator, self.alpha)
        self.assertEqual(
            report["attendance_totals"],
            {
                "sessions": 3,
                "records": 5,
                "present": 2,
                "absent": 1,
                "late": 1,
                "excused": 1,
            },
        )

    def test_the_report_totals_state_the_capacity_of_the_rows_shown(self):
        report, codes = build_report(self.coordinator, self.alpha)
        self.assertEqual(codes, [])
        self.assertEqual(
            report["totals"],
            {
                "enrollments": 4,
                "cohorts": 2,
                "seats_taken": 4,
                "seats_offered": 6,
                "cohorts_without_limit": 0,
                "cohorts_over_capacity": 0,
            },
        )
        report, codes = build_report(self.coordinator, self.alpha, self.period.pk)
        self.assertEqual(
            report["totals"],
            {
                "enrollments": 3,
                "cohorts": 1,
                "seats_taken": 3,
                "seats_offered": 5,
                "cohorts_without_limit": 0,
                "cohorts_over_capacity": 0,
            },
        )
