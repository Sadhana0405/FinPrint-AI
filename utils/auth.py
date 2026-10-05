from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(password):
    """Create a stored password hash. Never save the plain password."""
    return generate_password_hash(password)


def verify_password(password_hash, password):
    """Return True if the typed password matches the stored hash."""
    return check_password_hash(password_hash, password)


def is_logged_in():
    return "user_id" in session


def login_required(view_function):
    """Send visitors to /login if they are not signed in."""

    @wraps(view_function)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        return view_function(*args, **kwargs)

    return wrapped
