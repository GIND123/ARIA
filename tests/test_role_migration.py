"""Accounts created before the two role model still work afterwards.

Earlier versions had six roles. A study already underway has accounts in the
retired ones, and an upgrade that left them holding a role the permission table
no longer knows about would give them no permissions at all: signed in, and
unable to open anything.

The migration runs once, on the first launch after the upgrade, and is what
these tests check. FR 053 asks that existing annotations stay readable after a
schema update; the people who made them have to stay able to read them too.
"""

from __future__ import annotations

import pytest

from aria.core.models import Role, User
from aria.security.auth import Permission, has_permission, permissions_for
from aria.store.db import open_database
from aria.store.repository import Repository

#: Every role an earlier version could create, and what it becomes.
RETIRED_ACCOUNTS = (
    ("a_reviewer", "clinical_reviewer", Role.ANNOTATOR),
    ("a_manager", "data_manager", Role.ANNOTATOR),
    ("an_auditor", "auditor", Role.ANNOTATOR),
    ("a_lead", "lead_investigator", Role.ADMIN),
    ("an_annotator", "annotator", Role.ANNOTATOR),
    ("an_admin", "project_administrator", Role.ADMIN),
)


@pytest.fixture
def old_database(tmp_path):
    """A database as an earlier version left it, roles and all."""
    path = tmp_path / "before_upgrade.db"
    db = open_database(path)
    db.set_meta("db_schema_version", "1")
    repo = Repository(db)

    first = User(username="first", role="project_administrator")
    repo.create_user(first)
    repo.set_actor(first)
    for username, role, _expected in RETIRED_ACCOUNTS:
        repo.create_user(User(username=username, role=role))
    db.close()
    return path


def _users(path):
    db = open_database(path)
    try:
        return {u.username: u for u in Repository(db).list_users()}
    finally:
        db.close()


class TestTheUpgradeMovesEveryAccount:

    def test_each_retired_role_becomes_the_right_one(self, old_database):
        users = _users(old_database)
        for username, was, expected in RETIRED_ACCOUNTS:
            assert users[username].role == expected, (
                f"{username} was a {was} and is now a {users[username].role}"
            )

    def test_nobody_is_left_holding_an_unknown_role(self, old_database):
        for user in _users(old_database).values():
            assert user.role in Role.ALL, (
                f"{user.username} holds {user.role}, which has no permissions"
            )

    def test_everybody_can_still_annotate(self, old_database):
        """The point of the migration. An account that cannot open a case is
        an account that has lost its study."""
        for user in _users(old_database).values():
            assert has_permission(user, Permission.EDIT_ANNOTATIONS), (
                f"{user.username} can no longer annotate"
            )

    def test_an_unrecognised_role_is_not_left_stranded(self, tmp_path):
        """A role from a build that never shipped, or one written by hand.
        Anything unknown is made an annotator rather than left with nothing."""
        path = tmp_path / "odd.db"
        db = open_database(path)
        db.set_meta("db_schema_version", "1")
        repo = Repository(db)
        first = User(username="first", role="project_administrator")
        repo.create_user(first)
        repo.set_actor(first)
        repo.create_user(User(username="odd", role="something_else"))
        db.close()

        assert _users(path)["odd"].role == Role.ANNOTATOR

    def test_administrators_keep_their_administration(self, old_database):
        users = _users(old_database)
        for username in ("an_admin", "a_lead"):
            assert has_permission(users[username], Permission.MANAGE_USERS), (
                f"{username} can no longer manage the study"
            )

    def test_every_moved_profile_can_do_the_whole_job(self, old_database):
        """What the migration has to protect is now the other way round.

        While there were roles, the thing to check was that moving a reviewer
        onto the annotator role did not hand them the study settings. Nothing
        is withheld from anybody any more, so the risk is the opposite one: a
        profile carried over from an old database must not come back as
        something the rest of ARIA refuses to work with.
        """
        users = _users(old_database)
        for username in ("a_reviewer", "a_manager", "an_auditor", "a_lead"):
            user = users[username]
            assert user.active
            assert has_permission(user, Permission.EDIT_ANNOTATIONS)
            assert has_permission(user, Permission.MANAGE_USERS)


class TestTheUpgradeRunsOnceAndStays:

    def test_the_schema_version_moves_forward(self, old_database):
        """Checked against the constant rather than a number written here, so
        that adding the next migration does not break this test."""
        from aria.version import DB_SCHEMA_VERSION

        db = open_database(old_database)
        try:
            assert db.schema_version() == DB_SCHEMA_VERSION
        finally:
            db.close()

    def test_reopening_changes_nothing_further(self, old_database):
        first = {name: u.role for name, u in _users(old_database).items()}
        second = {name: u.role for name, u in _users(old_database).items()}
        assert first == second

    def test_a_fresh_database_needs_no_migration(self, tmp_path):
        from aria.version import DB_SCHEMA_VERSION

        db = open_database(tmp_path / "new.db")
        try:
            assert db.schema_version() == DB_SCHEMA_VERSION
        finally:
            db.close()


