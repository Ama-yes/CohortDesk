"""Session state for a signed-in account: the center they are working in.

The session holds only the slug of the chosen center. Membership itself is
re-checked at every tenant address against the server-side records (AUTH-005),
so a stale working-center value can never grant access by itself. django.contrib
.auth.logout flushes the whole session, which clears this key as well (AUTH-004).
"""

WORKING_CENTER = "working_center"


def working_center(request):
    """The slug the person is working in, or empty string."""
    return request.session.get(WORKING_CENTER, "")


def set_working_center(request, slug):
    request.session[WORKING_CENTER] = slug


def clear_working_center(request):
    request.session[WORKING_CENTER] = ""
