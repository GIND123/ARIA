"""The confounders, and the promise that they are not identifiers.

An index read off a radiograph means something different at thirty and at
seventy, in a man and in a postmenopausal woman, on and off a bisphosphonate.
Recording those beside the case is what lets an analysis allow for them instead
of a model learning them by accident.

The risk in recording anything about a patient is that it identifies them. So
the other half of this file is about what is deliberately absent: no name, no
date of birth, no free date at all, and an age ceiling, because a single
patient of a hundred and three in a study of two hundred has effectively been
named.
"""

from __future__ import annotations

import pytest

from aria.core.patient import (
    AGE_CEILING_YEARS,
    BoneMedication,
    BoneStatus,
    MenopausalStatus,
    PatientFactors,
    Sex,
    SmokingStatus,
)


class TestWhatIsRecorded:

    def test_an_empty_sheet_says_nothing(self):
        factors = PatientFactors()
        assert not factors.is_recorded
        assert factors.summary_line() == "No patient factors recorded."

    def test_the_summary_reads_as_a_sentence(self):
        factors = PatientFactors(
            age_years=67, sex=Sex.FEMALE.value,
            menopausal_status=MenopausalStatus.POSTMENOPAUSAL.value,
            height_cm=158.0, weight_kg=54.0, dxa_t_score=-2.9,
        )
        line = factors.summary_line()

        assert "67 years" in line
        assert "Female" in line
        assert "postmenopausal" in line
        assert "BMI" in line
        assert "-2.9" in line

    def test_body_mass_index_is_computed(self):
        factors = PatientFactors(height_cm=170.0, weight_kg=72.0)
        assert factors.bmi == pytest.approx(24.91, abs=0.01)

    def test_body_mass_index_needs_both_measurements(self):
        assert PatientFactors(height_cm=170.0).bmi is None
        assert PatientFactors(weight_kg=72.0).bmi is None
        assert PatientFactors().bmi is None

    def test_the_vocabularies_cover_what_a_study_needs(self):
        """Each of these changes how a cortical index should be read."""
        assert {m.value for m in Sex} >= {"female", "male", "not_stated"}
        assert {m.value for m in MenopausalStatus} >= {
            "premenopausal", "postmenopausal", "unknown"
        }
        assert {m.value for m in SmokingStatus} >= {"never", "former", "current"}
        assert {m.value for m in BoneStatus} >= {"osteoporosis", "osteopenia"}
        assert {m.value for m in BoneMedication} >= {"bisphosphonate", "corticosteroid"}


class TestNothingHereIdentifiesAnybody:
    """FR 047, FR 052. The sheet has to be safe to export."""

    def test_there_is_no_field_for_a_name_or_a_date(self):
        fields = set(PatientFactors.__dataclass_fields__)
        for forbidden in (
            "name", "patient_name", "date_of_birth", "dob", "birth_date",
            "hospital_number", "mrn", "nhs_number", "address", "postcode",
            "telephone", "email", "acquisition_date",
        ):
            assert forbidden not in fields, (
                f"The patient sheet has a {forbidden} field, which identifies"
            )

    def test_an_age_above_the_ceiling_is_brought_down_to_it(self):
        factors = PatientFactors(age_years=103)
        notes = factors.normalise()

        assert factors.age_years == AGE_CEILING_YEARS
        assert factors.age_is_capped
        assert notes and "identifies" in notes[0]

    def test_an_age_at_the_ceiling_is_kept_but_marked(self):
        factors = PatientFactors(age_years=AGE_CEILING_YEARS)
        notes = factors.normalise()

        assert factors.age_years == AGE_CEILING_YEARS
        assert factors.age_is_capped
        assert not notes, "Nothing was changed, so nothing needed saying"

    def test_an_ordinary_age_is_left_alone(self):
        factors = PatientFactors(age_years=67)
        assert factors.normalise() == []
        assert factors.age_years == 67
        assert not factors.age_is_capped

    def test_the_ceiling_is_low_enough_to_protect(self):
        """Ninety is the usual threshold, because above it the population is
        small enough that an exact age narrows to very few people."""
        assert AGE_CEILING_YEARS <= 90

    def test_the_summary_does_not_state_an_exact_capped_age(self):
        factors = PatientFactors(age_years=103)
        factors.normalise()
        assert "90+" in factors.summary_line()
        assert "103" not in factors.summary_line()


