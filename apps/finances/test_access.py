"""The finance pages and the receipt file proved through the URL layer.

SEC-004/SEC-006 and FIN-001..FIN-015's access half: every answer below is
chosen by the address exactly as a browser request would be, and every
answer is checked by the page kind the chain records. The same receipt
number at two centres names two different files, and no finance page of
one centre may carry a record of another. The desk role, the instructor
and the academic coordinator have no finance access at all (SRS 4.4).

The agreement itself is seeded through the service layer here on purpose:
these tests are about what the addresses answer, not about the service
rules, which apps/finances/test_model_rules.py proves. The actions this
phase's people take on the finance pages (record, reverse, withdraw) go
through POST because that is the only path a person has.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import CohortUser
from apps.organizations.models import Members, Organization
from apps.organizations.roles import (
    ROLE_FINANCE_OFFICER,
    ROLE_INSTRUCTOR,
    ROLE_OWNER,
    ROLE_RECEPTIONIST,
)
from apps.students.models import Student
from apps.students.statuses import STATUS_ACTIVE
from apps.academics.services import create_course, create_cohort, enroll_student

from .models import (
    Allocation,
    FeeInstallment,
    FinanceAuditEvent,
    FinanceSubmission,
    FinancialAdjustment,
    Payment,
    Receipt,
)
from .services import (
    create_fee_installment,
    create_fee_plan,
    open_fee_obligation,
    post_payment,
)
from .statuses import (
    ADJ_REVERSAL,
    AUDIT_PAYMENT_POSTED,
    METHOD_CASH,
    PAYMENT_RECORDED,
)

IN_DAYS = 10
OUT_DAYS = 20


class TwoCenters(TestCase):
    """Two centres whose first receipts both carry the number 1."""

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
        self.desk = CohortUser.objects.create_user(
            username="alpha-desk", password="x", display_name="Alpha Desk"
        )
        self.teacher = CohortUser.objects.create_user(
            username="alpha-teacher", password="x", display_name="Alpha Teacher"
        )
        self.money = CohortUser.objects.create_user(
            username="alpha-money", password="x", display_name="Alpha Money"
        )
        Members.objects.create(
            user=self.alpha_owner, organization=self.alpha, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.beta_owner, organization=self.beta, role=ROLE_OWNER
        )
        Members.objects.create(
            user=self.desk, organization=self.alpha, role=ROLE_RECEPTIONIST
        )
        Members.objects.create(
            user=self.teacher, organization=self.alpha, role=ROLE_INSTRUCTOR
        )
        Members.objects.create(
            user=self.money, organization=self.alpha, role=ROLE_FINANCE_OFFICER
        )
        self.alpha_student = Student.objects.create(
            organization=self.alpha,
            student_number="AC-1",
            name="Alpha Learner",
            contact="alpha@example.test",
            status=STATUS_ACTIVE,
        )
        self.beta_student = Student.objects.create(
            organization=self.beta,
            student_number="AC-1",
            name="Beta Learner",
            contact="beta@example.test",
            status=STATUS_ACTIVE,
        )
        self.agreement(self.alpha, self.alpha_owner, "Alpha A", "Alpha course",
                       self.alpha_student, "The autumn split",
                       Decimal("600"), Decimal("400"), Decimal("600"))
        self.agreement(self.beta, self.beta_owner, "Beta A", "Beta course",
                       self.beta_student, "The winter split",
                       Decimal("500"), Decimal("400"), Decimal("500"))

    def agreement(self, center, owner, cohort_name, course_title, student,
                  plan_name, first_amount, second_amount, payment_amount):
        """One opened agreement with a plan, two installments, one payment."""
        course, problems, notes = create_course(
            owner, center, course_title
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        cohort, problems, notes = create_cohort(
            owner, center, cohort_name, course.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        enrollment, problems, notes = enroll_student(
            owner,
            center,
            cohort.pk,
            student.student_number,
            agreed_fee=first_amount + second_amount,
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        obligation, problems, notes = open_fee_obligation(
            owner, center, enrollment.pk
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        fee_plan, problems, notes = create_fee_plan(
            owner, center, obligation.pk, plan_name
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        first, problems, notes = create_fee_installment(
            owner,
            center,
            fee_plan.pk,
            first_amount,
            date.today() + timedelta(days=IN_DAYS),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        second, problems, notes = create_fee_installment(
            owner,
            center,
            fee_plan.pk,
            second_amount,
            date.today() + timedelta(days=OUT_DAYS),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        payment, problems, notes = post_payment(
            owner,
            center,
            obligation.pk,
            payment_amount,
            METHOD_CASH,
            allocations=((first.pk, payment_amount),),
        )
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertIsNotNone(payment)
        if center == self.alpha:
            self.obligation = obligation
            self.first = first
            self.second = second
            self.payment = payment
            self.receipt = Receipt.objects.filter(
                organization=self.alpha, payment=payment
            ).first()

    def finances_url(self, center=None):
        return "/o/{}/center/finances/".format((center or self.alpha).slug)

    def obligations_url(self, center=None):
        return "{}obligations/".format(self.finances_url(center))

    def obligations_manage_url(self, center=None):
        return "{}manage/".format(self.obligations_url(center))

    def payments_url(self, center=None):
        return "{}payments/".format(self.finances_url(center))

    def payments_manage_url(self, center=None):
        return "{}manage/".format(self.payments_url(center))

    def payment_detail_url(self, number, center=None):
        return "{}payments/{}/".format(self.finances_url(center), number)

    def receipts_url(self, center=None):
        return "{}receipts/".format(self.finances_url(center))

    def receipt_url(self, number, center=None):
        return "{}receipts/{}/".format(self.finances_url(center), number)

    def receipt_csv_url(self, number, center=None):
        return "{}receipts/{}.csv".format(self.finances_url(center), number)

    def body(self, response):
        return response.content.decode()

    # -- the finance line opens for the roles that own it -----------------

    def test_finance_officer_opens_the_hub(self):
        self.client.force_login(self.money)
        answer = self.client.get(self.finances_url())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "finances")
        self.assertIn("Alpha Center", self.body(answer))

    def test_owner_reads_the_obligation_list(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.obligations_url())
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "finances_obligations")
        self.assertIn("Alpha Learner", self.body(answer))
        self.assertNotIn("Beta Learner", self.body(answer))
        self.assertNotIn("/o/beta-center/", self.body(answer))

    # -- the roles without any finance line are denied, not hidden --------

    def test_desk_instructor_and_coordinator_get_denied_pages(self):
        for person in (self.desk, self.teacher):
            self.client.force_login(person)
            for address in (
                self.finances_url(),
                self.obligations_url(),
                self.payments_url(),
                self.receipts_url(),
            ):
                answer = self.client.get(address)
                self.assertEqual(answer.status_code, 403)
                self.assertEqual(answer["X-CohortDesk-Page"], "denied")
                body = self.body(answer)
                self.assertIn("Alpha Center", body)
                self.assertNotIn("Alpha Learner", body)

    def test_signed_out_person_sees_no_finance_data(self):
        answer = self.client.get(self.finances_url())
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Alpha Learner", self.body(answer))

    # -- both directions of the tenant wall --------------------------------

    def test_other_centres_finance_pages_are_the_problem_page(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.finances_url(self.beta))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Beta Learner", self.body(answer))
        self.assertNotIn("beta@example.test", self.body(answer))

    def test_other_centres_record_number_is_a_local_not_found(self):
        """Another centre's payment number gets this centre's own page."""
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(
            self.payment_detail_url(self.beta_payment_pk(), self.alpha)
        )
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        body = self.body(answer)
        self.assertNotIn("Beta Learner", body)
        self.assertNotIn("beta@example.test", body)

    def beta_payment_pk(self):
        return Payment.objects.filter(
            organization=self.beta
        ).order_by("pk").first().pk

    # -- the same receipt number 1 is two different people's receipts ------

    def test_number_one_here_is_this_centres_receipt(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.receipt_url(1))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "finances_receipt")
        body = self.body(answer)
        self.assertIn("Alpha Learner", body)
        self.assertNotIn("Beta Learner", body)
        self.assertNotIn("/o/beta-center/", self.body(answer))

    def test_number_one_there_is_the_other_centres_receipt(self):
        self.client.force_login(self.beta_owner)
        answer = self.client.get(self.receipt_url(1, self.beta))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "finances_receipt")
        body = self.body(answer)
        self.assertIn("Beta Learner", body)
        self.assertNotIn("Alpha Learner", body)

    def test_number_one_is_not_a_bridge_between_centres(self):
        """The same address at both centres answers from that centre only."""
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(self.receipt_url(1, self.beta))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Beta Learner", self.body(answer))

    # -- the receipt file: same rows, same rules, its own download --------

    def test_receipt_download_is_a_csv_with_the_receipt_rows(self):
        self.client.force_login(self.money)
        answer = self.client.get(self.receipt_csv_url(1))
        self.assertEqual(answer.status_code, 200)
        self.assertIn("text/csv", answer["Content-Type"])
        self.assertIn(
            'filename="receipt-alpha-center-1.csv"',
            answer["Content-Disposition"],
        )
        lines = self.body(answer).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("AC-1", lines[1])
        self.assertIn("600.00", lines[1])
        self.assertNotIn("Beta Learner", self.body(answer))
        self.assertNotIn("500.00", self.body(answer))

    def test_other_centres_receipt_file_never_downloads_here(self):
        self.client.force_login(self.alpha_owner)
        answer = self.client.get(
            self.receipt_csv_url(1, self.beta),
        )
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "problem")
        self.assertNotIn("Content-Disposition", answer)

    def test_missing_receipt_number_is_the_centres_own_page(self):
        self.client.force_login(self.money)
        answer = self.client.get(self.receipt_csv_url(99))
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer["X-CohortDesk-Page"], "not_found")
        self.assertNotIn("Content-Disposition", answer)

    # -- the write addresses, both refused and accepted -------------------

    def test_duplicate_payment_is_refused_and_stays_out_of_the_rows(self):
        """The second 600 on the paid installment never splits it."""
        self.client.force_login(self.money)
        answer = self.client.post(
            self.payments_manage_url(),
            {
                "payment_action": "record",
                "payment_target": str(self.obligation.pk),
                "payment_amount": "600.00",
                "payment_method": METHOD_CASH,
                "payment_allocations": f"{self.first.pk}=600.00",
                "payment_token": "tok-second-600",
                "payment_confirm": "yes",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "finances_payments_manage")
        self.assertEqual(
            Payment.objects.filter(
                organization=self.alpha, obligation=self.obligation
            ).count(),
            1,
        )
        self.assertEqual(
            Receipt.objects.filter(organization=self.alpha).count(), 1
        )

    def test_reversal_recorded_through_the_address_counts_once(self):
        self.client.force_login(self.money)
        answer = self.client.post(
            self.payments_manage_url(),
            {
                "payment_action": "reverse",
                "payment_payment": str(self.payment.pk),
                "payment_amount": "200.00",
                "payment_reason": "The family moved away",
                "payment_token": "tok-taken-back-200",
                "payment_confirm": "yes",
            },
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(
            answer["X-CohortDesk-Page"], "finances_payments_manage"
        )
        self.assertEqual(
            FinancialAdjustment.objects.filter(
                organization=self.alpha,
                payment=self.payment,
                kind=ADJ_REVERSAL,
            ).count(),
            1,
        )
        self.assertEqual(self.payment.status, PAYMENT_RECORDED)
        self.assertEqual(
            Receipt.objects.filter(organization=self.alpha).count(), 1
        )
        self.assertEqual(
            Payment.objects.filter(organization=self.alpha).count(), 1
        )

    # -- the read addresses never take a write -----------------------------

    def test_write_methods_at_read_addresses_are_the_problem_page(self):
        self.client.force_login(self.alpha_owner)
        for address in (self.finances_url(), self.obligations_url(),
                        self.receipts_url(), self.receipt_csv_url(1)):
            answer = self.client.put(address, {})
            self.assertEqual(answer.status_code, 405)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")
            answer = self.client.delete(address)
            self.assertEqual(answer.status_code, 405)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")


class PaymentSubmissionTests(TwoCenters):
    """D86: one page's own submission code posts the money only once.

    A posting through the address needs both this page's fresh
    submission code and an explicit confirmation (the SRS 8.1 rule
    that financially sensitive actions need confirming and show their
    consequences), and the same code posted twice never takes the
    money a second time. The code is a claim of one person at one
    centre: the same code offered by another person or at another
    centre posts its own new money instead.
    """

    def post(self, center, fields):
        answer = self.client.post(
            self.payments_manage_url(center), fields
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(
            answer["X-CohortDesk-Page"], "finances_payments_manage"
        )
        return answer

    def test_a_post_needs_confirmation_and_its_own_code(self):
        """A cold post with neither changes nothing at all."""
        self.client.force_login(self.money)
        self.post(
            self.alpha,
            {
                "payment_action": "record",
                "payment_target": str(self.obligation.pk),
                "payment_amount": "400.00",
                "payment_method": METHOD_CASH,
                "payment_allocations": f"{self.second.pk}=400.00",
            },
        )
        self.assertEqual(
            Payment.objects.filter(organization=self.alpha).count(), 1
        )
        self.assertEqual(
            Receipt.objects.filter(organization=self.alpha).count(), 1
        )
        self.assertEqual(FinanceSubmission.objects.count(), 0)

    def test_same_submission_code_posts_the_payment_once(self):
        """The replay of a taken code shows the saved payment, not new money."""
        self.client.force_login(self.money)
        fields = {
            "payment_action": "record",
            "payment_target": str(self.obligation.pk),
            "payment_amount": "400.00",
            "payment_method": METHOD_CASH,
            "payment_allocations": f"{self.second.pk}=400.00",
            "payment_token": "tok-keep",
            "payment_confirm": "yes",
        }
        self.post(self.alpha, fields)
        posted = Payment.objects.filter(
            organization=self.alpha, obligation=self.obligation
        ).order_by("pk").last()
        self.assertEqual(
            FinanceSubmission.objects.filter(
                organization=self.alpha,
                recorded_by=self.money,
                token="tok-keep",
            ).first().payment.pk,
            posted.pk,
        )
        answer = self.post(self.alpha, fields)
        self.assertIn("already posted here", self.body(answer))
        self.assertEqual(
            Payment.objects.filter(organization=self.alpha).count(), 2
        )
        self.assertEqual(
            Receipt.objects.filter(organization=self.alpha).count(), 2
        )
        self.assertEqual(
            Allocation.objects.filter(organization=self.alpha).count(), 2
        )
        self.assertEqual(
            FinanceSubmission.objects.filter(
                organization=self.alpha,
                recorded_by=self.money,
                token="tok-keep",
            ).count(),
            1,
        )
        self.assertEqual(
            FinanceAuditEvent.objects.filter(
                payment=posted,
                action=AUDIT_PAYMENT_POSTED,
            ).count(),
            1,
        )

    def test_fresh_submission_code_posts_the_next_payment(self):
        """After a taken code, a fresh one still moves money of its own."""
        self.client.force_login(self.money)
        for token, amount in (("tok-300", "300.00"), ("tok-100", "100.00")):
            self.post(
                self.alpha,
                {
                    "payment_action": "record",
                    "payment_target": str(self.obligation.pk),
                    "payment_amount": amount,
                    "payment_method": METHOD_CASH,
                    "payment_allocations": f"{self.second.pk}={amount}",
                    "payment_token": token,
                    "payment_confirm": "yes",
                },
            )
        self.assertEqual(
            Payment.objects.filter(organization=self.alpha).count(), 3
        )
        self.assertEqual(
            Receipt.objects.filter(organization=self.alpha).count(), 3
        )
        self.assertEqual(
            Allocation.objects.filter(organization=self.alpha).count(), 3
        )

    def test_reversal_code_replays_to_one_adjustment(self):
        """Replaying a reversal's code never records a second reversal."""
        self.client.force_login(self.money)
        fields = {
            "payment_action": "reverse",
            "payment_payment": str(self.payment.pk),
            "payment_amount": "200.00",
            "payment_reason": "The family moved away",
            "payment_token": "tok-r-keep",
            "payment_confirm": "yes",
        }
        self.post(self.alpha, fields)
        answer = self.post(self.alpha, fields)
        self.assertIn("already posted here", self.body(answer))
        self.assertEqual(
            FinancialAdjustment.objects.filter(
                organization=self.alpha,
                payment=self.payment,
                kind=ADJ_REVERSAL,
            ).count(),
            1,
        )
        self.post(
            self.alpha,
            {
                "payment_action": "reverse",
                "payment_payment": str(self.payment.pk),
                "payment_amount": "100.00",
                "payment_reason": "A second family moved",
                "payment_token": "tok-r-new",
                "payment_confirm": "yes",
            },
        )
        self.assertEqual(
            FinancialAdjustment.objects.filter(
                organization=self.alpha,
                payment=self.payment,
                kind=ADJ_REVERSAL,
            ).count(),
            2,
        )
        self.assertEqual(
            Payment.objects.filter(organization=self.alpha).count(), 1
        )
        self.assertEqual(
            Receipt.objects.filter(organization=self.alpha).count(), 1
        )

    def test_a_code_is_local_to_its_person_and_its_centre(self):
        """A guessed code posts this person's own money, not the claim."""
        self.client.force_login(self.money)
        self.post(
            self.alpha,
            {
                "payment_action": "record",
                "payment_target": str(self.obligation.pk),
                "payment_amount": "300.00",
                "payment_method": METHOD_CASH,
                "payment_allocations": f"{self.second.pk}=300.00",
                "payment_token": "shared-code",
                "payment_confirm": "yes",
            },
        )
        self.client.force_login(self.alpha_owner)
        self.post(
            self.alpha,
            {
                "payment_action": "record",
                "payment_target": str(self.obligation.pk),
                "payment_amount": "100.00",
                "payment_method": METHOD_CASH,
                "payment_allocations": f"{self.second.pk}=100.00",
                "payment_token": "shared-code",
                "payment_confirm": "yes",
            },
        )
        self.assertEqual(
            Payment.objects.filter(organization=self.alpha).count(), 3
        )
        self.client.force_login(self.beta_owner)
        beta_obligation = Payment.objects.filter(
            organization=self.beta
        ).first().obligation
        beta_second = FeeInstallment.objects.filter(
            organization=self.beta,
            plan__obligation=beta_obligation,
        ).order_by("pk")[1]
        self.post(
            self.beta,
            {
                "payment_action": "record",
                "payment_target": str(beta_obligation.pk),
                "payment_amount": "400.00",
                "payment_method": METHOD_CASH,
                "payment_allocations": f"{beta_second.pk}=400.00",
                "payment_token": "shared-code",
                "payment_confirm": "yes",
            },
        )
        self.assertEqual(
            Payment.objects.filter(organization=self.beta).count(), 2
        )
        self.assertEqual(
            Receipt.objects.filter(organization=self.beta).count(), 2
        )


