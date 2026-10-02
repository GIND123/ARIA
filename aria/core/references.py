"""Published reference values for the panoramic indices.

A mental index of 3.1 mm means nothing on its own. It means something once it
is read against the distribution it came from, and that distribution is not the
same everywhere: the published Indian means are consistently lower than the
Western ones, so scoring an Indian patient against a Western reference finds
low bone where there is none.

Each set below is quoted from its source with the population it describes, and
ARIA says which set a comparison used. Nothing is applied by default. Choosing
the reference is a study decision, and a tool that picks one quietly has made
that decision on the investigator's behalf.

Sources
-------
Palaskar and Ambildhok (2023), Journal of Oral Biology and Craniofacial
Research 13:150, reference values for an Indian population, developed on 130
adults aged 20 to 30 with normal densitometry and validated on 195 aged 40 to
60. Values for the Western populations are the figures that paper quotes from
Dagistan and Bilge (2010) and Devlin and Horner (2002).
"""

from __future__ import annotations

from dataclasses import dataclass

#: A cortex at or below this is the threshold the literature repeatedly puts
#: forward for referral, independent of any reference distribution.
THIN_CORTEX_MM = 3.0


@dataclass(frozen=True)
class ReferenceValue:
    """One index in one population, as published."""

    index: str
    mean: float
    sd: float | None = None
    unit: str = "mm"

    def z_score(self, value: float) -> float | None:
        """How many standard deviations a measurement sits from the mean.

        The sign is the one a reader expects: negative means thinner cortex
        than the reference, which is the direction of interest.
        """
        if self.sd in (None, 0):
            return None
        return (value - self.mean) / self.sd


@dataclass(frozen=True)
class ReferenceSet:
    """A published set of reference values, with where it came from."""

    key: str
    population: str
    source: str
    note: str
    values: dict

    def for_index(self, index: str) -> ReferenceValue | None:
        return self.values.get(index)


#: Indices these references cover. MCI is a grade rather than a measurement and
#: has no mean, so it is not here.
REFERENCED_INDICES = ("MI", "PMI", "GI", "AI")


REFERENCE_SETS = {
    "palaskar_2023_indian": ReferenceSet(
        key="palaskar_2023_indian",
        population="Indian adults",
        source=(
            "Palaskar and Ambildhok (2023), J Oral Biol Craniofac Res 13:150. "
            "Developed on 130 adults aged 20 to 30 with normal densitometry."
        ),
        note=(
            "The validation subset of 195 adults aged 40 to 60 gave medians of "
            "MI 3.50, PMI 0.27, GI 1.10 and AI 2.50. The paper reports that "
            "Western reference values are consistently higher, so scoring an "
            "Indian patient against them finds low bone that is not there."
        ),
        values={
            "MI": ReferenceValue("MI", 3.4, 0.6, "mm"),
            "PMI": ReferenceValue("PMI", 0.3, 0.04, ""),
            "GI": ReferenceValue("GI", 1.2, 0.4, "mm"),
            "AI": ReferenceValue("AI", 2.7, 0.5, "mm"),
        },
    ),
    "palaskar_2023_indian_female": ReferenceSet(
        key="palaskar_2023_indian_female",
        population="Indian adult women",
        source="Palaskar and Ambildhok (2023), table 1, female column.",
        note=(
            "Women had significantly lower MI, PMI and AI than men in that "
            "cohort, which is why the two are kept apart rather than averaged."
        ),
        values={
            "MI": ReferenceValue("MI", 3.2, 0.4, "mm"),
            "PMI": ReferenceValue("PMI", 0.3, 0.04, ""),
            "GI": ReferenceValue("GI", 1.2, 0.43, "mm"),
            "AI": ReferenceValue("AI", 2.5, 0.5, "mm"),
        },
    ),
    "palaskar_2023_indian_male": ReferenceSet(
        key="palaskar_2023_indian_male",
        population="Indian adult men",
        source="Palaskar and Ambildhok (2023), table 1, male column.",
        note="The male half of the same cohort.",
        values={
            "MI": ReferenceValue("MI", 3.7, 0.6, "mm"),
            "PMI": ReferenceValue("PMI", 0.3, 0.04, ""),
            "GI": ReferenceValue("GI", 1.2, 0.4, "mm"),
            "AI": ReferenceValue("AI", 2.9, 0.5, "mm"),
        },
    ),
    "dagistan_2010_western": ReferenceSet(
        key="dagistan_2010_western",
        population="Western adults",
        source=(
            "Dagistan and Bilge (2010), as quoted by Palaskar and Ambildhok "
            "(2023)."
        ),
        note=(
            "Quoted means only; the dispersion is not reproduced in the "
            "quoting paper, so no Z score is offered against this set."
        ),
        values={
            "MI": ReferenceValue("MI", 5.71, None, "mm"),
            "PMI": ReferenceValue("PMI", 0.35, None, ""),
            "GI": ReferenceValue("GI", 1.10, None, "mm"),
            "AI": ReferenceValue("AI", 3.41, None, "mm"),
        },
    ),
}