class TestNothingElseIsDisturbed:
    """A migration that rewrote more than roles would be a different problem."""

    def test_usernames_and_pseudonyms_survive(self, old_database):
        users = _users(old_database)
        assert len(users) == len(RETIRED_ACCOUNTS) + 1
        for username, _was, _expected in RETIRED_ACCOUNTS:
            assert users[username].username == username
            assert users[username].pseudonym, "An export pseudonym was lost"

    def test_accounts_stay_active(self, old_database):
        for user in _users(old_database).values():
            assert user.active

    def test_the_permission_table_covers_what_the_migration_produces(self):
        """Whatever a retired role becomes must be a role that has
        permissions, or the migration has simply moved the problem."""
        for produced in set(Role.RETIRED.values()):
            assert produced in Role.ALL
            assert permissions_for(produced), f"{produced} has no permissions"


class TestUpgradingDoesNotLockAStudyOut:
    """An index on a column a migration is about to add does not exist yet on
    an older file. Creating it while the database is being opened fails, and a
    study that cannot open its database cannot do anything at all.

    So tables are created first, migrations run next, and indexes are built
    last. These tests pin that order, because the failure only appears on a
    database made by an earlier build and never on a fresh one.
    """

    def _aged_database(self, tmp_path, drop):
        """A database at the current version with some columns taken away, the
        way a file written by an earlier build looks."""
        import json
        import sqlite3

        from aria.core.models import Annotation, Case, Project, SourceImage
        from aria.core.schema import ProjectSchema

        path = tmp_path / "aged.db"
        db = open_database(path)
        repo = Repository(db)
        user = User(username="first", role=Role.ADMIN)
        repo.create_user(user)
        repo.set_actor(user)
        project = Project(
            name="P", schema_json=json.dumps(ProjectSchema().to_dict())
        )
        repo.create_project(project)
        case = Case(
            project_id=project.id, pseudonym="C1",
            source=SourceImage(sha256="a" * 64, rows=10, columns=10),
        )
        repo.create_case(case)
        aset = repo.get_or_create_set(case.id, user.id)
        annotation = Annotation(
            set_id=aset.id, class_key="menton", side="NA", geometry_type="point",
        )
        annotation.set_points([(5.0, 6.0)])
        repo.save_annotations([annotation], expected_counter=0, case_id=case.id)
        db.set_meta("db_schema_version", "3")
        db.close()

        raw = sqlite3.connect(path)
        raw.execute("DROP INDEX IF EXISTS idx_cases_group")
        for table, column in drop:
            raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        raw.commit()
        raw.close()
        return path, case.id, aset.id

    def test_a_database_from_the_previous_build_still_opens(self, tmp_path):
        path, case_id, _set_id = self._aged_database(
            tmp_path,
            [
                ("cases", "patient_group"),
                ("cases", "reference_json"),
                ("annotations", "confidence"),
            ],
        )

        db = open_database(path)
        try:
            assert Repository(db).get_case(case_id) is not None
        finally:
            db.close()

    def test_the_work_in_it_survives(self, tmp_path):
        path, case_id, set_id = self._aged_database(
            tmp_path,
            [
                ("cases", "patient_group"),
                ("cases", "reference_json"),
                ("annotations", "confidence"),
            ],
        )

        db = open_database(path)
        try:
            repo = Repository(db)
            case = repo.get_case(case_id)
            assert case.pseudonym == "C1"
            assert case.patient_group == ""
            assert not case.reference.is_recorded
            annotations = repo.list_annotations(set_id)
            assert len(annotations) == 1
            assert annotations[0].confidence is None
        finally:
            db.close()

    def test_the_indexes_are_built_after_the_migration(self, tmp_path):
        """The index on the new column has to exist once the upgrade is done,
        or every later query falls back to a scan."""
        path, _case_id, _set_id = self._aged_database(
            tmp_path, [("cases", "patient_group")]
        )

        db = open_database(path)
        try:
            names = {
                row[0] for row in db.connect().execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert "idx_cases_group" in names
        finally:
            db.close()

    def test_a_fresh_database_gets_the_indexes_too(self, tmp_path):
        """Migrations are skipped on a new file, so the indexes cannot live
        only in a migration step."""
        db = open_database(tmp_path / "fresh.db")
        try:
            names = {
                row[0] for row in db.connect().execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            assert "idx_cases_group" in names
            assert "idx_ann_set" in names
        finally:
            db.close()
