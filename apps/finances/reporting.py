"""The derived financial figures - never a stored balance (FIN-005/§6.2).

FIN-005 and RULE 6.2: "calculate outstanding balance ... do not rely on a
manually editable balance field as the source of truth", so every figure
here is computed from the transactions themselves, every time a page asks.
The vocabulary of what counts is the one §6.2 names:

- the amount the obligation requires: the agreed amount the agreement
  opened with, less the discounts and waivers, plus the fee corrections
  (FIN-009 and §6.2's first rule);
- the allocations of the payments posted at it (FIN-004 and §6.7: a payment
  is received money only through the allocations it wrote);
- the refunds and the reversals: §6.2's reversal rule "a payment that was
  reversed is no longer counted as received money, and its allocation is
  undone in the outstanding figures" - the money goes back, the posted
  payment and its allocation stay in the history;
- outstanding = requires - allocations + refunds + reversals, floored at
  zero (the never below zero rule);
- collections = the payments themselves - refunds - reversals (§6.7's
  "collections"), and receivables = outstanding + overdue (§6.7's
  receivables rule);
- an overdue installment: an installment still outstanding whose due date
  already passed in the centre's own timezone (FIN-010 and §6.2).

The distribution of one payment's allocations against one installment is
what §6.7's "not an accounting ledger" line allows: the reversal accounting
walks a payment's allocations in their stored order and takes the reversed
amount back from them in that order (docs/DECISIONS.md D82 and open item 52:
the SRS names the money rule, not which installment a multi-installment
reversal puts back).
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import (
    Allocation,
    Expense,
    FeeInstallment,
    FeeObligation,
    FinancialAdjustment,
    Payment,
)
from .statuses import (
    ADJ_CORRECTION,
    ADJ_DISCOUNT,
    ADJ_REFUND,
    ADJ_REVERSAL,
    ADJ_WAIVER,
    EXPENSE_WITHDRAWN,
    PAYMENT_REVERSED,
)


def _amounts_total(records):
    """Sum of the records' amounts as exact Decimal (FIN-014: never float)."""
    total = 0
    for record in records:
        total += record.amount
    return total


def required_amount(obligation):
    """The obligation's amount at stake: opens less discounts/waivers, plus
    the fee corrections (§6.2 and FIN-009)."""
    total = obligation.fee_snapshot
    total -= _amounts_total(
        FinancialAdjustment.objects.filter(
            obligation=obligation, kind=ADJ_DISCOUNT
        )
    )
    total -= _amounts_total(
        FinancialAdjustment.objects.filter(
            obligation=obligation, kind=ADJ_WAIVER
        )
    )
    total += _amounts_total(
        FinancialAdjustment.objects.filter(
            obligation=obligation, kind=ADJ_CORRECTION
        )
    )
    if total < 0:
        return type(obligation.fee_snapshot)(0)
    return total


def _obligation_payments(obligation):
    return Payment.objects.filter(obligation=obligation)


def _obligation_allocations(obligation):
    return Allocation.objects.filter(payment__obligation=obligation)


def _obligation_takings(obligation):
    """The refunds and the reversals at this agreement (FIN-008/6.2)."""
    return FinancialAdjustment.objects.filter(
        obligation=obligation,
        kind__in=(ADJ_REFUND, ADJ_REVERSAL),
    )


def _installment_allocations(installment):
    return Allocation.objects.filter(installment=installment)


def _installment_takings(installment):
    """The reversal money put back at this installment (§6.2's rule).

    A reversal names the payment whose posting it cancels; its allocation
    walk is the one in `reversal_take_backs`, so the money this installment
    had put in is exactly what the walk took from it.
    """
    total = 0
    for reversal in FinancialAdjustment.objects.filter(
        kind=ADJ_REVERSAL,
        payment__obligation=installment.plan.obligation,
    ):
        for back in reversal_take_backs(reversal):
            if back["installment_id"] == installment.pk:
                total += back["amount"]
    return total


