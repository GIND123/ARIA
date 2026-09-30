"""Patient level factors that confound mandibular bone density.

A cortical width or a Klemetti grade read off a radiograph does not mean the
same thing in a woman of seventy and a man of thirty. Age and sex are the two
that dominate, and after them come menopausal status, body mass, smoking,
medication that acts on bone, and whether the patient is already known to be
osteoporotic. A model trained on the indices alone learns the confounders as if
they were signal; a model given them can be asked to account for them.

Nothing here is an identifier. There is no name, no date of birth, no hospital
number, and no free date of any kind. Age is held in whole years and anything
of ninety or above is held as ninety, because in a small study a single
centenarian is identifying on their own. What is recorded is the handful of
facts a reader needs to interpret an index, and no more.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

#: Ages at or above this are recorded as this value. A published study with one
#: patient of a hundred and three has effectively named them.
AGE_CEILING_YEARS = 90

#: Below this an age is almost certainly a data entry error, and a mandible
#: this young is still growing, which changes what a cortical index means.
AGE_FLOOR_YEARS = 1


class Sex(str, Enum):
    """Sex recorded for analysis, which is not the same as gender identity.

    Bone density references are published against sex, so this is the variable
    the indices have to be interpreted against. Where the two differ, or where
    somebody would rather not say, the value is recorded as not stated rather
    than guessed.
    """

    FEMALE = "female"
    MALE = "male"
    INTERSEX = "intersex"
    NOT_STATED = "not_stated"

    @property
    def display(self) -> str:
        return {
            Sex.FEMALE: "Female",
            Sex.MALE: "Male",
            Sex.INTERSEX: "Intersex",
            Sex.NOT_STATED: "Not stated",
        }[self]


class MenopausalStatus(str, Enum):
    """The single largest step change in female cortical bone."""

    PREMENOPAUSAL = "premenopausal"
    PERIMENOPAUSAL = "perimenopausal"
    POSTMENOPAUSAL = "postmenopausal"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"

    @property
    def display(self) -> str:
        return {
            MenopausalStatus.PREMENOPAUSAL: "Premenopausal",
            MenopausalStatus.PERIMENOPAUSAL: "Perimenopausal",
            MenopausalStatus.POSTMENOPAUSAL: "Postmenopausal",
            MenopausalStatus.NOT_APPLICABLE: "Not applicable",
            MenopausalStatus.UNKNOWN: "Unknown",
        }[self]


class SmokingStatus(str, Enum):
    NEVER = "never"
    FORMER = "former"
    CURRENT = "current"
    UNKNOWN = "unknown"

    @property
    def display(self) -> str:
        return {
            SmokingStatus.NEVER: "Never smoked",
            SmokingStatus.FORMER: "Former smoker",
            SmokingStatus.CURRENT: "Current smoker",
            SmokingStatus.UNKNOWN: "Unknown",
        }[self]


class BoneStatus(str, Enum):
    """What is already known about this patient's skeleton, if anything."""

    NORMAL = "normal"
    OSTEOPENIA = "osteopenia"
    OSTEOPOROSIS = "osteoporosis"
    NOT_ASSESSED = "not_assessed"
    UNKNOWN = "unknown"

    @property
    def display(self) -> str:
        return {
            BoneStatus.NORMAL: "Normal",
            BoneStatus.OSTEOPENIA: "Osteopenia",
            BoneStatus.OSTEOPOROSIS: "Osteoporosis",
            BoneStatus.NOT_ASSESSED: "Not assessed",
            BoneStatus.UNKNOWN: "Unknown",
        }[self]


class BoneMedication(str, Enum):
    """Medication that changes cortical bone, and therefore changes the index."""

    NONE = "none"
    BISPHOSPHONATE = "bisphosphonate"
    DENOSUMAB = "denosumab"
    HORMONE_THERAPY = "hormone_therapy"
    CORTICOSTEROID = "corticosteroid"
    OTHER = "other"
    UNKNOWN = "unknown"

    @property
    def display(self) -> str:
        return {
            BoneMedication.NONE: "None",
            BoneMedication.BISPHOSPHONATE: "Bisphosphonate",
            BoneMedication.DENOSUMAB: "Denosumab",
            BoneMedication.HORMONE_THERAPY: "Hormone therapy",
            BoneMedication.CORTICOSTEROID: "Corticosteroid",
            BoneMedication.OTHER: "Other",
            BoneMedication.UNKNOWN: "Unknown",
        }[self]


