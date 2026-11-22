"""Signing in, out, and choosing a center, through the URLs (AUTH-001..004).

Every request is a real form POST carrying the CSRF token the page itself put
in its cookie, so the proof covers the framework's session and form checks, not
a call into a view.
"""

import re

from django.test import Client, TestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import ROLE_INSTRUCTOR, ROLE_OWNER


class SigningIn(TestCase):
    def setUp(self):
        self.single = Organization.objects.create(
            name="Single Center", slug="single-center", primary_currency="DZD"
        )
        self.other = Organization.objects.create(
            name="Other Center", slug="other-center", primary_currency="EUR"
        )
        self.person = CohortUser.objects.create_user(
            username="solo-person", password="right-password", display_name="Solo Person"
        )
        self.unattached = CohortUser.objects.create_user(
            username="no-center", password="right-password", display_name="No Center"
        )
        Members.objects.create(
            user=self.person, organization=self.single, role=ROLE_OWNER
        )

    def test_one_center_signs_straight_into_its_dashboard(self):
        """One centre is decided for the person; the address is theirs."""
        page = self.client.get("/accounts/sign-in/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["X-CohortDesk-Page"], "sign_in")
        self.assertIn('name="username"', page.content.decode())
        answer = self.client.post(
            "/accounts/sign-in/",
            {
                "username": "solo-person",
                "password": "right-password",
            },
            follow=True,
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "dashboard")
        self.assertIn("Single Center", answer.content.decode())
        self.assertNotIn("Other Center", answer.content.decode())

    def test_wrong_password_stays_on_the_page(self):
        """A failed sign-in is answered on the same page, never signed in."""
        page = self.client.get("/accounts/sign-in/")
        answer = self.client.post(
            "/accounts/sign-in/",
            {
                "username": "solo-person",
                "password": "not-the-password",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "sign_in")
        self.assertIn("could not sign you in", answer.content.decode())
        denied = self.client.get("/o/single-center/center/members/")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied["X-CohortDesk-Page"], "problem")

    def test_two_centers_are_asked_about(self):
        """More than one centre: the person is sent to the chooser."""
        Members.objects.create(
            user=self.person, organization=self.other, role=ROLE_OWNER
        )
        page = self.client.get("/accounts/sign-in/")
        answer = self.client.post(
            "/accounts/sign-in/",
            {
                "username": "solo-person",
                "password": "right-password",
            },
            follow=True,
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "choose")
        body = answer.content.decode()
        self.assertIn('value="single-center"', body)
        self.assertIn('value="other-center"', body)

    def test_no_center_goes_to_the_chooser_saying_so(self):
        """An account with no membership gets nothing offered."""
        page = self.client.get("/accounts/sign-in/")
        answer = self.client.post(
            "/accounts/sign-in/",
            {
                "username": "no-center",
                "password": "right-password",
            },
            follow=True,
        )
        self.assertEqual(answer["X-CohortDesk-Page"], "choose")
        self.assertIn('id="choose_none"', answer.content.decode())

    def test_chooser_only_offers_theirs(self):
        """Another centre is not even an option to pick."""
        self.client.force_login(self.person)
        page = self.client.get("/accounts/choose/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["X-CohortDesk-Page"], "choose")
        body = page.content.decode()
        self.assertIn('value="single-center"', body)
        self.assertNotIn('value="other-center"', body)

    def test_picking_a_center_moves_the_working_center(self):
        """A centre that is not theirs changes nothing at all."""
        Members.objects.create(
            user=self.person, organization=self.other, role=ROLE_INSTRUCTOR
        )
        self.client.force_login(self.person)
        page = self.client.get("/accounts/choose/")
        answer = self.client.post(
            "/accounts/choose/",
            {
                "center": "other-center",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "choose")
        self.assertIn("Other Center", answer.content.decode())
        answer = self.client.post(
            "/accounts/choose/",
            {
                "center": "no-such-center",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("Nothing was changed", answer.content.decode())
        self.assertIn("Other Center", answer.content.decode())

    def test_signing_out_leaves_the_session_behind(self):
        """Signing out ends the session, at the landing page that says so."""
        page = self.client.get("/accounts/sign-in/")
        signed = self.client.post(
            "/accounts/sign-in/",
            {
                "username": "solo-person",
                "password": "right-password",
            },
            follow=True,
        )
        self.assertEqual(signed["X-CohortDesk-Page"], "dashboard")
        answer = self.client.get("/accounts/sign-out/", follow=True)
        self.assertEqual(answer["X-CohortDesk-Page"], "landing")
        self.assertIn("signed out", answer.content.decode())
        denied = self.client.get("/o/single-center/center/members/")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied["X-CohortDesk-Page"], "problem")


class BrowserFormContract(TestCase):
    """What a real browser needs that the default test client hides.

    The default client disables CSRF checks and posts fields directly, so it
    cannot see two defects a browser cannot get past: a page whose form has no
    CSRF token (every POST answered 403) and a form with no submit control
    (a person has no way to send it). Both were found by the browser smoke
    test, not by the suite; these tests keep them from coming back.
    """

    def setUp(self):
        self.center = Organization.objects.create(
            name="Form Center", slug="form-center", primary_currency="DZD"
        )
        self.person = CohortUser.objects.create_user(
            username="form-person", password="right-password"
        )
        Members.objects.create(
            user=self.person, organization=self.center, role=ROLE_OWNER
        )

    def token_of(self, body):
        found = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"', body
        )
        self.assertIsNotNone(found, "the form carries no CSRF token")
        return found.group(1)

    def test_sign_in_page_carries_a_token_and_a_submit_button(self):
        body = self.client.get("/accounts/sign-in/").content.decode()
        self.token_of(body)
        self.assertIn('type="submit"', body)
        self.assertIn(">Sign in</button>", body)

    def test_a_csrf_enforcing_client_signs_in_through_the_rendered_form(self):
        strict = Client(enforce_csrf_checks=True)
        body = strict.get("/accounts/sign-in/").content.decode()
        answer = strict.post(
            "/accounts/sign-in/",
            {
                "username": "form-person",
                "password": "right-password",
                "csrfmiddlewaretoken": self.token_of(body),
            },
            follow=True,
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "dashboard")
        self.assertIn("Form Center", answer.content.decode())

    def test_chooser_carries_a_token_and_a_submit_button(self):
        self.client.force_login(self.person)
        body = self.client.get("/accounts/choose/").content.decode()
        self.token_of(body)
        self.assertIn('type="submit"', body)

    def test_shell_offers_sign_out_when_signed_in_and_sign_in_otherwise(self):
        anon = self.client.get("/accounts/sign-in/").content.decode()
        self.assertIn('href="/accounts/sign-in/"', anon)
        self.assertNotIn('href="/accounts/sign-out/"', anon)
        self.client.force_login(self.person)
        inside = self.client.get("/accounts/choose/").content.decode()
        self.assertIn('href="/accounts/sign-out/"', inside)
        self.assertIn("Sign out", inside)
        self.assertNotIn('href="/accounts/sign-in/"', inside)

    def test_chooser_marks_the_working_center_and_links_into_it(self):
        other = Organization.objects.create(
            name="Other Form Center", slug="other-form-center", primary_currency="EUR"
        )
        Members.objects.create(
            user=self.person, organization=other, role=ROLE_OWNER
        )
        self.client.force_login(self.person)
        body = self.client.post(
            "/accounts/choose/", {"center": "other-form-center"}
        ).content.decode()
        self.assertIn(
            '<option value="other-form-center" selected>', body
        )
        self.assertIn('href="/o/other-form-center/center/dashboard/"', body)