class ReceiptIdentityTests(TwoCenters):
    """FIN-007: the receipt and its file name who issued what, where.

    Detail page and CSV carry the centre, the receipt number, the
    payment reference, the enrollment reference, the student, the
    amount with its currency, the method and the date-time - and
    neither page of one centre ever carries the other centre's copy.
    """

    def test_receipt_detail_names_its_centre_and_enrollment(self):
        self.client.force_login(self.money)
        answer = self.client.get(self.receipt_url(1))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer["X-CohortDesk-Page"], "finances_receipt")
        body = self.body(answer)
        self.assertIn("Alpha Center", body)
        self.assertIn("Enrollment", body)
        enrollment_pk = str(
            self.receipt.payment.obligation.enrollment.pk
        )
        self.assertIn("#" + enrollment_pk, body)
        self.assertNotIn("Beta Center", body)
        self.assertNotIn("Beta Learner", body)

    def test_receipt_file_carries_the_same_identity_rows(self):
        self.client.force_login(self.money)
        answer = self.client.get(self.receipt_csv_url(1))
        self.assertEqual(answer.status_code, 200)
        self.assertIn("text/csv", answer["Content-Type"])
        lines = self.body(answer).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("Center", lines[0])
        self.assertIn("Payment", lines[0])
        self.assertIn("Enrollment", lines[0])
        self.assertIn("Alpha Center", lines[1])
        enrollment_pk = str(
            self.receipt.payment.obligation.enrollment.pk
        )
        self.assertIn("#" + enrollment_pk, lines[1])
        self.assertIn("AC-1 (Alpha Learner)", lines[1])
        self.assertIn("600.00", lines[1])
        self.assertNotIn("Beta Center", self.body(answer))

    def test_receipt_identity_never_crosses_the_tenant_wall(self):
        """Another centre's receipt file answers with this centre's page."""
        self.client.force_login(self.alpha_owner)
        for address in (self.receipt_url(1, self.beta),
                        self.receipt_csv_url(1, self.beta)):
            answer = self.client.get(address)
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer["X-CohortDesk-Page"], "problem")
            body = self.body(answer)
            self.assertNotIn("Beta Center", body)
            self.assertNotIn("Beta Learner", body)
            self.assertNotIn("Beta Course", body)