class TestImplausibleEntriesAreClearedRatherThanStored:
    """A sheet copied from an incomplete record should never refuse to save."""

    def test_an_impossible_age_is_cleared(self):
        factors = PatientFactors(age_years=0)
        notes = factors.normalise()
        assert factors.age_years is None
        assert notes

    @pytest.mark.parametrize("field,value", [
        ("height_cm", 900.0), ("height_cm", 3.0),
        ("weight_kg", 900.0), ("weight_kg", 2.0),
        ("dxa_t_score", -20.0), ("dxa_t_score", 40.0),
    ])
    def test_an_implausible_measurement_is_cleared_with_a_note(self, field, value):
        factors = PatientFactors(**{field: value})
        notes = factors.normalise()

        assert getattr(factors, field) is None
        assert notes and "plausible" in notes[0]

    def test_normalising_never_raises(self):
        """A form that will not save gets worked around, not corrected."""
        PatientFactors(
            age_years=-5, height_cm=0.1, weight_kg=9999.0, dxa_t_score=99.0,
            sex="nonsense", menopausal_status="nonsense",
        ).normalise()

    def test_menopausal_status_is_cleared_for_patients_it_cannot_apply_to(self):
        factors = PatientFactors(
            sex=Sex.MALE.value,
            menopausal_status=MenopausalStatus.POSTMENOPAUSAL.value,
        )
        notes = factors.normalise()

        assert factors.menopausal_status == MenopausalStatus.NOT_APPLICABLE.value
        assert notes

    def test_a_female_patient_keeps_her_menopausal_status(self):
        factors = PatientFactors(
            sex=Sex.FEMALE.value,
            menopausal_status=MenopausalStatus.POSTMENOPAUSAL.value,
        )
        factors.normalise()
        assert factors.menopausal_status == MenopausalStatus.POSTMENOPAUSAL.value


class TestTheSheetSurvivesBeingStored:

    def test_it_round_trips(self):
        factors = PatientFactors(
            age_years=67, sex=Sex.FEMALE.value,
            menopausal_status=MenopausalStatus.POSTMENOPAUSAL.value,
            height_cm=158.0, weight_kg=54.0,
            smoking_status=SmokingStatus.FORMER.value,
            bone_status=BoneStatus.OSTEOPOROSIS.value,
            dxa_t_score=-2.9, dxa_site="femoral neck",
            medication=BoneMedication.BISPHOSPHONATE.value,
            notes="Referred from the fracture clinic.",
        )
        assert PatientFactors.from_dict(factors.to_dict()) == factors

    def test_it_reaches_the_database_and_comes_back(self, repo, imported_case):
        factors = PatientFactors(age_years=67, sex=Sex.FEMALE.value, weight_kg=54.0)
        factors.normalise()

        repo.set_patient_factors(imported_case.id, factors)
        again = repo.get_case(imported_case.id)

        assert again.patient.age_years == 67
        assert again.patient.sex == Sex.FEMALE.value
        assert again.patient.weight_kg == pytest.approx(54.0)

    def test_recording_it_is_audited(self, repo, imported_case):
        """An age changed after the indices were computed changes what those
        indices mean, so it belongs in the history like any clinical change."""
        factors = PatientFactors(age_years=67)
        repo.set_patient_factors(imported_case.id, factors)

        events = [r.event for r in repo.audit_records(limit=5)]
        assert "patient_factors_recorded" in events

    def test_a_case_with_no_sheet_reads_as_empty(self, imported_case):
        assert not imported_case.patient.is_recorded


