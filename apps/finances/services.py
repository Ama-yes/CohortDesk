"""The business actions of one center's fee obligations, payments and expenses.

Every action re-asks the central authorization layer (apps.organizations.services.require)
before anything is read or written (SEC-001/SEC-004), and resolves every
number the page was given through the caller's own organization first
(RULE 6.1), so a number a browser typed can never name another center's
record. Nothing here is a page: a page parses what was sent, calls these
functions, and shows what was not accepted on the page the person is on.

The contract is the one the whole product uses (contract):
(record_or_none, problems, notes)
- record: the created or updated record, or None when nothing was saved;
- problems: list of (field, english validator message) rows from full_clean
  (the same shape the other services return; DECISIONS D20 - the English
  wording is shown as text only, and its translation is open in
  docs/OPEN_QUESTIONS.md);
- notes: short business codes the page answers with its own words.

Statuses are never free text: OBLIGATION_TRANSITIONS, PAYMENT_REVERSED and
EXPENSE_TRANSITIONS are the only place a status is ever moved, and they are
here so docs/DECISIONS.md can record what the product owner should confirm.

The saving rule (§13 and 6.2's financial guarantee): every financial
transaction and the records it writes save inside one atomic block, so a
payment is never half-saved with its allocations and its receipt, and no
crash between the two can leave a payment without its receipt or an
allocation without its payment. The receipt number is taken from a per
centre counter record held under select_for_update inside the same block,
which is why MAX() plus one is never the receipt numbering rule (D81, and
6.7 requires it).
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import Error, IntegrityError, transaction
from django.utils import timezone

from apps.organizations.services import require
from apps.students.services import _field_problems
from apps.students.statuses import STATUS_ARCHIVED
from apps.academics.services import _enrollment_here
from apps.academics.statuses import ENROLLMENT_CANCELLED, ENROLLMENT_WITHDRAWN

from .models import (
    Allocation,
    Expense,
    FeeInstallment,
    FeeObligation,
    FeePlan,
    FinanceAuditEvent,
    FinancialAdjustment,
    FinanceSubmission,
    Payment,
    Receipt,
    ReceiptCounter,
)
from .reporting import (
    _installment_outstanding as _installment_outstanding_of,
    obligation_figures,
    payment_figures,
)
from .statuses import (
    ADJUSTMENT_KINDS,
    ADJ_CORRECTION,
    ADJ_DISCOUNT,
    ADJ_REFUND,
    ADJ_REVERSAL,
    ADJ_WAIVER,
    AUDIT_ADJUSTMENT_RECORDED,
    AUDIT_EXPENSE_RECORDED,
    AUDIT_EXPENSE_WITHDRAWN,
    AUDIT_OBLIGATION_CLOSED,
    AUDIT_OBLIGATION_OPENED,
    AUDIT_PAYMENT_POSTED,
    AUDIT_PAYMENT_REVERSED,
    AUDIT_PLAN_SAVED,
    EXPENSE_RECORDED,
    EXPENSE_TRANSITIONS,
    EXPENSE_WITHDRAWN,
    OBLIGATION_CLOSED,
    OBLIGATION_OPEN,
    OBLIGATION_PAID,
    OBLIGATION_TRANSITIONS,
    PAYMENT_METHOD_CHOICES,
    PAYMENT_RECORDED,
    PAYMENT_REVERSED,
    RECEIPT_COUNTER_NAME,
)

# The fields a payment may not move through an edit: the posted payment and
# its receipt stay whole (FIN-008: nothing posted is silently edited).
EDITABLE_PAYMENT_FIELDS = ("note",)
EDITABLE_OBLIGATION_FIELDS = ("note",)

# The obligations a payment may be posted at: a closed agreement's money is
# settled (6.2's closing rule: closed is agreement state, and the actions
# that post money belong to an agreement still open).
OPEN_OBLIGATION_STATUSES = (OBLIGATION_OPEN, OBLIGATION_PAID)


def _record_at(model, organization, number):
    """This center's record of this model with that number, or None (RULE 6.1).

    The page shows whole numbers, so a value that is not one names no record
    here; a service never reaches another center's record because it filters
    on the caller's organization first (SEC-004).
    """
    text = str(number).strip()
    if not text.isdigit():
        return None
    return model.objects.filter(
        organization=organization, pk=int(text)
    ).first()


def _obligation_here(organization, number):
    return _record_at(FeeObligation, organization, number)


def _plan_here(organization, number):
    return _record_at(FeePlan, organization, number)


def _installment_here(organization, number):
    return _record_at(FeeInstallment, organization, number)


def _payment_here(organization, number):
    return _record_at(Payment, organization, number)


def _expense_here(organization, number):
    return _record_at(Expense, organization, number)


def _obligation_installments(obligation):
    """Every installment this agreement's plans scheduled (§7's tree)."""
    return FeeInstallment.objects.filter(plan__obligation=obligation)


def _installment_outstanding(installment):
    """What this installment still has outstanding (§6.2's derived rule).

    This is the reporting layer's one answer for the figure - the
    installment's own part less what the payments' allocations put at it,
    plus what a reversal put back - so the action that posts money and the
    page that shows the outstanding never disagree about what an
    installment still asks for (FIN-004, FIN-010, FIN-005's rule that the
    balance is derived, never stored).
    """
    return _installment_outstanding_of(installment)


def _amounts_total(values):
    """Sum of exact Decimals (FIN-014: never a float)."""
    total = Decimal("0")
    for value in values:
        total = total + value
    return total


class _PostingRefused(Exception):
    """Internal signal: a posting was refused while its token was taken.

    A normal return from inside the atomic block would commit whatever the
    block had already written, so a business refusal found after the
    submission row was registered leaves the block the way an exception
    does: the atomic block rolls the payment, receipt, allocations, audit
    and the submission row back completely, and the outer handler turns
    this into the (None, [], notes) answer the pages already speak. The
    token was therefore never consumed and stays retryable.
    """

    def __init__(self, notes=()):
        super().__init__()
        self.notes = list(notes)


def _locked_obligation(organization, obligation):
    """This center's agreement row under the shared financial lock order.

    One documented order for every financial mutation at an agreement:
    1 this obligation row (this helper), 2 its installments in
    ascending pk order (_locked_installments), 3 the existing payment
    row when a reversal reverses one, 4 the receipt counter when a
    payment posts one (_next_receipt_number). The row is re-read here,
    never reused from before the lock, so every balance read after this
    point is computed from the locked rows. Where the backend does
    row locking (PostgreSQL does), select_for_update is what makes a
    second concurrent posting at the same agreement wait; SQLite
    instead runs one whole transaction at a time, which is the
    stronger guarantee for this project's suite here. PostgreSQL's
    row locking itself has not been exercised by this project's
    tests.
    """
    return FeeObligation.objects.filter(
        organization=organization, pk=obligation.pk
    ).select_for_update().first()


def _locked_installments(organization, obligation):
    """This agreement's installments, locked in ascending pk order (step 2)."""
    return list(
        FeeInstallment.objects.filter(
            organization=organization,
            plan__obligation=obligation,
        ).order_by("pk").select_for_update()
    )


def _submission_row(organization, user, token):
    """One person's submission row for one token, or None (read-only).

    The view's pre-check and the losing side of a same-token race both
    read this row after their own transaction rolled back; only
    _register_submission ever creates it, inside the operation's
    transaction (D86's idempotency rule).
    """
    if not token:
        return None
    return FinanceSubmission.objects.filter(
        organization=organization, recorded_by=user, token=token
    ).first()


def _register_submission(organization, user, token):
    """Take this person's token inside the operation's transaction.

    Creates the row against the (organization, recorded_by, token)
    uniqueness: a second submission with the same token at the same
    center by the same person fails against it, which is how a
    concurrent same-token race is detected - the loser's whole
    transaction rolls back, and the winner's row is then read back to
    return its original saved outcome. A business refusal raised as
    _PostingRefused rolls this row back too, so the token is only
    consumed by an action that was actually taken.
    """
    record = FinanceSubmission(
        organization=organization,
        recorded_by=user,
        token=str(token),
    )
    record.full_clean(exclude=["organization"])
    record.save()
    return record


def _next_receipt_number(organization):
    """The next receipt number at this centre (6.7 and D81's numbering rule).

    One counter record per centre, moved inside the atomic block that saves
    the payment and its receipt (the caller's own block): an existing
    counter row is taken under select_for_update where the database
    supports row locking, so a second concurrent posting at this centre
    waits for the move and never takes the same number. Where row locking
    is not what the database does - SQLite is one of those - the whole
    transaction holds the database to itself, which is the stronger
    guarantee, so the same number still cannot be taken twice. The first
    number of a centre creates the counter row inside the same block: if
    two concurrent first postings race to create it, the loser fails
    against the counter's one-per-centre uniqueness and the block rolls
    back with the payment, its receipt, its allocations and its audit, so
    the posting is refused with the receipt_busy note and the person can
    retry it against the now-existing row. A receipt number can still
    never be duplicated: the receipt's own one-per-centre uniqueness is
    checked against the saved rows, and that failure also rolls the whole
    posting back with the same refusal.
    """
    counters = ReceiptCounter.objects.filter(
        organization=organization, name=RECEIPT_COUNTER_NAME
    ).order_by("pk")
    counter = counters.select_for_update().first()
    if counter is not None:
        counter.running_value += 1
        counter.save(
            update_fields=["running_value", "updated_at", "organization"]
        )
        return counter.running_value
    counter = ReceiptCounter(
        organization=organization,
        name=RECEIPT_COUNTER_NAME,
        running_value=1,
    )
    counter.full_clean(exclude=["organization"])
    counter.save()
    return counter.running_value


def _audit_row(organization, action, actor, **records):
    """One audit row of one audited financial action (6.5 and §13's row).

    What happened, where and who did it: the action, this centre's own
    record it happened to, and the person who did it. Saving it with the
    action inside the same atomic block is what keeps the trail traceable
    (RULE 6.5: financial actions audited).
    """
    row = FinanceAuditEvent(
        organization=organization,
        action=action,
        actor=actor,
        obligation=records.get("obligation"),
        plan=records.get("plan"),
        payment=records.get("payment"),
        expense=records.get("expense"),
    )
    row.full_clean(exclude=["organization"])
    row.save()
    return row


def open_fee_obligation(user, organization, enrollment_number, note=""):
    """FIN-001: open one agreement for one enrollment of this center.

    The agreement's snapshot is the enrollment's own agreed amount and
    currency (COH-004 and FIN-001's anchor: the enrollment carries them),
    and the agreement is opened at it. One agreement per enrollment
    (§7's entity row in the singular of the enrollment's records).
    """
    require(user, organization, "finances.plan.manage")
    enrollment = _enrollment_here(organization, enrollment_number)
    if enrollment is None:
        return None, [], ["enrollment_not_found"]
    if enrollment.student.status == STATUS_ARCHIVED:
        return None, [], ["student_archived"]
    if enrollment.status in (ENROLLMENT_WITHDRAWN, ENROLLMENT_CANCELLED):
        return None, [], ["enrollment_closed"]
    if enrollment.agreed_fee is None:
        return None, [], ["fee_not_agreed"]
    if FeeObligation.objects.filter(
        enrollment=enrollment
    ).exists():
        return None, [], ["already_open"]
    currency = enrollment.fee_currency or organization.primary_currency
    record = FeeObligation(
        organization=organization,
        enrollment=enrollment,
        student=enrollment.student,
        fee_snapshot=enrollment.agreed_fee,
        currency=currency,
        status=OBLIGATION_OPEN,
        opened_at=timezone.now(),
        note=str(note),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    with transaction.atomic():
        record.save()
        _audit_row(
            organization,
            AUDIT_OBLIGATION_OPENED,
            user,
            obligation=record,
        )
    return record, [], []


def create_fee_plan(user, organization, obligation_number, name, note=""):
    """FIN-002: make one installment plan under one agreement (6.7).

    A plan is the schedule an agreement may be split into; the money rule
    (§6.2's plan rule: a plan's installments never add above the agreement's
    agreed amount) is what the installment records add up to.
    """
    require(user, organization, "finances.plan.manage")
    obligation = _obligation_here(organization, obligation_number)
    if obligation is None:
        return None, [], ["obligation_not_found"]
    if obligation.status not in OPEN_OBLIGATION_STATUSES:
        return None, [], ["obligation_closed"]
    record = FeePlan(
        organization=organization,
        obligation=obligation,
        name=str(name),
        note=str(note),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    with transaction.atomic():
        record.save()
        _audit_row(
            organization, AUDIT_PLAN_SAVED, user, plan=record
        )
    return record, [], []


def create_fee_installment(user, organization, plan_number, amount,
                           due_date, note=""):
    """FIN-002: schedule one installment of one plan at this center.

    The installment's date is its due date, and its amount is one scheduled
    part. §6.2's plan rule is enforced before the save: the installments of
    this agreement's plans never add above what the agreement amounts to.
    """
    require(user, organization, "finances.plan.manage")
    plan = _plan_here(organization, plan_number)
    if plan is None:
        return None, [], ["plan_not_found"]
    obligation = plan.obligation
    if obligation.status not in OPEN_OBLIGATION_STATUSES:
        return None, [], ["obligation_closed"]
    scheduled = _amounts_total(
        [
            installment.amount
            for installment in _obligation_installments(obligation)
        ]
    )
    figures = obligation_figures(obligation, organization)
    if scheduled + amount > figures["required"]:
        return None, [], ["plan_over_fee"]
    last = FeeInstallment.objects.filter(
        plan=plan
    ).order_by("-position").first()
    position = 1 if last is None else last.position + 1
    record = FeeInstallment(
        organization=organization,
        plan=plan,
        amount=amount,
        due_date=due_date,
        position=position,
        note=str(note),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    with transaction.atomic():
        record.save()
        _audit_row(
            organization, AUDIT_PLAN_SAVED, user, plan=plan
        )
    return record, [], []


def post_payment(user, organization, obligation_number, amount, method,
                 reference="", allocations=(), note="", token=""):
    """FIN-003/FIN-004/FIN-006: post one payment and allocate it (6.2/6.7).

    The payment names this centre's agreement, and its amount is the money
    recorded (FIN-003's amount, method and reference; FIN-006's methods the
    SRS names). Its allocations (§7's Allocation row) put the payment's
    money against the agreement's outstanding installments: FIN-004's
    "partial payments and allocation of a payment to one or more
    outstanding installments where permitted", and 6.7's rule that the
    allocations of a payment add exactly to that payment and never put more
    at an installment than that installment still has outstanding. The
    payment, its receipt, its allocations and its audit row save in one
    atomic transaction (§13 and 6.2's financial guarantee), so a payment
    and the money it moves are never half-saved.
    Payment idempotency (D86): when a token names the submission, its
    row is registered inside the same block against the one-per-person
    token uniqueness and linked to the saved payment: a replay of the
    same token answers with the original saved outcome and never posts
    a second payment, receipt, allocation or audit row, and a business
    refusal raised inside the block rolls the token row back with
    everything else, leaving the token retryable.
    """
    require(user, organization, "finances.payment.record")
    obligation = _obligation_here(organization, obligation_number)
    if obligation is None:
        return None, [], ["obligation_not_found"]
    if obligation.status not in OPEN_OBLIGATION_STATUSES:
        return None, [], ["obligation_closed"]
    if amount <= 0:
        return None, [], ["amount_not_positive"]
    if str(method) not in [code for code, _label in PAYMENT_METHOD_CHOICES]:
        return None, [], ["method_unknown"]
    targets = {}
    for installment_number, part in allocations:
        installment = _installment_here(
            organization, installment_number
        )
        if installment is None or installment.plan.obligation_id != (
            obligation.pk
        ):
            return None, [], ["installment_not_here"]
        if installment.pk in targets:
            return None, [], ["installment_repeated"]
        if part <= 0:
            return None, [], ["allocation_not_positive"]
        targets[installment.pk] = part
    total = _amounts_total(targets.values())
    if total != amount:
        return None, [], ["allocation_not_whole_payment"]
    record = Payment(
        organization=organization,
        obligation=obligation,
        amount=amount,
        method=str(method),
        reference=str(reference),
        recorded_at=timezone.now(),
        recorded_by=user,
        status=PAYMENT_RECORDED,
        note=str(note),
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    try:
        with transaction.atomic():
            # Token first (D86): the row is taken against the
            # (organization, recorded_by, token) uniqueness inside the
            # block that saves the whole posting, and it is linked to
            # the outcome before the block commits, so a committed
            # row always names one saved outcome.
            submission = None
            if token:
                submission = _register_submission(organization, user, token)
            # Lock order step 1: the agreement row, re-read here so
            # nothing below uses a row read before the locks.
            locked = _locked_obligation(organization, obligation)
            if locked is None:
                raise _PostingRefused(["obligation_not_found"])
            if locked.status not in OPEN_OBLIGATION_STATUSES:
                raise _PostingRefused(["obligation_closed"])
            # Lock order step 2: this agreement's installments, taken
            # in ascending pk order; the outstanding figures below come
            # from the locked rows only.
            locked_rows = {
                row.pk: row
                for row in _locked_installments(organization, locked)
            }
            plans = []
            for installment_number, part in allocations:
                installment = _installment_here(
                    organization, installment_number
                )
                if (
                    installment is None
                    or installment.plan.obligation_id != locked.pk
                ):
                    raise _PostingRefused(["installment_not_here"])
                locked_row = locked_rows.get(installment.pk)
                if locked_row is None:
                    raise _PostingRefused(["installment_not_here"])
                if part > _installment_outstanding(locked_row):
                    raise _PostingRefused(["installment_fully_allocated"])
                plans.append((locked_row, part))
            # The receipt names its payment, so the payment saves first
            # and the receipt is built with the saved payment in hand:
            # an FK can only name a record that already has its number
            # (6.7's one-to-one tie; the payment, its receipt, its
            # allocations and its audit row still save inside the one
            # atomic block, so a payment is never half-saved with its
            # receipt).
            record.save()
            receipt = Receipt(
                organization=organization,
                payment=record,
                number=_next_receipt_number(organization),
                student=locked.student,
                amount=record.amount,
                currency=locked.currency,
                date=record.recorded_at,
                method=record.method,
            )
            receipt.full_clean(exclude=["organization"])
            receipt.save()
            for installment, part in plans:
                allocation = Allocation(
                    organization=organization,
                    payment=record,
                    installment=installment,
                    amount=part,
                )
                allocation.full_clean(exclude=["organization"])
                allocation.save()
            _audit_row(
                organization,
                AUDIT_PAYMENT_POSTED,
                user,
                payment=record,
            )
            if submission is not None:
                submission.payment = record
                submission.save(update_fields=["payment"])
            # 6.2's auto-move: an agreement whose money is all settled
            # reads paid, inside the same block, from the locked row.
            if locked.status == OBLIGATION_OPEN:
                figures = obligation_figures(locked, organization)
                if figures["outstanding"] <= 0:
                    locked.status = OBLIGATION_PAID
                    locked.status_changed_at = timezone.now()
                    locked.save(
                        update_fields=["status", "status_changed_at"]
                    )
    except _PostingRefused as stopped:
        return None, [], stopped.notes
    except IntegrityError:
        if token:
            winner = _submission_row(organization, user, token)
            if winner is not None:
                if winner.payment is None and winner.adjustment is not None:
                    # The submission row D86 claims is (centre, person,
                    # token) and carries no action dimension, so a code
                    # already consumed by a reversal must never answer
                    # this payment posting with that reversal: the
                    # posting is refused whole and the person takes the
                    # page's fresh code instead.
                    return None, [], ["token_used_for_other_action"]
                return winner.payment or winner.adjustment, [], [
                    "duplicate_submission",
                ]
        return None, [], ["receipt_busy"]
    return record, [], []


def reverse_payment(user, organization, payment_number, amount,
                    reason="", token=""):
    """FIN-008/FIN-009: reverse one posted payment at this center (§6.2).

    Reversing a payment's posting is one financial transaction that is not
    a payment, and it names the payment it cancels. 6.2's reversal rule is
    what moves the money: "a payment that was reversed is no longer counted
    as received money, and its allocation is undone in the outstanding
    figures" - the money goes back, the posted payment, its allocations and
    its receipt stay in the history (FIN-008: traceable transactions,
    nothing silently deleted). Cumulative reversals never take more from a
    payment than the payment's own amount: a payment already fully reversed
    cannot be reversed again, and a second partial reversal may only take
    what the payment still holds.
    Transaction discipline (§13's financial safety): the payment's
    agreement row, its installments in ascending pk order and the
    payment row itself are taken in the shared lock order inside
    one atomic block, and the payment's figures and status are
    revalidated from the locked row, so a concurrent posting at
    the same agreement can never interleave. With a token, its
    submission row registers inside the same block and links to
    the saved reversal.
    """
    require(user, organization, "finances.payment.record")
    payment = _payment_here(organization, payment_number)
    if payment is None:
        return None, [], ["payment_not_found"]
    if payment.status == PAYMENT_REVERSED:
        return None, [], ["already_reversed"]
    if amount <= 0:
        return None, [], ["amount_not_positive"]
    adjustment = FinancialAdjustment(
        organization=organization,
        obligation=payment.obligation,
        kind=ADJ_REVERSAL,
        amount=amount,
        payment=payment,
        reason=str(reason),
        actor=user,
    )
    try:
        adjustment.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    try:
        with transaction.atomic():
            submission = None
            if token:
                submission = _register_submission(organization, user, token)
            # The payment-to-agreement tie is PROTECT and never moves,
            # so it is safe to resolve before the locks; every figure
            # and status below is read from rows taken under them.
            # Lock order step 1: the agreement row.
            locked = _locked_obligation(organization, payment.obligation)
            if locked is None:
                raise _PostingRefused(["payment_not_found"])
            # Lock order step 2: the agreement's installments in
            # ascending pk order. A reversal moves no installment row
            # itself - the per-installment reconciliation is the
            # reporting layer's derived rule over these rows - but
            # taking them keeps this action on the one documented
            # order, so an interleaved posting can never sit between
            # the agreement lock and the payment lock.
            _locked_installments(organization, locked)
            # Lock order step 3: the payment row itself.
            locked_payment = Payment.objects.filter(
                organization=organization, pk=payment.pk
            ).select_for_update().first()
            if locked_payment is None:
                raise _PostingRefused(["payment_not_found"])
            if locked_payment.status == PAYMENT_REVERSED:
                raise _PostingRefused(["already_reversed"])
            figures = payment_figures(locked_payment)
            if figures["taken_back"] + amount > figures["amount"]:
                raise _PostingRefused(["over_taken"])
            adjustment.save()
            if figures["taken_back"] + amount >= figures["amount"]:
                locked_payment.status = PAYMENT_REVERSED
                locked_payment.status_changed_at = timezone.now()
                locked_payment.full_clean()
                locked_payment.save(
                    update_fields=["status", "status_changed_at"]
                )
            _audit_row(
                organization,
                AUDIT_PAYMENT_REVERSED,
                user,
                payment=locked_payment,
            )
            if submission is not None:
                submission.adjustment = adjustment
                submission.save(update_fields=["adjustment"])
    except _PostingRefused as stopped:
        return None, [], stopped.notes
    except IntegrityError:
        if token:
            winner = _submission_row(organization, user, token)
            if winner is not None:
                if winner.adjustment is None and winner.payment is not None:
                    # The code was consumed by a payment, not by a
                    # reversal: answering with that payment would tell
                    # the person this reversal had posted, which it
                    # had not, so the reversal is refused whole.
                    return None, [], ["token_used_for_other_action"]
                return winner.adjustment or winner.payment, [], [
                    "duplicate_submission",
                ]
        raise
    return adjustment, [], []


def record_financial_adjustment(user, organization, obligation_number,
                                kind, amount, reason=""):
    """FIN-008/FIN-009: record one financial adjustment at this center.

    The five kinds §7's FinancialAdjustment row and FIN-008/FIN-009 name,
    each with the reason, the authorizing user and its audit row (FIN-009's
    requirement in full). A discount and a waiver move the agreement's
    agreed amount down; a fee correction moves it up (a fee correction as a
    signed move is not modelled: docs/OPEN_QUESTIONS.md asks the owner). A
    refund puts money back from what the payments received at this
    agreement. A reversal of one payment's posting goes through reverse_payment,
    which names the payment it cancels (FIN-008 and §6.2's reversal rule):
    here the reference would not name one, so the transaction is refused.
    """
    require(user, organization, "finances.adjustment.record")
    obligation = _obligation_here(organization, obligation_number)
    if obligation is None:
        return None, [], ["obligation_not_found"]
    kind = str(kind).strip().lower()
    if kind not in ADJUSTMENT_KINDS:
        return None, [], ["kind_unknown"]
    if kind == ADJ_REVERSAL:
        return None, [], ["reversal_needs_payment"]
    if amount <= 0:
        return None, [], ["amount_not_positive"]
    adjustment = FinancialAdjustment(
        organization=organization,
        obligation=obligation,
        kind=kind,
        amount=amount,
        reason=str(reason),
        actor=user,
    )
    try:
        adjustment.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    with transaction.atomic():
        # Lock order step 1 again for this action: the agreement row
        # is taken before any required or received figure is read, so
        # a payment posted at this agreement by another transaction
        # is waited for and two conflicting adjustments can never both
        # cross the same ceiling. Nothing is saved above this point,
        # so a refusal here saves nothing and an adjustment carries
        # no token to consume.
        locked = _locked_obligation(organization, obligation)
        if locked is None:
            return None, [], ["obligation_not_found"]
        figures = obligation_figures(locked, organization)
        if kind in (ADJ_DISCOUNT, ADJ_WAIVER):
            if figures["required"] < amount:
                return None, [], ["over_required"]
        elif kind == ADJ_REFUND:
            if figures["collections"] < amount:
                return None, [], ["over_received"]
        adjustment.save()
        _audit_row(
            organization,
            AUDIT_ADJUSTMENT_RECORDED,
            user,
            obligation=locked,
        )
    return adjustment, [], []


def set_financial_obligation_status(user, organization, obligation_number,
                                    new_status):
    """FIN-005/6.2: move one agreement through this centre's transition table.

    An agreement closed by the closing action keeps its figures (6.2's closing
    rule), and closing an agreement whose money is all settled is allowed; a
    reopened agreement is not modelled (docs/OPEN_QUESTIONS.md asks the owner).
    Marking an agreement paid is refused while money is still outstanding:
    6.2 reads an agreement paid only once its money is settled, and
    post_payment's auto-move is the other way into that same state. The
    status move and its audit row save inside one atomic block on the
    agreement row taken under the posting lock (D85), so this action and a
    payment at the same agreement never read each other's pre-lock state.
    """
    require(user, organization, "finances.plan.manage")
    obligation = _obligation_here(organization, obligation_number)
    if obligation is None:
        return None, [], ["obligation_not_found"]
    target = str(new_status).strip().lower()
    with transaction.atomic():
        # Lock order step 1 (D85): the agreement row under the same lock
        # the postings take, so a payment posting and this status move
        # cannot interleave, and nothing below reads the pre-lock row.
        locked = _locked_obligation(organization, obligation)
        if locked is None:
            return None, [], ["obligation_not_found"]
        if target not in OBLIGATION_TRANSITIONS.get(locked.status, ()):
            return None, [], ["transition"]
        if target == OBLIGATION_PAID:
            if obligation_figures(locked, organization)["outstanding"] > 0:
                return None, [], ["transition"]
        locked.status = target
        locked.status_changed_at = timezone.now()
        locked.full_clean()
        locked.save(update_fields=["status", "status_changed_at"])
        if target == OBLIGATION_CLOSED:
            _audit_row(
                organization,
                AUDIT_OBLIGATION_CLOSED,
                user,
                obligation=locked,
            )
    return locked, [], []


def record_financial_expense(user, organization, category, amount,
                             date, payee="", description="",
                             attachment_reference="", currency=""):
    """FIN-011: record one basic operating expense at this center.

    FIN-011's fields exactly: the category, the amount and its currency,
    the date, the payee or description, the optional attachment reference
    and the person who recorded it. FIN-012's line: an expense is tracked
    apart from the fee money and never joins the collections figures, and
    no basic cash summary is presented as audited profit or statutory
    accounts (docs/DECISIONS.md D83 and open item 53: the file a real
    attachment document would be is not built).
    FIN-011's currency is always this center's own (FIN-001's anchor):
    the service derives it from the center, and a currency offered
    with a new expense that is not the center's is refused with the
    currency_mismatch note and saves nothing. Historical records
    keep the currency they were recorded with.
    """
    require(user, organization, "finances.expense.manage")
    center_currency = str(organization.primary_currency).strip().upper()
    if currency and str(currency).strip().upper() != center_currency:
        return None, [], ["currency_mismatch"]
    if amount <= 0:
        return None, [], ["amount_not_positive"]
    record = Expense(
        organization=organization,
        category=str(category),
        amount=amount,
        currency=center_currency,
        date=date,
        payee=str(payee),
        description=str(description),
        attachment_reference=str(attachment_reference),
        recorded_by=user,
        status=EXPENSE_RECORDED,
    )
    try:
        record.full_clean(exclude=["organization"])
    except ValidationError as error:
        return None, _field_problems(error), []
    with transaction.atomic():
        record.save()
        _audit_row(
            organization,
            AUDIT_EXPENSE_RECORDED,
            user,
            expense=record,
        )
    return record, [], []


def withdraw_financial_expense(user, organization, expense_number):
    """FIN-011/6.2: withdraw one expense record at this center.

    Withdrawing is an operational status move (EXPENSE_TRANSITIONS is the
    only place an expense status moves), not a silent deletion: the record
    and its audit stay, and the expense figures stop counting it (FIN-011's
    "recorded-by user" history stays traceable).
    """
    require(user, organization, "finances.expense.manage")
    expense = _expense_here(organization, expense_number)
    if expense is None:
        return None, [], ["expense_not_found"]
    with transaction.atomic():
        # Take the expense row under the same transaction discipline as the
        # financial status moves, so two withdrawal attempts cannot both see
        # an active row and each write their own audit history.
        locked_expense = Expense.objects.filter(
            organization=organization, pk=expense.pk
        ).select_for_update().first()
        if locked_expense is None:
            return None, [], ["expense_not_found"]
        if locked_expense.status == EXPENSE_WITHDRAWN:
            return locked_expense, [], ["already_withdrawn"]
        if EXPENSE_WITHDRAWN not in EXPENSE_TRANSITIONS.get(
            locked_expense.status, ()
        ):
            return None, [], ["transition"]
        # The status move and its audit row commit together, so a failed
        # audit save can never leave a withdrawn expense without the
        # traceable history FIN-011 asks for.
        locked_expense.status = EXPENSE_WITHDRAWN
        locked_expense.status_changed_at = timezone.now()
        locked_expense.full_clean()
        locked_expense.save(update_fields=["status", "status_changed_at"])
        _audit_row(
            organization,
            AUDIT_EXPENSE_WITHDRAWN,
            user,
            expense=locked_expense,
        )
    return locked_expense, [], []
