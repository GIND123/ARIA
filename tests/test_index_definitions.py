"""The indices are measured to the definitions the clinical team gave.

The definitions came as a document: what each index is, where it is measured
from and to, and the paper each one is taken from. Two of those details were
not reflected in the application.

The Klemetti evaluation area has two bounds, not one. It runs from the distal
aspect of the mental foramen to the antegonial region. ARIA stated only the
first, and checked only that a grading region existed, not that it sat in the
band the classification is defined on. A grade read from the symphysis is a
grade of different bone, and comparing it with a published figure compares two
different things.

And nothing recorded where any definition came from. An exported index whose
definition is unstated cannot be compared with anybody else's.
"""

from __future__ import annotations

import pytest

from aria.core.references import (
    DEFINITION_SOURCES,
    all_citations,
    definition_for,
)
from aria.core.schema import CLASS_BY_KEY, MCIGrade, ProjectSchema, Side
from aria.core.validation import validate_for_submission


class TestThePmiRatiosUseTheRightHeights:
    """X over Z and X over Y, where X is the cortical width, Z runs from the
    superior border of the foramen and Y from the inferior border."""

    def test_the_two_heights_are_defined_from_opposite_foramen_margins(self):
        superior = CLASS_BY_KEY["pmi_superior_line"].description.lower()
        inferior = CLASS_BY_KEY["pmi_inferior_line"].description.lower()

        assert "superior" in superior and "inferior mandibular border" in superior
        assert "inferior mental foramen" in inferior

    def test_the_superior_height_is_the_longer_of_the_two(
        self, repo, calibrated_case, annotator
    ):
        """It starts higher up the ramus, so it has to be longer. If these ever
        came out the other way round the two ratios would be swapped."""
        from aria.core.measurements import MeasurementEngine, MeasurementKind
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        measurements = MeasurementEngine(ProjectSchema()).compute(data)
        by_kind = {
            (m.kind, m.side): m for m in measurements if m.value_px is not None
        }

        for side in ("R", "L"):
            superior = by_kind.get((MeasurementKind.PMI_SUPERIOR_HEIGHT.value, side))
            inferior = by_kind.get((MeasurementKind.PMI_INFERIOR_HEIGHT.value, side))
            if superior is None or inferior is None:
                continue
            assert superior.value_px > inferior.value_px

    def test_the_inferior_ratio_is_the_larger(self, repo, calibrated_case, annotator):
        """Same numerator over a shorter denominator."""
        from aria.core.measurements import MeasurementEngine, MeasurementKind
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        measurements = MeasurementEngine(ProjectSchema()).compute(data)
        ratios = {
            (m.kind, m.side): m.value_ratio
            for m in measurements if m.value_ratio is not None
        }

        for side in ("R", "L"):
            superior = ratios.get((MeasurementKind.PMI_SUPERIOR.value, side))
            inferior = ratios.get((MeasurementKind.PMI_INFERIOR.value, side))
            if superior is None or inferior is None:
                continue
            assert inferior > superior


