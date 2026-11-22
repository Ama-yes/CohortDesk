"""Organization (tenant) and Members models.

Organization is the tenant boundary (SEC-002, ORG-*). Every tenant-owned model
added later references it directly or through a strictly enforced parent
relationship. Per-model validation lives in each model's clean_fields hook and
model constraints; full_clean() invokes both, so forms and any code path that
calls full_clean() share one rule set.
"""
import re
from zoneinfo import ZoneInfo

from django.db import models
from .roles import ROLE_CHOICES
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone

SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,49}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
LOCALES = ("en", "fr", "ar")

# COH-005: what a center does when an enrollment would exceed a cohort's
# capacity. "prevent" refuses the enrollment; "warn" saves it and tells the
# person the cohort is over capacity.
CAPACITY_PREVENT = "prevent"
CAPACITY_WARN = "warn"
CAPACITY_POLICIES = (CAPACITY_PREVENT, CAPACITY_WARN)

# SCH-005/SCH-006: what a center does when a session would double-book a room
# or an instructor. "prevent" refuses the operation; "warn" saves it and names
# the conflicting session.
SCHEDULE_PREVENT = "prevent"
SCHEDULE_WARN = "warn"
SCHEDULE_POLICIES = (SCHEDULE_PREVENT, SCHEDULE_WARN)


class Organization(models.Model):
    """A training center: the tenant boundary of the product."""
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=50, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("disabled", "Disabled")],
        default="active",
    )
    # ORG-003: exactly one primary currency per organization. Money fields
    # (finance milestone) must validate against this and use Decimal types.
    primary_currency = models.CharField(max_length=3)
    # ORG-002: configured IANA display timezone (SCHED-005 renders in it).
    display_timezone = models.CharField(max_length=64, default="UTC")
    # ORG-004: default display language of the tenant interface.
    preferred_locale = models.CharField(
        max_length=2,
        choices=[(code, code) for code in LOCALES],
        default="en",
    )
    # COH-005: the center decides whether a full cohort prevents or merely
    # warns about another enrollment.
    capacity_policy = models.CharField(
        max_length=8,
        choices=[
            (CAPACITY_PREVENT, "Prevent"),
            (CAPACITY_WARN, "Warn"),
        ],
        default=CAPACITY_PREVENT,
    )
    # SCH-005/SCH-006: the same choice for room and instructor double-booking.
    schedule_conflict_policy = models.CharField(
        max_length=8,
        choices=[
            (SCHEDULE_PREVENT, "Prevent"),
            (SCHEDULE_WARN, "Warn"),
        ],
        default=SCHEDULE_PREVENT,
    )

    def clean_fields(self, exclude=None):
        """Honors exclude so a caller can name the one field at fault.

        update_settings validates with full_clean(exclude=[other settings
        fields]) one field at a time (DECISIONS D20); every field not named in
        exclude keeps its own rule.
        """
        excluded = set(exclude or ())
        if "name" not in excluded:
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError("Organization name is required.")
        if "slug" not in excluded:
            self.slug = self.slug.strip().lower()
            if not SLUG_PATTERN.match(self.slug):
                raise ValidationError(
                    "Organization slug must match {} (lowercase, unique).".format(SLUG_PATTERN.pattern)
                )
        if "primary_currency" not in excluded and not CURRENCY_PATTERN.match(
            self.primary_currency
        ):
            raise ValidationError(
                "Primary currency must be a 3-letter ISO code (e.g. DZD, EUR)."
            )
        if "display_timezone" not in excluded:
            try:
                ZoneInfo(self.display_timezone)
            except Exception:
                raise ValidationError("Display timezone must be a valid IANA timezone.")
        if "preferred_locale" not in excluded and self.preferred_locale not in LOCALES:
            raise ValidationError("Preferred locale must be one of en, fr, ar.")
        if (
            "capacity_policy" not in excluded
            and self.capacity_policy not in CAPACITY_POLICIES
        ):
            raise ValidationError(
                "Capacity policy must be prevent or warn."
            )
        if (
            "schedule_conflict_policy" not in excluded
            and self.schedule_conflict_policy not in SCHEDULE_POLICIES
        ):
            raise ValidationError(
                "Schedule conflict policy must be prevent or warn."
            )


class Members(models.Model):
    """A user's role inside one organization. Not global groups (MGMT-001/002)."""
    user = models.ForeignKey(
        "accounts.CohortUser",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="members",
    )
    role = models.CharField(max_length=64, choices=ROLE_CHOICES)
    status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("suspended", "Suspended")],
        default="active",
    )
    joined_at = models.DateTimeField(default=timezone.now)
    suspended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.constraints.UniqueConstraint(
                fields=["user", "organization"],
                name="members_user_organization",
            ),
        ]