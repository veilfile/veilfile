from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView
from config import health
from drop_core import device as device_views
from drop_core import oauth as oauth_views
from drop_core import views as core_views
from pages import views as pages_views
from drop_core.throttle import throttle_login
from oauth2_provider import urls as oauth2_provider_urls


# Phase 2 OAuth authorization server (django-oauth-toolkit). Only the
# routes Veilfile uses are mounted, under DOT's "oauth2_provider" namespace
# so reverse("oauth2_provider:...") (metadata documents, DCR responses)
# keeps working. Deliberately absent: DOT's device flow, introspection,
# the applications/* and authorized_tokens/* pages, and OIDC. Each one
# either issued tokens around the Veilfile consent page and its
# email-verification gate, or exposed token data to any DCR client.
# Every route that checks or hashes a client secret (token, revoke_token,
# register) is a Throttled view: drop_core.tests_oauth_hardening fails when a
# route is added without being classified.
_oauth_urlpatterns = [
    path(
        "authorize/",
        oauth_views.VeilfileAuthorizationView.as_view(),
        name="authorize",
    ),
    path("token/", oauth_views.ThrottledTokenView.as_view(), name="token"),
    path(
        "revoke_token/",
        oauth_views.ThrottledRevokeTokenView.as_view(),
        name="revoke-token",
    ),
    path(
        "register/",
        oauth_views.ThrottledDynamicClientRegistrationView.as_view(),
        name="dcr-register",
    ),
    # RFC 7592 management endpoint with the same metadata validation as
    # DCR (PUT can replace redirect_uris and grant_types).
    path(
        "register/<str:client_id>/",
        oauth_views.ValidatedDCRManagementView.as_view(),
        name="dcr-register-management",
    ),
] + oauth2_provider_urls.metadata_urlpatterns

urlpatterns = [
    path("o/", include((_oauth_urlpatterns, "oauth2_provider"))),
    # RFC 8414 + RFC 9728 metadata must live at the origin root, not under /o/.
    path("", include(oauth2_provider_urls.metadata_urlpatterns)),
    # Login (site and admin) and password reset are throttled; password
    # reset/change also disconnect OAuth apps. Mounted ahead of the stock
    # routes they replace (same paths and names).
    path("admin/login/", throttle_login(admin.site.login)),
    path('admin/', admin.site.urls),
    path(
        "accounts/login/",
        throttle_login(core_views.SignInView.as_view()),
        name="login",
    ),
    path(
        "accounts/password_reset/",
        core_views.ThrottledPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        core_views.RevokingPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "accounts/password_change/",
        core_views.RevokingPasswordChangeView.as_view(),
        name="password_change",
    ),
    path('accounts/', include('django.contrib.auth.urls')),
    path('accounts/signup/', core_views.signup, name='signup'),
    # Hand-typed shortcut: /signup lands on the real signup page.
    path(
        "signup",
        RedirectView.as_view(url="/accounts/signup/", permanent=False),
        name="signup-shortcut",
    ),
    path(
        "signup/",
        RedirectView.as_view(url="/accounts/signup/", permanent=False),
    ),
    path('accounts/verify-email/', core_views.email_verification, name='email-verification'),
    path('accounts/verify-email/<str:token>/', core_views.verify_email, name='verify-email'),
    path('api/v1/', include('drop_core.urls')),
    path('api/v1/billing/', include('drop_billing.urls')),
    path('', include('drop_mcp.urls')),  # POST /mcp — Streamable HTTP MCP endpoint
    # Render health check (normally answered by HealthCheckMiddleware
    # before routing; see config.health).
    path('healthz', health.healthz, name='healthz'),
    path('llms.txt', core_views.llms_txt, name='llms-txt'),
    # Public MCP directory manifest (mcpub.dev probes this unauthenticated).
    # Verbatim copy of the packet CMO filed; no secrets, no auth, JSON.
    path(
        ".well-known/mcp.json",
        TemplateView.as_view(
            template_name="wellknown/mcp.json",
            content_type="application/json",
        ),
        name="mcp-well-known",
    ),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="robots.txt", content_type="text/plain"),
    ),
    path("sitemap.xml", pages_views.sitemap, name="sitemap"),
    path('a/<str:token>', core_views.serve_artifact, name='serve-artifact'),
    # Device authorization flow browser pages. Trailing slashes optional:
    # a 301 on a POST would drop the form body.
    path("device", device_views.device_page, name="device"),
    path("device/", device_views.device_page),
    path("device/approve", device_views.device_approve),
    path("device/approve/", device_views.device_approve, name="device-approve"),
    path("device/deny", device_views.device_deny),
    path("device/deny/", device_views.device_deny, name="device-deny"),
    path('blog/', include('blog.urls')),
    path('docs/', include('docs.urls')),
    path("plans/", pages_views.plans, name="plans"),
    path("how/", pages_views.chapter, {"name": "how"}, name="how"),
    path("begin/", pages_views.chapter, {"name": "begin"}, name="begin"),
    path("promises/", pages_views.chapter, {"name": "promises"}, name="promises"),
    # Old address of Promises (spec 4.3): permanent, so search engines move it over.
    path("trust/", RedirectView.as_view(url="/promises/", permanent=True), name="trust-redirect"),
    path("about/", pages_views.chapter, {"name": "about"}, name="about"),
    path('', include('legal.urls')),
    path("app/components/", pages_views.component_catalog, name="component-catalog"),
    # The signed-in dashboard (spec 4.8): five sections, each a Django view. Anything else under /app/ is the shell's 404.
    path("app/", include("dashboard.urls")),
    path("", pages_views.landing, name="landing"),  # the threshold
]

# Error pages (spec 4.9), drawn by pages/errors.py: 404 and 403 on the shell, 500 self-contained.
# A failed CSRF check has its own view, CSRF_FAILURE_VIEW in settings.py.
handler403 = "pages.errors.permission_denied"
handler404 = "pages.errors.not_found"
handler500 = "pages.errors.server_error"