class TestTheFactorsReachTheExport:

    def test_the_case_row_carries_them(self, repo, calibrated_case, annotator, project):
        from tests.conftest import build_annotation_set
        from aria.io.exporters.csv_export import case_row

        factors = PatientFactors(
            age_years=67, sex=Sex.FEMALE.value,
            menopausal_status=MenopausalStatus.POSTMENOPAUSAL.value,
            height_cm=158.0, weight_kg=54.0, dxa_t_score=-2.9,
            dxa_site="femoral neck",
        )
        factors.normalise()
        repo.set_patient_factors(calibrated_case.id, factors)

        data = build_annotation_set(repo, repo.get_case(calibrated_case.id), annotator)
        row = case_row(data, project, annotator)

        assert row["patient_age_years"] == 67
        assert row["patient_sex"] == "female"
        assert row["patient_menopausal_status"] == "postmenopausal"
        assert row["patient_bmi"] == pytest.approx(21.63, abs=0.01)
        assert row["patient_dxa_t_score"] == pytest.approx(-2.9)
        assert row["patient_dxa_site"] == "femoral neck"

    def test_every_factor_column_is_documented(self):
        """An exported number nobody can interpret is not evidence (FR 049)."""
        from aria.io.exporters.csv_export import CASE_FIELDS, DATA_DICTIONARY

        documented = {n for n, table, *_ in DATA_DICTIONARY if table == "cases"}
        for column in CASE_FIELDS:
            assert column in documented, f"{column} is exported but undocumented"

    def test_the_age_ceiling_is_explained_in_the_dictionary(self):
        """A reader has to know that 90 can mean older than 90, or they will
        treat the ceiling as a real distribution."""
        from aria.io.exporters.csv_export import DATA_DICTIONARY

        entry = next(
            d for n, t, *d in DATA_DICTIONARY
            if n == "patient_age_years" and t == "cases"
        )
        assert "90" in entry[-1]

    def test_an_unrecorded_sheet_exports_as_empty_not_as_zero(self, repo, calibrated_case, annotator, project):
        """Zero would be read as an age, a weight and a T score of zero."""
        from tests.conftest import build_annotation_set
        from aria.io.exporters.csv_export import case_row

        data = build_annotation_set(repo, calibrated_case, annotator)
        row = case_row(data, project, annotator)

        assert row["patient_age_years"] is None
        assert row["patient_bmi"] is None
        assert row["patient_dxa_t_score"] is None


class TestFreeTextIsScannedBeforeItLeaves:
    """The notes box is the one place an identifier can still be typed in.

    Everything else on the sheet is a number or a chosen value. Notes is free
    text, it is exported, and somebody will eventually paste a line out of a
    referral into it.
    """

    @pytest.mark.parametrize("note", [
        "Ring the daughter on 07700 900123",
        "Contact 555-123-4567 for the records",
        "Call +44 20 7946 0958 before reporting",
        "mother can be reached: (020) 7946 0958",
    ])
    def test_a_contact_number_in_the_notes_is_caught(self, note):
        """It used to be caught only in one national format, so a United
        Kingdom mobile went into exports unnoticed."""
        from aria.io.deident import scan_payload

        factors = PatientFactors(age_years=67, notes=note)
        findings = scan_payload({"patient": factors.to_dict()}, [])

        assert findings, f"A contact number passed the scan: {note!r}"
        assert any(f.kind == "phone" for f in findings)

    def test_an_email_in_the_notes_is_caught(self):
        from aria.io.deident import scan_payload

        factors = PatientFactors(notes="Results to jane.smith@example.com")
        assert scan_payload({"patient": factors.to_dict()}, [])

    @pytest.mark.parametrize("note", [
        "Referred from the fracture clinic.",
        "Widths 3.1, 2.8, 3.4, 2.9, 3.0 mm on the right.",
        "Reviewed 2026, 14 cases, 3 returned.",
        "Cortical margin clear, no endosteal resorption.",
        "",
    ])
    def test_an_ordinary_note_is_not_flagged(self, note):
        """A scanner that cries wolf gets switched off."""
        from aria.io.deident import scan_payload

        factors = PatientFactors(age_years=67, notes=note)
        assert not scan_payload({"patient": factors.to_dict()}, [])

    def test_identifiers_of_the_data_are_not_mistaken_for_people(self):
        """Annotation and case identifiers carry long digit runs inside a
        token. Flagging those would make the scan useless."""
        from aria.io.deident import scan_payload

        payload = {
            "id": "ann_36c039a3f19742399213",
            "case_id": "cas_80bdd29b79d147279244",
            "sop_instance_uid": "1.2.826.0.1.3680043.10.9999.1.2.3",
        }
        assert not scan_payload(payload, [])

    def test_a_clean_sheet_passes_the_scan(self):
        from aria.io.deident import scan_payload

        factors = PatientFactors(
            age_years=67, sex=Sex.FEMALE.value,
            menopausal_status=MenopausalStatus.POSTMENOPAUSAL.value,
            height_cm=158.0, weight_kg=54.0, dxa_t_score=-2.9,
            dxa_site="femoral neck",
            smoking_status=SmokingStatus.FORMER.value,
            bone_status=BoneStatus.OSTEOPOROSIS.value,
            medication=BoneMedication.BISPHOSPHONATE.value,
        )
        assert not scan_payload({"patient": factors.to_dict()}, [])
