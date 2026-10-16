"""Staff account management (create, edit, deactivate, reactivate, issue links, read history).

The rules live HERE, not in the view, so they hold whoever calls: the API, a command or a shell.
Every write runs in one transaction that locks (in id order, so requests cannot deadlock) the acting
account, the target account and the company's active MDs, and then works on those fresh rows. That is
what makes "the company always keeps an active MD" and "a deactivated person can no longer act" safe
under concurrent requests. Passwords, tokens and links never reach the audit trail.
"""
import re
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import F, Q

from . import account_services as acc
from . import services
from .models import AccountEvent, User

MD = User.Role.MD.value
MANAGER = User.Role.MANAGER.value
STAFF = User.Role.STAFF.value
SALES = User.Role.SALES.value
VIEWER = User.Role.VIEWER.value
ALL_ROLES = (MD, MANAGER, STAFF, SALES, VIEWER)

# Who may create / edit / deactivate / reactivate / issue links for which roles. A role that is not a key
# here manages nobody. (Which roles may use the staff API at all is the `manage_users` row of the matrix in
# api/permissions.py; a test keeps the two in step.)
ROLE_MANAGES = {
    MD: frozenset(ALL_ROLES),
    MANAGER: frozenset({STAFF, SALES, VIEWER}),
}

USERNAME_LIMIT = User._meta.get_field("username").max_length      # the email becomes the username
NAME_LIMIT = User._meta.get_field("first_name").max_length
REASON_LIMIT = 500
UNSET = object()
# Deliberately says nothing about WHERE the clash is (this company, another company, or an old username).
EMAIL_TAKEN = "This email address is not available."


class StaffStateError(Exception):
    """The account is in the wrong state for this action (already deactivated, last MD...). Answered with 409."""


class StaffValidationError(Exception):
    """Bad input. `errors` is {"field": ["message"]}, the same shape as the API's other field errors."""

    def __init__(self, errors):
        self.errors = {k: (v if isinstance(v, list) else [v]) for k, v in errors.items()}
        super().__init__("; ".join(m for msgs in self.errors.values() for m in msgs))


# ---------- what a person may do (also used to show or hide controls) ----------

def manageable_roles(actor):
    return ROLE_MANAGES.get(actor.role, frozenset())


def role_label(role):
    return User.Role(role).label if role in ALL_ROLES else str(role)


def role_choices(actor):
    return [{"value": r, "label": role_label(r)} for r in ALL_ROLES if r in manageable_roles(actor)]


def may_manage(actor, target):
    """True when `actor` may change `target`: same company, a role that manages that role, and not a
    Django admin account. (Self-service limits such as 'not your own role' are enforced when acting.)"""
    allowed = manageable_roles(actor)
    return bool(
        allowed and actor.company_id is not None and actor.company_id == target.company_id
        and not (target.is_staff or target.is_superuser) and target.role in allowed)


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


# ---------- locking and rule checks ----------

def _lock(actor, target_pk=None):
    """Lock and return fresh (actor, target, active_mds). Raises PermissionDenied when the actor can no
    longer act (deactivated, demoted, or in no company) or the target is not in the actor's company."""
    if actor.company_id is None:
        raise PermissionDenied("You are not allowed to manage staff accounts.")
    query = Q(pk=actor.pk) | Q(company_id=actor.company_id, role=MD, is_active=True)
    if target_pk is not None:
        query |= Q(pk=target_pk)
    rows = {u.pk: u for u in User.objects.select_for_update().filter(query).order_by("pk")}
    me = rows.get(actor.pk)
    if me is None or not me.is_active or me.company_id is None or not manageable_roles(me):
        raise PermissionDenied("You are not allowed to manage staff accounts.")
    target = None
    if target_pk is not None:
        target = rows.get(int(target_pk))
        if target is None or target.company_id != me.company_id:
            raise PermissionDenied("Account not found in your company.")
    active_mds = [u for u in rows.values() if u.company_id == me.company_id and u.role == MD and u.is_active]
    return me, target, active_mds


