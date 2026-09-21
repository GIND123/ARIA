"""In application self tests.

These run inside the installed application, against the real code paths, and
report in plain language. They exist because a packaged build can fail in ways a
development machine never sees: a missing codec, a read only folder, a database
that cannot use write ahead logging, a stale working copy.

Each test states what it checks and why it matters, so a failure tells someone
what is actually broken rather than only that something is.

They are also the fast smoke suite: ``pytest -m smoke`` runs the same functions.
"""

from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class TestResult:
    key: str
    name: str
    group: str
    passed: bool
    detail: str = ""
    matters: str = ""
    duration_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "group": self.group,
            "passed": self.passed, "detail": self.detail, "matters": self.matters,
            "duration_ms": round(self.duration_ms, 2), "error": self.error,
        }


@dataclass
class SelfTestReport:
    results: list = field(default_factory=list)
    generated_at: str = ""

    @property
    def failures(self) -> list:
        return [r for r in self.results if not r.passed]

    @property
    def passed(self) -> bool:
        return not self.failures

    def by_group(self) -> dict:
        grouped: dict = {}
        for r in self.results:
            grouped.setdefault(r.group, []).append(r)
        return grouped

    def summary(self) -> str:
        total = len(self.results)
        failed = len(self.failures)
        if failed == 0:
            return f"All {total} self tests passed."
        return (
            f"{failed} of {total} self tests failed. "
            f"The application may not behave correctly."
        )

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "passed": self.passed,
            "summary": self.summary(),
            "n_tests": len(self.results),
            "n_failures": len(self.failures),
            "results": [r.to_dict() for r in self.results],
        }

    def to_text(self) -> str:
        lines = [f"ARIA self test report", f"Generated {self.generated_at}", "", self.summary(), ""]
        for group, results in self.by_group().items():
            lines.append(group)
            lines.append("-" * len(group))
            for r in results:
                mark = "pass" if r.passed else "FAIL"
                lines.append(f"  [{mark}] {r.name}   {r.duration_ms:.0f} ms")
                if r.detail:
                    lines.append(f"      {r.detail}")
                if not r.passed:
                    lines.append(f"      Why it matters: {r.matters}")
                    if r.error:
                        lines.append(f"      Error: {r.error}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_geometry() -> tuple:
    """Distances, perpendiculars and rasterisation."""
    from ..core import geometry as geo

    assert abs(geo.euclidean((0, 0), (3, 4)) - 5.0) < 1e-9, "Euclidean distance is wrong"

    peri = [(x, 100.0) for x in range(0, 300, 10)]
    tangent = geo.tangent_at(peri, (150, 100), 40)
    assert abs(abs(tangent[0]) - 1.0) < 1e-6, "Tangent of a horizontal line is not horizontal"

    endo = [(x, 96.0) for x in range(0, 300, 10)]
    result = geo.construct_cortical_width((150.0, 80.0), peri, endo)
    assert abs(result.length_px() - 4.0) < 1e-6, (
        f"Cortical width construction gave {result.length_px():.4f} instead of 4"
    )

    polygon = [(2, 2), (8, 2), (8, 6), (2, 6)]
    mask = geo.rasterize_polygon(polygon, (10, 10))
    assert int(mask.sum()) == 24, f"Rasterised area was {int(mask.sum())} instead of 24"

    resampled = geo.resample_polyline([(0, 0), (10, 0)], 2.0)
    spacings = [
        geo.euclidean(resampled[i], resampled[i + 1]) for i in range(len(resampled) - 1)
    ]
    assert all(abs(s - 2.0) < 1e-6 for s in spacings), "Resampling produced uneven spacing"

    return True, "Distance, tangent, construction and rasterisation all correct."


def check_measurements() -> tuple:
    """Derived measurement arithmetic, including the assessable sides rule."""
    from ..core.measurements import MeasurementEngine
    from ..core.models import Annotation, AnnotationSet, Case, CaseData, CategoricalLabel
    from ..core.schema import MCIGrade, Side
    from ..core.units import Calibration, CalibrationSource, ValidationStatus

    cal = Calibration(
        source=CalibrationSource.DICOM_PIXEL_SPACING,
        row_spacing_mm=0.1, col_spacing_mm=0.1,
        status=ValidationStatus.VALIDATED,
    )
    case = Case(calibration=cal)
    aset = AnnotationSet()

    def line(key, side, a, b):
        ann = Annotation(set_id=aset.id, class_key=key, side=side.value, geometry_type="line")
        ann.set_points([a, b])
        return ann

    annotations = [
        line("mcw_line", Side.RIGHT, (100, 200), (100, 240)),          # 40 px, 4.0 mm
        line("pmi_superior_line", Side.RIGHT, (100, 140), (100, 240)),  # 100 px, 10.0 mm
        line("mcw_line", Side.LEFT, (300, 200), (300, 260)),            # 60 px, 6.0 mm
    ]
    data = CaseData(case=case, annotation_set=aset, annotations=annotations)
    results = {(m.kind, m.side): m for m in MeasurementEngine().compute(data)}

    mcw = results[("mandibular_cortical_width", "R")]
    assert abs(mcw.value_px - 40.0) < 1e-9, "Cortical width pixel distance is wrong"
    assert abs(mcw.value_mm - 4.0) < 1e-9, f"Cortical width gave {mcw.value_mm} mm instead of 4"

    pmi = results[("pmi_superior", "R")]
    assert abs(pmi.value_ratio - 0.4) < 1e-9, (
        f"Panoramic mandibular index gave {pmi.value_ratio} instead of 0.4"
    )

    mean = results[("mandibular_cortical_width", "NA")]
    assert abs(mean.value_mm - 5.0) < 1e-9, "Bilateral mean is wrong"
    assert set(mean.sides_used) == {"R", "L"}, "Bilateral mean did not record its sides"

    ai_mean = results[("antegonial_index", "NA")]
    assert not ai_mean.assessable, (
        "A mean was produced although neither side was annotated"
    )

    return True, "Cortical width, panoramic index and the assessable sides rule are correct."


def check_units() -> tuple:
    """Millimetres are withheld until a calibration is validated."""
    from ..core.units import Calibration, CalibrationSource, ValidationStatus

    unvalidated = Calibration(
        source=CalibrationSource.DICOM_PIXEL_SPACING,
        row_spacing_mm=0.076, col_spacing_mm=0.076,
        status=ValidationStatus.UNVALIDATED,
    )
    assert not unvalidated.millimetres_available, (
        "Millimetre values were offered from an unvalidated calibration"
    )
    assert unvalidated.length_mm((0, 0), (100, 0)) is None, (
        "An unvalidated calibration produced a millimetre distance"
    )

    unvalidated.status = ValidationStatus.VALIDATED
    assert abs(unvalidated.length_mm((0, 0), (100, 0)) - 7.6) < 1e-9, (
        "Validated calibration converts distance incorrectly"
    )

    anisotropic = Calibration(
        source=CalibrationSource.DICOM_PIXEL_SPACING,
        row_spacing_mm=0.2, col_spacing_mm=0.1,
        status=ValidationStatus.VALIDATED,
    )
    horizontal = anisotropic.distance_mm(100, 0)
    vertical = anisotropic.distance_mm(0, 100)
    assert abs(horizontal - 10.0) < 1e-9 and abs(vertical - 20.0) < 1e-9, (
        "Unequal row and column spacing is not applied per axis"
    )

    return True, "Millimetre gating and per axis conversion behave correctly."


def check_texture() -> tuple:
    """Fractal dimension against shapes with a known dimension."""
    from ..core.texture import box_count_dimension

    filled = np.ones((64, 64), dtype=bool)
    d_filled, _r2, _detail = box_count_dimension(filled)
    assert abs(d_filled - 2.0) < 0.1, (
        f"A filled square measured {d_filled:.3f} instead of about 2"
    )

    line = np.zeros((64, 64), dtype=bool)
    line[32, :] = True
    d_line, _r2, _detail = box_count_dimension(line)
    assert abs(d_line - 1.0) < 0.1, (
        f"A straight line measured {d_line:.3f} instead of about 1"
    )

    sierpinski = np.zeros((64, 64), dtype=bool)
    for y in range(64):
        for x in range(64):
            if (x & (63 - y)) == 0:
                sierpinski[y, x] = True
    d_sierpinski, _r2, _detail = box_count_dimension(sierpinski)
    assert 1.4 < d_sierpinski < 1.8, (
        f"A Sierpinski triangle measured {d_sierpinski:.3f}, outside the expected range"
    )

    return True, (
        f"Filled square {d_filled:.3f}, line {d_line:.3f}, "
        f"Sierpinski {d_sierpinski:.3f}."
    )


def check_agreement() -> tuple:
    """Agreement statistics against values that can be checked by hand."""
    from ..core.agreement import cohen_kappa, dice, icc, iou

    perfect = np.array([[1.0, 1.0], [2, 2], [3, 3], [4, 4], [5, 5]])
    assert abs(icc(perfect, "2,1")["icc"] - 1.0) < 1e-6, (
        "Perfect agreement did not give an intraclass correlation of one"
    )

    offset = np.array([[1.0, 2.0], [2, 3], [3, 4], [4, 5], [5, 6]])
    consistency = icc(offset, "3,1")["icc"]
    absolute = icc(offset, "2,1")["icc"]
    assert abs(consistency - 1.0) < 1e-6, "Consistency form did not ignore a constant offset"
    assert absolute < consistency, "Absolute agreement did not penalise a constant offset"

    a = np.zeros((10, 10), dtype=bool)
    a[2:8, 2:8] = True
    b = np.zeros((10, 10), dtype=bool)
    b[3:9, 3:9] = True
    expected_dice = 2 * 25 / 72
    assert abs(dice(a, b) - expected_dice) < 1e-9, "Dice coefficient is wrong"
    assert abs(iou(a, b) - 25 / 47) < 1e-9, "Intersection over union is wrong"

    labels = ["C1", "C2", "C3", "C1", "C2"]
    assert abs(cohen_kappa(labels, labels, ["C1", "C2", "C3"])["kappa"] - 1.0) < 1e-9, (
        "Identical labels did not give a kappa of one"
    )

    return True, "Intraclass correlation, Dice, intersection over union and kappa are correct."


def check_mask_encoding() -> tuple:
    """Brush masks survive encoding and decoding exactly."""
    from ..ui.viewer.canvas import decode_mask, encode_mask

    rng = np.random.default_rng(11)
    for _trial in range(3):
        mask = np.zeros((60, 80), dtype=bool)
        ys, xs = np.mgrid[0:60, 0:80]
        cy, cx = int(rng.integers(15, 45)), int(rng.integers(20, 60))
        mask |= ((xs - cx) ** 2 + (ys - cy) ** 2) < 180
        rle, bbox = encode_mask(mask)
        decoded, _ = decode_mask(rle, bbox)
        restored = np.zeros_like(mask)
        restored[bbox[1] : bbox[1] + bbox[3], bbox[0] : bbox[0] + bbox[2]] = decoded
        assert np.array_equal(restored, mask), "A brush mask changed when encoded and decoded"

    return True, "Brush masks encode and decode without losing a pixel."


def check_mask_export() -> tuple:
    """Indexed mask files read back with identical class indices (AC 006)."""
    from ..io.exporters.mask_export import (
        build_class_index, verify_round_trip, write_indexed_png,
    )

    class_index = build_class_index()
    label = np.zeros((40, 60), dtype=np.uint8)
    values = sorted(class_index.values())[:4]
    for i, value in enumerate(values):
        label[i * 8 : i * 8 + 6, 5 : 55] = value

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mask.png"
        write_indexed_png(path, label, class_index)
        result = verify_round_trip(path, label)
        assert result["ok"], f"Mask round trip failed: {result.get('reason')}"

    return True, "An indexed mask reads back with identical class indices and shape."


def check_import_guards() -> tuple:
    """Unsupported and hostile files are refused with an explanation."""
    import struct
    import zlib

    from ..io.guards import inspect_file

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)

        jpeg = folder / "photo.jpg"
        jpeg.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 4000)
        result = inspect_file(jpeg)
        assert not result.accepted, "A JPEG was accepted"
        assert result.first_problem().remedy, "A rejection gave no next step"

        mislabelled = folder / "scan.png"
        mislabelled.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 4000)
        assert not inspect_file(mislabelled).accepted, (
            "A JPEG named as a PNG was accepted"
        )

        empty = folder / "empty.png"
        empty.write_bytes(b"")
        assert not inspect_file(empty).accepted, "An empty file was accepted"

        header = struct.pack(">IIBBBBB", 100000, 100000, 8, 0, 0, 0, 0)
        chunk = (
            struct.pack(">I", 13) + b"IHDR" + header
            + struct.pack(">I", zlib.crc32(b"IHDR" + header))
        )
        bomb = folder / "huge.png"
        bomb.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk + b"\x00" * 200)
        result = inspect_file(bomb)
        assert not result.accepted, "A file declaring 100000 by 100000 pixels was accepted"

    return True, "Unsupported formats, mislabelled files and oversized headers are refused."


