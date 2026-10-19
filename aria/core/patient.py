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


class ReferenceModality(str, Enum):
    """How the reference outcome was obtained.

    An OPG does not measure bone mineral density. Any claim that it predicts
    one rests entirely on what it was trained against, so the reference has to
    say what it is.
    """

    NONE = "none"
    DXA = "dxa"
    QCT = "quantitative_ct"
    MICRO_CT = "micro_ct"
    ULTRASOUND = "quantitative_ultrasound"
    OTHER = "other"

    @property
    def display(self) -> str:
        return {
            ReferenceModality.NONE: "None",
            ReferenceModality.DXA: "DXA",
            ReferenceModality.QCT: "Quantitative CT",
            ReferenceModality.MICRO_CT: "Micro CT",
            ReferenceModality.ULTRASOUND: "Quantitative ultrasound",
            ReferenceModality.OTHER: "Other",
        }[self]


class ReferenceQuality(str, Enum):
    """Whether the reference is fit to be trained against."""

    UNKNOWN = "unknown"
    ACCEPTABLE = "acceptable"
    QUESTIONABLE = "questionable"
    UNUSABLE = "unusable"

    @property
    def display(self) -> str:
        return {
            ReferenceQuality.UNKNOWN: "Not assessed",
            ReferenceQuality.ACCEPTABLE: "Acceptable",
            ReferenceQuality.QUESTIONABLE: "Questionable",
            ReferenceQuality.UNUSABLE: "Unusable",
        }[self]


#: Beyond this many days between the radiograph and the reference examination,
#: the two are describing different skeletons. The figure is a default for a
#: project to override, not a clinical rule.
DEFAULT_REFERENCE_WINDOW_DAYS = 365


@dataclass
class ReferenceOutcome:
    """The measured truth an OPG derived estimate would be trained against.

    Held apart from the radiographic findings on purpose. A radiograph and a
    densitometry result are two different examinations of one patient, and a
    dataset that cannot say which modality, which site, which device and how
    far apart in time cannot be used to train a bone density model honestly.

    No date is stored, only the interval in days, because a pair of dates
    narrows a patient down and the interval is what the analysis needs.
    """

    modality: str = ReferenceModality.NONE.value
    #: Where it was measured: femoral neck, lumbar spine, total hip, and so on.
    site: str = ""
    t_score: float | None = None
    z_score: float | None = None
    #: Areal or volumetric density, with its unit stated rather than assumed.
    bmd_value: float | None = None
    bmd_unit: str = ""
    #: Days between the radiograph and the reference examination. Negative
    #: means the reference came first.
    days_from_image: int | None = None
    device: str = ""
    #: Who or what produced it, as a source rather than a person: a hospital
    #: department, a study arm, a registry.
    source: str = ""
    quality: str = ReferenceQuality.UNKNOWN.value
    notes: str = ""

    @property
    def modality_enum(self) -> ReferenceModality:
        try:
            return ReferenceModality(self.modality)
        except ValueError:
            return ReferenceModality.NONE

    @property
    def is_recorded(self) -> bool:
        return self != ReferenceOutcome()

    def usable_as_target(self, window_days: int = DEFAULT_REFERENCE_WINDOW_DAYS) -> tuple:
        """Whether this may be treated as a labelled target, and why not.

        FR 066: a record without an adequate reference outcome may still be
        used for annotation training, but must not be treated as a labelled
        bone density target. The decision belongs to whoever assembles the
        dataset, so the reasons are returned rather than acted on.
        """
        reasons: list = []
        if self.modality_enum is ReferenceModality.NONE:
            reasons.append("No reference modality is recorded.")
        if not self.site:
            reasons.append("No anatomical site is recorded for the reference.")
        if self.t_score is None and self.z_score is None and self.bmd_value is None:
            reasons.append("The reference carries no value.")
        if self.bmd_value is not None and not self.bmd_unit:
            reasons.append(
                "A density value is recorded without its unit, so it cannot be "
                "compared with anything."
            )
        if self.quality in (
            ReferenceQuality.QUESTIONABLE.value, ReferenceQuality.UNUSABLE.value,
        ):
            reasons.append(
                f"The reference is marked {ReferenceQuality(self.quality).display.lower()}."
            )
        if self.days_from_image is None:
            reasons.append(
                "The interval between the radiograph and the reference "
                "examination is not recorded."
            )
        elif abs(int(self.days_from_image)) > window_days:
            reasons.append(
                f"The reference was taken {abs(int(self.days_from_image))} days "
                f"from the radiograph, beyond the {window_days} day window, so "
                f"the two may describe different skeletons."
            )
        return (not reasons, reasons)

    def summary_line(self) -> str:
        if not self.is_recorded:
            return "No reference outcome recorded."
        parts = [self.modality_enum.display]
        if self.site:
            parts.append(self.site)
        if self.t_score is not None:
            parts.append(f"T {self.t_score:+g}")
        if self.z_score is not None:
            parts.append(f"Z {self.z_score:+g}")
        if self.bmd_value is not None:
            parts.append(f"{self.bmd_value:g} {self.bmd_unit}".strip())
        if self.days_from_image is not None:
            parts.append(f"{self.days_from_image:+d} days from the image")
        return ", ".join(parts) + "."

    def to_dict(self) -> dict:
        d = asdict(self)
        usable, reasons = self.usable_as_target()
        d["usable_as_target"] = usable
        d["not_usable_because"] = reasons
        return d

    @classmethod
    def from_dict(cls, data: dict | None) -> "ReferenceOutcome":
        if not data:
            return cls()
        data = dict(data)
        for derived in ("usable_as_target", "not_usable_because"):
            data.pop(derived, None)
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})


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
