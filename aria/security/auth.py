"""Accounts, password handling, sessions and role permissions (NFR 001).

Passwords are stored as scrypt digests. scrypt is memory hard, so an attacker
who obtains the database cannot trade cheap parallel hardware for speed the way
they can against a plain iterated hash. The parameters are recorded alongside
each digest so they can be raised later without invalidating existing accounts.

Permissions are least privilege and are checked in one place. There are two
roles: an annotator does the work on a case from import through to export, and
an administrator does that and runs the study as well. A guest, where an
administrator allows one, is not an account at all and can only look.

A forgotten password has three ways back, in the order to try them:

1. the account's recovery key, which proves who is asking and needs nobody else,
2. an administrator, who sets a temporary password from Administration, Accounts,
3. the workstation reset on the command line, for when there is neither. It
   cannot ask for proof, because whoever can run programs as this computer's
   account can already open the database file. What it adds is a record of the
   reset, and that the reset is never silent: the old password stops working.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from ..core.audit import AuditEvent
from ..core.models import Role, User, utc_now

#: scrypt work factors. Raising N later is safe: existing digests carry the
#: parameters they were created with and are rehashed on the next login.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64


class Permission(str, Enum):
    # Data
    IMPORT_CASES = "import_cases"
    VIEW_CASES = "view_cases"
    VIEW_IMAGE = "view_image"
    EDIT_ANNOTATIONS = "edit_annotations"
    SUBMIT_ANNOTATIONS = "submit_annotations"
    DELETE_ANNOTATIONS = "delete_annotations"
    # Calibration
    VALIDATE_CALIBRATION = "validate_calibration"
    CONFIRM_LATERALITY = "confirm_laterality"
    # Review
    REVIEW_CASES = "review_cases"
    ADJUDICATE = "adjudicate"
    ASSIGN_CASES = "assign_cases"
    VIEW_AGREEMENT = "view_agreement"
    # Export
    EXPORT_DATA = "export_data"
    CREATE_BUNDLE = "create_bundle"
    # Administration
    MANAGE_USERS = "manage_users"
    MANAGE_PROJECTS = "manage_projects"
    MANAGE_SCHEMA = "manage_schema"
    MANAGE_THRESHOLDS = "manage_thresholds"
    MANAGE_POLICY = "manage_policy"
    APPROVE_CALIBRATION_SET = "approve_calibration_set"
    # Audit
    VIEW_AUDIT = "view_audit"
    VERIFY_AUDIT = "verify_audit"
    RUN_DIAGNOSTICS = "run_diagnostics"


#: Role to permission mapping. Any permission not listed is denied.
#: What administering a study means, as opposed to doing the work in it.
#: These are the permissions that separate the two roles, and the only ones an
#: annotator does not hold.
STUDY_ADMINISTRATION = {
    Permission.MANAGE_USERS,
    Permission.MANAGE_PROJECTS,
    Permission.MANAGE_SCHEMA,
    Permission.MANAGE_THRESHOLDS,
    Permission.MANAGE_POLICY,
    Permission.APPROVE_CALIBRATION_SET,
}

#: An annotator does the whole job on a case: brings the image in, marks it up,
#: measures it, reviews it and takes the result out again. Written as every
#: permission except the administration set, so that a capability added later
#: reaches the people doing the work without anybody having to remember to add
#: it here.
_ANNOTATOR = {p for p in Permission if p not in STUDY_ADMINISTRATION}

#: A guest looks at the case list and the images, and that is all. Written out
#: rather than derived, the opposite of the annotator, so that a capability
#: added later stays out of a guest's reach until somebody decides otherwise.
GUEST_PERMISSIONS = frozenset({Permission.VIEW_CASES, Permission.VIEW_IMAGE})

ROLE_PERMISSIONS: dict = {
    Role.ANNOTATOR: set(_ANNOTATOR),
    #: An administrator does everything an annotator does, and runs the study
    #: as well.
    Role.ADMIN: set(_ANNOTATOR) | STUDY_ADMINISTRATION,
    Role.GUEST: set(GUEST_PERMISSIONS),
}

#: The identifier every guest session acts under in the audit history. No
#: account row carries it.
GUEST_ID = "usr_guest"


def guest_user() -> User:
    """The identity a guest session runs as. Never stored as an account."""
    return User(
        id=GUEST_ID, username="guest", display_name="Guest",
        role=Role.GUEST, pseudonym="GUEST",
    )


def permissions_for(role: str) -> set:
    return ROLE_PERMISSIONS.get(role, set())


def has_permission(user: User | None, permission: Permission) -> bool:
    if user is None or not user.active:
        return False
    return permission in permissions_for(user.role)


def require(user: User | None, permission: Permission) -> None:
    if not has_permission(user, permission):
        role = Role.DISPLAY.get(user.role, "unknown") if user else "not signed in"
        raise PermissionError(
            f"This action needs the {permission.value.replace('_', ' ')} "
            f"permission, which the {role} role does not have."
        )


# ---------------------------------------------------------------------------
# Password handling
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: bytes | None = None) -> tuple:
    """Return ``(encoded_digest, encoded_salt)`` for storage."""
    if salt is None:
        salt = secrets.token_bytes(32)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=SCRYPT_DKLEN, maxmem=256 * 1024 * 1024,
    )
    encoded = f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${digest.hex()}"
    return encoded, salt.hex()


def verify_password(password: str, encoded: str, salt_hex: str) -> bool:
    """Constant time password check that also understands older parameters."""
    try:
        parts = encoded.split("$")
        if len(parts) != 5 or parts[0] != "scrypt":
            return False
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        expected = bytes.fromhex(parts[4])
        salt = bytes.fromhex(salt_hex)
    except (ValueError, IndexError):
        return False
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
            dklen=len(expected), maxmem=256 * 1024 * 1024,
        )
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str) -> bool:
    """True when a stored digest uses weaker parameters than the current ones."""
    try:
        parts = encoded.split("$")
        return not (
            parts[0] == "scrypt"
            and int(parts[1]) >= SCRYPT_N
            and int(parts[2]) >= SCRYPT_R
        )
    except (ValueError, IndexError):
        return True


def generate_temporary_password(length: int = 14) -> str:
    """A readable one time password for a new account."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