def check_privacy_scan() -> tuple:
    """The export scanner finds identifiers and leaves structural values alone."""
    from ..io.deident import scan_payload

    findings = scan_payload(
        {
            "patient_name": "SMITH^JOHN",
            "contact": "someone@example.org",
            "institutionName": "Example Hospital",
        }
    )
    kinds = {f.kind for f in findings}
    assert "prohibited_key" in kinds, "A patient name was not detected"
    assert "email" in kinds, "An email address was not detected"

    clean = scan_payload(
        {
            "dicom_uids": {"study_instance_uid": "1.2.840.10008.5.1.4.1.1.1"},
            "transfer_syntax_uid": "1.2.840.10008.1.2.1",
            "source_checksum_sha256": "a" * 64,
            "case_pseudonym": "ARIA-AB12CD34",
        }
    )
    assert not clean, (
        f"The scanner reported {len(clean)} false findings on identifiers and digests"
    )

    return True, "Identifiers are detected and structural values are not false flagged."


def check_deidentification() -> tuple:
    """Identifier remapping is stable and not reversible without the salt."""
    from ..io.deident import pseudonym_for, remap_uid

    a = remap_uid("1.2.840.113619.2.55.3", "salt-one")
    b = remap_uid("1.2.840.113619.2.55.3", "salt-one")
    c = remap_uid("1.2.840.113619.2.55.3", "salt-two")
    assert a == b, "The same identifier mapped to two different values"
    assert a != c, "A different salt produced the same mapping"
    assert a.startswith("2.25."), "A remapped identifier is not under the expected root"
    assert len(a) <= 64, "A remapped identifier exceeds the length limit"

    p1 = pseudonym_for("patient-123", "salt-one")
    p2 = pseudonym_for("patient-123", "salt-one")
    assert p1 == p2 and "patient" not in p1.lower(), "Pseudonym generation is not stable or leaks"

    return True, "Identifier remapping is stable, salted and correctly formed."