def _check_manage(me, target, new_role=None):
    allowed = manageable_roles(me)
    if me.company_id != target.company_id:
        raise PermissionDenied("Account not found in your company.")
    if target.is_staff or target.is_superuser:
        raise PermissionDenied("Django admin accounts are protected and cannot be changed here.")
    if target.role not in allowed:
        raise PermissionDenied(f"Your role cannot manage {role_label(target.role)} accounts.")
    if new_role is not None and new_role not in allowed:
        raise PermissionDenied(f"Your role cannot assign the {role_label(new_role)} role.")


def _guard_last_md(active_mds, target):
    """The company must keep at least one active MD. (Single requests can never hit this, because only an
    active MD may act on an MD; it is the safety net for requests that race each other.)"""
    if target.role == MD and target.is_active and all(u.pk == target.pk for u in active_mds):
        raise StaffStateError("A company must keep at least one active MD.")


def _check_department(me, role, department, errors):
    if department is not None and department.company_id != me.company_id:
        errors["department"] = "Choose a department from your company."
    elif role == STAFF and department is None:
        errors["department"] = "A department is required for Staff accounts."


def _name_errors(errors, field, label, value):
    if not value:
        errors[field] = f"{label} is required."
    elif len(value) > NAME_LIMIT:
        errors[field] = f"{label} is too long (maximum {NAME_LIMIT} characters)."


# ---------- reads ----------

def open_obligation_count(user):
    """Open obligations (on active sales) this person owns: the same definition as every queue and report."""
    return services.open_obligations(user.company).filter(owner=user).count()


def account_history(actor, target):
    """The account's audit trail. Needs a role that manages the target's role, so a Manager cannot read the
    history of an MD or a Manager. (Read-only Django admin accounts can be read by an MD.)"""
    allowed = manageable_roles(actor)
    if actor.company_id is None or actor.company_id != target.company_id:
        raise PermissionDenied("Account not found in your company.")
    if not allowed or target.role not in allowed:
        raise PermissionDenied(f"Your role cannot see the history of {role_label(target.role)} accounts.")
    return AccountEvent.objects.filter(company_id=actor.company_id, target_user=target).select_related("actor")


# ---------- writes ----------

@transaction.atomic
def create_staff(actor, *, email, first_name, last_name, role, department=None):
    """Create an account with no usable password. The company is always the actor's company."""
    me, _, _ = _lock(actor)
    if role not in ALL_ROLES:
        raise StaffValidationError({"role": "Choose a valid role."})
    if role not in manageable_roles(me):
        raise PermissionDenied(f"Your role cannot create {role_label(role)} accounts.")
    errors = {}
    first, last = _norm(first_name), _norm(last_name)
    _name_errors(errors, "first_name", "First name", first)
    _name_errors(errors, "last_name", "Last name", last)
    address = _norm(email).lower()
    try:
        validate_email(address)
    except ValidationError:
        errors["email"] = "Enter a valid email address."
    else:
        if len(address) > USERNAME_LIMIT:
            errors["email"] = f"The email address is too long (maximum {USERNAME_LIMIT} characters)."
        elif User.objects.filter(Q(email__iexact=address) | Q(username__iexact=address)).exists():
            errors["email"] = EMAIL_TAKEN
    _check_department(me, role, department, errors)
    if errors:
        raise StaffValidationError(errors)
    try:
        with transaction.atomic():
            user = User(username=address, email=address, first_name=first, last_name=last,
                        company_id=me.company_id, role=role, department=department)
            user.set_unusable_password()
            user.save()
    except IntegrityError:                              # lost a race for the same address
        raise StaffValidationError({"email": EMAIL_TAKEN}) from None
    changes = {"role": [None, role], "first_name": [None, first], "last_name": [None, last]}
    if department is not None:
        changes["department"] = [None, department.name]
    acc.log_account_event(me.company_id, AccountEvent.Action.CREATED, target_user=user, actor=me, changes=changes)
    return user