#: Nothing is applied unless a project chooses it. A silently applied reference
#: is a conclusion the investigator never agreed to.
DEFAULT_REFERENCE_SET = ""


def get_reference_set(key: str) -> ReferenceSet | None:
    return REFERENCE_SETS.get(key)


def choose_set_for(key: str, sex: str = "") -> ReferenceSet | None:
    """The chosen set, narrowed by sex where that set has a split.

    The published values differ between men and women for three of the four
    indices, so using the pooled figure for a patient whose sex is recorded
    throws away information that is already available.
    """
    base = REFERENCE_SETS.get(key)
    if base is None:
        return None
    suffix = {"female": "_female", "male": "_male"}.get(sex, "")
    if suffix:
        return REFERENCE_SETS.get(f"{key}{suffix}", base)
    return base


def compare(index: str, value: float, reference: ReferenceSet | None) -> dict:
    """Place one measured index against a reference set.

    Returns the comparison in full, including the source, so that a reader can
    see which distribution produced the verdict rather than taking the verdict
    on trust.
    """
    out = {
        "index": index,
        "value": value,
        "reference_set": reference.key if reference else "",
        "population": reference.population if reference else "",
        "source": reference.source if reference else "",
        "mean": None,
        "sd": None,
        "z_score": None,
        "below_reference": None,
        "note": "",
    }
    if reference is None:
        out["note"] = "No reference set chosen, so the value stands on its own."
        return out

    published = reference.for_index(index)
    if published is None:
        out["note"] = f"This reference set does not cover {index}."
        return out

    out["mean"] = published.mean
    out["sd"] = published.sd
    z = published.z_score(value)
    out["z_score"] = z
    out["below_reference"] = value < published.mean
    if z is None:
        out["note"] = (
            "The source quotes a mean without its dispersion, so the value can "
            "be compared with the mean but not scored against it."
        )
    return out


def thin_cortex_flag(mi_mm: float | None) -> str:
    """The one rule that does not depend on choosing a population.

    A mandibular cortex of three millimetres or less is the threshold the
    literature repeatedly puts forward for referral for densitometry. It is a
    prompt to investigate, not a diagnosis, and it is only meaningful from a
    calibration that has been checked.
    """
    if mi_mm is None:
        return ""
    if mi_mm <= THIN_CORTEX_MM:
        return (
            f"The cortex measures {mi_mm:.2f} mm, at or below the "
            f"{THIN_CORTEX_MM:.0f} mm commonly used as a threshold for "
            f"referring a patient for densitometry. This is a prompt to "
            f"investigate, not a diagnosis."
        )
    return ""


#: Which measurement each published index corresponds to. The paper's Mental
#: Index is the mandibular cortical width measured at the mental foramen, which
#: ARIA records under its own name; the aliases are kept so a reader of either
#: literature finds what they expect.
INDEX_FOR_MEASUREMENT = {
    "mandibular_cortical_width": "MI",
    "pmi_superior": "PMI",
    "pmi_inferior": "PMI",
    "antegonial_index": "AI",
    "gonial_index": "GI",
}


def compare_measurement(measurement, reference: ReferenceSet | None) -> dict:
    """Place one computed measurement against a reference set.

    A ratio is compared on its ratio, a width on its millimetres. A width with
    no validated calibration has no millimetres to compare, and is returned
    uncompared rather than compared against its pixel count.
    """
    index = INDEX_FOR_MEASUREMENT.get(getattr(measurement, "kind", ""))
    if index is None:
        return {}

    if index == "PMI":
        value = getattr(measurement, "value_ratio", None)
    else:
        value = getattr(measurement, "value_mm", None)
    if value is None:
        return {
            "index": index, "value": None, "reference_set": "",
            "z_score": None, "below_reference": None,
            "note": (
                "No millimetre value, so there is nothing to compare. A width "
                "needs a validated calibration before it can be read against a "
                "published distribution."
            ),
        }
    return compare(index, float(value), reference)
