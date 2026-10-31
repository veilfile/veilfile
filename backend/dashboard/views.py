"""The dashboard (spec 4.8): the five sections at /app/, as ordinary Django views on layouts/app.html.

Every view here is wrapped in dashboard_view: a signed-out request goes to sign in and back, every answer is kept out of search results,
and the CSRF cookie is set. Every state change on a dashboard page is a POST from a form on a page, and every object an address names is
looked up within the signed-in user's own account (a stranger's, or a missing one, is the shell's 404).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_safe

from .overview_page import overview_context

from dashboard import billing_page
from dashboard.file_rows import file_rows, state_of
from drop_billing import account as billing_account
from drop_core import account
from drop_core.models import Artifact, get_or_create_workspace
from pages.errors import not_found


def dashboard_view(view):
    """`view` for signed-in visitors only, kept out of search results, with the CSRF cookie set.

    login_required is the inner layer of the answer and the header the outer, so the redirect to sign in carries X-Robots-Tag too: every
    answer under /app/ does (an error page drawn for an address under /app/ gets it from pages/errors.py).
    """
    guarded = login_required(ensure_csrf_cookie(view))

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = guarded(request, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex, nofollow"
        # Nothing of an account may come back from the browser's cache after Sign out (Back), and Billing holds a Stripe invoice link.
        patch_cache_control(response, no_store=True, private=True)
        return response

    return wrapper


@dashboard_view
@require_safe
def overview(request):
    """Overview (spec 4.8). What it shows, and what it does when a read fails: dashboard/overview_page.py."""
    return render(request, "dashboard/overview.html", overview_context(request))


# Files (spec 4.8)

FILES_PER_PAGE = 50
REVOKED = "The file is revoked."
# Only a live file is revoked here, as only a live file is offered for it. An expired file is dead and the sweep deletes it
# (the one a failed revoke kept too); a held one is the safety review's to release.
NOT_LIVE = "That file's link has already stopped working, so there is nothing to revoke."
# account.revoke_artifact answers 204 with {"storage_deleted": False} when the storage delete failed (services.REVOKE_PENDING_DELETE):
# the link is dead, the row is kept (expired) and the sweep deletes the file later. The page says so instead of claiming the file is gone.
REVOKED_PENDING = ("The file is revoked. Its link has stopped working, but storage did not confirm the delete. "
                   "The file will be removed in a later cleanup.")


@dashboard_view
@require_safe
@never_cache
def files(request):
    """Files: the workspace's files, newest first, FILES_PER_PAGE to a page, 'Older files' by the endpoint's own cursor.

    The page holds a bearer link for each live file (for Copy), so it is never cached. A ?cursor= that is not one of the
    endpoint's (typed by hand, or cut short) shows the newest page with a sentence, not an error: a link a reader mistyped
    should not end at a dead page.
    """
    cursor = request.GET.get("cursor") or None
    answer = account.list_artifacts(request.user, cursor=cursor, limit=FILES_PER_PAGE)
    bad_cursor = answer.status == 400
    if bad_cursor:
        cursor = None
        answer = account.list_artifacts(request.user, limit=FILES_PER_PAGE)
    here = reverse("app-files")
    next_cursor = answer.headers.get("X-Next-Cursor")
    return render(request, "dashboard/files.html", {
        "files": file_rows(request.user, answer.body),
        "older_url": f"{here}?{urlencode({'cursor': next_cursor})}" if next_cursor else "",
        "newest_url": here if cursor else "",
        "bad_cursor": bad_cursor,
    })


def _own_file(user, artifact_id):
    """The user's own file, else the shell's 404: another workspace's file and a missing one cannot be told apart.

    It looks in get_or_create_workspace(user), the workspace the list and account.revoke_artifact read, so the page, the
    confirmation and the revoke agree on which files are the user's.
    """
    artifact = Artifact.objects.filter(id=artifact_id, workspace=get_or_create_workspace(user)).first()
    if artifact is None:
        raise Http404("No such file.")
    return artifact


@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def file_revoke(request, artifact_id):
    """Revoke a file: a GET shows the confirmation and changes nothing; the POST revokes and goes back to Files.

    Another user's file and a missing one are the same 404, whatever the file's state (the ownership check comes first). A
    file that is not live (expired or held) is not revoked here, for a GET or a POST: the visitor goes back to Files with a
    sentence and nothing changes."""
    artifact = _own_file(request.user, artifact_id)
    if state_of(artifact.expires_at, artifact.safety_status == "held", timezone.now()) != "live":
        messages.info(request, NOT_LIVE)
        return redirect("app-files")
    if request.method != "POST":
        return render(request, "dashboard/file_revoke.html", {"artifact": artifact})
    answer = account.revoke_artifact(request.user, artifact_id)
    if answer.status == 404:
        raise Http404("No such file.")  # gone between the lookup and the revoke
    if answer.body["storage_deleted"]:
        messages.success(request, REVOKED)
    else:
        messages.warning(request, REVOKED_PENDING)
    return redirect("app-files")


@dashboard_view
@require_safe
def billing(request):
    """Billing: the plan, what Stripe says of it, what is over a limit and the ways to change plan. It reads the database and
    nothing else: billing.status makes no Stripe call, and nor does a GET of any billing address."""
    status = billing_account.status(request.user).body
    pending = billing_page.pending_for(request.session, status["plan"], current_interval=billing_page.interval_of(status["billing_interval"]))
    return render(request, "dashboard/billing.html", billing_page.page_context(status, pending))


@dashboard_view
def billing_checkout(request):
    """POST: start Stripe Checkout for the plan (and, if the form names one, the billing interval) the form named. The browser is
    sent (303) to the address Stripe returned, if it is an https one; anything else is one sentence on Billing. A GET does nothing
    and goes to Billing."""
    if request.method != "POST":
        return redirect("app-billing")
    try:
        answer = billing_account.checkout(request.user, request.POST.get("plan", ""), request.POST.get("interval") or "month")
    except Exception:
        messages.error(request, billing_page.unexpected("checkout"))
        return redirect("app-billing")
    url = billing_page.https_only(billing_page.body_of(answer).get("checkout_url")) if answer.status == 200 else None
    if url:
        return HttpResponseRedirect(url, status=303)
    messages.error(request, billing_page.refusal(answer, billing_page.NO_SUCH_PLAN, "checkout"))
    return redirect("app-billing")


@dashboard_view
def billing_portal(request):
    """POST: open the Stripe customer portal. As checkout: 303 to Stripe's https address, or one sentence on Billing."""
    if request.method != "POST":
        return redirect("app-billing")
    try:
        answer = billing_account.portal(request.user)
    except Exception:
        messages.error(request, billing_page.unexpected("portal"))
        return redirect("app-billing")
    url = billing_page.https_only(billing_page.body_of(answer).get("portal_url")) if answer.status == 200 else None
    if url:
        return HttpResponseRedirect(url, status=303)
    messages.error(request, billing_page.refusal(answer, billing_page.NO_BILLING_ACCOUNT, "portal"))
    return redirect("app-billing")


