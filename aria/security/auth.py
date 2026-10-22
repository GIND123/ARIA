"""What an action is for, now that nothing is withheld.

ARIA used to authenticate. A password was required to open a study, accounts
carried roles, and each role held a subset of the permissions below. On a
labelling tool that runs on one workstation against one local file, that bought
very little: anybody who can run the program can already open the database
beside it, so the password guarded a door in a building with no walls. What it
cost was not small. A new annotator could only be created through a wizard or
by an administrator, a forgotten password could shut a study out of its own
data, and the way back was a command line.

So authentication is gone, and with it passwords, sessions, lockouts, recovery
keys, roles and the guest mode that existed to let somebody look without one.
Who is annotating is recorded as a profile instead, which is a name rather than
a credential: see ``aria.core.profile``.

The :class:`Permission` vocabulary stays. Nothing consults it to refuse
anything, and ``has_permission`` is true for everyone. It is kept because the
call sites read as a description of what each action is for, because a reader
of the audit trail still benefits from the categories, and because a tool that
one day has a reason to withhold something has somewhere to put it. Deleting it
would mean deciding that such a reason can never exist, which is a bigger claim
than this change needs to make.
"""

from __future__ import annotations

from enum import Enum

from ..core.models import User


class Permission(str, Enum):
    """The kinds of thing ARIA does.

    A vocabulary, not a gate. Every profile holds all of them.
    """

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


#: Every permission, held by every profile.
ALL_PERMISSIONS = frozenset(Permission)


def permissions_for(role: str = "") -> set:
    """Everything. The argument is accepted and ignored."""
    return set(ALL_PERMISSIONS)


def has_permission(user: User | None = None, permission: Permission | None = None) -> bool:
    """True. There is nothing left to refuse."""
    return True


def require(user: User | None = None, permission: Permission | None = None) -> None:
    """Does nothing, and is kept so call sites read as before."""
    return None


def production_access_blocked(user=None, require_calibration: bool = False) -> str:
    """No reason to hold a profile back from production cases.

    The calibration set it used to gate on needed a reviewer to approve it, and
    there are no roles left to do the approving. A project that wants
    calibration before submission states that in its schema, and the validator
    reports it on the case.
    """
    return ""
