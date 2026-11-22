"""The account layer's own pages: landing, signing in, choosing a centre.

No address here is inside any tenant (AUTH-002): these pages speak for the
person, and never show a centre's records. The centres a signed-in person
belongs to are the only centre data an account page ever shows, and they come
from the services' re-verified membership lookup, never from a session value.

Sign-in and sign-out use django.contrib.auth, so the session cookie, the CSRF
token, and the logged-in state all come from the framework. The working centre
recorded in the session is only a convenience: every page that uses it calls
it here, and the views that answer a tenant address re-check the membership
regardless of it (SESSION-001).

After signing in: exactly one centre -> that centre's dashboard (303),
otherwise -> the choose-a-centre page (AUTH-003/004). A failed sign-in is
answered on the same sign-in page, never as a page of its own (RULE 6.3).
"""

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

from apps.pages import page, project_ui
from apps.accounts.services import (
    clear_working_center,
    set_working_center,
    working_center,
)
from apps.organizations.services import centers
from apps.organizations.views import authenticated

SIGN_IN_ADDRESS = "/accounts/sign-in/"
CHOOSE_ADDRESS = "/accounts/choose/"


def sign_in_fields(ui):
    """The sign-in form: the account layer's own two fields."""
    return {
        "id": "accounts/sign-in",
        "method": "post",
        "action": SIGN_IN_ADDRESS,
        "submit": ui["sign_in"],
        "fields": [
            {
                "id": "username",
                "title": ui["username"],
                "type": "text",
                "value": "",
            },
            {
                "id": "password",
                "title": ui["password"],
                "type": "password",
                "value": "",
            },
        ],
    }


def landing(request):
    """The product's landing page: signed in or not, and nothing else."""
    ui = project_ui(request)
    data = {"signed_in": bool(authenticated(request))}
    return page(request, "landing", data)


def sign_in(request):
    """Sign in, and say where the person is working afterwards."""
    ui = project_ui(request)
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return page(request, "sign_in", {"form": sign_in_fields(ui)})
    if request.method != "POST":
        return page(request, "problem", {}, status=405)
    user = authenticate(request, username=request.POST.get("username", ""),
                        password=request.POST.get("password", ""))
    if user is None:
        data = {
            "form": sign_in_fields(ui),
            "note": ui["sign_in_failed"],
            "note_kind": "rejected",
        }
        return page(request, "sign_in", data)
    login(request, user)
    clear_working_center(request)
    own = centers(request.user)
    if len(own) == 1:
        set_working_center(request, own[0].slug)
        return redirect(f"/o/{own[0].slug}/center/dashboard/")
    return redirect(CHOOSE_ADDRESS)


def sign_out(request):
    """End the session, and stay on the landing page that says so."""
    ui = project_ui(request)
    logout(request)
    data = {
        "signed_in": False,
        "note": ui["signed_out"],
        "note_kind": "signed_out",
    }
    return page(request, "landing", data)


def choose(request):
    """Pick the centre a signed-in person works in. Only theirs are offered."""
    ui = project_ui(request)
    if not authenticated(request):
        return page(request, "denied", {}, status=403)
    if request.method not in ("GET", "HEAD", "OPTIONS", "POST"):
        return page(request, "problem", {}, status=405)
    own = centers(request.user)
    data = {
        "centers": [{"slug": center.slug, "name": center.name} for center in own],
        "working_center": "",
    }
    if request.method == "POST":
        picked = request.POST.get("center", "")
        chosen = [center for center in own if center.slug == picked]
        if chosen:
            set_working_center(request, chosen[0].slug)
        else:
            data["note"] = ui["nothing_changed"]
            data["note_kind"] = "unchanged"
    slug = working_center(request)
    current = [center for center in own if center.slug == slug]
    if current:
        data["working_center"] = current[0].name
        data["working_slug"] = current[0].slug
    return page(request, "choose", data)
