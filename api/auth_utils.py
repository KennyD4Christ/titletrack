from django.contrib.auth import authenticate

from tracker.models import User

USERNAME_LIMIT = User._meta.get_field("username").max_length


def _burn(password):
    """Spend about as long as a real password check, so a failure does not reveal which step it failed at."""
    User().set_password(password)


def authenticate_identifier(request, identifier, password):
    """Sign-in by username (exactly as before) or by email address (any letter case).

    1. The text is tried as a username first, so every existing account and every existing client keeps
       working.
    2. If a username matches the text exactly, that account is the ONLY candidate: a wrong password fails and
       is never retried as somebody's email address (so one piece of text can never reach two accounts).
    3. Otherwise, if the text contains '@', it is matched to ONE account by email, ignoring case, and that
       account's own username is used to check the password.
    Text longer than a username can be, an unknown email, and an email shared by several accounts all fail
    exactly like a wrong password. Returns the user or None."""
    identifier = (identifier or "").strip()
    if not identifier or len(identifier) > USERNAME_LIMIT:
        _burn(password)
        return None
    user = authenticate(request, username=identifier, password=password)
    if user is not None or "@" not in identifier:
        return user
    if User.objects.filter(username=identifier).exists():
        _burn(password)
        return None
    usernames = list(User.objects.filter(email__iexact=identifier).values_list("username", flat=True)[:2])
    if len(usernames) != 1:
        _burn(password)
        return None
    return authenticate(request, username=usernames[0], password=password)
