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


class TestTheSameRadiographTwiceIsFound:
    """FR 070. A checksum catches the same file; it does not catch the same
    study exported again, and those are the copies that leak across a split."""

    def test_a_case_carries_a_perceptual_hash(self, importer, project, admin, sample_dicom):
        outcome = importer.import_file(
            str(sample_dicom), project.id, imported_by=admin.id
        )
        assert outcome.succeeded, outcome.error_message
        assert outcome.case.perceptual_hash

    def test_the_hash_survives_rewindowing_and_requantising(self, sample_dicom):
        """The two things that happen when a study is exported again."""
        import numpy as np

        from aria.io.dicom_reader import read_dicom
        from aria.io.image import hamming_distance, perceptual_hash

        pixels = read_dicom(str(sample_dicom)).pixels
        original = perceptual_hash(pixels)

        brighter = np.clip(
            pixels.astype(np.int32) * 1.15, 0, int(pixels.max())
        ).astype(pixels.dtype)
        eight_bit = (pixels // max(1, int(pixels.max()) // 255)).astype(np.uint8)

        assert hamming_distance(original, perceptual_hash(brighter)) <= 2
        assert hamming_distance(original, perceptual_hash(eight_bit)) <= 2

    def test_two_different_radiographs_are_far_apart(self, sample_dicom, sample_png):
        """So that the threshold has somewhere to sit."""
        from aria.io.dicom_reader import read_dicom
        from aria.io.image import hamming_distance, perceptual_hash
        from aria.io.png_reader import read_png

        a = perceptual_hash(read_dicom(str(sample_dicom)).pixels)
        b = perceptual_hash(read_png(str(sample_png)).pixels)
        assert hamming_distance(a, b) > 20

    def test_a_missing_hash_is_not_treated_as_a_match(self):
        """An empty hash compared with anything has to read as far away, or
        every uncomputed case would look like a duplicate of every other."""
        from aria.io.image import hamming_distance

        assert hamming_distance("", "abc") > 1000
        assert hamming_distance("abc", "") > 1000
        assert hamming_distance("ab", "abcd") > 1000

    def test_a_reimported_study_is_reported(self, repo, project, admin, sample_dicom):
        """Two cases holding the same pixels, recorded as different patients."""
        from aria.core.models import Case, SourceImage
        from aria.io.dicom_reader import read_dicom
        from aria.io.image import perceptual_hash

        repo.set_actor(admin)
        digest = perceptual_hash(read_dicom(str(sample_dicom)).pixels)
        for index, (group, split) in enumerate(
            [("PT-A", "train"), ("PT-B", "test")]
        ):
            case = Case(
                project_id=project.id, pseudonym=f"DUP-{index}",
                source=SourceImage(sha256=f"{index:064d}", rows=10, columns=10),
                patient_group=group, split=split, perceptual_hash=digest,
            )
            repo.create_case(case)

        pairs = repo.find_near_duplicates(project.id)
        assert pairs, "The same radiograph twice went unreported"
        assert pairs[0]["distance"] == 0
        assert pairs[0]["leaks_across_split"] is True
        assert pairs[0]["identical_file"] is False

    def test_the_same_patient_on_one_side_of_the_split_does_not_leak(
        self, repo, project, admin
    ):
        """Two images of one patient, both in training, are not a leak."""
        from aria.core.models import Case, SourceImage

        repo.set_actor(admin)
        for index in range(2):
            repo.create_case(
                Case(
                    project_id=project.id, pseudonym=f"SAME-{index}",
                    source=SourceImage(sha256=f"{index:064d}", rows=10, columns=10),
                    patient_group="PT-A", split="train",
                    perceptual_hash="0" * 21,
                )
            )

        pairs = repo.find_near_duplicates(project.id)
        assert pairs
        assert pairs[0]["leaks_across_split"] is False

    def test_the_hash_reaches_the_export(self, repo, calibrated_case, annotator, project):
        from aria.io.exporters.csv_export import case_row
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        assert case_row(data, project, annotator)["perceptual_hash"]


class TestWhatWasLeftOutSaysWhy:
    """FR 071. An exclusion with no reason reads as an oversight."""

    def test_archiving_records_the_reason(self, repo, imported_case):
        repo.archive_case(imported_case.id, "Cropped below the antegonial region.")
        case = repo.get_case(imported_case.id)

        assert case.archived
        assert "antegonial" in case.exclusion_reason

    def test_the_reason_can_be_set_on_its_own(self, repo, imported_case):
        repo.set_exclusion_reason(imported_case.id, "Awaiting a reference scan.")
        assert repo.get_case(imported_case.id).exclusion_reason == (
            "Awaiting a reference scan."
        )

    def test_an_included_case_has_no_reason(self, imported_case):
        assert imported_case.exclusion_reason == ""

    def test_the_reason_reaches_the_export(self, repo, calibrated_case, annotator, project):
        from aria.io.exporters.csv_export import case_row
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        assert "exclusion_reason" in case_row(data, project, annotator)


class TestADatasetReleaseIsFrozen:
    """FR 046 and FR 072. A release is what a model is trained from, so the
    schema cannot be allowed to move underneath it."""

    def _release(self, repo, project, admin, schema):
        repo.set_actor(admin)
        return repo.create_dataset_release(
            project.id, "v1.0", schema,
            notes="First release.",
            inclusion_rules="Dentate adults with both antegonial regions visible.",
        )

    def test_a_release_records_what_it_contains(
        self, repo, project, admin, schema, imported_case
    ):
        release = self._release(repo, project, admin, schema)

        assert release["name"] == "v1.0"
        assert release["n_cases"] >= 1
        assert release["sha256"]
        assert release["inclusion_rules"]

    def test_the_schema_is_copied_rather_than_referenced(
        self, repo, project, admin, schema, imported_case
    ):
        """The whole point. Two models trained on one named dataset have to
        have been trained on the same data."""
        import json

        release = self._release(repo, project, admin, schema)
        before = repo.get_dataset_release(release["id"])["schema"]["point_tolerance_px"]

        schema.point_tolerance_px = 99.0
        project.schema_json = json.dumps(schema.to_dict())
        repo.update_project(project, "tolerance changed")

        after = repo.get_dataset_release(release["id"])["schema"]["point_tolerance_px"]
        assert after == before
        assert after != 99.0

    def test_the_split_is_recorded_case_by_case(
        self, repo, project, admin, schema, imported_case
    ):
        release = self._release(repo, project, admin, schema)
        stored = repo.get_dataset_release(release["id"])

        assert stored["cases"]
        for row in stored["cases"]:
            assert "split" in row
            assert "patient_group" in row

    def test_a_release_states_whether_it_is_patient_independent(
        self, repo, project, admin, schema
    ):
        from aria.core.models import Case, SourceImage

        repo.set_actor(admin)
        for index, split in enumerate(("train", "test")):
            repo.create_case(
                Case(
                    project_id=project.id, pseudonym=f"LEAK-{index}",
                    source=SourceImage(sha256=f"{index:064d}", rows=10, columns=10),
                    patient_group="PT-A", split=split,
                )
            )

        release = repo.create_dataset_release(project.id, "v1.1", schema)
        assert release["patient_independent"] is False
        assert release["splits"]["n_leaking_groups"] == 1

    def test_a_release_is_cut_even_when_it_has_defects(
        self, repo, project, admin, schema
    ):
        """Refusing would push the work somewhere with no record of it. A
        release that states its own defects is more useful than none."""
        from aria.core.models import Case, SourceImage

        repo.set_actor(admin)
        for index, split in enumerate(("train", "test")):
            repo.create_case(
                Case(
                    project_id=project.id, pseudonym=f"BAD-{index}",
                    source=SourceImage(sha256=f"{index:064d}", rows=10, columns=10),
                    patient_group="PT-A", split=split,
                )
            )

        release = repo.create_dataset_release(project.id, "v1.2", schema)
        assert release["id"]
        assert release["patient_independent"] is False

    def test_a_release_names_exclusions_that_give_no_reason(
        self, repo, project, admin, schema, imported_case
    ):
        repo.archive_case(imported_case.id, "")
        release = self._release(repo, project, admin, schema)

        assert imported_case.pseudonym in release["excluded_without_a_reason"]

    def test_an_excluded_case_with_a_reason_is_not_named(
        self, repo, project, admin, schema, imported_case
    ):
        repo.archive_case(imported_case.id, "Positioning error, ramus clipped.")
        release = self._release(repo, project, admin, schema)

        assert release["excluded_without_a_reason"] == []
        assert release["n_excluded"] == 1

    def test_the_checksum_changes_when_the_contents_do(
        self, repo, project, admin, schema, imported_case
    ):
        from aria.core.models import Case, SourceImage

        first = self._release(repo, project, admin, schema)
        repo.create_case(
            Case(
                project_id=project.id, pseudonym="EXTRA",
                source=SourceImage(sha256="f" * 64, rows=10, columns=10),
            )
        )
        second = repo.create_dataset_release(project.id, "v2.0", schema)

        assert first["sha256"] != second["sha256"]

    def test_releases_are_listed(self, repo, project, admin, schema, imported_case):
        self._release(repo, project, admin, schema)
        repo.create_dataset_release(project.id, "v2.0", schema)

        names = [r["name"] for r in repo.list_dataset_releases(project.id)]
        assert set(names) == {"v1.0", "v2.0"}

    def test_cutting_a_release_is_audited(
        self, repo, project, admin, schema, imported_case
    ):
        self._release(repo, project, admin, schema)
        details = [r.detail for r in repo.audit_records(limit=10)]
        assert any("Dataset release v1.0" in (d or "") for d in details)


class TestAThicknessLineCrossesTheCortexSquarely:
    """FR 054. Cortical width measured off perpendicular is overstated by one
    over the cosine of the deviation, and nothing in the image shows it."""

    def test_a_square_line_is_not_flagged(self, repo, calibrated_case, annotator):
        from aria.core.schema import ProjectSchema
        from aria.core.validation import validate_for_submission
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        result = validate_for_submission(data, ProjectSchema())

        assert not [
            i for i in result.issues
            if i.code == "thickness_line_not_perpendicular"
        ]

    def test_a_tilted_line_is_flagged_with_the_error_it_causes(
        self, repo, calibrated_case, annotator
    ):
        import math

        from aria.core.schema import ProjectSchema, Side
        from aria.core.validation import validate_for_submission
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        line = data.present("mcw_line", Side.RIGHT)
        start, end = line.points()
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        angle = math.radians(40)
        line.set_points([
            start,
            (start[0] + length * math.sin(angle), start[1] + length * math.cos(angle)),
        ])
        repo.save_annotations(
            [line], expected_counter=data.annotation_set.edit_counter,
            case_id=calibrated_case.id,
        )

        again = repo.load_set_data(data.annotation_set.id)
        flagged = [
            i for i in validate_for_submission(again, ProjectSchema()).issues
            if i.code == "thickness_line_not_perpendicular"
        ]
        assert flagged, "A line 40 degrees off perpendicular passed"
        assert "per cent" in flagged[0].message

    def test_it_is_a_warning_rather_than_a_refusal(
        self, repo, calibrated_case, annotator
    ):
        import math

        from aria.core.schema import ProjectSchema, Side
        from aria.core.validation import validate_for_submission
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        line = data.present("mcw_line", Side.RIGHT)
        start, end = line.points()
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        angle = math.radians(40)
        line.set_points([
            start,
            (start[0] + length * math.sin(angle), start[1] + length * math.cos(angle)),
        ])
        repo.save_annotations(
            [line], expected_counter=data.annotation_set.edit_counter,
            case_id=calibrated_case.id,
        )

        result = validate_for_submission(
            repo.load_set_data(data.annotation_set.id), ProjectSchema()
        )
        flagged = [
            i for i in result.issues
            if i.code == "thickness_line_not_perpendicular"
        ]
        assert all(i.severity == "warning" for i in flagged)

    def test_the_tolerance_is_tight_enough_to_matter(self):
        """Fifteen degrees inflates a width by three and a half per cent, which
        is already the order of the differences this literature reports."""
        from aria.core.schema import ProjectSchema

        assert ProjectSchema().line_perpendicularity_tolerance_deg <= 20.0
