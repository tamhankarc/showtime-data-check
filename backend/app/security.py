import base64
import hashlib
import hmac
import secrets
from datetime import timedelta, timezone
from fastapi import HTTPException, Request
from .models import AuthSession, LoginThrottle, utcnow

COOKIE = "sdc_session"


def password_hash(password, salt=None):
    if len(password) < 12:
        raise ValueError("Use a password of at least 12 characters.")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000)
    return f"pbkdf2_sha256$600000${salt}${base64.b64encode(digest).decode()}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) < 600000 or int(iterations) > 2000000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
        return hmac.compare_digest(base64.b64encode(actual).decode(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def credentials(settings, username):
    if hmac.compare_digest(username, settings.admin_username):
        return "admin", settings.admin_password_hash
    if hmac.compare_digest(username, settings.viewer_username):
        return "viewer", settings.viewer_password_hash
    return "", ""


def authenticate(db, settings, username, password, ip):
    key = hashlib.sha256((ip + "|" + username.casefold()).encode()).hexdigest()
    now = utcnow()
    throttle = db.get(LoginThrottle, key)
    if throttle and now - aware(throttle.window_start) < timedelta(minutes=15) and throttle.attempts >= 8:
        raise HTTPException(429, "Too many login attempts. Please try again in 15 minutes.")
    role, encoded = credentials(settings, username)
    # Unknown names incur the same password work without disclosing either login.
    encoded_check = encoded or settings.admin_password_hash
    valid = verify_password(password, encoded_check)
    if not valid or not role or not encoded:
        if not throttle:
            throttle = LoginThrottle(key=key, attempts=0, window_start=now)
            db.add(throttle)
        if now - aware(throttle.window_start) >= timedelta(minutes=15):
            throttle.attempts, throttle.window_start = 0, now
        throttle.attempts += 1
        db.commit()
        raise HTTPException(401, "Incorrect username or password.")
    if throttle:
        db.delete(throttle)
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), username=username, role=role,
                       credential_version=hashlib.sha256(encoded.encode()).hexdigest(),
                       expires_at=now + timedelta(hours=settings.session_hours)))
    db.commit()
    return token, {"username": username, "role": role}


def session_user(request: Request, db, settings):
    token = request.cookies.get(COOKIE, "")
    record = db.get(AuthSession, hashlib.sha256(token.encode()).hexdigest()) if token else None
    if not record or aware(record.expires_at) <= utcnow():
        raise HTTPException(401, "Sign in to continue.")
    role, encoded = credentials(settings, record.username)
    if role != record.role or not hmac.compare_digest(record.credential_version, hashlib.sha256(encoded.encode()).hexdigest()):
        raise HTTPException(401, "Please sign in again.")
    return {"username": record.username, "role": record.role}


def require_admin(user):
    if user["role"] != "admin":
        raise HTTPException(403, "Full-access login is required to change reference data.")
