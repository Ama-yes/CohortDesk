"""Foundation tests for Organization, CohortUser and Members (milestone M0).

These run against the test database built from the real migrations. Membership
isolation tests here verify FK scoping only — full tenant isolation is audited
in a later milestone.
"""
from django import db
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import CohortUser
from .models import Organization, Members
from .roles import ROLE_OWNER, ROLE_INSTRUCTOR, ROLE_ADMINISTRATOR


class OrganizationTestCase(TestCase):
    def test_organization_creation(self):
        org = Organization.objects.create(
            name="Bright Center",
            slug="bright-center",
            primary_currency="DZD",
        )
        org.full_clean()
        self.assertEqual(org.slug, "bright-center")
        self.assertEqual(org.status, "active")

    def test_organization_rejects_bad_slug(self):
        org = Organization(
            name="Bright Center",
            slug="Bad Slug!",
            primary_currency="DZD",
        )
        with self.assertRaises(ValidationError):
            org.full_clean()


class UserCreationTestCase(TestCase):
    def test_user_creation(self):
        user = CohortUser.objects.create_user(
            username="u-one",
            password="test-password",
            display_name="  Test User  ",
        )
        user.full_clean()
        self.assertEqual(user.username, "u-one")
        self.assertEqual(user.display_name, "Test User")


class MembershipTestCase(TestCase):
    def test_membership_creation(self):
        org = Organization.objects.create(
            name="Center One", slug="center-one", primary_currency="DZD"
        )
        user = CohortUser.objects.create_user(username="member-one", password="x")
        member = Members.objects.create(
            user=user, organization=org, role=ROLE_OWNER
        )
        self.assertIn(member, org.members.all())
        self.assertIn(member, user.memberships.all())

    def test_duplicate_membership_rejected(self):
        org = Organization.objects.create(
            name="Center Two", slug="center-two", primary_currency="EUR"
        )
        user = CohortUser.objects.create_user(username="member-two", password="x")
        Members.objects.create(user=user, organization=org, role=ROLE_INSTRUCTOR)
        with self.assertRaises(db.IntegrityError):
            Members.objects.create(
                user=user, organization=org, role=ROLE_ADMINISTRATOR
            )

    def test_membership_isolation_scoped(self):
        org1 = Organization.objects.create(
            name="Center A", slug="center-a", primary_currency="DZD"
        )
        org2 = Organization.objects.create(
            name="Center B", slug="center-b", primary_currency="EUR"
        )
        u1 = CohortUser.objects.create_user(username="iso-one", password="x")
        u2 = CohortUser.objects.create_user(username="iso-two", password="x")
        Members.objects.create(user=u1, organization=org1, role=ROLE_OWNER)
        Members.objects.create(user=u2, organization=org2, role=ROLE_OWNER)
        self.assertEqual(org1.members.count(), 1)
        self.assertEqual(org2.members.count(), 1)
        self.assertEqual(u1.memberships.count(), 1)
        self.assertEqual(u1.memberships.filter(organization=org2).count(), 0)
        self.assertEqual(
            list(org1.members.values_list("user_id", flat=True)),
            [u1.pk],
        )