@transaction.atomic
def update_staff(actor, target_pk, *, first_name=UNSET, last_name=UNSET, department=UNSET, role=UNSET):
    """Edit name, department and/or role. The email is never editable. Raises PermissionDenied,
    StaffValidationError, StaffStateError, or ValueError('Nothing to change.')."""
    me, target, active_mds = _lock(actor, target_pk)
    new_role = target.role if role is UNSET else role
    if new_role not in ALL_ROLES:
        raise StaffValidationError({"role": "Choose a valid role."})
    role_changed = new_role != target.role
    _check_manage(me, target, new_role=new_role if role_changed else None)
    if role_changed:
        if target.pk == me.pk:
            raise PermissionDenied("You cannot change your own role.")
        _guard_last_md(active_mds, target)

    errors, new = {}, {}
    if first_name is not UNSET:
        new["first_name"] = _norm(first_name)
        _name_errors(errors, "first_name", "First name", new["first_name"])
    if last_name is not UNSET:
        new["last_name"] = _norm(last_name)
        _name_errors(errors, "last_name", "Last name", new["last_name"])
    new_department = target.department if department is UNSET else department
    if role_changed or department is not UNSET:       # an unrelated edit never trips over an old record
        _check_department(me, new_role, new_department, errors)
    if errors:
        raise StaffValidationError(errors)

    profile, update_fields = {}, []
    for field, value in new.items():
        if getattr(target, field) != value:
            profile[field] = [getattr(target, field), value]
            setattr(target, field, value)
            update_fields.append(field)
    if department is not UNSET and new_department != target.department:
        profile["department"] = [target.department.name if target.department else None,
                                 new_department.name if new_department else None]
        target.department = new_department
        update_fields.append("department")
    if role_changed:
        before_role = target.role
        target.role = new_role
        update_fields.append("role")
    if not update_fields:
        raise ValueError("Nothing to change.")
    target.save(update_fields=update_fields)
    if profile:
        acc.log_account_event(me.company_id, AccountEvent.Action.PROFILE_UPDATED, target_user=target, actor=me,
                              changes=profile)
    if role_changed:
        acc.log_account_event(me.company_id, AccountEvent.Action.ROLE_CHANGED, target_user=target, actor=me,
                              changes={"role": [before_role, new_role]})
    return target


@dataclass
class Deactivation:
    user: User
    open_obligations: int


@transaction.atomic
def deactivate(actor, target_pk, reason=""):
    """Deactivate an account and kill its outstanding links. Obligations it owns are NOT touched: they stay
    assigned until someone reassigns them (the count is returned and written to the history)."""
    reason = (reason or "").strip()
    if len(reason) > REASON_LIMIT:
        raise StaffValidationError({"reason": f"The reason is too long (maximum {REASON_LIMIT} characters)."})
    me, target, active_mds = _lock(actor, target_pk)
    _check_manage(me, target)
    if target.pk == me.pk:
        raise PermissionDenied("You cannot deactivate your own account.")
    if not target.is_active:
        raise StaffStateError("This account is already deactivated.")
    _guard_last_md(active_mds, target)
    open_count = open_obligation_count(target)
    target.is_active = False
    target.link_serial = F("link_serial") + 1
    target.save(update_fields=["is_active", "link_serial"])
    target.refresh_from_db()
    note = (reason + "\n" if reason else "") + f"{open_count} open obligation(s) remain assigned to this person."
    acc.log_account_event(me.company_id, AccountEvent.Action.DEACTIVATED, target_user=target, actor=me,
                          changes={"is_active": [True, False]}, note=note)
    return Deactivation(target, open_count)


@transaction.atomic
def reactivate(actor, target_pk):
    """Reactivate an account. Links issued before the deactivation stay dead."""
    me, target, _ = _lock(actor, target_pk)
    _check_manage(me, target)
    if target.is_active:
        raise StaffStateError("This account is already active.")
    target.is_active = True
    target.link_serial = F("link_serial") + 1
    target.save(update_fields=["is_active", "link_serial"])
    target.refresh_from_db()
    acc.log_account_event(me.company_id, AccountEvent.Action.REACTIVATED, target_user=target, actor=me,
                          changes={"is_active": [False, True]})
    return target


def issue_link_for(actor, target_pk, purpose, reveal_if_email_fails=False):
    """Issue a setup or reset link for an account the actor may manage, then email it or hand it back once
    (see account_services.deliver_link). Raises PermissionDenied or acc.AccountLinkError (inactive account)."""
    if purpose not in (acc.SETUP, acc.RESET):
        raise ValueError("Unknown link purpose.")
    with transaction.atomic():
        me, target, _ = _lock(actor, target_pk)
        _check_manage(me, target)
        issued = acc.issue_link(target, purpose, actor=me)
    return acc.deliver_link(issued, actor=me, reveal_if_email_fails=reveal_if_email_fails)
