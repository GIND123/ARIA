"""Reading an index against the population it came from.

A mental index of 3.1 mm is not low or normal on its own. Palaskar and
Ambildhok (2023) make the point plainly: the published Indian means are
consistently lower than the Western ones, so scoring an Indian patient against
a Western reference finds low bone where there is none. The same 3.1 mm sits a
quarter of a standard deviation below the mean for an Indian woman and far
below a Western mean of 5.71.

So ARIA holds several published sets, applies none by default, and says which
one produced a comparison whenever it reports one.
"""

from __future__ import annotations

import pytest

from aria.core.references import (
    REFERENCE_SETS,
    REFERENCED_INDICES,
    THIN_CORTEX_MM,
    choose_set_for,
    compare,
    get_reference_set,
    thin_cortex_flag,
)


class TestTheSetsAreQuotedWithTheirSource:

    def test_every_set_names_its_population_and_source(self):
        for key, reference in REFERENCE_SETS.items():
            assert reference.key == key
            assert reference.population, f"{key} does not say who it describes"
            assert reference.source, f"{key} does not say where it came from"

    def test_the_indian_sets_carry_the_published_figures(self):
        """Table 1 of the paper, for the cohort the references were built on."""
        pooled = get_reference_set("palaskar_2023_indian")
        assert pooled.for_index("MI").mean == pytest.approx(3.4)
        assert pooled.for_index("PMI").mean == pytest.approx(0.3)
        assert pooled.for_index("GI").mean == pytest.approx(1.2)
        assert pooled.for_index("AI").mean == pytest.approx(2.7)

    def test_the_sexes_are_kept_apart(self):
        """Women had significantly lower MI, PMI and AI in that cohort, so
        averaging the two throws away the difference."""
        women = get_reference_set("palaskar_2023_indian_female")
        men = get_reference_set("palaskar_2023_indian_male")

        assert women.for_index("MI").mean < men.for_index("MI").mean
        assert women.for_index("AI").mean < men.for_index("AI").mean

    def test_every_set_covers_only_indices_it_claims(self):
        for reference in REFERENCE_SETS.values():
            for index in reference.values:
                assert index in REFERENCED_INDICES


class TestComparingAValue:

    def test_a_value_below_the_mean_scores_negative(self):
        result = compare("MI", 3.1, get_reference_set("palaskar_2023_indian"))

        assert result["below_reference"] is True
        assert result["z_score"] == pytest.approx((3.1 - 3.4) / 0.6)
        assert result["z_score"] < 0

    def test_a_value_above_the_mean_scores_positive(self):
        result = compare("MI", 4.0, get_reference_set("palaskar_2023_indian"))
        assert result["below_reference"] is False
        assert result["z_score"] > 0

    def test_the_comparison_says_which_reference_produced_it(self):
        """A Z score whose reference is not stated cannot be interpreted."""
        result = compare("MI", 3.1, get_reference_set("palaskar_2023_indian"))

        assert result["reference_set"] == "palaskar_2023_indian"
        assert "Indian" in result["population"]
        assert "Palaskar" in result["source"]

    def test_the_same_value_reads_differently_against_different_populations(self):
        """The whole reason the sets are kept separate."""
        indian = compare("MI", 3.1, get_reference_set("palaskar_2023_indian_female"))
        western = compare("MI", 3.1, get_reference_set("dagistan_2010_western"))

        assert indian["mean"] == pytest.approx(3.2)
        assert western["mean"] == pytest.approx(5.71)
        assert abs(indian["value"] - indian["mean"]) < abs(
            western["value"] - western["mean"]
        )

    def test_a_mean_without_dispersion_gives_no_score(self):
        """The quoting paper does not reproduce the Western dispersion, so a Z
        score against it would be invented."""
        result = compare("MI", 3.1, get_reference_set("dagistan_2010_western"))

        assert result["mean"] is not None
        assert result["z_score"] is None
        assert "dispersion" in result["note"]

    def test_no_reference_means_no_verdict(self):
        result = compare("MI", 3.1, None)

        assert result["z_score"] is None
        assert result["reference_set"] == ""
        assert "stands on its own" in result["note"]

    def test_an_index_the_set_does_not_cover_says_so(self):
        result = compare("MCI", 2.0, get_reference_set("palaskar_2023_indian"))
        assert result["z_score"] is None
        assert "does not cover" in result["note"]


