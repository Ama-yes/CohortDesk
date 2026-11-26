"""Student and Application: records one center has, not records the product has.

Every instance hangs directly from one Organization (SEC-002: the tenant
boundary; RULE 6.1: every read, write, and search enforces organization
scope). A student number is unique inside one center and the same number
in another center names a different person (STU-002: organization-scoped;
apps/students/test_access.py proves the cross-tenant half of both).

Student stays one model: STU-003 asks for an optional emergency/guardian
contact, and one profile field answers that. A separate guardian model
would only be needed if one learner may have several guardians - an open
question in docs/OPEN_QUESTIONS.md, not something to invent here.
Application.desired_course is free text typed by center staff on the
applications page: it is explicitly NOT a reference to a Course model,
which is a later feature (docs/OPEN_QUESTIONS.md), and the applications
page says so in the center's own words, so nobody may mistake it for a
course record.

Per-model validation lives in each model's clean_fields hook; full_clean()
invokes it, so the service layer and any other code path that calls
full_clean share one rule set (the same rule the tenant settings follow,
DECISIONS D20). Two details keep that rule usable by a page:

- every rejection is raised as {field: message}, so a caller can mark the row
  the person typed (a bare ValidationError is filed by Django under
  "__all__", which names no row);
- the hook finishes with Django's own field validation, so the declared
  max_length/choices rules are never lost by overriding clean_fields.
"""
import re
from datetime import date

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone

from .statuses import APPLICATION_STATUS_CHOICES, STATUS_CHOICES
from apps.organizations.models import Organization

STUDENT_NUMBER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,19}$")


class Student(models.Model):
    """One learner at one center (STU-001/STU-002/STU-003/STU-007/STU-008)."""
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="students",
    )
    student_number = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=120, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="applicant")
    registered_at = models.DateTimeField(default=timezone.now)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """STU-002/STU-003 rules, one place (the tenant settings hook pattern).

        Honors exclude so a service can name the one field at fault when it
        validates with full_clean(exclude=[other fields]) (DECISIONS D20).
        """
        excluded = set(exclude or ())
        if "student_number" not in excluded:
            self.student_number = str(self.student_number).strip().upper()
            if not STUDENT_NUMBER_PATTERN.match(self.student_number):
                raise ValidationError(
                    {
                        "student_number": (
                            "Student number must match {} (up to 20 "
                            "characters of letters, digits, dash or "
                            "underscore, starting with a letter or digit)."
                        ).format(STUDENT_NUMBER_PATTERN.pattern)
                    }
                )
        if "name" not in excluded:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError({"name": "Student name is required."})
        if "contact" not in excluded:
            self.contact = str(self.contact).strip()
        if "address" not in excluded:
            self.address = str(self.address).strip()
        if "note" not in excluded:
            self.note = str(self.note).strip()
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        if "birth_date" not in excluded and self.birth_date is not None:
            if self.birth_date > date.today():
                raise ValidationError(
                    {"birth_date": "A date of birth cannot be in the future."}
                )
        super().clean_fields(exclude=excluded)

    class Meta:
        constraints = [
            models.constraints.UniqueConstraint(
                fields=["organization", "student_number"],
                name="students_organization_student_number",
            ),
        ]


class Application(models.Model):
    """One admission record at one center (ADM-001/ADM-002/ADM-003/ADM-004).

    An application is a record of the center's admission procedure, never a
    request a learner signs in with (STU-004: students have no login;
    RULE 6.1: it belongs to exactly one center). The application date is
    created_at: the record IS the application, made when it was recorded.
    desired_course is free text typed by center staff, kept apart from a
    Course model on purpose - courses are a later feature (docs/OPEN_QUESTIONS.md).
    converted_student names the Student record this application created
    (ADM-004: conversion without re-entering information).
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    student_number = models.CharField(max_length=20, blank=True)
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=120, blank=True)
    source = models.CharField(max_length=60, blank=True)
    desired_course = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=16,
        choices=APPLICATION_STATUS_CHOICES,
        default="new",
    )
    note = models.CharField(max_length=300, blank=True)
    converted_student = models.ForeignKey(
        Student,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="converted_applications",
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def clean_fields(self, exclude=None):
        """ADM-002 rules, one place (the tenant settings hook pattern)."""
        excluded = set(exclude or ())
        if "student_number" not in excluded:
            self.student_number = str(self.student_number).strip().upper()
            if self.student_number and not STUDENT_NUMBER_PATTERN.match(
                self.student_number
            ):
                raise ValidationError(
                    {
                        "student_number": (
                            "A named student number must match {}."
                        ).format(STUDENT_NUMBER_PATTERN.pattern)
                    }
                )
        if "name" not in excluded:
            self.name = str(self.name).strip()
            if not self.name:
                raise ValidationError(
                    {"name": "An application must name the person it is about."}
                )
        if "contact" not in excluded:
            self.contact = str(self.contact).strip()
        if "source" not in excluded:
            self.source = str(self.source).strip()
        if "desired_course" not in excluded:
            self.desired_course = str(self.desired_course).strip()
        if "note" not in excluded:
            self.note = str(self.note).strip()
        if "status" not in excluded:
            self.status = str(self.status).strip().lower()
        if "converted_student" not in excluded:
            linked = self.converted_student
            if (
                linked is not None
                and linked.organization_id != self.organization_id
            ):
                raise ValidationError(
                    {
                        "converted_student": (
                            "That student belongs to another center."
                        )
                    }
                )
        super().clean_fields(exclude=excluded)