#: Capitals and digits, without the ones easily misread for each other (I, L,
#: O, 0 and 1), so a key written on paper and typed back in reads the same.
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_recovery_key(groups: int = 5, group_length: int = 4) -> str:
    """A recovery key such as ``K7QM-3XRP-9DWB-HN2T-F4ZC``.

    Twenty characters from 31 is about 99 bits, far past guessing at the rate a
    memory hard digest allows. That is why a wrong key does not count towards
    the lockout the way a wrong password does: the lockout would protect
    nothing, and would let anyone lock the owner out by typing nonsense.
    """
    return "-".join(
        "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(group_length))
        for _ in range(groups)
    )


def normalise_recovery_key(text: str) -> str:
    """The key as stored, whatever case, spaces or dashes it was typed with."""
    return "".join(c for c in text.upper() if c.isalnum())


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """One signed in session, with idle timeout tracking (NFR 005)."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    user: User | None = None
    started_at: str = field(default_factory=utc_now)
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timeout_minutes: int = 30
    locked: bool = False

    def touch(self) -> None:
        self.last_activity = datetime.now(timezone.utc)

    def idle_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.last_activity).total_seconds()

    def is_expired(self) -> bool:
        if self.timeout_minutes <= 0:
            return False
        return self.idle_seconds() > self.timeout_minutes * 60

    def seconds_until_timeout(self) -> float:
        if self.timeout_minutes <= 0:
            return float("inf")
        return max(0.0, self.timeout_minutes * 60 - self.idle_seconds())

    def can(self, permission: Permission) -> bool:
        return not self.locked and has_permission(self.user, permission)


class AuthenticationError(RuntimeError):
    """Raised for a failed sign in, with a message safe to show a user."""


class AccountLockedError(AuthenticationError):
    pass


class AuthService:
    """Sign in, account creation and policy enforcement."""

    def __init__(self, repository, settings):
        self.repo = repository
        self.settings = settings

    # -- accounts ------------------------------------------------------------

    def create_account(
        self, username: str, display_name: str, role: str, password: str,
        pseudonym: str = "", must_change: bool = True,
    ) -> User:
        username = username.strip()
        if not username:
            raise ValueError("A username is required.")
        if self.repo.get_user_by_username(username):
            raise ValueError(f"An account named {username} already exists.")
        if role not in Role.ALL:
            raise ValueError(f"{role} is not a known role.")
        problems = self.settings.validate_password(password)
        if problems:
            raise ValueError(" ".join(problems))

        digest, salt = hash_password(password)
        user = User(
            username=username,
            display_name=display_name or username,
            role=role,
            pseudonym=pseudonym or self._next_pseudonym(role),
            password_hash=digest,
            password_salt=salt,
            must_change_password=must_change,
        )
        return self.repo.create_user(user)

    def _next_pseudonym(self, role: str) -> str:
        prefix = {Role.ANNOTATOR: "ANN", Role.ADMIN: "ADM"}.get(role, "USR")
        existing = [u.pseudonym for u in self.repo.list_users(include_inactive=True)]
        n = 1
        while f"{prefix}-{n:03d}" in existing:
            n += 1
        return f"{prefix}-{n:03d}"

    def bootstrap_needed(self) -> bool:
        """True when no account exists, so the first run must create one."""
        return self.repo.count_users() == 0

    # -- sign in -------------------------------------------------------------

    def authenticate(self, username: str, password: str) -> User:
        user = self.repo.get_user_by_username(username.strip())

        if user is None:
            # Spend comparable time so a missing account cannot be told apart
            # from a wrong password by timing.
            hash_password(password, salt=b"\x00" * 32)
            self.repo.log(
                "login_failure", "user", "", detail=f"Sign in attempt for unknown account {username!r}."
            )
            raise AuthenticationError("The username or password is not correct.")

        if user.locked_until:
            try:
                until = datetime.fromisoformat(user.locked_until)
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < until:
                    remaining = int((until - datetime.now(timezone.utc)).total_seconds() / 60) + 1
                    raise AccountLockedError(
                        f"This account is locked for another {remaining} minutes "
                        f"after repeated failed sign in attempts."
                    )
            except ValueError:
                pass

        if not user.active:
            self.repo.log(
                "login_failure", "user", user.id, detail="Sign in attempt on a deactivated account."
            )
            raise AuthenticationError(
                "This account has been deactivated. Contact the project administrator."
            )

        if not verify_password(password, user.password_hash, user.password_salt):
            user.failed_logins += 1
            limit = max(1, int(self.settings.max_failed_logins))
            if user.failed_logins >= limit:
                user.locked_until = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=max(1, int(self.settings.lockout_minutes)))
                ).isoformat()
                user.failed_logins = 0
            self.repo.update_user(user, detail="Failed sign in attempt recorded.")
            self.repo.log(
                "login_failure", "user", user.id,
                detail=f"Failed sign in for {user.username}.",
            )
            raise AuthenticationError("The username or password is not correct.")

        if needs_rehash(user.password_hash):
            user.password_hash, user.password_salt = hash_password(password)

        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = utc_now()
        self.repo.update_user(user, detail="Sign in recorded.")
        self.repo.set_actor(user)
        self.repo.log("login_success", "user", user.id, detail=f"{user.username} signed in.")
        return user

    def change_password(self, user: User, old_password: str, new_password: str) -> None:
        if not verify_password(old_password, user.password_hash, user.password_salt):
            raise AuthenticationError("The current password is not correct.")
        problems = self.settings.validate_password(new_password)
        if problems:
            raise ValueError(" ".join(problems))
        if verify_password(new_password, user.password_hash, user.password_salt):
            raise ValueError("The new password must be different from the current one.")
        user.password_hash, user.password_salt = hash_password(new_password)
        user.must_change_password = False
        self.repo.update_user(user, detail="Password changed by the account holder.")
        self.repo.log("password_changed", "user", user.id, detail="Password changed.")

    def reset_password(self, admin: User, target: User) -> str:
        require(admin, Permission.MANAGE_USERS)
        temporary = self._set_temporary_password(target)
        self.repo.update_user(target, detail=f"Password reset by {admin.username}.")
        self.repo.log(
            AuditEvent.PASSWORD_RESET, "user", target.id,
            detail=(
                f"The password for {target.username} was reset by the "
                f"administrator {admin.username}. A new one must be chosen at "
                f"the next sign in."
            ),
        )
        return temporary

    def reset_password_at_workstation(self, username: str, operator: str) -> tuple:
        """Reset a password for whoever is at this workstation.

        For when the account has no recovery key and there is no other
        administrator to reset it. It asks for no proof because it cannot: the
        person running it already has the database file. Returns the account
        and a temporary password that must be changed at the next sign in.
        """
        user = self.repo.get_user_by_username(username.strip())
        if user is None:
            raise LookupError(f"There is no account named {username.strip()!r}.")
        temporary = self._set_temporary_password(user)
        self.repo.update_user(
            user, detail=f"Password reset at the workstation by {operator}."
        )
        self.repo.log(
            AuditEvent.PASSWORD_RESET, "user", user.id,
            detail=(
                f"The password for {user.username} was reset from the command "
                f"line on this workstation, by the computer account {operator}. "
                f"A new one must be chosen at the next sign in."
            ),
        )
        return user, temporary

    @staticmethod
    def _set_temporary_password(user: User) -> str:
        temporary = generate_temporary_password()
        user.password_hash, user.password_salt = hash_password(temporary)
        user.must_change_password = True
        user.failed_logins = 0
        user.locked_until = None
        return temporary

    # -- recovery keys -------------------------------------------------------

    def issue_recovery_key(self, user: User, key: str | None = None) -> str:
        """Give an account a new recovery key, and return it this once.

        Only a digest is kept, so the key cannot be shown again. A new key
        replaces the old one, which stops working immediately. ``key`` is for
        setup, which shows the key before the account exists to hold it.
        """
        if user.is_guest:
            raise ValueError("A guest has no account to recover.")
        key = key or generate_recovery_key()
        user.recovery_hash, user.recovery_salt = hash_password(normalise_recovery_key(key))
        user.recovery_created_at = utc_now()
        self.repo.update_user(user, detail="Recovery key issued.")
        self.repo.log(
            AuditEvent.RECOVERY_KEY_ISSUED, "user", user.id,
            detail=(
                f"A recovery key was issued for {user.username}. Any earlier "
                f"key for this account no longer works."
            ),
        )
        return key

    def reset_with_recovery_key(self, username: str, key: str, new_password: str) -> tuple:
        """Set a new password for whoever holds the account's recovery key.

        Returns the account and its next recovery key. The key that was used is
        spent, so a written down key that somebody else later finds is useless
        once its owner has used it.
        """
        problems = self.settings.validate_password(new_password)
        if problems:
            raise ValueError(" ".join(problems))

        refused = "The username or recovery key is not correct."
        typed = normalise_recovery_key(key)
        user = self.repo.get_user_by_username(username.strip())
        if user is None or not user.has_recovery_key:
            # The same time and the same words as a wrong key, so whether an
            # account exists, or has a key, is not learned by trying.
            hash_password(typed or "-", salt=b"\x00" * 32)
            self.repo.log(
                AuditEvent.LOGIN_FAILURE, "user", user.id if user else "",
                detail=(
                    f"Password reset refused for {username.strip()!r}: "
                    + ("no such account." if user is None else "the account has no recovery key.")
                ),
            )
            raise AuthenticationError(refused)

        if not user.active:
            self.repo.log(
                AuditEvent.LOGIN_FAILURE, "user", user.id,
                detail="Password reset attempt on a deactivated account.",
            )
            raise AuthenticationError(
                "This account has been deactivated. Contact the project administrator."
            )

        if not verify_password(typed, user.recovery_hash, user.recovery_salt):
            self.repo.log(
                AuditEvent.LOGIN_FAILURE, "user", user.id,
                detail=(
                    f"Password reset refused for {user.username}: the recovery "
                    f"key did not match."
                ),
            )
            raise AuthenticationError(refused)

        user.password_hash, user.password_salt = hash_password(new_password)
        user.must_change_password = False
        # A correct key outranks a lockout: it is stronger proof than the
        # password the lockout exists to protect.
        user.failed_logins = 0
        user.locked_until = None
        next_key = generate_recovery_key()
        user.recovery_hash, user.recovery_salt = hash_password(normalise_recovery_key(next_key))
        user.recovery_created_at = utc_now()

        # Recorded against the account itself, since holding its key is what
        # proved who was asking. Nobody is signed in yet, so the actor is
        # cleared again afterwards and a failed sign in that follows is not
        # put down to this account.
        self.repo.set_actor(user)
        try:
            self.repo.update_user(
                user, detail="Password reset with the account's recovery key."
            )
            self.repo.log(
                AuditEvent.PASSWORD_RESET, "user", user.id,
                detail=(
                    f"The password for {user.username} was reset with its "
                    f"recovery key. That key is spent and a new one was issued."
                ),
            )
        finally:
            self.repo.set_actor(None)
        return user, next_key

    # -- guests --------------------------------------------------------------

    def sign_in_as_guest(self) -> User:
        """Start a guest session, where the policy allows one."""
        if not self.settings.allow_guest_access:
            raise AuthenticationError(
                "Guest access is turned off on this workstation. An "
                "administrator can turn it on in Administration, Accounts."
            )
        guest = guest_user()
        self.repo.set_actor(guest)
        self.repo.log(
            AuditEvent.GUEST_SESSION_STARTED, "user", guest.id,
            detail=(
                "A guest session started. A guest can look at cases and images "
                "and cannot change anything."
            ),
        )
        return guest

    def start_session(self, user: User) -> Session:
        return Session(
            user=user,
            timeout_minutes=int(self.settings.session_timeout_minutes),
        )

    def end_session(self, session: Session, reason: str = "signed out") -> None:
        if session.user:
            self.repo.log(
                "session_timeout" if "timeout" in reason else "logout",
                "user", session.user.id, detail=f"Session ended: {reason}.",
            )
        session.user = None
        session.locked = True


def production_access_blocked(user: User, require_calibration: bool) -> str:
    """Reason an annotator cannot take production cases yet (FR 046).

    Returns an empty string when access is allowed.
    """
    if not require_calibration:
        return ""
    if user.role != Role.ANNOTATOR:
        # Only the annotator role is gated. A reviewer approves a calibration
        # set, so gating the roles that do the approving, or a lead
        # investigator who is the only account on the study, would be a lock
        # with nobody on the other side of it to open it.
        return ""
    if user.calibration_passed:
        return ""
    return (
        "This account has not completed the annotator calibration set. A "
        "reviewer approves the calibration result before production cases are "
        "assigned."
    )