class TestChoosingASetForAPatient:

    @pytest.mark.parametrize("sex,expected", [
        ("female", "palaskar_2023_indian_female"),
        ("male", "palaskar_2023_indian_male"),
    ])
    def test_the_patients_sex_narrows_the_set(self, sex, expected):
        assert choose_set_for("palaskar_2023_indian", sex).key == expected

    def test_an_unrecorded_sex_falls_back_to_the_pooled_set(self):
        assert choose_set_for("palaskar_2023_indian", "").key == "palaskar_2023_indian"
        assert choose_set_for("palaskar_2023_indian", "not_stated").key == (
            "palaskar_2023_indian"
        )

    def test_a_set_with_no_split_is_returned_as_it_is(self):
        assert choose_set_for("dagistan_2010_western", "female").key == (
            "dagistan_2010_western"
        )

    def test_an_unknown_set_is_nothing_rather_than_a_guess(self):
        assert choose_set_for("no_such_set", "female") is None


class TestNothingIsAppliedUnlessItIsChosen:

    def test_a_new_project_has_no_reference_set(self):
        """Picking one on an investigator's behalf is a conclusion they never
        agreed to."""
        from aria.core.schema import ProjectSchema

        assert ProjectSchema().reference_set == ""

    def test_the_choice_is_stored_with_the_project(self):
        from aria.core.schema import ProjectSchema

        schema = ProjectSchema()
        schema.reference_set = "palaskar_2023_indian"
        assert ProjectSchema.from_dict(schema.to_dict()).reference_set == (
            "palaskar_2023_indian"
        )


class TestTheThinCortexPrompt:
    """The one rule that does not depend on choosing a population."""

    @pytest.mark.parametrize("value", [2.0, 2.9, 3.0])
    def test_a_thin_cortex_is_flagged(self, value):
        message = thin_cortex_flag(value)
        assert message
        assert "densitometry" in message
        assert "not a diagnosis" in message

    @pytest.mark.parametrize("value", [3.1, 4.0, 6.0])
    def test_a_normal_cortex_is_not(self, value):
        assert thin_cortex_flag(value) == ""

    def test_nothing_measured_means_nothing_said(self):
        assert thin_cortex_flag(None) == ""

    def test_the_threshold_is_the_published_one(self):
        assert THIN_CORTEX_MM == 3.0


class TestTheComparisonReachesTheExport:

    def test_rows_carry_the_comparison_when_a_set_is_chosen(
        self, repo, calibrated_case, annotator, project
    ):
        from aria.core.measurements import MeasurementEngine, measurements_to_rows
        from aria.core.schema import ProjectSchema
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        rows = measurements_to_rows(
            MeasurementEngine(ProjectSchema()).compute(data),
            get_reference_set("palaskar_2023_indian"),
        )
        compared = [r for r in rows if r["reference_set"]]

        assert compared, "No row was compared against the chosen reference"
        assert all(r["reference_population"] for r in compared)

    def test_rows_carry_nothing_when_no_set_is_chosen(
        self, repo, calibrated_case, annotator, project
    ):
        from aria.core.measurements import MeasurementEngine, measurements_to_rows
        from aria.core.schema import ProjectSchema
        from tests.conftest import build_annotation_set

        data = build_annotation_set(repo, calibrated_case, annotator)
        rows = measurements_to_rows(MeasurementEngine(ProjectSchema()).compute(data))

        assert all(r["reference_set"] == "" for r in rows)
        assert all(r["reference_z_score"] is None for r in rows)

    def test_every_reference_column_is_documented(self):
        from aria.io.exporters.csv_export import DATA_DICTIONARY

        documented = {
            n for n, table, *_ in DATA_DICTIONARY if table == "measurements_long"
        }
        for column in (
            "reference_set", "reference_population", "reference_mean",
            "reference_sd", "reference_z_score", "below_reference",
        ):
            assert column in documented, f"{column} is exported but undocumented"