def check_database() -> tuple:
    """Durability, append only history and the concurrent edit guard."""
    import sqlite3

    from ..core.models import Annotation, Project, Role, User
    from ..store.db import open_database
    from ..store.repository import ConcurrentEditError, Repository

    with tempfile.TemporaryDirectory() as tmp:
        db = open_database(Path(tmp) / "selftest.db")
        try:
            mode = db.query("PRAGMA journal_mode")[0][0]
            assert str(mode).lower() == "wal", (
                f"Write ahead logging is not active, journal mode is {mode}"
            )

            repo = Repository(db)
            user = User(username="selftest", role=Role.ADMIN)
            repo.create_user(user)
            repo.set_actor(user)
            project = Project(name="Self test")
            repo.create_project(project)

            from ..core.models import Case, SourceImage

            case = Case(
                project_id=project.id, pseudonym="ST-0001",
                source=SourceImage(sha256="deadbeef", rows=100, columns=100),
            )
            repo.create_case(case)
            aset = repo.get_or_create_set(case.id, user.id)

            annotation = Annotation(
                set_id=aset.id, class_key="mcw_line", side="R", geometry_type="line"
            )
            annotation.set_points([(10, 10), (10, 50)])
            counter = repo.save_annotation(annotation, expected_counter=0, case_id=case.id)
            assert counter == 1, "The edit counter did not advance"

            try:
                repo.save_annotation(annotation, expected_counter=0, case_id=case.id)
                raise AssertionError(
                    "A stale write was accepted, so two users could overwrite one another"
                )
            except ConcurrentEditError:
                pass

            for statement in (
                "UPDATE audit_log SET detail = 'x' WHERE sequence = 1",
                "DELETE FROM audit_log WHERE sequence = 1",
            ):
                try:
                    db.execute(statement)
                    raise AssertionError(
                        f"The audit log accepted {statement.split()[0].lower()}"
                    )
                except sqlite3.IntegrityError:
                    pass

            chain = repo.verify_audit_chain()
            assert chain["valid"], f"The audit chain did not verify: {chain['reason']}"

            integrity = db.integrity_check()
            assert integrity["ok"], "The database integrity check failed"

            return True, (
                f"Write ahead logging active, {chain['records_checked']} audit "
                f"records chained, append only enforced, stale writes refused."
            )
        finally:
            db.close()


