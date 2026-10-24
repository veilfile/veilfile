"""Forms for the Veilfile account flows."""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


class SignupForm(UserCreationForm):
    """Username/password signup with a required, unique email address.

    The email is the abuse-response contact path (account bans, takedowns),
    so it must be present and verified before API keys are issued.
    """

    email = forms.EmailField(required=True, max_length=254)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with this email address already exists."
            )
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class EmailUpdateForm(forms.Form):
    """Add or change the account's email address.

    The current password is required: the address receives password reset
    links, so a hijacked session alone must not be able to repoint it.
    """

    email = forms.EmailField(required=True, max_length=254)
    password = forms.CharField(
        required=True, strip=False, widget=forms.PasswordInput
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Your current password is incorrect.")
        return password

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        taken = (
            get_user_model()
            .objects.filter(email__iexact=email)
            .exclude(pk=self.user.pk)
            .exists()
        )
        if taken:
            raise forms.ValidationError(
                "This email address can't be used. Try another."
            )
        return email
