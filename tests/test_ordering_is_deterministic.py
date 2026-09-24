"""Listing order has to be decided by the stored rows, not by the engine.

Timestamps are recorded to the millisecond. A construction that produces
several annotations at once, or a script that creates two projects in
succession, gives them all the same timestamp, and a sort on the timestamp
alone then leaves the tie to whatever the query planner happens to do. The
order that comes back is the order annotations reach annotations.json, the rows
of annotations.csv and the identifiers COCO assigns, so an undecided tie means
the same stored set can export differently.

Each test here builds the same rows twice, with the same identifiers and the
same timestamps, inserted in opposite orders. Anything that still comes back in
two different orders is being ordered by insertion rather than by its content.
"""

from __future__ import annotations

import json

import pytest

from aria.core.models import (
    Annotation,
    Case,
    Project,
    Role,
    SourceImage,
    User,
    new_id,
)
from aria.core.schema import ProjectSchema
from aria.store.db import open_database
from aria.store.repository import Repository

#: One instant, shared by every row, which is the tie the tests are about.
STAMP = "2026-09-24T10:00:00.000+00:00"


def _repository(tmp_path, name):
    db = open_database(tmp_path / f"{name}.db")
    repo = Repository(db)
    user = User(id="USR-FIXED", username="u", role=Role.ADMIN)
    repo.create_user(user)
    repo.set_actor(user)
    return db, repo, user


def _annotations(set_id, specs):
    out = []
    for ident, x, y in specs:
        a = Annotation(
            id=ident, set_id=set_id, class_key="user_landmark",
            side="R", geometry_type="point",
        )
        a.set_points([(x, y)])
        a.created_at = STAMP
        a.updated_at = STAMP
        out.append(a)
    return out


class TestAnnotationOrderIsDecidedByTheRows:
    """The order of annotations.json must not depend on drawing order."""

    @pytest.fixture
    def specs(self):
        return [(f"ANN-{i:03d}", float(i), float(i)) for i in range(8)]

    def _listing(self, tmp_path, name, specs):
        db, repo, user = _repository(tmp_path, name)
        project = Project(
            id="PRJ-FIXED", name="P",
            schema_json=json.dumps(ProjectSchema().to_dict()),
        )
        repo.create_project(project)
        case = Case(
            id="CAS-FIXED", project_id=project.id, pseudonym="C1",
            source=SourceImage(sha256="a" * 64, rows=100, columns=100),
        )
        repo.create_case(case)
        aset = repo.get_or_create_set(case.id, user.id)
        repo.save_annotations(
            _annotations(aset.id, specs), expected_counter=0, case_id=case.id
        )
        listed = repo.list_annotations(aset.id)
        db.close()
        return [a.id for a in listed]

    def test_two_drawing_orders_give_one_listing(self, tmp_path, specs):
        """The defect: the same eight landmarks came back in two orders."""
        forwards = self._listing(tmp_path, "forwards", specs)
        backwards = self._listing(tmp_path, "backwards", list(reversed(specs)))

        assert forwards == backwards, (
            "The same eight annotations, recorded in a different order, list "
            "differently. Exports of identical work would not match."
        )

    def test_the_listing_is_stable_when_the_table_is_rewritten(self, tmp_path, specs):
        """A VACUUM renumbers rows in a table keyed on text, which is the kind
        of maintenance that reorders an undecided sort."""
        db, repo, user = _repository(tmp_path, "vacuumed")
        project = Project(name="P", schema_json=json.dumps(ProjectSchema().to_dict()))
        repo.create_project(project)
        case = Case(
            project_id=project.id, pseudonym="C1",
            source=SourceImage(sha256="b" * 64, rows=100, columns=100),
        )
        repo.create_case(case)
        aset = repo.get_or_create_set(case.id, user.id)
        repo.save_annotations(
            _annotations(aset.id, specs), expected_counter=0, case_id=case.id
        )

        before = [a.id for a in repo.list_annotations(aset.id)]
        db.connect().execute("VACUUM")
        after = [a.id for a in repo.list_annotations(aset.id)]
        db.close()

        assert before == after, "A VACUUM changed the order annotations list in"

    def test_a_deleted_annotation_does_not_disturb_the_rest(self, tmp_path, specs):
        """Deletion is a flag, not a removal, so the survivors keep their
        places rather than shuffling around the gap."""
        db, repo, user = _repository(tmp_path, "deleted")
        project = Project(name="P", schema_json=json.dumps(ProjectSchema().to_dict()))
        repo.create_project(project)
        case = Case(
            project_id=project.id, pseudonym="C1",
            source=SourceImage(sha256="c" * 64, rows=100, columns=100),
        )
        repo.create_case(case)
        aset = repo.get_or_create_set(case.id, user.id)
        objects = _annotations(aset.id, specs)
        repo.save_annotations(objects, expected_counter=0, case_id=case.id)

        before = [a.id for a in repo.list_annotations(aset.id)]
        repo.delete_annotation(objects[3].id, case_id=case.id)
        after = [a.id for a in repo.list_annotations(aset.id)]
        db.close()

        assert after == [i for i in before if i != objects[3].id]