def check_storage() -> tuple:
    """Atomic writes and verified copies."""
    from ..io.fsutil import atomic_write_bytes, copy_preserving, sha256_file

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        target = folder / "nested" / "file.bin"
        payload = b"ARIA self test payload" * 100
        atomic_write_bytes(target, payload)
        assert target.read_bytes() == payload, "An atomic write did not produce the content"

        leftovers = list(folder.rglob(".aria_tmp_*"))
        assert not leftovers, f"{len(leftovers)} temporary files were left behind"

        copy = folder / "copy.bin"
        digest = copy_preserving(target, copy)
        assert digest == sha256_file(copy), "A verified copy does not match its source"

    return True, "Atomic writes leave no partial files and copies are verified by checksum."


def check_reproducibility() -> tuple:
    """Measurements recompute from an export alone (AC 004)."""
    from ..core.measurements import MeasurementEngine, reproduce_from_export
    from ..core.models import Annotation, AnnotationSet, Case, CaseData
    from ..core.schema import Side
    from ..core.units import Calibration, CalibrationSource, ValidationStatus
    from ..io.exporters.json_export import geometry_document

    cal = Calibration(
        source=CalibrationSource.DICOM_PIXEL_SPACING,
        row_spacing_mm=0.076076, col_spacing_mm=0.076076,
        status=ValidationStatus.VALIDATED,
    )
    case = Case(pseudonym="ST-REPRO", calibration=cal)
    aset = AnnotationSet()

    def line(key, side, a, b):
        ann = Annotation(set_id=aset.id, class_key=key, side=side.value, geometry_type="line")
        ann.set_points([a, b])
        return ann

    data = CaseData(
        case=case, annotation_set=aset,
        annotations=[
            line("mcw_line", Side.RIGHT, (900, 1200), (900, 1160)),
            line("pmi_superior_line", Side.RIGHT, (900, 980), (900, 1200)),
            line("pmi_inferior_line", Side.RIGHT, (900, 1020), (900, 1200)),
            line("antegonial_index_line", Side.LEFT, (700, 1200), (700, 1165)),
        ],
    )
    original = {(m.kind, m.side): m for m in MeasurementEngine().compute(data)}
    document = geometry_document(data, None)
    reproduced = {
        (m.kind, m.side): m
        for m in reproduce_from_export(document, document["calibration"])
    }

    checked = 0
    for key, source in original.items():
        target = reproduced.get(key)
        assert target is not None, f"{key} was missing when recomputed from the export"
        for field_name in ("value_px", "value_mm", "value_ratio"):
            a = getattr(source, field_name)
            b = getattr(target, field_name)
            if a is None and b is None:
                continue
            assert a is not None and b is not None and abs(a - b) < 1e-9, (
                f"{key} {field_name} differed when recomputed: {a} against {b}"
            )
            checked += 1

    return True, f"{checked} values recomputed from the export document and matched."


