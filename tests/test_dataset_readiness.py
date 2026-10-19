"""What the labelling has to carry so the dataset is usable afterwards.

Audited against the end to end specification for the measurement and decision
support system that will be built on this data. Most of it was already there.
Four things were not, and all four are the kind that cannot be added later:
they are either recorded while somebody is looking at the radiograph, or they
are gone.

The worst of them is patient grouping. Deidentification removes every means of
recognising that two radiographs belong to one person, so unless the grouping
is captured at import there is no way to make a split patient independent
afterwards. A model trained across such a split learns the patient rather than
the disease, and every number measured on the test set after that is
optimistic.
"""

from __future__ import annotations

import pytest

from aria.core.models import Annotation, Case, SourceImage
from aria.core.patient import (
    DEFAULT_REFERENCE_WINDOW_DAYS,
    ReferenceModality,
    ReferenceOutcome,
    ReferenceQuality,
)
from aria.core.schema import QualityFlag


class TestEveryImageBelongsToAPatient:
    """FR 067, FR 069 and AC 010: splits have to be patient independent."""

    def test_an_imported_case_lands_in_a_group(self, importer, project, admin, sample_dicom):
        outcome = importer.import_file(
            str(sample_dicom), project.id, imported_by=admin.id
        )
        assert outcome.succeeded, outcome.error_message
        assert outcome.case.patient_group, "An imported case has no patient group"

    def test_the_group_is_not_the_identifier_it_came_from(
        self, importer, project, admin, sample_dicom
    ):
        """A salted digest, so the dataset can group without naming anybody."""
        import pydicom

        patient_id = str(
            getattr(pydicom.dcmread(str(sample_dicom)), "PatientID", "") or ""
        )
        outcome = importer.import_file(
            str(sample_dicom), project.id, imported_by=admin.id
        )

        group = outcome.case.patient_group
        assert group
        if patient_id:
            assert patient_id not in group

    def test_the_group_is_derived_from_the_patient_not_the_image(
        self, importer, project, admin, sample_dicom
    ):
        """Two radiographs carrying one patient identifier have to group
        together, which is what makes the grouping worth having. The digest is
        checked directly, because the duplicate guard stops the same file being
        imported twice and a second visit is not available as a fixture.
        """
        import pydicom

        from aria.io.deident import pseudonym_for

        outcome = importer.import_file(
            str(sample_dicom), project.id, imported_by=admin.id
        )
        dataset = pydicom.dcmread(str(sample_dicom))
        identifier = str(getattr(dataset, "PatientID", "") or "") or str(
            getattr(dataset, "StudyInstanceUID", "") or ""
        )
        if not identifier:
            pytest.skip("The fixture carries no identifier to group on")

        expected = pseudonym_for(identifier, importer.salt, prefix="PT")
        assert outcome.case.patient_group == expected, (
            "The group is not a function of the patient, so a second "
            "radiograph of the same patient would not join it"
        )

    def test_an_image_with_no_identifier_becomes_its_own_patient(
        self, importer, project, admin, sample_png
    ):
        """The safe assumption. Treating an unknown as its own patient can only
        split one patient's images apart, which is a smaller error than merging
        two patients into one group."""
        outcome = importer.import_file(
            str(sample_png), project.id, imported_by=admin.id
        )
        assert outcome.succeeded, outcome.error_message
        assert outcome.case.patient_group
        assert outcome.case.pseudonym in outcome.case.patient_group

    def test_two_different_patients_do_not_share_a_group(
        self, importer, project, admin, sample_dicom, sample_png
    ):
        first = importer.import_file(str(sample_dicom), project.id, imported_by=admin.id)
        second = importer.import_file(str(sample_png), project.id, imported_by=admin.id)

        assert first.case.patient_group != second.case.patient_group

    def test_the_group_survives_being_stored(self, repo, imported_case):
        assert repo.get_case(imported_case.id).patient_group == (
            imported_case.patient_group
        )

    def test_a_group_can_be_set_by_hand(self, repo, imported_case):
        """For images that arrive with no identifier, where the only person who
        knows two radiographs are of one patient is holding the referral."""
        repo.set_patient_group(imported_case.id, "PT-MANUAL")
        assert repo.get_case(imported_case.id).patient_group == "PT-MANUAL"

    def test_setting_a_group_is_audited(self, repo, imported_case):
        repo.set_patient_group(imported_case.id, "PT-MANUAL")
        details = [r.detail for r in repo.audit_records(limit=5)]
        assert any("PT-MANUAL" in (d or "") for d in details)


