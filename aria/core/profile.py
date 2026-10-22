"""Who is annotating, without asking anybody to prove it.

ARIA is a labelling tool that runs on one workstation against one local file.
There is no server, no network and nothing to attack across one, so a password
on the way in bought very little: anybody who can run the program can already
open the database beside it. What it cost was real. There was no way to make an
account except through a setup wizard, a forgotten password could lock a study
out of its own data, and the way back from that was a command line.

So there is no sign in. What remains is a profile: a name, chosen from a list
or typed once, that says who is annotating.

That part is not ceremony. Every annotation records who drew it, agreement
between observers is measured by comparing two named annotators on the same
case, and duplicate annotation is blinded by annotator. Drop the name and the
audit trail becomes "somebody", inter-observer agreement cannot be computed at
all, and the export loses the column that says whose work each row is. The name
is what the research needs; the password never was.

A profile is an assertion, not a credential. Anyone at the workstation can
select any profile or make a new one, and the history records the profile that
was active rather than a person who was authenticated. That distinction is
written into the audit view so nobody mistakes one for the other.
"""

from __future__ import annotations

import re

from .models import Role, User, utc_now

#: Kept on the user record so that databases written by earlier versions, which
#: had roles and passwords, still load. Every profile gets the role that can do
#: everything, because there is nothing left to withhold it from.
PROFILE_ROLE = Role.ADMIN

_PSEUDONYM_PATTERN = re.compile(r"[^A-Z0-9]+")


class ProfileError(ValueError):
    """A profile could not be created or changed, with the reason."""


def normalise_name(name: str) -> str:
    """Trim and collapse the spacing of a display name."""
    return " ".join((name or "").split())


def pseudonym_for(name: str, existing=()) -> str:
    """A short, stable label for exports, derived from the name.

    Exports identify an annotator by pseudonym rather than by name, so that a
    dataset can be shared without carrying the names of the people who made it.
    Derived from the name so it is recognisable to the team who produced it,
    and numbered when two names would otherwise collide.
    """
    letters = _PSEUDONYM_PATTERN.sub("", normalise_name(name).upper())
    stem = (letters[:3] or "ANN")
    taken = {p.upper() for p in existing if p}
    for n in range(1, 1000):
        candidate = f"{stem}-{n:03d}"
        if candidate not in taken:
            return candidate
    raise ProfileError("There are too many profiles with a similar name.")


class ProfileService:
    """Create, list and choose the profile that annotates.

    Deliberately thin. It holds no secret, grants nothing and refuses nothing
    except a name that cannot be told apart from another one.
    """

    def __init__(self, repository):
        self.repo = repository

    # -- reading -------------------------------------------------------------

    def list_profiles(self, include_inactive: bool = False) -> list:
        return self.repo.list_users(include_inactive=include_inactive)

    def any_profile_exists(self) -> bool:
        return self.repo.count_users() > 0

    def get(self, profile_id: str):
        for user in self.list_profiles(include_inactive=True):
            if user.id == profile_id:
                return user
        return None

    def by_name(self, name: str):
        wanted = normalise_name(name).casefold()
        for user in self.list_profiles(include_inactive=True):
            if normalise_name(user.display_name).casefold() == wanted:
                return user
            if (user.username or "").casefold() == wanted:
                return user
        return None

    # -- writing -------------------------------------------------------------

    def create(self, name: str, pseudonym: str = ""):
        """Make a profile. The only rule is that names stay distinguishable."""
        display = normalise_name(name)
        if not display:
            raise ProfileError("A profile needs a name.")
        if len(display) > 80:
            raise ProfileError("That name is too long for a profile.")
        if self.by_name(display) is not None:
            raise ProfileError(
                f"There is already a profile named {display}. Two profiles with "
                f"one name cannot be told apart in an export or in the history."
            )

        existing = [u.pseudonym for u in self.list_profiles(include_inactive=True)]
        user = User(
            username=display,
            display_name=display,
            role=PROFILE_ROLE,
            pseudonym=pseudonym.strip() or pseudonym_for(display, existing),
            active=True,
            created_at=utc_now(),
        )
        return self.repo.create_user(user)

    def rename(self, user, name: str):
        display = normalise_name(name)
        if not display:
            raise ProfileError("A profile needs a name.")
        clash = self.by_name(display)
        if clash is not None and clash.id != user.id:
            raise ProfileError(f"There is already a profile named {display}.")
        user.username = display
        user.display_name = display
        self.repo.update_user(user, detail=f"Profile renamed to {display}.")
        return user

    def deactivate(self, user):
        """Retire a profile without deleting it.

        The annotations it made stay attributed to it, because removing the
        name would orphan every row it produced and silently break any
        agreement figure computed from them.
        """
        user.active = False
        self.repo.update_user(user, detail=f"Profile {user.display_name} retired.")
        return user

    def reactivate(self, user):
        user.active = True
        self.repo.update_user(user, detail=f"Profile {user.display_name} restored.")
        return user

    def ensure_default(self, name: str = "Annotator"):
        """The profile a first run starts with when nobody chose one."""
        existing = self.list_profiles(include_inactive=True)
        if existing:
            return existing[0]
        return self.create(name)


def record_profile(repository, user) -> None:
    """Write to the history which profile is now annotating.

    The audit trail used to open with a sign in, which was a claim that
    somebody had proved who they were. This records the weaker and truer
    thing: that a name was selected at this workstation. Keeping it means the
    history still accounts for a change of hands partway through a session,
    which is what an agreement figure computed afterwards depends on.
    """
    from .audit import AuditEvent

    name = user.display_name or user.username
    repository.log(
        AuditEvent.PROFILE_SELECTED, "user", user.id,
        after={"name": name, "pseudonym": user.pseudonym},
        detail=(
            f"{name} is annotating, exported as {user.pseudonym}. A profile "
            f"is selected at this workstation, not authenticated."
        ),
    )


class ActiveProfile:
    """The profile this window is annotating as.

    Stands where the sign in session used to, so that the window, the
    controller and the status bar keep reading ``.user`` as before. It holds no
    secret and never expires: there is nothing to time out of, because there
    was nothing to prove on the way in.
    """

    def __init__(self, user):
        self.user = user
        self.started_at = utc_now()
        #: Nothing locks on idle any more. Kept so the window's timer and its
        #: event filter can go on asking without a special case.
        self.timeout_minutes = 0

    def touch(self) -> None:
        """Activity used to defer a lock. Nothing locks now."""

    def is_expired(self) -> bool:
        return False

    @property
    def display_name(self) -> str:
        return self.user.display_name if self.user else ""