def check_display_independence() -> tuple:
    """Display controls never change stored pixels or coordinates (AC 003)."""
    from ..core.models import SourceImage
    from ..io.image import AVAILABLE_FILTERS, DisplaySettings, ImageData

    rng = np.random.default_rng(5)
    pixels = rng.integers(0, 4096, (120, 200)).astype(np.uint16)
    image = ImageData(
        pixels=pixels.copy(),
        meta=SourceImage(rows=120, columns=200, bits_stored=12, photometric_interpretation="MONOCHROME2"),
    )
    before = image.pixels.copy()

    for name, _label in AVAILABLE_FILTERS:
        settings = DisplaySettings(
            window_centre=2048, window_width=4096, invert=True,
            contrast=1.8, brightness=0.4, gamma=1.3, filter_name=name,
        )
        output = image.to_display(settings)
        assert output.dtype == np.uint8, f"The {name} filter did not return an 8 bit image"
        assert output.shape == pixels.shape, f"The {name} filter changed the image shape"

    assert np.array_equal(image.pixels, before), (
        "Display settings modified the stored pixel array"
    )
    return True, (
        f"{len(AVAILABLE_FILTERS)} display paths exercised with stored pixels unchanged."
    )


def check_monochrome1() -> tuple:
    """MONOCHROME1 images are displayed with the correct polarity (FR 004)."""
    from ..core.models import SourceImage
    from ..io.image import ImageData

    gradient = np.tile(np.linspace(0, 4095, 256).astype(np.uint16), (32, 1))

    mono2 = ImageData(
        pixels=gradient.copy(),
        meta=SourceImage(rows=32, columns=256, photometric_interpretation="MONOCHROME2"),
    )
    mono1 = ImageData(
        pixels=gradient.copy(),
        meta=SourceImage(rows=32, columns=256, photometric_interpretation="MONOCHROME1"),
    )

    out2 = mono2.to_display(mono2.default_display_settings())
    out1 = mono1.to_display(mono1.default_display_settings())

    assert out2[0, -1] > out2[0, 0], "MONOCHROME2 did not render high values as bright"
    assert out1[0, -1] < out1[0, 0], "MONOCHROME1 was not inverted for display"
    return True, "Both photometric interpretations render with the correct polarity."