class TestTheSplitCanBeProvedPatientIndependent:

    def _case(self, repo, project, group, split, index):
        case = Case(
            project_id=project.id, pseudonym=f"CASE-{index:03d}",
            source=SourceImage(sha256=f"{index:064d}", rows=10, columns=10),
            patient_group=group, split=split,
        )
        repo.create_case(case)
        return case

    def test_a_clean_dataset_reports_no_leakage(self, repo, project, admin):
        repo.set_actor(admin)
        self._case(repo, project, "PT-A", "train", 1)
        self._case(repo, project, "PT-A", "train", 2)
        self._case(repo, project, "PT-B", "test", 3)

        summary = repo.split_summary(project.id)
        assert summary["patient_independent"]
        assert summary["n_leaking_groups"] == 0

    def test_a_patient_across_two_splits_is_reported(self, repo, project, admin):
        """The failure this exists to catch."""
        repo.set_actor(admin)
        self._case(repo, project, "PT-A", "train", 1)
        self._case(repo, project, "PT-A", "test", 2)

        summary = repo.split_summary(project.id)
        assert not summary["patient_independent"]
        assert summary["n_leaking_groups"] == 1
        leak = summary["leaking_groups"][0]
        assert leak["patient_group"] == "PT-A"
        assert {s["split"] for s in leak["splits"]} == {"train", "test"}

    def test_the_summary_counts_patients_as_well_as_images(self, repo, project, admin):
        """Two images of one patient are one patient, and a split described in
        images alone overstates how much independent data there is."""
        repo.set_actor(admin)
        self._case(repo, project, "PT-A", "train", 1)
        self._case(repo, project, "PT-A", "train", 2)
        self._case(repo, project, "PT-B", "train", 3)

        summary = repo.split_summary(project.id)
        train = next(s for s in summary["splits"] if s["split"] == "train")
        assert train["n_cases"] == 3
        assert train["n_patients"] == 2

    def test_unassigned_cases_are_not_counted_as_leaking(self, repo, project, admin):
        """A case in no split cannot be in two."""
        repo.set_actor(admin)
        self._case(repo, project, "PT-A", "train", 1)
        self._case(repo, project, "PT-A", "", 2)

        assert repo.split_summary(project.id)["patient_independent"]

    def test_the_group_reaches_the_export(self, repo, calibrated_case, annotator, project):
        from aria.io.exporters.csv_export import case_row
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        row = case_row(data, project, annotator)

        assert row["patient_group"] == calibrated_case.patient_group
        assert row["patient_group"], "The export cannot be split patient independently"


