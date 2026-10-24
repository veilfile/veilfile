"""Session-auth backends for the browser login form.

The login form's identifier field accepts a username or an email address.
Signup enforces a unique email (SignupForm.clean_email), so an email
identifier resolves to exactly one account; a username that happens to
contain "@" still falls through to the username lookup when no email
matches.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Authenticate with either the username or the email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        identifier = username.strip()
        UserModel = get_user_model()
        user = None
        if "@" in identifier:
            user = (
                UserModel.objects.filter(email__iexact=identifier)
                .order_by("pk")
                .first()
            )
        if user is None:
            try:
                user = UserModel._default_manager.get_by_natural_key(identifier)
            except UserModel.DoesNotExist:
                # Run the hasher anyway so unknown identifiers do not
                # answer faster than wrong-password attempts.
                UserModel().set_password(password)
                return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