@dashboard_view
@require_http_methods(["GET", "HEAD", "POST"])
def billing_switch(request, plan):
    """GET: the page that asks before a plan switch. POST: the switch. The address names the plan, and ?interval=year a yearly
    price (none is monthly); the confirmation form posts to its own address, so the interval rides along. A plan that is not for
    sale, or an interval that is neither, is the shell's 404."""
    interval = request.GET.get("interval", "month")
    if not billing_page.for_sale(plan, interval):
        # Returned, not raised, so that dashboard_view puts its header on it as on every /app/ response.
        return not_found(request)
    if request.method == "POST":
        return _switch_plan(request, plan, interval)
    return _confirm_switch(request, plan, interval)


def _confirm_switch(request, plan, interval):
    """The confirmation page. It only shows a form; there is nothing to confirm without a subscription or for the plan and
    interval held."""
    status = billing_account.status(request.user).body
    if not status["has_subscription"]:
        messages.info(request, billing_page.NOT_SUBSCRIBED)
        return redirect("app-billing")
    current_interval = billing_page.interval_of(status["billing_interval"])
    if (status["plan"], current_interval) == (plan, interval):
        messages.info(request, billing_page.switch_result(plan, "unchanged", interval))
        return redirect("app-billing")
    return render(request, "dashboard/billing_switch.html", billing_page.switch_context(status["plan"], plan, current_interval, interval))


def _switch_plan(request, plan, interval):
    """The switch. Plan state changes only inside billing.switch, from what Stripe returns (invariant 3); this keeps the
    unpaid-switch hint and turns the answer into the one line Billing shows."""
    try:
        answer = billing_account.switch(request.user, plan, interval)
    except Exception:
        messages.error(request, billing_page.unexpected("switch"))
        return redirect("app-billing")
    body = billing_page.body_of(answer)
    if answer.status == 200:
        billing_page.forget_pending(request.session)
        messages.success(request, billing_page.switch_result(body.get("plan") or plan, body.get("status"), interval))
        return redirect("app-billing")
    if answer.status == 402 and body.get("code") == "payment_required":
        billing_page.remember_pending(request.session, plan, body.get("invoice_url"), interval=interval)
    messages.error(request, billing_page.refusal(answer, billing_page.NO_SUCH_PLAN, "switch"))
    return redirect("app-billing")