class TestTheReferenceOutcomeSaysWhatItIs:
    """FR 064 to FR 066. An OPG does not measure bone mineral density, so a
    model trained against this depends entirely on what it records."""

    def test_an_empty_reference_says_nothing(self):
        outcome = ReferenceOutcome()
        assert not outcome.is_recorded
        assert outcome.summary_line() == "No reference outcome recorded."

    def test_a_complete_reference_is_usable(self):
        outcome = ReferenceOutcome(
            modality=ReferenceModality.DXA.value, site="femoral neck",
            t_score=-2.6, days_from_image=45, device="Hologic Discovery",
            quality=ReferenceQuality.ACCEPTABLE.value,
        )
        usable, reasons = outcome.usable_as_target()
        assert usable, reasons

    @pytest.mark.parametrize("missing,expected", [
        ({"modality": ReferenceModality.NONE.value}, "modality"),
        ({"site": ""}, "site"),
        ({"t_score": None}, "value"),
        ({"days_from_image": None}, "interval"),
    ])
    def test_an_incomplete_reference_is_not_a_target(self, missing, expected):
        fields = {
            "modality": ReferenceModality.DXA.value, "site": "femoral neck",
            "t_score": -2.6, "days_from_image": 45,
            "quality": ReferenceQuality.ACCEPTABLE.value,
        }
        fields.update(missing)
        usable, reasons = ReferenceOutcome(**fields).usable_as_target()

        assert not usable
        assert any(expected in r.lower() for r in reasons), reasons

    def test_a_density_without_its_unit_is_not_a_target(self):
        """A number whose unit is unknown cannot be compared with anything."""
        outcome = ReferenceOutcome(
            modality=ReferenceModality.DXA.value, site="lumbar spine",
            bmd_value=0.82, bmd_unit="", days_from_image=10,
            quality=ReferenceQuality.ACCEPTABLE.value,
        )
        usable, reasons = outcome.usable_as_target()
        assert not usable
        assert any("unit" in r for r in reasons)

    def test_a_reference_too_far_from_the_image_is_not_a_target(self):
        outcome = ReferenceOutcome(
            modality=ReferenceModality.DXA.value, site="femoral neck",
            t_score=-2.6, days_from_image=DEFAULT_REFERENCE_WINDOW_DAYS + 1,
            quality=ReferenceQuality.ACCEPTABLE.value,
        )
        usable, reasons = outcome.usable_as_target()
        assert not usable
        assert any("different skeletons" in r for r in reasons)

    @pytest.mark.parametrize("quality", ["questionable", "unusable"])
    def test_a_reference_marked_doubtful_is_not_a_target(self, quality):
        outcome = ReferenceOutcome(
            modality=ReferenceModality.DXA.value, site="femoral neck",
            t_score=-2.6, days_from_image=30, quality=quality,
        )
        assert not outcome.usable_as_target()[0]

    def test_no_dates_are_stored(self):
        """A pair of dates narrows a patient down. The interval is what the
        analysis needs."""
        fields = set(ReferenceOutcome.__dataclass_fields__)
        for forbidden in ("date", "acquired_at", "examination_date", "scan_date"):
            assert not any(forbidden in f for f in fields), fields

    def test_it_round_trips(self, repo, imported_case):
        outcome = ReferenceOutcome(
            modality=ReferenceModality.QCT.value, site="distal radius",
            t_score=-1.8, z_score=-0.9, bmd_value=0.74, bmd_unit="g/cm2",
            days_from_image=-12, device="Scanco", source="Radiology",
            quality=ReferenceQuality.ACCEPTABLE.value,
        )
        repo.set_reference_outcome(imported_case.id, outcome)
        again = repo.get_case(imported_case.id).reference

        assert again.modality == ReferenceModality.QCT.value
        assert again.bmd_unit == "g/cm2"
        assert again.days_from_image == -12

    def test_it_reaches_the_export_with_its_verdict(
        self, repo, calibrated_case, annotator, project
    ):
        from aria.io.exporters.csv_export import case_row
        from tests.conftest import build_annotation_set

        repo.set_reference_outcome(
            calibrated_case.id,
            ReferenceOutcome(
                modality=ReferenceModality.DXA.value, site="femoral neck",
                t_score=-2.6, days_from_image=45,
                quality=ReferenceQuality.ACCEPTABLE.value,
            ),
        )
        data = build_annotation_set(
            repo, repo.get_case(calibrated_case.id), annotator
        )
        row = case_row(data, project, annotator)

        assert row["reference_modality"] == "dxa"
        assert row["reference_site"] == "femoral neck"
        assert row["reference_usable_as_target"] is True

    def test_an_unrecorded_reference_exports_as_empty_not_zero(
        self, repo, calibrated_case, annotator, project
    ):
        from aria.io.exporters.csv_export import case_row
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        row = case_row(data, project, annotator)

        assert row["reference_t_score"] is None
        assert row["reference_bmd_value"] is None
        assert row["reference_usable_as_target"] is False