def check_validation() -> tuple:
    """Submission is blocked while required content is missing (FR 041)."""
    from ..core.models import AnnotationSet, Case, CaseData, SourceImage
    from ..core.schema import ProjectSchema
    from ..core.validation import validate_for_submission

    case = Case(pseudonym="ST-VAL", source=SourceImage(rows=1000, columns=2000))
    data = CaseData(case=case, annotation_set=AnnotationSet())
    result = validate_for_submission(data, ProjectSchema())

    assert not result.can_submit, "An empty case was allowed to be submitted"
    assert result.blockers, "No blocking issues were reported for an empty case"
    assert all(i.remedy for i in result.blockers), (
        "A blocking issue did not say what to do about it"
    )
    codes = {i.code for i in result.blockers}
    assert "laterality_missing" in codes, "Missing laterality did not block submission"
    assert "required_label_missing" in codes, "Missing required labels did not block submission"

    return True, (
        f"{len(result.blockers)} blocking issues reported, each with a remedy."
    )


def check_schema_integrity() -> tuple:
    """The label schema is internally consistent."""
    from ..core.schema import CLASS_BY_KEY, LABEL_CLASSES, ProjectSchema, Side

    keys = [c.key for c in LABEL_CLASSES]
    assert len(keys) == len(set(keys)), "Two label classes share a key"

    codes = [c.short_code for c in LABEL_CLASSES]
    assert len(codes) == len(set(codes)), "Two label classes share a short code"

    for cls in LABEL_CLASSES:
        assert cls.colour.startswith("#") and len(cls.colour) == 7, (
            f"{cls.key} has a malformed colour"
        )
        for dependency in cls.depends_on:
            assert dependency in CLASS_BY_KEY, (
                f"{cls.key} depends on {dependency}, which is not in the schema"
            )
        if cls.side_scoped:
            assert cls.line_style_for(Side.RIGHT) != cls.line_style_for(Side.LEFT), (
                f"{cls.key} draws both sides identically, so side is carried by colour alone"
            )

    schema = ProjectSchema()
    restored = ProjectSchema.from_dict(schema.to_dict())
    assert restored.required_classes == schema.required_classes, (
        "The project schema does not survive being saved and loaded"
    )

    return True, (
        f"{len(LABEL_CLASSES)} classes, unique keys and codes, side distinguishable "
        f"without colour."
    )


def check_theme_contrast() -> tuple:
    """Interface text meets the contrast target (NFR 010)."""
    from ..ui.theme import HIGH_CONTRAST, PALETTE, contrast_ratio

    pairs = [
        ("body text", PALETTE.text, PALETTE.panel),
        ("secondary text", PALETTE.text_dim, PALETTE.panel),
        ("text on controls", PALETTE.text, PALETTE.raised),
        ("success", PALETTE.success, PALETTE.panel),
        ("warning", PALETTE.warning, PALETTE.panel),
        ("danger", PALETTE.danger, PALETTE.panel),
    ]
    worst_name, worst = "", 99.0
    for name, fg, bg in pairs:
        ratio = contrast_ratio(fg, bg)
        if ratio < worst:
            worst_name, worst = name, ratio
        assert ratio >= 4.5, (
            f"{name} has a contrast ratio of {ratio:.2f}, below the 4.5 target"
        )

    hc = contrast_ratio(HIGH_CONTRAST.text, HIGH_CONTRAST.panel)
    assert hc >= 7.0, f"High contrast mode only reaches {hc:.2f}"

    return True, f"Lowest ratio is {worst:.2f} on {worst_name}, high contrast reaches {hc:.1f}."


