"""Tenant isolation proved through the URL layer (AUTH-005, SEC-001/003/004).

The test client asks config.urls itself, so reverse()/resolve() choose the view
by the address, exactly as a browser's request would, and every answer is
checked by the page kind the chain records. Nothing here reaches a view
function directly: the proof goes through the addresses.
"""

from django.test import TestCase

from apps.accounts.models import CohortUser
from .models import Members, Organization
from .roles import ROLE_OWNER, ROLE_INSTRUCTOR


class TwoCenters(TestCase):
    def setUp(self):
        self.alpha = Organization.objects.create(
            name="Alpha Center", slug="alpha-center", primary_currency="DZD"
        )
        self.beta = Organization.objects.create(
            name="Beta Center", slug="beta-center", primary_currency="EUR"
        )
        self.alpha_owner = CohortUser.objects.create_user(
            username="alpha-owner", password="x", display_name="Alpha Owner"
        )
        self.beta_owner = CohortUser.objects.create_user(
            username="beta-owner", password="x", display_name="Beta Owner"
        )
        self.outsider = CohortUser.objects.create_user(
            username="outsider", password="x", display_name="Outsider"
        )
        Members.objects.create(
            user=self.alpha_owner, organization=self.alpha, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.beta_owner, organization=self.beta, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.outsider, organization=self.alpha, role=ROLE_INSTRUCTOR
        )

    def test_other_tenant_address_is_not_readable(self):
        """A person in one centre cannot open the other centre's page."""
        self.client.force_login(self.alpha_owner)
        answer = self.client.get("/o/beta-center/center/dashboard/")
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        body = answer.content.decode()
        self.assertNotIn("Beta Center", body)
        self.assertNotIn("EUR", body)

    def test_unknown_address_matches_other_tenant_answer(self):
        """An unseen centre and an unknown address answer the same way."""
        self.client.force_login(self.alpha_owner)
        unseen = self.client.get("/o/beta-center/center/dashboard/")
        unknown = self.client.get("/o/no-such-center/center/dashboard/")
        self.assertEqual(unseen.status_code, unknown.status_code)
        self.assertEqual(unseen.content, unknown.content)
        self.assertEqual(unseen["X-CohortDesk-Page"], unknown["X-CohortDesk-Page"])

    def test_signed_out_person_sees_no_center_data(self):
        """Nothing a centre holds is ever in the answer for no person."""
        answer = self.client.get("/o/alpha-center/center/members/")
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        body = answer.content.decode()
        self.assertNotIn("Alpha Owner", body)
        self.assertNotIn("alpha-owner", body)

    def test_suspended_membership_is_not_access(self):
        """A session is never access: a suspended person loses the pages."""
        self.client.force_login(self.beta_owner)
        self.assertEqual(
            self.client.get("/o/beta-center/center/members/").status_code, 200
        )
        Members.objects.filter(
            user=self.beta_owner, organization=self.beta
        ).update(status="suspended")
        answer = self.client.get("/o/beta-center/center/members/")
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_role_not_permitted_is_denied_not_absent(self):
        """A member here, but not for this page: this centre's own no."""
        self.client.force_login(self.outsider)
        answer = self.client.get("/o/alpha-center/center/settings/")
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        body = answer.content.decode()
        self.assertIn("Alpha Center", body)  # this centre's own page says which one
        self.assertNotIn("DZD", body)  # but not what its settings hold

    def test_hidden_link_is_not_authorization(self):
        """Coming straight at the address, with no link, gives the same no."""
        self.client.force_login(self.outsider)
        page = self.client.get("/o/alpha-center/center/settings/change/")
        self.assertEqual(page.status_code, 403)
        self.assertEqual(page["X-CohortDesk-Page"], "denied")
        answer = self.client.post(
            "/o/alpha-center/center/settings/change/",
            {
                "primary_currency": "USD",
            },
        )
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
        self.assertEqual(self.alpha.primary_currency, "DZD")

    def test_bad_value_marked_on_the_same_page(self):
        """A rejected change is marked on the page the person is on."""
        self.client.force_login(self.alpha_owner)
        page = self.client.get("/o/alpha-center/center/settings/change/")
        self.assertEqual(page.status_code, 200)
        answer = self.client.post(
            "/o/alpha-center/center/settings/change/",
            {
                "primary_currency": "bad!",
                "display_timezone": "Not/AZone",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "settings_change")
        body = answer.content.decode()
        self.assertIn('id="primary_currency_problem"', body)
        self.assertIn('id="display_timezone_problem"', body)
        self.assertEqual(self.alpha.primary_currency, "DZD")
        self.assertEqual(self.alpha.display_timezone, "UTC")

    def test_duplicate_person_changes_nothing(self):
        """Adding a person who is already here leaves nothing changed."""
        self.client.force_login(self.alpha_owner)
        page = self.client.get("/o/alpha-center/center/members/manage/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["X-CohortDesk-Page"], "members_manage")
        answer = self.client.post(
            "/o/alpha-center/center/members/manage/",
            {
                "members_action": "add",
                "members_target": "alpha-owner",
                "members_role": "instructor",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "members_manage")
        self.assertIn('id="members_target_problem"', answer.content.decode())
        self.assertEqual(self.alpha.members.count(), 2)

    def test_write_to_a_read_only_address_is_the_problem_page(self):
        """A write at a read-only centre address is 405, not a 500.

        The problem page is a project page; it must be built without the
        centre, or page() looks for a centre template named "problem".
        """
        self.client.force_login(self.alpha_owner)
        addresses = (
            "/o/alpha-center/center/dashboard/",
            "/o/alpha-center/center/settings/",
            "/o/alpha-center/center/members/",
        )
        for address in addresses:
            answer = self.client.post(address, {})
            self.assertEqual(answer.status_code, 405)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")

    def test_listed_people_are_this_center_only(self):
        """The people list a centre shows never carries another centre's."""
        self.client.force_login(self.alpha_owner)
        body = self.client.get("/o/alpha-center/center/members/").content.decode()
        self.assertIn("Alpha Owner", body)
        self.assertNotIn("Beta Owner", body)
        self.assertNotIn("beta-center", body)

    def test_navigation_names_only_pages_this_role_may_open(self):
        """The centre's page chain offers only the names this role holds (RULE 6.8, SEC-001)."""
        self.client.force_login(self.outsider)
        body = self.client.get("/o/alpha-center/center/dashboard/").content.decode()
        self.assertIn('data-kind="dashboard"', body)
        self.assertIn('data-kind="students"', body)
        self.assertIn('data-kind="courses"', body)
        for hidden in (
            "settings",
            "members",
            "applications",
            "cohorts",
            "enrollments",
            "staff",
        ):
            self.assertNotIn('data-kind="{}"'.format(hidden), body)
        self.assertIn('data-kind="accounts.choose"', body)

    def test_hidden_page_name_still_answers_by_permission(self):
        """A name hidden from navigation is not what closes or opens the address."""
        self.client.force_login(self.outsider)
        answer = self.client.get("/o/alpha-center/center/courses/")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "courses")
        answer = self.client.get("/o/alpha-center/center/cohorts/")
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer["X-CohortDesk-Page"], "denied")