class TestProjectOrderIsDecidedByTheRows:
    """Two projects created in the same millisecond need a settled order."""

    def _listing(self, tmp_path, name, order):
        db, repo, _ = _repository(tmp_path, name)
        for ident in order:
            project = Project(
                id=ident, name=ident,
                schema_json=json.dumps(ProjectSchema().to_dict()),
            )
            project.created_at = STAMP
            repo.create_project(project)
        listed = [p.id for p in repo.list_projects()]
        db.close()
        return listed

    def test_creation_order_does_not_decide_the_listing(self, tmp_path):
        ids = ["PRJ-A", "PRJ-B", "PRJ-C", "PRJ-D"]
        forwards = self._listing(tmp_path, "pf", ids)
        backwards = self._listing(tmp_path, "pb", list(reversed(ids)))

        assert forwards == backwards, (
            "Projects created in one millisecond list in creation order, so "
            "which project opens first is left to chance."
        )


class TestCaseOrderIsDecidedByTheRows:
    """Cases imported in one batch share an import timestamp.

    Within a project the pseudonym settles the tie, because a project cannot
    hold the same pseudonym twice. Across projects it does not: the pseudonyms
    are numbered from one in each project, so a listing that spans projects has
    a genuine tie on both the timestamp and the pseudonym. That listing is what
    the review and export screens read.
    """

    def _listing(self, tmp_path, name, order):
        db, repo, _ = _repository(tmp_path, name)
        for suffix in ("ONE", "TWO"):
            repo.create_project(
                Project(
                    id=f"PRJ-{suffix}", name=suffix,
                    schema_json=json.dumps(ProjectSchema().to_dict()),
                )
            )
        for project_id, ident in order:
            # The same pseudonym in both projects, which is ordinary: each
            # project numbers its own cases.
            case = Case(
                id=ident, project_id=project_id, pseudonym="CASE-001",
                source=SourceImage(sha256=ident.ljust(64, "0"), rows=10, columns=10),
            )
            case.imported_at = STAMP
            repo.create_case(case)
        listed = [c.id for c in repo.list_cases()]
        db.close()
        return listed

    def test_import_order_does_not_decide_the_listing(self, tmp_path):
        rows = [("PRJ-ONE", "CAS-A"), ("PRJ-TWO", "CAS-B")]
        forwards = self._listing(tmp_path, "cf", rows)
        backwards = self._listing(tmp_path, "cb", list(reversed(rows)))

        assert forwards == backwards, (
            "Cases listed across projects fall back on import order once the "
            "timestamp and the pseudonym both tie."
        )


class TestExportsAreReproducible:
    """The point of settling the order: identical work exports identically.

    This is the property the ordering fix exists to protect. The annotation
    array of annotations.json, the rows of annotations.csv and the identifiers
    COCO assigns all follow the listing order, so a tie left to the engine
    makes two exports of the same work differ for no reason a reader could
    explain.
    """

    def _build(self, tmp_path, name, specs):
        db, repo, user = _repository(tmp_path, name)
        project = Project(
            id="PRJ-FIXED", name="Reproducibility",
            schema_json=json.dumps(ProjectSchema().to_dict()),
        )
        repo.create_project(project)
        case = Case(
            id="CAS-FIXED", project_id=project.id, pseudonym="CASE-001",
            source=SourceImage(sha256="d" * 64, rows=1200, columns=2400),
        )
        repo.create_case(case)
        aset = repo.get_or_create_set(case.id, user.id)
        repo.save_annotations(
            _annotations(aset.id, specs), expected_counter=0, case_id=case.id
        )
        return db, repo, project, user, aset

    @staticmethod
    def _serialise(repo, aset, project, user, portable: bool = False):
        from aria.io.exporters.json_export import geometry_document

        document = geometry_document(repo.load_set_data(aset.id), project, user)
        if portable:
            # Two separate databases cannot be expected to agree on the set
            # identifier, which is per database, or on the moment each row was
            # written. Everything else, the order included, is the annotation.
            for entry in document["annotations"]:
                entry.pop("set_id", None)
                entry.pop("updated_at", None)
            return json.dumps(document["annotations"], indent=2, sort_keys=True)
        return json.dumps(document, indent=2, sort_keys=True)

    def test_exporting_the_same_set_twice_gives_one_answer(self, tmp_path):
        """One database, read twice, with no writes in between. Nothing here
        is allowed to differ, so the whole document is compared."""
        specs = [(f"ANN-{i:03d}", float(i), float(i)) for i in range(10)]
        db, repo, project, user, aset = self._build(tmp_path, "twice", specs)
        try:
            first = self._serialise(repo, aset, project, user)
            second = self._serialise(repo, aset, project, user)
        finally:
            db.close()

        assert first == second, "Two reads of one annotation set exported differently"

    def test_the_same_work_recorded_in_two_orders_exports_the_same(self, tmp_path):
        specs = [(f"ANN-{i:03d}", float(i) * 3.5, float(i) * 1.25) for i in range(10)]

        documents = []
        for name, order in (("export_a", specs), ("export_b", list(reversed(specs)))):
            db, repo, project, user, aset = self._build(tmp_path, name, order)
            try:
                documents.append(self._serialise(repo, aset, project, user, portable=True))
            finally:
                db.close()

        assert documents[0] == documents[1], (
            "Two exports of the same ten annotations do not match. A reader "
            "comparing them would see a difference that is not in the data."
        )
