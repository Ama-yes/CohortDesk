"""Model and service tests for one center's staff records and assignments.

The rules under test are the staff half of SRS Milestone 2 as this product's
services implement them: a staff record is owned by one organization
(RULE 6.1), it can exist without a login identity (STAFF-006), its employment
status moves only through the service table (STAFF-002/STAFF-005), and an
active instructor can be assigned to courses and cohorts of the same center
(STAFF-003), with the assigned-cohort list README names for STAFF-004.

Every action here is called the way a page calls it (service function, not
model API), because the service layer is the one place the rules live.
Cross-tenant proof through the URL layer lives in apps/staff/test_access.py.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.academics.models import Cohort, Course
from apps.academics.services import create_cohort, create_course
from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_ACADEMIC_COORDINATOR,
    ROLE_ADMINISTRATOR,
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)

from .models import CohortInstructor, CourseInstructor, Staff
from .services import (
    STAFF_ENTRY_STATUSES,
    STAFF_TRANSITIONS,
    assign_staff_to_cohort,
    assign_staff_to_course,
    create_staff,
    edit_staff,
    remove_staff_from_cohort,
    remove_staff_from_course,
    set_staff_status,
    link_staff_account,
    unlink_staff_account,
)
from .statuses import (
    STAFF_STATUSES,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_INACTIVE,
    STATUS_ON_LEAVE,
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
        self.admin = self.make_user("alpha-admin", ROLE_ADMINISTRATOR)
        self.coordinator = self.make_user("alpha-coord", ROLE_ACADEMIC_COORDINATOR)
        self.receptionist = self.make_user("alpha-desk", ROLE_RECEPTIONIST)
        self.instructor = self.make_user("alpha-teacher", ROLE_INSTRUCTOR)
        self.finance = self.make_user("alpha-money", ROLE_FINANCE_OFFICER)
        self.alpha_course = self.make_course(self.owner, self.alpha, "Spanish A1")
        self.beta_course = self.make_course(
            self.beta_owner, self.beta, "Beta French"
        )
        self.alpha_cohort = self.make_cohort(
            self.owner, self.alpha, self.alpha_course, "Alpha Autumn"
        )
        self.beta_cohort = self.make_cohort(
            self.beta_owner, self.beta, self.beta_course, "Beta Autumn"
        )
        self.teacher = self.make_staff(self.owner, self.alpha, "T-001",
                                       "Alpha Instructor")

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
        return record

    def make_cohort(self, user, organization, course, name):
        record, problems, notes = create_cohort(
            user, organization, name, course.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        return record

    def make_staff(self, user, organization, number, name, **kwargs):
        record, problems, notes = create_staff(
            user, organization, number, name, **kwargs
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(record)
        return record


class VocabularyTests(TestCase):
    """STAFF-002/STAFF-005: employment status is fixed, not free text."""

    def test_staff_vocabulary(self):
        self.assertEqual(
            STAFF_STATUSES,
            (STATUS_ACTIVE, STATUS_ON_LEAVE, STATUS_INACTIVE, STATUS_ARCHIVED),
        )

    def test_new_staff_cannot_start_archived(self):
        self.assertNotIn(STATUS_ARCHIVED, STAFF_ENTRY_STATUSES)

    def test_archived_is_terminal(self):
        self.assertEqual(STAFF_TRANSITIONS[STATUS_ARCHIVED], ())

    def test_every_status_has_a_transition_row(self):
        for status in STAFF_STATUSES:
            self.assertIn(status, STAFF_TRANSITIONS)


class StaffTests(CenterFixture):
    """STAFF-001/STAFF-002/STAFF-006: an organization-owned staff record."""

    def test_staff_number_is_trimmed_and_uppercased(self):
        record, problems, notes = create_staff(
            self.owner,
            self.alpha,
            "  s-9  ",
            "  Sam Teacher  ",
            email="  sam@example.test  ",
            job_title="  Tutor  ",
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.staff_number, "S-9")
        self.assertEqual(record.name, "Sam Teacher")
        self.assertEqual(record.email, "sam@example.test")
        self.assertEqual(record.job_title, "Tutor")
        self.assertEqual(record.organization, self.alpha)
        self.assertEqual(record.employment_status, STATUS_ACTIVE)
        self.assertIsNotNone(record.status_changed_at)

    def test_staff_record_needs_no_login_identity(self):
        """STAFF-006: creating a staff record grants nobody access."""
        before = CohortUser.objects.count()
        record, problems, notes = create_staff(
            self.owner, self.alpha, "T-100", "No Login"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(CohortUser.objects.count(), before)
        self.assertIsNone(record.user)
        self.assertEqual(record.organization, self.alpha)

    def test_staff_number_pattern_is_enforced(self):
        record, problems, notes = create_staff(
            self.owner, self.alpha, "bad number", "Bad Number"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, [])
        self.assertEqual(problems[0][0], "staff_number")

    def test_name_is_required(self):
        record, problems, notes = create_staff(
            self.owner, self.alpha, "T-101", "   "
        )
        self.assertIsNone(record)
        self.assertEqual(problems[0][0], "name")

    def test_archived_is_not_an_entry_status(self):
        record, problems, notes = create_staff(
            self.owner,
            self.alpha,
            "T-102",
            "Old Teacher",
            employment_status=STATUS_ARCHIVED,
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["entry"])
        self.assertFalse(Staff.objects.filter(staff_number="T-102").exists())

    def test_duplicate_staff_number_is_refused_inside_one_center(self):
        record, problems, notes = create_staff(
            self.owner, self.alpha, "T-001", "Same Number"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["number_used"])
        self.assertEqual(
            Staff.objects.filter(
                organization=self.alpha, staff_number="T-001"
            ).count(),
            1,
        )

    def test_same_staff_number_in_another_center_is_a_different_record(self):
        record, problems, notes = create_staff(
            self.beta_owner, self.beta, "T-001", "Beta Instructor"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.organization, self.beta)

    def test_database_refuses_a_duplicate_number(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Staff.objects.create(
                    organization=self.alpha,
                    staff_number="T-001",
                    name="Database Duplicate",
                )

    def test_create_requires_the_management_permission(self):
        for user in (self.coordinator, self.receptionist, self.instructor,
                     self.finance):
            with self.assertRaises(PermissionDenied):
                create_staff(user, self.alpha, "T-200", "Not Allowed")
        self.assertEqual(
            Staff.objects.filter(staff_number="T-200").exists(), False
        )

    def test_administrator_creates_staff(self):
        record, problems, notes = create_staff(
            self.admin, self.alpha, "T-201", "Admin Made"
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.organization, self.alpha)

    def test_edit_changes_one_field_only(self):
        record, problems, notes = edit_staff(
            self.owner, self.alpha, "T-001", "job_title", "  Senior Tutor "
        )
        self.assertEqual((problems, notes), ([], []))
        record.refresh_from_db()
        self.assertEqual(record.job_title, "Senior Tutor")
        self.assertEqual(record.name, "Alpha Instructor")

    def test_edit_rejects_an_unlistable_field(self):
        record, problems, notes = edit_staff(
            self.owner, self.alpha, "T-001", "employment_status", "inactive"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["field_not_editable"])
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.employment_status, STATUS_ACTIVE)

    def test_edit_rejects_a_blank_name_and_changes_nothing(self):
        record, problems, notes = edit_staff(
            self.owner, self.alpha, "T-001", "name", "   "
        )
        self.assertIsNone(record)
        self.assertEqual(problems[0][0], "name")
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.name, "Alpha Instructor")

    def test_edit_unknown_staff_is_not_found(self):
        record, problems, notes = edit_staff(
            self.owner, self.alpha, "T-999", "name", "Ghost"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])

    def test_edit_another_centres_staff_number_is_not_found(self):
        beta = create_staff(self.beta_owner, self.beta, "B-001", "Beta Teacher")[0]
        record, problems, notes = edit_staff(
            self.owner, self.alpha, beta.staff_number, "name", "Stolen"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_found"])
        beta.refresh_from_db()
        self.assertEqual(beta.name, "Beta Teacher")

    def test_status_moves_through_the_table(self):
        record, problems, notes = set_staff_status(
            self.owner, self.alpha, "T-001", STATUS_ON_LEAVE
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.employment_status, STATUS_ON_LEAVE)
        record, problems, notes = set_staff_status(
            self.owner, self.alpha, "T-001", STATUS_ACTIVE
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.employment_status, STATUS_ACTIVE)

    def test_inactive_can_return_to_active(self):
        set_staff_status(self.owner, self.alpha, "T-001", STATUS_INACTIVE)
        record, problems, notes = set_staff_status(
            self.owner, self.alpha, "T-001", STATUS_ACTIVE
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(record.employment_status, STATUS_ACTIVE)

    def test_archive_sets_the_moment_and_is_terminal(self):
        record, problems, notes = set_staff_status(
            self.owner, self.alpha, "T-001", STATUS_ARCHIVED
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertIsNotNone(record.archived_at)
        self.assertIsNotNone(record.status_changed_at)
        record, problems, notes = set_staff_status(
            self.owner, self.alpha, "T-001", STATUS_ACTIVE
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.employment_status, STATUS_ARCHIVED)

    def test_unknown_status_is_refused(self):
        record, problems, notes = set_staff_status(
            self.owner, self.alpha, "T-001", "retired"
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["transition"])

    def test_status_change_requires_the_management_permission(self):
        with self.assertRaises(PermissionDenied):
            set_staff_status(
                self.coordinator, self.alpha, "T-001", STATUS_INACTIVE
            )


class AssignmentTests(CenterFixture):
    """STAFF-003: an active instructor teaches courses and cohorts here."""

    def test_active_instructor_is_assigned_to_a_course_and_a_cohort(self):
        course, problems, notes = assign_staff_to_course(
            self.owner, self.alpha, "T-001", self.alpha_course.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(course.staff, self.teacher)
        self.assertEqual(course.course, self.alpha_course)
        self.assertEqual(course.organization, self.alpha)
        cohort, problems, notes = assign_staff_to_cohort(
            self.owner, self.alpha, "T-001", self.alpha_cohort.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(cohort.cohort, self.alpha_cohort)

    def test_duplicate_assignment_is_refused(self):
        assign_staff_to_course(
            self.owner, self.alpha, "T-001", self.alpha_course.pk
        )
        record, problems, notes = assign_staff_to_course(
            self.owner, self.alpha, "T-001", self.alpha_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["already_assigned"])
        self.assertEqual(CourseInstructor.objects.count(), 1)

    def test_database_refuses_a_duplicate_pair(self):
        CourseInstructor.objects.create(
            organization=self.alpha,
            staff=self.teacher,
            course=self.alpha_course,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CourseInstructor.objects.create(
                    organization=self.alpha,
                    staff=self.teacher,
                    course=self.alpha_course,
                )

    def test_staff_on_leave_cannot_be_assigned(self):
        set_staff_status(self.owner, self.alpha, "T-001", STATUS_ON_LEAVE)
        record, problems, notes = assign_staff_to_course(
            self.owner, self.alpha, "T-001", self.alpha_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["staff_not_active"])
        self.assertEqual(CourseInstructor.objects.count(), 0)

    def test_unknown_staff_course_or_cohort_is_named(self):
        record, problems, notes = assign_staff_to_course(
            self.owner, self.alpha, "T-999", self.alpha_course.pk
        )
        self.assertEqual(notes, ["staff_not_found"])
        record, problems, notes = assign_staff_to_course(
            self.owner, self.alpha, "T-001", 99999
        )
        self.assertEqual(notes, ["course_not_found"])
        record, problems, notes = assign_staff_to_cohort(
            self.owner, self.alpha, "T-001", 99999
        )
        self.assertEqual(notes, ["cohort_not_found"])

    def test_another_centres_course_or_cohort_cannot_be_assigned(self):
        record, problems, notes = assign_staff_to_course(
            self.owner, self.alpha, "T-001", self.beta_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["course_not_found"])
        record, problems, notes = assign_staff_to_cohort(
            self.owner, self.alpha, "T-001", self.beta_cohort.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["cohort_not_found"])
        self.assertEqual(CourseInstructor.objects.count(), 0)
        self.assertEqual(CohortInstructor.objects.count(), 0)

    def test_another_centres_staff_number_is_not_found(self):
        create_staff(self.beta_owner, self.beta, "B-001", "Beta Teacher")
        record, problems, notes = assign_staff_to_course(
            self.owner, self.alpha, "B-001", self.alpha_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["staff_not_found"])

    def test_assignments_are_removed_and_a_missing_one_is_named(self):
        assign_staff_to_course(
            self.owner, self.alpha, "T-001", self.alpha_course.pk
        )
        assign_staff_to_cohort(
            self.owner, self.alpha, "T-001", self.alpha_cohort.pk
        )
        record, problems, notes = remove_staff_from_course(
            self.owner, self.alpha, "T-001", self.alpha_course.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(CourseInstructor.objects.count(), 0)
        record, problems, notes = remove_staff_from_cohort(
            self.owner, self.alpha, "T-001", self.alpha_cohort.pk
        )
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual(CohortInstructor.objects.count(), 0)
        record, problems, notes = remove_staff_from_course(
            self.owner, self.alpha, "T-001", self.alpha_course.pk
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["not_assigned"])

    def test_assignment_requires_the_assignment_permission(self):
        for user in (self.coordinator, self.receptionist, self.instructor):
            with self.assertRaises(PermissionDenied):
                assign_staff_to_course(
                    user, self.alpha, "T-001", self.alpha_course.pk
                )
        self.assertEqual(CourseInstructor.objects.count(), 0)


class LinkingTests(CenterFixture):
    """Explicit staff-record to account linking (STAFF-006, Phase 3A).

    Only an owner or an administrator at the centre may link or unlink
    (staff.link); linking is always explicit, so no automatic matching by
    name or email happens anywhere. Only an account that is already an
    active member of THIS centre can be linked here, each account holds at
    most one staff link per centre, and each staff record has at most one
    account. A link never creates or moves a person, never changes a
    membership or a role (SEC-001), and never touches employment status or
    assignments (STAFF-002/STAFF-003). Archived records keep their link
    until they are unlinked but cannot take a new one; on leave records can,
    and an inactive record cannot yet (docs/OPEN_QUESTIONS.md asks whether
    it should). One account legitimately a member of two centres can be
    linked at both, through that centre's own staff record.
    """

    def test_owner_links_an_active_staff_record_to_an_account_of_this_centre(self):
        account = CohortUser.objects.create(username="link-test-teacher")
        Members.objects.create(
            organization=self.alpha, user=account, role=ROLE_INSTRUCTOR
        )
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", account
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [STATUS_ACTIVE])
        self.assertEqual(record.user, account)
        self.assertEqual(record.staff_number, "T-001")
        self.assertEqual(record.employment_status, STATUS_ACTIVE)

    def test_owner_unlinks_and_the_employment_and_assignments_are_unchanged(self):
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        record, problems, notes = unlink_staff_account(
            self.owner, self.alpha, "T-001"
        )
        self.assertEqual(notes, ["unlinked"])
        self.assertIsNone(record.user)
        staff = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertEqual(staff.employment_status, STATUS_ACTIVE)
        self.assertEqual(CourseInstructor.objects.filter(staff=staff).count(), 0)

    def test_account_of_another_centre_cannot_be_linked_here(self):
        self.make_staff(self.beta_owner, self.beta, "B-001", "Beta Teacher")
        record, problems, notes = link_staff_account(
            self.beta_owner, self.beta, "B-001", self.instructor
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["account_not_found"])
        self.assertFalse(
            Staff.objects.filter(organization=self.beta).exclude(user=None).exists()
        )

    def test_suspended_membership_is_not_here_for_linking(self):
        account = CohortUser.objects.create(username="suspended-teacher")
        Members.objects.create(
            organization=self.alpha,
            user=account,
            role=ROLE_INSTRUCTOR,
            status="suspended",
        )
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", account
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["account_not_found"])

    def test_second_link_to_an_already_used_account_is_refused(self):
        self.make_staff(self.owner, self.alpha, "T-002", "Second Instructor")
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-002", self.instructor
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["account_taken"])
        self.assertEqual(
            Staff.objects.filter(organization=self.alpha, user=self.instructor).count(),
            1,
        )

    def test_already_linked_pair_is_answered_without_touching_anything(self):
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["already_linked"])
        staff = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertEqual(staff.user, self.instructor)

    def test_inactive_staff_record_cannot_be_newly_linked(self):
        self.make_staff(self.owner, self.alpha, "T-009", "Returned Departed")
        set_staff_status(self.owner, self.alpha, "T-009", STATUS_INACTIVE)
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-009", self.instructor
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["staff_not_linkable"])
        self.assertFalse(
            Staff.objects.filter(organization=self.alpha, staff_number="T-009")
            .exclude(user=None)
            .exists()
        )

    def test_archived_staff_record_cannot_be_newly_linked(self):
        self.make_staff(self.owner, self.alpha, "T-010", "Closed")
        set_staff_status(self.owner, self.alpha, "T-010", STATUS_ARCHIVED)
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-010", self.instructor
        )
        self.assertIsNone(record)
        self.assertEqual(notes, ["staff_not_linkable"])
        self.assertFalse(
            Staff.objects.filter(organization=self.alpha, staff_number="T-010")
            .exclude(user=None)
            .exists()
        )

    def test_archived_record_can_still_be_unlinked(self):
        self.make_staff(self.owner, self.alpha, "T-010", "Closed")
        link_staff_account(self.owner, self.alpha, "T-010", self.instructor)
        set_staff_status(self.owner, self.alpha, "T-010", STATUS_ARCHIVED)
        record, problems, notes = unlink_staff_account(
            self.owner, self.alpha, "T-010"
        )
        self.assertEqual(notes, ["unlinked"])
        self.assertIsNone(record.user)
        self.assertEqual(record.employment_status, STATUS_ARCHIVED)

    def test_link_is_refused_without_the_permission(self):
        for user in (self.coordinator, self.receptionist):
            with self.assertRaises(PermissionDenied):
                link_staff_account(
                    user, self.alpha, "T-001", self.instructor
                )
        self.assertFalse(
            Staff.objects.filter(organization=self.alpha).exclude(user=None).exists()
        )

    def test_unlink_refused_without_the_permission_leaves_the_link_intact(self):
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        for user in (self.coordinator, self.receptionist, self.instructor):
            with self.assertRaises(PermissionDenied):
                unlink_staff_account(user, self.alpha, "T-001")
        staff = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertEqual(staff.user, self.instructor)

    def test_link_never_changes_a_membership_or_a_role(self):
        before = Members.objects.get(
            user=self.instructor, organization=self.alpha
        ).role
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        self.assertEqual(
            Members.objects.get(
                user=self.instructor, organization=self.alpha
            ).role,
            before,
        )
        self.assertEqual(
            Members.objects.filter(user=self.instructor).count(), 1
        )

    def test_link_never_touches_employment_status_or_assignments(self):
        course = self.make_course(self.owner, self.alpha, "Linked Course")
        self.make_cohort(self.owner, self.alpha, course, "Linked Cohort")
        assign_staff_to_course(
            self.owner, self.alpha, "T-001", course.pk
        )
        assign_staff_to_cohort(
            self.owner, self.alpha, "T-001", self.alpha_cohort.pk
        )
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        staff = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertEqual(staff.employment_status, STATUS_ACTIVE)
        self.assertEqual(CourseInstructor.objects.filter(staff=staff).count(), 1)
        self.assertEqual(CohortInstructor.objects.filter(staff=staff).count(), 1)

    def test_on_leave_record_can_be_linked(self):
        self.make_staff(
            self.owner, self.alpha, "T-011", "On Leave Instructor"
        )
        set_staff_status(self.owner, self.alpha, "T-011", STATUS_ON_LEAVE)
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-011", self.instructor
        )
        self.assertEqual(notes, [STATUS_ON_LEAVE])
        self.assertEqual(record.employment_status, STATUS_ON_LEAVE)

    def test_the_database_refuses_a_second_link_to_one_account_at_one_centre(self):
        linked, _problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        self.make_staff(self.owner, self.alpha, "T-002", "Second Instructor")
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Staff.objects.filter(
                organization=self.alpha, staff_number="T-002"
            ).update(user=self.instructor)
        refused = Staff.objects.get(organization=self.alpha, staff_number="T-002")
        self.assertIsNone(refused.user)

    def test_one_account_can_be_linked_at_two_centres_through_the_right_records(self):
        self.make_staff(self.beta_owner, self.beta, "T-001", "Beta Instructor")
        account = CohortUser.objects.create(username="two-centre-teacher")
        Members.objects.create(
            organization=self.alpha, user=account, role=ROLE_INSTRUCTOR
        )
        Members.objects.create(
            organization=self.beta, user=account, role=ROLE_INSTRUCTOR
        )
        record_a, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", account
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        record_b, problems, notes = link_staff_account(
            self.beta_owner, self.beta, "T-001", account
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        self.assertEqual(account.staff_links.count(), 2)
        self.assertNotEqual(record_a, record_b)

    def test_linking_creates_no_account_and_grants_no_access(self):
        before_users = CohortUser.objects.count()
        before_members = Members.objects.count()
        account = self.instructor
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", account
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        # Nothing was created or moved anywhere:
        self.assertEqual(CohortUser.objects.count(), before_users)
        self.assertEqual(Members.objects.count(), before_members)
        self.assertEqual(account.staff_links.count(), 1)

    def test_a_link_survives_when_its_account_is_no_longer_active_here(self):
        """The service refuses to unlink a row whose account is not an active
        member of this centre any more, so a person can never lose a link
        they cannot see; the record keeps its link until the membership
        comes back."""
        record, problems, notes = link_staff_account(
            self.owner, self.alpha, "T-001", self.instructor
        )
        self.assertEqual(notes, [STATUS_ACTIVE])
        Members.objects.filter(
            user=self.instructor, organization=self.alpha
        ).update(status="suspended")
        record, problems, notes = unlink_staff_account(
            self.owner, self.alpha, "T-001"
        )
        self.assertEqual(notes, ["account_not_found"])
        staff = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        self.assertIsNotNone(staff.user)
        Members.objects.filter(
            user=self.instructor, organization=self.alpha
        ).update(status="active")
        record, problems, notes = unlink_staff_account(
            self.owner, self.alpha, "T-001"
        )
        self.assertEqual(notes, ["unlinked"])
        self.assertIsNone(record.user)

    def test_the_model_guard_refuses_a_link_to_an_account_that_is_not_here(self):
        beta_only = CohortUser.objects.create(username="beta-only")
        Members.objects.create(
            organization=self.beta, user=beta_only, role=ROLE_INSTRUCTOR
        )
        record = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        record.user = beta_only
        with self.assertRaises(ValidationError):
            record.full_clean()
        self.assertFalse(
            Staff.objects.filter(organization=self.alpha).exclude(user=None).exists()
        )

    def test_the_model_guard_refuses_a_link_at_another_centre(self):
        record = Staff.objects.get(organization=self.alpha, staff_number="T-001")
        record.organization = self.beta
        record.user = self.instructor
        with self.assertRaises(ValidationError):
            record.full_clean()
        self.assertFalse(
            Staff.objects.filter(organization=self.beta).exclude(user=None).exists()
        )