def _installment_outstanding(installment):
    """One installment's part still owed: scheduled less allocated, plus what
    a reversal put back (FIN-004, FIN-008, §6.2)."""
    total = installment.amount
    total -= _amounts_total(_installment_allocations(installment))
    total += _installment_takings(installment)
    if total < 0:
        total = type(installment.amount)(0)
    return total


def reversal_take_backs(reversal):
    """One reversal's money against the allocations of its payment, in the
    stored order (§6.2's "its allocation is undone in the outstanding
    figures" rule; convention D82, open item 52).

    Returns rows of (installment_id, amount) whose sum is exactly the
    reversal's amount: the payment's allocations walked in their stored
    order (installment's own schedule order), each taking only what it
    still holds of it once the reversals recorded before this one took
    their shares - so a partial reversal of the same payment can happen
    more than once, never puts a previous reversal's money at an
    installment a second time, and a fully reversed payment leaves
    nothing reversible. Because every reversal's rows come out of the
    same walk over the same still-held amounts, the per-installment
    outstanding and the obligation's outstanding reconcile exactly
    (D84, the corrected cumulative rule; the SRS names the money rule,
    not which installment a multi-installment reversal puts back - open
    item 52 asks only for the tie of the stored order, never for the
    split). The reversals are read in their recorded order
    (created_at, then pk), and this reversal's own rows are the ones the
    walk writes for it.
    """
    allocations = list(Allocation.objects.filter(
        payment=reversal.payment
    ).order_by(
        "payment__obligation__created_at",
        "installment__plan__created_at",
        "installment__position",
        "installment__amount",
    ))
    held = {allocation.pk: allocation.amount for allocation in allocations}
    rows_of_this = []
    for running in FinancialAdjustment.objects.filter(
        kind=ADJ_REVERSAL, payment=reversal.payment
    ).order_by("created_at", "pk"):
        remaining = running.amount
        rows = []
        for allocation in allocations:
            if remaining <= 0:
                break
            take = min(held[allocation.pk], remaining)
            if take <= 0:
                continue
            held[allocation.pk] -= take
            remaining -= take
            rows.append(
                {
                    "installment_id": allocation.installment_id,
                    "amount": take,
                }
            )
        if running.pk == reversal.pk:
            rows_of_this = rows
    return rows_of_this


def _obligation_today(organization):
    """Today's date in this centre's own timezone, or UTC if its zone is bad.

    The same answer the schedules and the attendance pages give: the centre's
    display timezone decides what "today" is, so "overdue" means overdue
    where the centre lives, never where the browser or UTC sits (ORG-002,
    SCH-009's answer for the schedules).
    """
    try:
        zone = ZoneInfo(organization.display_timezone)
    except LookupError:
        zone = UTC
    return timezone.now().astimezone(zone).date()


def _overdue_installments(obligation, organization):
    """This agreement's installments still owed whose date already passed.

    FIN-010's "overdue installments": the figure and the overdue count read
    the same rows. An installment whose whole part is allocated is never
    overdue; a closed agreement still shows what it did not collect (6.2's
    closing rule: closing is agreement state, not the money).
    """
    today = _obligation_today(organization)
    return [
        installment
        for installment in FeeInstallment.objects.filter(
            plan__obligation=obligation
        )
        if installment.due_date < today
        and _installment_outstanding(installment) > 0
    ]


def obligation_figures(obligation, organization):
    """One agreement's whole figures (FIN-005/6.2/6.7).

    The rows the pages show, and the words the pages say are never here:
    the page calls `obligation_rows` and the templates read the glossary
    through `ui`.
    """
    payments = _obligation_payments(obligation)
    allocations = _obligation_allocations(obligation)
    takings = _obligation_takings(obligation)
    required = required_amount(obligation)
    allocated = _amounts_total(allocations)
    taken = _amounts_total(takings)
    outstanding = required - (allocated - taken)
    if outstanding < 0:
        outstanding = type(required)(0)
    overdue = _overdue_installments(obligation, organization)
    overdue_amount = 0
    for installment in overdue:
        overdue_amount += _installment_outstanding(installment)
    return {
        "status": obligation.status,
        "opened_at": obligation.opened_at,
        "payments": payments.count(),
        "required": required,
        "allocated": allocated,
        "taken_back": taken,
        "outstanding": outstanding,
        "installments": FeeInstallment.objects.filter(
            plan__obligation=obligation
        ).count(),
        "installments_left": len(
            [
                installment
                for installment in FeeInstallment.objects.filter(
                    plan__obligation=obligation
                )
                if _installment_outstanding(installment) > 0
            ]
        ),
        "overdue": len(overdue),
        "overdue_amount": overdue_amount,
        "collections": _amounts_total(payments) - taken,
        "payment_amount": _amounts_total(payments),
    }