class TestTheQualityVocabularyIsComplete:
    """FR 027. A flag with nowhere to record it is information thrown away."""

    def test_the_flags_the_downstream_system_names_are_present(self):
        required = {
            "Not visible", "Ambiguous", "Multiple foramina",
            "Anatomical variant", "Cropped anatomy", "Positioning error",
            "Motion", "Metal artefact", "Exposure problem",
            "Pathology affecting measurement", "Other",
        }
        assert required <= {f.display for f in QualityFlag}

    def test_an_accessory_foramen_can_be_recorded(self):
        """It changes where the cortical width is measured, so an annotator
        needs somewhere to say so rather than leaving a reader to wonder why a
        landmark looks misplaced."""
        assert QualityFlag.MULTIPLE_FORAMINA.display == "Multiple foramina"

    def test_metal_is_distinguished_from_artefact_in_general(self):
        """A restoration over the measurement site is the common reason a
        cortex cannot be read, and a model trained on those learns the metal."""
        assert QualityFlag.METAL_ARTEFACT in set(QualityFlag)
        assert QualityFlag.ARTEFACT in set(QualityFlag)
        assert QualityFlag.METAL_ARTEFACT != QualityFlag.ARTEFACT

    def test_disease_is_distinguished_from_a_hard_to_read_image(self):
        assert QualityFlag.PATHOLOGY_AFFECTING_MEASUREMENT.display == (
            "Pathology affecting measurement"
        )


class TestAnnotationsCarryConfidence:
    """FR 026 lists confidence alongside visibility, and they are not the
    same: a landmark can be perfectly visible and still be one of two
    candidates."""

    def test_the_field_exists_and_defaults_to_unstated(self):
        annotation = Annotation()
        assert "confidence" in Annotation.__dataclass_fields__
        assert annotation.confidence is None

    def test_it_survives_being_stored(self, repo, imported_case, annotator):
        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        annotation = Annotation(
            set_id=aset.id, class_key="menton", side="NA", geometry_type="point",
            confidence=0.6,
        )
        annotation.set_points([(10.0, 20.0)])
        repo.save_annotations([annotation], expected_counter=0, case_id=imported_case.id)

        stored = repo.get_annotation(annotation.id)
        assert stored.confidence == pytest.approx(0.6)

    def test_unstated_stays_unstated_rather_than_becoming_certain(
        self, repo, imported_case, annotator
    ):
        """Empty means not stated. Reading it as 1.0 would weight every
        unmarked landmark as fully confident."""
        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        annotation = Annotation(
            set_id=aset.id, class_key="menton", side="NA", geometry_type="point",
        )
        annotation.set_points([(10.0, 20.0)])
        repo.save_annotations([annotation], expected_counter=0, case_id=imported_case.id)

        assert repo.get_annotation(annotation.id).confidence is None

    def test_it_reaches_both_exports(self, repo, calibrated_case, annotator, project):
        from aria.io.exporters.json_export import geometry_document
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        document = geometry_document(data, project, annotator)

        assert all("confidence" in a for a in document["annotations"])

    def test_the_column_is_documented(self):
        from aria.io.exporters.csv_export import ANNOTATION_FIELDS, DATA_DICTIONARY

        documented = {
            n for n, table, *_ in DATA_DICTIONARY if table == "annotations"
        }
        assert "confidence" in ANNOTATION_FIELDS
        assert "confidence" in documented


class TestEveryExportedColumnCanBeInterpreted:
    """FR 096: documented variables, units, allowed values, missing codes."""

    def test_no_case_or_annotation_column_is_undocumented(self):
        from aria.io.exporters.csv_export import (
            ANNOTATION_FIELDS, CASE_FIELDS, DATA_DICTIONARY,
        )

        for table, fields in (
            ("cases", CASE_FIELDS), ("annotations", ANNOTATION_FIELDS),
        ):
            documented = {n for n, t, *_ in DATA_DICTIONARY if t == table}
            missing = [f for f in fields if f not in documented]
            assert not missing, f"{table} exports undocumented columns: {missing}"

    def test_the_columns_with_a_fixed_vocabulary_list_it(self):
        """A reader cannot validate a categorical column whose allowed values
        are not written down."""
        from aria.io.exporters.csv_export import DATA_DICTIONARY

        entries = {n: d for n, t, *d in DATA_DICTIONARY if t == "cases"}
        for column in (
            "split", "patient_sex", "patient_medication", "reference_modality",
            "reference_quality",
        ):
            description = entries[column][-1]
            assert "llowed values" in description, column
