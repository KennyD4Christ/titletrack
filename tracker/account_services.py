"""Account links (first password setup, password reset) and the account audit trail.

Design in one paragraph: a link token is a Django-signed, timestamped string that contains the account id,
the purpose ("setup" or "reset"), the account's `link_serial` and a fingerprint of its current password hash.
Nothing about a token is stored. A token stops working when: it expires (setup 3 days, reset 1 day); a newer
link is issued (the serial changes); the password is set or changed (serial and fingerprint change); the
account is deactivated or protected; or it is used for the other purpose (different signing salt).
Tokens, links and passwords are never written to the audit trail or to logs.
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import AccountEvent, User

logger = logging.getLogger(__name__)

SETUP = "setup"
RESET = "reset"

# Settled requirements. Do not change.
LINK_LIFETIME = {SETUP: timedelta(days=3), RESET: timedelta(days=1)}
LINK_PATH = {SETUP: "/set-password", RESET: "/reset-password"}   # React routes; the token travels in #token=...

_FORBIDDEN_CHANGE_KEYS = ("token", "password", "secret", "link")


class AccountLinkError(Exception):
    """The account may not receive a link (inactive, no company, protected, other company)."""


class InvalidLink(Exception):
    """The token is malformed, expired, used, replaced or belongs to nothing. Deliberately says nothing more."""


# ---------- audit ----------

def log_account_event(company_id, action, *, target_user=None, actor=None, changes=None, note="", target_email=None):
    """Append one AccountEvent. The target's email is copied in, so the history stays readable if the
    account changes later. Refuses change-keys that look like secrets."""
    changes = changes or {}
    for key in changes:
        if any(word in str(key).lower() for word in _FORBIDDEN_CHANGE_KEYS):
            raise ValueError(f"Refusing to store '{key}' in the account history.")
    if target_email is None:
        target_email = (target_user.email or "") if target_user is not None else ""
    return AccountEvent.objects.create(
        company_id=company_id, target_user=target_user, target_email=target_email, actor=actor,
        action=action, changes=changes, note=note)


# ---------- tokens ----------

def _salt(purpose):
    return f"titletrack.account-link.{purpose}"


def _fingerprint(user):
    # Changes whenever the stored password hash changes; reveals nothing about it.
    return salted_hmac("titletrack.account-link.password", user.password).hexdigest()[:16]


def _make_token(user, purpose):
    value = f"{user.pk}.{purpose}.{user.link_serial}.{_fingerprint(user)}"
    return signing.TimestampSigner(salt=_salt(purpose)).sign(value)


def _check_token(token, purpose, lock=False):
    """Return the account the token is valid for. Raises InvalidLink for every kind of failure (same message).
    With lock=True (inside a transaction) the account row is locked, so two uses of one link cannot both win."""
    if purpose not in LINK_LIFETIME:
        raise InvalidLink()
    if not isinstance(token, str) or not token or len(token) > 300:
        raise InvalidLink()
    try:
        value = signing.TimestampSigner(salt=_salt(purpose)).unsign(
            token, max_age=LINK_LIFETIME[purpose].total_seconds())
        pk, token_purpose, serial, fingerprint = value.split(".")
        pk, serial = int(pk), int(serial)
    except (signing.BadSignature, ValueError):          # SignatureExpired is a BadSignature
        raise InvalidLink() from None
    if token_purpose != purpose:
        raise InvalidLink()
    users = User.objects.select_for_update() if lock else User.objects
    user = users.filter(pk=pk).first()
    if (user is None or not user.is_active or user.company_id is None or user.is_staff or user.is_superuser
            or user.link_serial != serial or _fingerprint(user) != fingerprint):
        raise InvalidLink()
    return user


def invalidate_links(user):
    """Kill every outstanding link of this account (call when deactivating it). Safe to call repeatedly."""
    User.objects.filter(pk=user.pk).update(link_serial=F("link_serial") + 1)
    user.refresh_from_db(fields=["link_serial"])


# ---------- issuing ----------

@dataclass
class IssuedLink:
    purpose: str
    expires_at: object
    user_id: int
    company_id: int
    email: str
    token: str = field(repr=False)          # never appears in repr()/logs

    @property
    def url(self):
        return f"{settings.SITE_URL.rstrip('/')}{LINK_PATH[self.purpose]}#token={self.token}"


def _check_may_receive_link(user, actor):
    if user.company_id is None:
        raise AccountLinkError("This account does not belong to a company.")
    if not user.is_active:
        raise AccountLinkError("This account is deactivated.")
    if user.is_staff or user.is_superuser:
        raise AccountLinkError("Django admin accounts are protected and cannot be given a link here.")
    if actor is not None and actor.company_id != user.company_id:
        raise PermissionDenied("Account belongs to another company.")


@transaction.atomic
def issue_link(user, purpose, actor=None, note=""):
    """Create a new link for the account, replacing every older link, and record who asked.
    Role rules (for example 'a Manager may not issue links for an MD') belong to the API layer (stage 2)."""
    if purpose not in LINK_LIFETIME:
        raise ValueError("Unknown link purpose.")
    locked = User.objects.select_for_update().get(pk=user.pk)
    _check_may_receive_link(locked, actor)
    locked.link_serial = F("link_serial") + 1
    locked.save(update_fields=["link_serial"])
    locked.refresh_from_db()
    expires_at = timezone.now() + LINK_LIFETIME[purpose]
    action = AccountEvent.Action.SETUP_LINK_ISSUED if purpose == SETUP else AccountEvent.Action.RESET_LINK_ISSUED
    log_account_event(locked.company_id, action, target_user=locked, actor=actor,
                      changes={"expires_at": [None, expires_at.isoformat()]}, note=note)
    return IssuedLink(purpose=purpose, expires_at=expires_at, user_id=locked.pk, company_id=locked.company_id,
                      email=locked.email, token=_make_token(locked, purpose))


# ---------- delivery ----------

@dataclass
class LinkOutcome:
    status: str                  # "emailed" | "revealed" | "email_failed"
    expires_at: object
    link: str = field(default=None, repr=False)     # set ONLY when status == "revealed": show it once, never store it


def _email_body(issued):
    days = LINK_LIFETIME[issued.purpose].days
    what = "set your password" if issued.purpose == SETUP else "reset your password"
    return (
        f"Use this link to {what} for TitleTrack:\n\n{issued.url}\n\n"
        f"The link works once and expires in {days} day{'s' if days != 1 else ''}. "
        "If you did not expect this email, ignore it.\n")


def deliver_link(issued, actor=None, reveal_if_email_fails=False):
    """Email the link, or hand it back once. Call OUTSIDE any open transaction, so the audit rows written
    here are kept even when the caller later fails.
    - email delivery disabled: the link is returned once to the administrator (audited as revealed).
    - delivery enabled and sent: nothing is returned.
    - delivery enabled and failed: audited; the link is returned only if `reveal_if_email_fails` was asked for."""
    user = User.objects.get(pk=issued.user_id)
    if not settings.EMAIL_DELIVERY_ENABLED:
        log_account_event(issued.company_id, AccountEvent.Action.LINK_REVEALED, target_user=user, actor=actor,
                          note="Email delivery is disabled: link shown once to the administrator.")
        return LinkOutcome("revealed", issued.expires_at, issued.url)
    reason = None
    if not issued.email:
        reason = "The account has no email address."
    else:
        try:
            send_mail("Your TitleTrack link", _email_body(issued), settings.DEFAULT_FROM_EMAIL,
                      [issued.email], fail_silently=False)
        except Exception as exc:                          # SMTP, DNS, timeout...: log the type only
            logger.warning("Account link email failed (%s).", type(exc).__name__)
            reason = f"Sending failed ({type(exc).__name__})."
    if reason is None:
        return LinkOutcome("emailed", issued.expires_at)
    log_account_event(issued.company_id, AccountEvent.Action.EMAIL_FAILED, target_user=user, actor=actor, note=reason)
    if reveal_if_email_fails:
        log_account_event(issued.company_id, AccountEvent.Action.LINK_REVEALED, target_user=user, actor=actor,
                          note="Email failed: link shown once to the administrator on request.")
        return LinkOutcome("revealed", issued.expires_at, issued.url)
    return LinkOutcome("email_failed", issued.expires_at)


def issue_and_deliver(user, purpose, actor=None, reveal_if_email_fails=False, note=""):
    issued = issue_link(user, purpose, actor=actor, note=note)
    return deliver_link(issued, actor=actor, reveal_if_email_fails=reveal_if_email_fails)


# ---------- using a link ----------

@transaction.atomic
def complete_password_link(token, purpose, new_password):
    """Set the account's password from a valid link; the link (and any other) stops working at once.
    Raises InvalidLink (generic) or ValidationError (password rejected; the link stays usable).
    It does NOT log anybody in: the person signs in afterwards with the new password."""
    user = _check_token(token, purpose, lock=True)
    validate_password(new_password, user)
    user.set_password(new_password)
    user.link_serial = F("link_serial") + 1
    user.save(update_fields=["password", "link_serial"])
    user.refresh_from_db()
    log_account_event(user.company_id, AccountEvent.Action.PASSWORD_SET, target_user=user, actor=user,
                      note="Setup link" if purpose == SETUP else "Reset link")
    return user