def obligation_rows(obligation, organization):
    """This agreement's figures as one page's rows (FIN-005/FIN-010)."""
    figures = obligation_figures(obligation, organization)
    return [
        {
            "id": "status",
            "value": figures["status"],
        },
        {
            "id": "opened_at",
            "value": figures["opened_at"],
        },
        {
            "id": "payments",
            "value": figures["payments"],
        },
        {
            "id": "required",
            "value": figures["required"],
        },
        {
            "id": "allocated",
            "value": figures["allocated"],
        },
        {
            "id": "taken_back",
            "value": figures["taken_back"],
        },
        {
            "id": "outstanding",
            "value": figures["outstanding"],
        },
        {
            "id": "installments",
            "value": figures["installments"],
        },
        {
            "id": "installments_left",
            "value": figures["installments_left"],
        },
        {
            "id": "overdue",
            "value": figures["overdue"],
        },
        {
            "id": "overdue_amount",
            "value": figures["overdue_amount"],
        },
        {
            "id": "collections",
            "value": figures["collections"],
        },
        {
            "id": "payment_amount",
            "value": figures["payment_amount"],
        },
    ]


def payment_figures(payment):
    """One payment and what became of it (FIN-003/FIN-008/6.2).

    A payment whose posting a reversal cancelled shows reversed here and in
    its history; the figure of what is received never counts it (6.2's
    reversal rule), while the payment's amount stays in history (FIN-008).
    """
    reversals = FinancialAdjustment.objects.filter(
        kind=ADJ_REVERSAL, payment=payment
    )
    taken_back = _amounts_total(reversals)
    return {
        "amount": payment.amount,
        "taken_back": taken_back,
        "received": payment.amount - taken_back,
        "reversed": payment.status == PAYMENT_REVERSED,
    }


def financial_rows(organization):
    """The centre's financial figures as the summaries page shows (FIN-010).

    FIN-010's summaries: collections, outstanding balances, overdue
    installments and payment history; FIN-012's rule: the rows keep
    collections, receivables and expenses apart, and nothing is called a
    profit or statutory accounts.
    """
    obligations = FeeObligation.objects.filter(organization=organization)
    total_required = 0
    total_received = 0
    total_overdue_amount = 0
    overdue_rows = []
    total_collections = 0
    for obligation in obligations:
        figures = obligation_figures(obligation, organization)
        total_required += figures["required"]
        total_received += figures["required"] - figures["outstanding"]
        total_collections += figures["collections"]
        total_overdue_amount += figures["overdue_amount"]
        overdue_rows.extend(
            _overdue_installments(obligation, organization)
        )
    expenses = Expense.objects.filter(organization=organization).exclude(
        status=EXPENSE_WITHDRAWN
    )
    expense_total = 0
    for expense in expenses:
        expense_total += expense.amount
    receivables = total_required - total_received
    return [
        {
            "id": "obligations",
            "value": obligations.count(),
        },
        {
            "id": "required",
            "value": total_required,
        },
        {
            "id": "received",
            "value": total_received,
        },
        {
            "id": "outstanding",
            "value": receivables,
        },
        {
            "id": "overdue",
            "value": len(overdue_rows),
        },
        {
            "id": "overdue_amount",
            "value": total_overdue_amount,
        },
        {
            "id": "collections",
            "value": total_collections,
        },
        {
            "id": "expense_total",
            "value": expense_total,
        },
        {
            "id": "expenses",
            "value": expenses.count(),
        },
    ]