def check_paths(paths=None) -> tuple:
    """The data folders exist and can be written to."""
    from ..config import Paths
    from ..io.fsutil import ensure_writable

    paths = paths or Paths()
    problems = []
    for label, folder in (
        ("data", paths.data_dir), ("settings", paths.config_dir),
        ("sources", paths.sources_dir), ("working", paths.working_dir),
        ("exports", paths.exports_dir), ("backups", paths.backups_dir),
    ):
        ok, message = ensure_writable(folder)
        if not ok:
            problems.append(f"{label}: {message}")
    assert not problems, "; ".join(problems)
    return True, "Every data folder exists and is writable."


def check_dicom_reader() -> tuple:
    """The DICOM reader handles stored bit widths and signed data correctly."""
    import pydicom
    from pydicom.dataset import Dataset

    from ..io.dicom_reader import normalise_stored_pixels

    ds = Dataset()
    ds.BitsStored = 14
    ds.BitsAllocated = 16
    ds.HighBit = 13
    ds.PixelRepresentation = 0
    raw = np.array([[0xFFFF, 0x3FFF, 0x0000]], dtype=np.uint16)
    out = normalise_stored_pixels(raw, ds)
    assert out.max() <= 0x3FFF, (
        f"Undefined high bits were not masked, maximum was {out.max()}"
    )
    assert out[0, 1] == 0x3FFF, "A valid 14 bit value was altered by masking"

    ds.PixelRepresentation = 1
    ds.BitsStored = 12
    ds.HighBit = 11
    signed_raw = np.array([[0x0FFF, 0x0001]], dtype=np.uint16)
    signed = normalise_stored_pixels(signed_raw, ds)
    assert signed[0, 0] == -1, (
        f"Two's complement data was not sign extended, got {signed[0, 0]}"
    )

    return True, "Stored bit masking and sign extension are correct."


