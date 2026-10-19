"""The patient factor sheet for one case.

Filled in beside the image rather than in a spreadsheet somewhere, so that the
confounders travel with the annotations into the export and a model can be
asked to account for them instead of learning them by accident.

Everything on this form is optional. A factor sheet is usually copied from a
record that is itself incomplete, and a form that refuses to save until every
box is filled gets worked around rather than completed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.patient import (
    AGE_CEILING_YEARS,
    BoneMedication,
    BoneStatus,
    MenopausalStatus,
    PatientFactors,
    ReferenceModality,
    ReferenceOutcome,
    ReferenceQuality,
    Sex,
    SmokingStatus,
)
from ..widgets.common import Banner, SectionLabel, make_button


def _combo(parent, enum_cls, current: str) -> QComboBox:
    combo = QComboBox(parent)
    for member in enum_cls:
        combo.addItem(member.display, member.value)
    index = combo.findData(current)
    if index >= 0:
        combo.setCurrentIndex(index)
    return combo


class PatientFactorsDialog(QDialog):
    """Read and edit the confounders recorded against a case."""

    def __init__(self, controller, case, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.case = case
        self.factors = PatientFactors.from_dict(case.patient.to_dict())
        self.reference = ReferenceOutcome.from_dict(case.reference.to_dict())

        self.setWindowTitle(f"Patient factors, {case.pseudonym}")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QLabel("What else is true of this patient", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        intro = QLabel(
            "A cortical width means something different at thirty and at "
            "seventy. Recording these here keeps them with the annotations, so "
            "an analysis can allow for them. Every field is optional.",
            self,
        )
        intro.setWordWrap(True)
        intro.setProperty("dim", True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)

        # -- the two that matter most ----------------------------------------
        self.age = QSpinBox(self)
        self.age.setRange(0, AGE_CEILING_YEARS)
        self.age.setSpecialValueText("Not recorded")
        self.age.setSuffix(" years")
        self.age.setValue(self.factors.age_years or 0)
        self.age.setToolTip(
            f"Whole years at the time of the radiograph. {AGE_CEILING_YEARS} "
            f"means {AGE_CEILING_YEARS} or above: an exact age higher than "
            f"that identifies a patient on its own."
        )
        form.addRow("Age", self.age)

        self.sex = _combo(self, Sex, self.factors.sex)
        self.sex.setToolTip(
            "Bone density references are published against sex, so this is "
            "what an index is interpreted against."
        )
        form.addRow("Sex", self.sex)

        self.menopause = _combo(self, MenopausalStatus, self.factors.menopausal_status)
        form.addRow("Menopausal status", self.menopause)
        layout.addLayout(form)

        # -- build -----------------------------------------------------------
        layout.addWidget(SectionLabel("Build", self))
        build = QFormLayout()
        build.setSpacing(8)
        self.height = QDoubleSpinBox(self)
        self.height.setRange(0.0, 250.0)
        self.height.setDecimals(1)
        self.height.setSuffix(" cm")
        self.height.setSpecialValueText("Not recorded")
        self.height.setValue(self.factors.height_cm or 0.0)
        build.addRow("Height", self.height)

        self.weight = QDoubleSpinBox(self)
        self.weight.setRange(0.0, 400.0)
        self.weight.setDecimals(1)
        self.weight.setSuffix(" kg")
        self.weight.setSpecialValueText("Not recorded")
        self.weight.setValue(self.factors.weight_kg or 0.0)
        build.addRow("Weight", self.weight)

        self.bmi_label = QLabel("", self)
        self.bmi_label.setProperty("dim", True)
        build.addRow("Body mass index", self.bmi_label)
        layout.addLayout(build)

        # -- bone -------------------------------------------------------------
        layout.addWidget(SectionLabel("Bone", self))
        bone = QFormLayout()
        bone.setSpacing(8)
        self.smoking = _combo(self, SmokingStatus, self.factors.smoking_status)
        bone.addRow("Smoking", self.smoking)

        self.bone_status = _combo(self, BoneStatus, self.factors.bone_status)
        bone.addRow("Known bone status", self.bone_status)

        self.medication = _combo(self, BoneMedication, self.factors.medication)
        self.medication.setToolTip(
            "Medication acting on bone changes the cortex, and therefore "
            "changes the index read off it."
        )
        bone.addRow("Bone medication", self.medication)
        layout.addLayout(bone)

        # The reference examination. Kept in its own section because it is a
        # different examination of the same patient, and because a value here
        # without its modality, site, unit and interval cannot be trained
        # against.
        layout.addWidget(SectionLabel("Reference examination", self))
        ref_hint = QLabel(
            "The measured truth a bone density estimate would be compared "
            "against. An OPG does not measure bone mineral density, so a model "
            "trained on this data depends entirely on what is recorded here.",
            self,
        )
        ref_hint.setWordWrap(True)
        ref_hint.setProperty("dim", True)
        layout.addWidget(ref_hint)

        reference = QFormLayout()
        reference.setSpacing(8)
        self.ref_modality = _combo(self, ReferenceModality, self.reference.modality)
        reference.addRow("Modality", self.ref_modality)

        self.ref_site = QLineEdit(self.reference.site, self)
        self.ref_site.setPlaceholderText("Femoral neck, lumbar spine, and so on")
        reference.addRow("Site", self.ref_site)

        self.ref_t = QDoubleSpinBox(self)
        self.ref_t.setRange(-9.0, 6.0)
        self.ref_t.setDecimals(2)
        self.ref_t.setSingleStep(0.1)
        self.ref_t.setSpecialValueText("Not recorded")
        self.ref_t.setValue(
            self.reference.t_score if self.reference.t_score is not None else -9.0
        )
        reference.addRow("T score", self.ref_t)

        self.ref_z = QDoubleSpinBox(self)
        self.ref_z.setRange(-9.0, 6.0)
        self.ref_z.setDecimals(2)
        self.ref_z.setSingleStep(0.1)
        self.ref_z.setSpecialValueText("Not recorded")
        self.ref_z.setValue(
            self.reference.z_score if self.reference.z_score is not None else -9.0
        )
        reference.addRow("Z score", self.ref_z)

        self.ref_days = QSpinBox(self)
        self.ref_days.setRange(-3650, 3650)
        self.ref_days.setSuffix(" days from the image")
        self.ref_days.setValue(self.reference.days_from_image or 0)
        self.ref_days.setToolTip(
            "Negative means the reference examination came first. No dates are "
            "stored: a pair of dates narrows a patient down, and the interval "
            "is what the analysis needs."
        )
        reference.addRow("Interval", self.ref_days)

        self.ref_device = QLineEdit(self.reference.device, self)
        self.ref_device.setPlaceholderText("Densitometer or scanner")
        reference.addRow("Device", self.ref_device)

        self.ref_quality = _combo(self, ReferenceQuality, self.reference.quality)
        reference.addRow("Fit to train against", self.ref_quality)
        layout.addLayout(reference)

        self.notes = QTextEdit(self)
        self.notes.setPlainText(self.factors.notes)
        self.notes.setPlaceholderText(
            "Anything else an analyst would need. No names, numbers or dates."
        )
        self.notes.setMaximumHeight(64)
        layout.addWidget(self.notes)

        self.banner = Banner(self)
        layout.addWidget(self.banner)

        buttons = QDialogButtonBox(Qt.Horizontal, self)
        self.save_button = make_button("Save", "save", accent=True, parent=self)
        self.cancel_button = make_button("Cancel", parent=self)
        buttons.addButton(self.save_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.cancel_button, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)
        self.height.valueChanged.connect(self._update_bmi)
        self.weight.valueChanged.connect(self._update_bmi)
        self.sex.currentIndexChanged.connect(self._update_menopause)
        self._update_bmi()
        self._update_menopause()

    # -- live feedback -------------------------------------------------------

    def _update_bmi(self) -> None:
        factors = PatientFactors(
            height_cm=self.height.value() or None,
            weight_kg=self.weight.value() or None,
        )
        bmi = factors.bmi
        self.bmi_label.setText(
            f"{bmi:g} kg/m2" if bmi is not None else "Needs height and weight"
        )

    def _update_menopause(self) -> None:
        """Only offered for the patients it applies to."""
        applies = self.sex.currentData() == Sex.FEMALE.value
        self.menopause.setEnabled(applies)
        if not applies:
            index = self.menopause.findData(MenopausalStatus.NOT_APPLICABLE.value)
            if index >= 0:
                self.menopause.setCurrentIndex(index)

    # -- saving --------------------------------------------------------------

    def collect(self) -> PatientFactors:
        """The form as a record, before it has been normalised."""
        return PatientFactors(
            age_years=self.age.value() or None,
            sex=self.sex.currentData(),
            menopausal_status=self.menopause.currentData(),
            height_cm=self.height.value() or None,
            weight_kg=self.weight.value() or None,
            smoking_status=self.smoking.currentData(),
            bone_status=self.bone_status.currentData(),
            medication=self.medication.currentData(),
            notes=self.notes.toPlainText().strip(),
        )

    def _save(self) -> None:
        factors = self.collect()
        notes = factors.normalise()
        self.factors = factors

        if notes:
            # Said plainly and then saved, rather than refused. The sheet is
            # copied from records that are themselves incomplete, and a form
            # that will not save gets worked around instead of corrected.
            self.banner.show_message(" ".join(notes), "warn")

        self.controller.set_patient_factors(self.case.id, factors)

        reference = ReferenceOutcome(
            modality=self.ref_modality.currentData(),
            site=self.ref_site.text().strip(),
            t_score=None if self.ref_t.value() <= -9.0 else self.ref_t.value(),
            z_score=None if self.ref_z.value() <= -9.0 else self.ref_z.value(),
            bmd_value=self.reference.bmd_value,
            bmd_unit=self.reference.bmd_unit,
            days_from_image=self.ref_days.value() or None,
            device=self.ref_device.text().strip(),
            source=self.reference.source,
            quality=self.ref_quality.currentData(),
            notes=self.reference.notes,
        )
        self.reference = reference
        self.controller.set_reference_outcome(self.case.id, reference)

        usable, reasons = reference.usable_as_target()
        if reference.is_recorded and not usable:
            # Said rather than refused. An incomplete reference is still worth
            # recording, and whoever assembles the dataset decides what to do
            # with it; what matters is that nobody later mistakes it for a
            # usable training target.
            self.banner.show_message(
                "Saved. This reference cannot be used as a training target "
                "yet: " + " ".join(reasons),
                "warn",
            )
            return

        self.accept()
