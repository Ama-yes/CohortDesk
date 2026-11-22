"""Project-level views: the safe health endpoint only.

The product landing page is not here: it belongs to the account layer
(apps/accounts/views), because it decides whether the current request has a
signed-in person. Nothing this view answers exposes configuration, counts or
personal data.
"""

from django.http import HttpResponse


def health(request):
    """Cheap health check for production monitoring. No secrets, no counts."""
    return HttpResponse("ok", content_type="text/plain")