@dataclass
class PatientFactors:
    """Everything recorded about the patient, and nothing that identifies them."""

    age_years: int | None = None
    sex: str = Sex.NOT_STATED.value
    menopausal_status: str = MenopausalStatus.UNKNOWN.value
    height_cm: float | None = None
    weight_kg: float | None = None
    smoking_status: str = SmokingStatus.UNKNOWN.value
    bone_status: str = BoneStatus.UNKNOWN.value
    #: Lowest T score from densitometry, with the site it was measured at.
    #: This is the reference standard a radiographic index is compared against.
    dxa_t_score: float | None = None
    dxa_site: str = ""
    medication: str = BoneMedication.UNKNOWN.value
    notes: str = ""

    # -- derived -------------------------------------------------------------

    @property
    def age_is_capped(self) -> bool:
        return self.age_years is not None and self.age_years >= AGE_CEILING_YEARS

    @property
    def bmi(self) -> float | None:
        """Body mass index, or nothing when either measurement is missing."""
        if not self.height_cm or not self.weight_kg or self.height_cm <= 0:
            return None
        metres = self.height_cm / 100.0
        return round(self.weight_kg / (metres * metres), 2)

    @property
    def is_recorded(self) -> bool:
        """True once anything at all has been entered."""
        return self != PatientFactors()

    @property
    def sex_enum(self) -> Sex:
        try:
            return Sex(self.sex)
        except ValueError:
            return Sex.NOT_STATED

    def summary_line(self) -> str:
        """What a reader needs beside an index, in one line."""
        if not self.is_recorded:
            return "No patient factors recorded."
        parts = []
        if self.age_years is not None:
            parts.append(
                f"{AGE_CEILING_YEARS}+ years" if self.age_is_capped
                else f"{self.age_years} years"
            )
        parts.append(self.sex_enum.display)
        if self.sex_enum is Sex.FEMALE:
            status = MenopausalStatus(self.menopausal_status)
            if status not in (MenopausalStatus.UNKNOWN, MenopausalStatus.NOT_APPLICABLE):
                parts.append(status.display.lower())
        if self.bmi is not None:
            parts.append(f"BMI {self.bmi:g}")
        if self.dxa_t_score is not None:
            parts.append(f"T score {self.dxa_t_score:+g}")
        return ", ".join(parts) + "."

    # -- validation ----------------------------------------------------------

    def normalise(self) -> list:
        """Put the record into a state that is safe to store and export.

        Returns the notes a person should see. Nothing raises: a factor sheet
        is filled in beside a case, often from a record that is itself
        incomplete, and refusing the lot because one number is odd would push
        the work into a spreadsheet nobody audits.
        """
        notes: list = []

        if self.age_years is not None:
            age = int(self.age_years)
            if age >= AGE_CEILING_YEARS:
                if age > AGE_CEILING_YEARS:
                    notes.append(
                        f"An age of {age} is recorded as {AGE_CEILING_YEARS}, "
                        f"because an exact age this high identifies a patient "
                        f"on its own in a study of any normal size."
                    )
                age = AGE_CEILING_YEARS
            elif age < AGE_FLOOR_YEARS:
                notes.append(
                    f"An age of {age} years is not plausible and has been "
                    f"cleared."
                )
                age = None
            self.age_years = age

        if self.sex_enum is not Sex.FEMALE and self.menopausal_status not in (
            MenopausalStatus.NOT_APPLICABLE.value, MenopausalStatus.UNKNOWN.value,
        ):
            notes.append(
                "Menopausal status applies to female patients and has been "
                "set to not applicable."
            )
            self.menopausal_status = MenopausalStatus.NOT_APPLICABLE.value

        for name, low, high in (
            ("height_cm", 50.0, 250.0),
            ("weight_kg", 10.0, 400.0),
            ("dxa_t_score", -8.0, 6.0),
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if not (low <= float(value) <= high):
                notes.append(
                    f"A {name.replace('_', ' ')} of {value:g} is outside the "
                    f"plausible range {low:g} to {high:g} and has been cleared."
                )
                setattr(self, name, None)

        return notes

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bmi"] = self.bmi
        d["age_is_capped"] = self.age_is_capped
        return d

    @classmethod
    def from_dict(cls, data: dict | None) -> "PatientFactors":
        if not data:
            return cls()
        data = dict(data)
        for derived in ("bmi", "age_is_capped"):
            data.pop(derived, None)
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})