def check_bundle_layout() -> tuple:
    """A training bundle is written, verified and read back."""
    from ..config import Settings
    from ..core.models import (
        Annotation, AnnotationSet, Case, CaseData, CategoricalLabel, Project,
        Role, SourceImage, User,
    )
    from ..core.schema import ProjectSchema, Side
    from ..core.units import Calibration, CalibrationSource, ValidationStatus
    from ..io.exporters.bundle import BundleExporter, BundleOptions, verify_bundle
    from ..store.db import open_database
    from ..store.repository import Repository

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        from ..config import Paths

        paths = Paths(
            data_dir=folder / "data", config_dir=folder / "cfg",
            log_dir=folder / "logs", cache_dir=folder / "cache",
        )
        paths.ensure()
        db = open_database(paths.database)
        try:
            repo = Repository(db)
            project = Project(name="Self test")
            cal = Calibration(
                source=CalibrationSource.DICOM_PIXEL_SPACING,
                row_spacing_mm=0.076, col_spacing_mm=0.076,
                status=ValidationStatus.VALIDATED,
            )
            case = Case(
                project_id=project.id, pseudonym="ST-BUNDLE", calibration=cal,
                source=SourceImage(sha256="a" * 64, rows=400, columns=600, source_format="png"),
            )
            aset = AnnotationSet(case_id=case.id)
            mcw = Annotation(
                set_id=aset.id, class_key="mcw_line", side="R", geometry_type="line"
            )
            mcw.set_points([(100, 200), (100, 240)])
            region = Annotation(
                set_id=aset.id, class_key="mci_region", side="R", geometry_type="box"
            )
            region.set_points([(50, 150), (150, 250)])
            data = CaseData(
                case=case, annotation_set=aset,
                annotations=[mcw, region],
                categorical=[
                    CategoricalLabel(set_id=aset.id, key="mci_grade", side="R", value="C2")
                ],
            )

            exporter = BundleExporter(repo, paths, Settings(), ProjectSchema())
            destination = folder / "bundle.zip"
            user = User(username="st", role=Role.ANNOTATOR, pseudonym="ANN-999")
            result = exporter.build(
                destination, project, [(data, user, None, [])],
                BundleOptions(include_raw=False),
            )
            assert result.path is not None and destination.exists(), "No bundle was written"
            assert not result.privacy_findings, (
                f"The identifier scan flagged {len(result.privacy_findings)} items"
            )

            report = verify_bundle(destination)
            assert report["ok"], f"The bundle did not verify: {report}"

            import zipfile

            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
            for required in (
                "manifest.json", "README.md", "checksums.sha256",
                "metadata/data_dictionary.csv", "metadata/measurements_long.csv",
                "annotations/ST-BUNDLE/annotations.json",
                "annotations/ST-BUNDLE/measurements.json",
            ):
                assert required in names, f"The bundle is missing {required}"

            return True, (
                f"Bundle written with {report['verified']} verified files and the "
                f"expected layout."
            )
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("geometry", "Geometry and construction", "Core calculations", check_geometry,
     "Every measurement is built on these, so an error here would affect all of them."),
    ("measurements", "Derived measurements", "Core calculations", check_measurements,
     "These are the values a study reports."),
    ("units", "Units and calibration gating", "Core calculations", check_units,
     "A millimetre value from an unvalidated scale would be a fabricated number."),
    ("texture", "Texture features", "Core calculations", check_texture,
     "Fractal dimension is reported as a research feature and must be correct."),
    ("agreement", "Agreement statistics", "Core calculations", check_agreement,
     "These decide whether two annotators are judged to agree."),
    ("reproducibility", "Reproducing measurements from an export", "Core calculations",
     check_reproducibility,
     "A reviewer must be able to arrive at the same numbers from the export alone."),
    ("display", "Display independence", "Viewer", check_display_independence,
     "If a display control moved a coordinate, every measurement would be unreliable."),
    ("monochrome", "Photometric polarity", "Viewer", check_monochrome1,
     "An inverted radiograph would be read wrongly."),
    ("mask_encoding", "Brush mask encoding", "Viewer", check_mask_encoding,
     "A mask that changed when stored would silently alter a region."),
    ("schema", "Label schema integrity", "Schema", check_schema_integrity,
     "The schema drives drawing, validation and export, so it must be consistent."),
    ("contrast", "Interface contrast", "Schema", check_theme_contrast,
     "Text below the contrast target is hard to read on a dim clinical display."),
    ("guards", "Import guardrails", "Data handling", check_import_guards,
     "These stop an unsuitable file from reaching the decoder or exhausting memory."),
    ("dicom", "DICOM pixel handling", "Data handling", check_dicom_reader,
     "Unmasked high bits or unsigned reads of signed data would corrupt the display."),
    ("privacy", "Identifier scanning", "Data handling", check_privacy_scan,
     "This is the last check before anything leaves the institution."),
    ("deident", "Identifier remapping", "Data handling", check_deidentification,
     "Unstable remapping would break the link between images of one study."),
    ("storage", "Atomic writes", "Storage", check_storage,
     "Autosave depends on a write that cannot be left half finished."),
    ("database", "Database durability and history", "Storage", check_database,
     "This is what makes completed work survive a power loss and history tamper evident."),
    ("paths", "Data folders", "Storage", check_paths,
     "The application cannot save anything if these are not writable."),
    ("bundle", "Training bundle", "Export", check_bundle_layout,
     "The bundle is how a dataset is handed on, so its layout and checksums must hold."),
]


def run_self_tests(paths=None, progress=None, only: set | None = None) -> SelfTestReport:
    """Run every self test and return a report."""
    from ..core.models import utc_now

    report = SelfTestReport(generated_at=utc_now())
    selected = [c for c in CHECKS if only is None or c[0] in only]

    for index, (key, name, group, function, matters) in enumerate(selected):
        if progress is not None:
            progress(index, len(selected), name)
        start = time.perf_counter()
        try:
            if key == "paths":
                passed, detail = function(paths)
            else:
                passed, detail = function()
            report.results.append(
                TestResult(
                    key=key, name=name, group=group, passed=bool(passed),
                    detail=detail, matters=matters,
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                )
            )
        except AssertionError as exc:
            report.results.append(
                TestResult(
                    key=key, name=name, group=group, passed=False,
                    detail=str(exc), matters=matters, error=str(exc),
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                )
            )
        except Exception as exc:
            report.results.append(
                TestResult(
                    key=key, name=name, group=group, passed=False,
                    detail=f"The test could not complete: {exc}",
                    matters=matters, error=f"{type(exc).__name__}: {exc}",
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                )
            )

    if progress is not None:
        progress(len(selected), len(selected), "")
    return report
