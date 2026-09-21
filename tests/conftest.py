"""Shared fixtures.

The fixtures build a real database and a real project rather than mocks, because
the behaviour worth testing here lives in the interaction between the store, the
schema and the measurement rules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "Test Artifacts"


@pytest.fixture
def paths(tmp_path):
    from aria.config import Paths

    p = Paths(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "cfg",
        log_dir=tmp_path / "logs",
        cache_dir=tmp_path / "cache",
    )
    p.ensure()
    return p


@pytest.fixture
def settings():
    from aria.config import Settings

    s = Settings()
    s.backup_on_launch = False
    return s


@pytest.fixture
def database(paths):
    from aria.store.db import open_database

    db = open_database(paths.database)
    yield db
    db.close()


@pytest.fixture
def repo(database):
    from aria.store.repository import Repository

    return Repository(database)


@pytest.fixture
def admin(repo):
    from aria.core.models import Role, User

    user = User(
        username="admin", display_name="Test Administrator",
        role=Role.ADMIN, pseudonym="ADM-001",
    )
    repo.create_user(user)
    repo.set_actor(user)
    return user


@pytest.fixture
def annotator(repo):
    from aria.core.models import Role, User

    user = User(
        username="ann", display_name="Test Annotator",
        role=Role.ANNOTATOR, pseudonym="ANN-001", calibration_passed=True,
    )
    repo.create_user(user)
    return user


@pytest.fixture
def schema():
    from aria.core.schema import ProjectSchema

    return ProjectSchema()


@pytest.fixture
def project(repo, admin, schema):
    from aria.core.models import Project

    p = Project(
        name="Test study", created_by=admin.id,
        schema_json=json.dumps(schema.to_dict()),
    )
    repo.create_project(p)
    return p


@pytest.fixture
def importer(repo, paths, settings):
    from aria.io.importer import Importer

    return Importer(repo, paths, settings)


@pytest.fixture
def sample_dicom():
    files = sorted(SAMPLES.glob("*.dcm"))
    if not files:
        pytest.skip("No sample DICOM files are present")
    return files[0]


@pytest.fixture
def sample_png():
    files = sorted(SAMPLES.glob("*.png"))
    if not files:
        pytest.skip("No sample PNG files are present")
    return files[0]


@pytest.fixture
def imported_case(importer, project, admin, sample_dicom):
    outcome = importer.import_file(str(sample_dicom), project.id, imported_by=admin.id)
    assert outcome.succeeded, outcome.error_message
    return outcome.case


@pytest.fixture
def calibrated_case(repo, imported_case):
    from aria.core.models import utc_now

    cal = imported_case.calibration
    cal.validate("ADM-001", utc_now())
    repo.set_calibration(imported_case.id, cal)
    repo.confirm_laterality(imported_case.id, "ADM-001", "Confirmed for the test")
    return repo.get_case(imported_case.id)


def build_annotation_set(repo, case, annotator, complete: bool = True):
    """Create a full, valid annotation set on a case.

    Geometry is placed at fixed fractions of the image so the expected
    measurements can be written down in the test rather than discovered from
    the output.
    """
    from aria.core.models import Annotation, CategoricalLabel
    from aria.core.schema import MCIGrade, Side

    aset = repo.get_or_create_set(case.id, annotator.id)
    width, height = case.source.columns, case.source.rows
    y_peri = height * 0.78
    y_endo = y_peri - 40.0

    objects = []

    def add(key, side, points, geometry):
        a = Annotation(
            set_id=aset.id, class_key=key, side=side.value,
            geometry_type=geometry, created_by=annotator.id,
        )
        a.set_points(points)
        objects.append(a)
        return a

    peri = [(x, y_peri) for x in range(int(width * 0.18), int(width * 0.84), 40)]
    endo = [(x, y_endo) for x in range(int(width * 0.18), int(width * 0.84), 40)]

    for side in (Side.RIGHT, Side.LEFT):
        add("periosteal_border", side, peri, "polyline")
        add("endosteal_border", side, endo, "polyline")

    for side, fx in ((Side.RIGHT, 0.32), (Side.LEFT, 0.68)):
        cx = width * fx
        add("mental_foramen_centre", side, [(cx, y_peri - 200)], "point")
        add("mental_foramen_superior", side, [(cx, y_peri - 220)], "point")
        add("mental_foramen_inferior", side, [(cx, y_peri - 180)], "point")
        # Cortical width is exactly 40 pixels on both sides.
        add("mcw_line", side, [(cx, y_peri), (cx, y_endo)], "line")
        # Superior height 220 px, inferior height 180 px.
        add("pmi_superior_line", side, [(cx, y_peri - 220), (cx, y_peri)], "line")
        add("pmi_inferior_line", side, [(cx, y_peri - 180), (cx, y_peri)], "line")
        add("mci_region", side, [(cx - 120, y_endo - 10), (cx - 20, y_peri + 10)], "box")
        add("antegonial_point", side, [(width * (0.21 if side is Side.RIGHT else 0.79), y_peri)], "point")
        ax = width * (0.21 if side is Side.RIGHT else 0.79)
        add("antegonial_index_line", side, [(ax, y_peri), (ax, y_peri - 35)], "line")
        add("gonion", side, [(width * (0.185 if side is Side.RIGHT else 0.815), y_peri)], "point")
        gx = width * (0.185 if side is Side.RIGHT else 0.815)
        add("gonial_index_line", side, [(gx, y_peri), (gx, y_peri - 28)], "line")

    add("menton", Side.MIDLINE, [(width * 0.5, y_peri + 30)], "point")

    repo.save_annotations(objects, expected_counter=0, case_id=case.id, project_id=case.project_id)

    if complete:
        for side, grade in ((Side.RIGHT, MCIGrade.C2), (Side.LEFT, MCIGrade.C1)):
            repo.set_grade(
                CategoricalLabel(
                    set_id=aset.id, key="mci_grade", side=side.value,
                    value=grade.value, created_by=annotator.id,
                ),
                case_id=case.id,
            )
    return repo.load_set_data(aset.id)
