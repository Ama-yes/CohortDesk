from django.db import models
from django.contrib.auth.models import AbstractUser


class CohortUser(AbstractUser):
    """A global login identity, never bound to one organization.

    Access to any tenant comes from organizations.Membership. `username` is an
    internal handle (not shown in the product UI). Student and staff records
    are separate concepts and have no login accounts (AUTH-001, MGMT-003).
    """

    display_name = models.CharField(max_length=100, blank=True)

    def clean_fields(self, exclude=None):
        self.display_name = self.display_name.strip()