class TestTheKlemettiEvaluationAreaHasTwoBounds:

    def test_the_label_states_both_of_them(self):
        """It used to give only the foramen bound."""
        description = CLASS_BY_KEY["mci_region"].description.lower()

        assert "mental foramen" in description
        assert "antegonial" in description

    def test_the_not_assessable_grade_states_both_too(self):
        definition = MCIGrade.NOT_ASSESSABLE.definition.lower()
        assert "mental foramen" in definition
        assert "antegonial" in definition

    def _issues(self, repo, case, annotator):
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, case, annotator)
        result = validate_for_submission(data, ProjectSchema())
        return data, [
            i for i in result.issues
            if i.code == "mci_region_outside_evaluation_area"
        ]

    def test_a_region_in_the_band_is_not_flagged(
        self, repo, calibrated_case, annotator
    ):
        _data, flagged = self._issues(repo, calibrated_case, annotator)
        assert not flagged, [i.message for i in flagged]

    @pytest.mark.parametrize("side", [Side.RIGHT, Side.LEFT])
    def test_a_region_toward_the_midline_is_flagged(
        self, repo, calibrated_case, annotator, side
    ):
        """Mesial to the foramen is not where the grade is read from, and it is
        a different direction in image coordinates on each side."""
        data, _ = self._issues(repo, calibrated_case, annotator)
        foramen = data.present("mental_foramen_centre", side)
        antegonial = data.present("antegonial_point", side)
        region = data.present("mci_region", side)

        # A quarter of the band's length past the foramen, away from the
        # antegonial point.
        span = antegonial.points()[0][0] - foramen.points()[0][0]
        x = foramen.points()[0][0] - span * 0.25
        region.set_points([(x - 40, 800.0), (x + 40, 900.0)])
        repo.save_annotations(
            [region], expected_counter=data.annotation_set.edit_counter,
            case_id=calibrated_case.id,
        )

        again = repo.load_set_data(data.annotation_set.id)
        result = validate_for_submission(again, ProjectSchema())
        flagged = [
            i for i in result.issues
            if i.code == "mci_region_outside_evaluation_area"
            and i.side == side.value
        ]
        assert flagged, f"A region mesial to the foramen passed on the {side.value} side"
        assert "midline" in flagged[0].message

    @pytest.mark.parametrize("side", [Side.RIGHT, Side.LEFT])
    def test_a_region_past_the_antegonial_region_is_flagged(
        self, repo, calibrated_case, annotator, side
    ):
        data, _ = self._issues(repo, calibrated_case, annotator)
        foramen = data.present("mental_foramen_centre", side)
        antegonial = data.present("antegonial_point", side)
        region = data.present("mci_region", side)

        span = antegonial.points()[0][0] - foramen.points()[0][0]
        x = antegonial.points()[0][0] + span * 0.25
        region.set_points([(x - 40, 800.0), (x + 40, 900.0)])
        repo.save_annotations(
            [region], expected_counter=data.annotation_set.edit_counter,
            case_id=calibrated_case.id,
        )

        again = repo.load_set_data(data.annotation_set.id)
        result = validate_for_submission(again, ProjectSchema())
        flagged = [
            i for i in result.issues
            if i.code == "mci_region_outside_evaluation_area"
            and i.side == side.value
        ]
        assert flagged, f"A region past the antegonial point passed on {side.value}"
        assert "antegonial" in flagged[0].message

    def test_it_is_a_warning_rather_than_a_blocker(
        self, repo, calibrated_case, annotator
    ):
        """The annotator may have a reason. It is said, and recorded, and the
        case still submits."""
        data, _ = self._issues(repo, calibrated_case, annotator)
        region = data.present("mci_region", Side.RIGHT)
        region.set_points([(10.0, 800.0), (90.0, 900.0)])
        repo.save_annotations(
            [region], expected_counter=data.annotation_set.edit_counter,
            case_id=calibrated_case.id,
        )

        again = repo.load_set_data(data.annotation_set.id)
        result = validate_for_submission(again, ProjectSchema())
        flagged = [
            i for i in result.issues
            if i.code == "mci_region_outside_evaluation_area"
        ]
        assert flagged
        assert all(i.severity == "warning" for i in flagged)

    def test_nothing_is_said_when_a_landmark_is_missing(
        self, repo, imported_case, annotator
    ):
        """Both bounds are needed to judge placement, so with either absent
        this stays quiet rather than guessing."""
        from aria.core.models import Annotation

        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        region = Annotation(
            set_id=aset.id, class_key="mci_region", side="R", geometry_type="box",
        )
        region.set_points([(10.0, 800.0), (90.0, 900.0)])
        repo.save_annotations([region], expected_counter=0, case_id=imported_case.id)

        data = repo.load_set_data(aset.id)
        result = validate_for_submission(data, ProjectSchema())

        assert not [
            i for i in result.issues
            if i.code == "mci_region_outside_evaluation_area"
        ]


class TestEveryDefinitionCarriesItsSource:

    def test_each_index_has_a_definition_and_a_citation(self):
        for kind, (definition, citations) in DEFINITION_SOURCES.items():
            assert definition, f"{kind} has no definition"
            assert citations, f"{kind} cites nothing"
            for citation in citations:
                assert citation.pmid, f"{kind} cites a source with no PMID"

    def test_the_indices_the_study_reports_are_all_covered(self):
        for kind in (
            "mandibular_cortical_width", "pmi_superior", "pmi_inferior",
            "antegonial_index", "gonial_index", "mci_grade",
        ):
            assert kind in DEFINITION_SOURCES

    def test_the_cortical_width_is_named_as_both_of_its_names(self):
        """It is published as Mandibular Cortical Width and as Mental Index,
        and a reader who knows one name has to find the other."""
        definition = definition_for("mandibular_cortical_width")["definition"]
        assert "Mandibular Cortical Width" in definition
        assert "Mental Index" in definition

    def test_the_two_pmi_definitions_differ_in_the_right_place(self):
        superior = definition_for("pmi_superior")["definition"]
        inferior = definition_for("pmi_inferior")["definition"]

        assert "superior border" in superior
        assert "inferior border of the mental foramen" in inferior
        assert superior != inferior

    def test_the_klemetti_definition_states_the_band_and_the_grades(self):
        definition = definition_for("mci_grade")["definition"]
        assert "mental foramen" in definition and "antegonial" in definition
        for grade in ("C1", "C2", "C3"):
            assert grade in definition

    def test_an_unknown_measurement_gets_nothing_rather_than_a_guess(self):
        assert definition_for("not_a_measurement") == {
            "definition": "", "sources": []
        }

    def test_the_citations_are_listed_once_each(self):
        citations = all_citations()
        pmids = [c.pmid for c in citations]
        assert len(pmids) == len(set(pmids))
        assert len(citations) >= 3

    def test_a_citation_reads_as_a_reference(self):
        text = all_citations()[0].text()
        assert "PMID:" in text
        assert text.endswith(".") or "PMID" in text


class TestTheDefinitionsReachTheExport:

    def test_each_measurement_row_carries_its_definition(
        self, repo, calibrated_case, annotator
    ):
        from aria.core.measurements import MeasurementEngine, measurements_to_rows
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        rows = measurements_to_rows(MeasurementEngine(ProjectSchema()).compute(data))
        described = [r for r in rows if r["definition"]]

        assert described, "No measurement row says what it measured"
        assert all(r["definition_source"] for r in described)
        assert any("PMID" in r["definition_source"] for r in described)

    def test_both_new_columns_are_documented(self):
        from aria.io.exporters.csv_export import DATA_DICTIONARY

        documented = {
            n for n, table, *_ in DATA_DICTIONARY if table == "measurements_long"
        }
        assert "definition" in documented
        assert "definition_source" in documented
