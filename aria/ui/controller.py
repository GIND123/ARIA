"""Application controller: the one place the interface talks to the store.

Panels never touch the repository directly. They ask the controller, and the
controller emits signals when something changes. That keeps autosave, the undo
journal, the audit trail and the optimistic locking in one place instead of
spread across a dozen widgets, each of which would have to remember to do all
four.

Autosave model
--------------
An edit gesture is a unit. While a handle is being dragged the change stays in
the graphics item; when the gesture ends, the controller writes it in one
transaction and pushes one entry onto the undo journal. That gives an autosave
that is both durable and meaningful to undo, rather than one database write per
mouse move.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.audit import AuditEvent
from ..core.measurements import MeasurementEngine
from ..core.models import (
    Annotation,
    CaseData,
    CategoricalLabel,
    QualityFlagRecord,
    Review,
    ReviewComment,
    Role,
    SetKind,
    utc_now,
)
from ..core.schema import CaseState, MCIGrade, Presence, ProjectSchema, Side, get_class
from ..core.units import Calibration
from ..core.validation import validate_for_submission
from ..io.image import DisplaySettings, ImageData
from ..security.auth import Permission, has_permission
from ..store.repository import CaseLockedError, ConcurrentEditError


class Controller(QObject):
    """Owns the open case and applies every change to it."""

    # Case lifecycle
    project_changed = Signal(object)
    case_opened = Signal(object)            # CaseData
    case_closed = Signal()
    case_list_changed = Signal()
    image_loaded = Signal(object)           # ImageData

    # Content
    annotations_changed = Signal()
    annotation_added = Signal(object)
    annotation_removed = Signal(str)
    annotation_updated = Signal(object)
    measurements_changed = Signal(list)
    grades_changed = Signal()
    flags_changed = Signal()
    calibration_changed = Signal(object)
    validation_changed = Signal(object)

    # Status
    dirty_changed = Signal(bool)
    saved = Signal(str)                     # timestamp text
    undo_state_changed = Signal(bool, bool)
    status_message = Signal(str, int)       # message, timeout ms
    error_raised = Signal(str, str, str)    # title, message, remedy
    read_only_changed = Signal(bool, str)

    def __init__(self, repository, paths, config, session, parent=None):
        super().__init__(parent)
        self.repo = repository
        self.paths = paths
        self.config = config
        self.settings = config.settings
        self.session = session

        self.project = None
        self.schema = ProjectSchema()
        self.case_data: CaseData | None = None
        self.image: ImageData | None = None
        self.display_settings = DisplaySettings()
        self.read_only = False
        self.read_only_reason = ""
        self._expected_counter = 0
        self._dirty = False
        self._last_saved = ""

        self._engine = MeasurementEngine(self.schema)
        self._importer = None

        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(45_000)
        self._lock_timer.timeout.connect(self._heartbeat)

        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(250)
        self._validate_timer.timeout.connect(self._run_validation)

    # -- helpers -------------------------------------------------------------

    @property
    def user(self):
        return self.session.user if self.session else None

    def can(self, permission: Permission) -> bool:
        return has_permission(self.user, permission)

    def importer(self):
        if self._importer is None:
            from ..io.importer import Importer

            self._importer = Importer(self.repo, self.paths, self.settings)
        return self._importer

    def _mark_dirty(self, dirty: bool = True) -> None:
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirty_changed.emit(dirty)

    def _note_saved(self) -> None:
        self._last_saved = utc_now()
        self._mark_dirty(False)
        self.saved.emit(self._last_saved)

    def _guard_editable(self) -> bool:
        """Refuse an edit and explain why, rather than failing silently."""
        if self.case_data is None:
            return False
        if self.read_only:
            # Read only is already shown in the status bar and the panel is
            # disabled, so a modal dialog on every attempt would be noise. A
            # status message is enough to explain a click that did nothing.
            self.status_message.emit(
                self.read_only_reason
                or "This case is open read only, so it cannot be edited.",
                6000,
            )
            return False
        if not self.can(Permission.EDIT_ANNOTATIONS):
            self.status_message.emit(
                "This account cannot edit annotations. Sign in with an annotator "
                "or reviewer account to make changes.",
                6000,
            )
            return False
        state = self.case_data.annotation_set.state_enum
        if state not in (CaseState.IN_PROGRESS, CaseState.ASSIGNED, CaseState.RETURNED):
            self.error_raised.emit(
                "This case is not open for editing",
                f"The annotation set is {state.display.lower()}.",
                (
                    "A submitted set is reopened by a reviewer returning it."
                    if state is CaseState.SUBMITTED
                    else "Reopen the case from the review panel."
                ),
            )
            return False
        return True

    # -- project -------------------------------------------------------------

    def set_project(self, project) -> None:
        self.project = project
        if project is not None:
            try:
                self.schema = ProjectSchema.from_dict(json.loads(project.schema_json or "{}"))
            except (ValueError, TypeError):
                self.schema = ProjectSchema()
        else:
            self.schema = ProjectSchema()
        self._engine = MeasurementEngine(self.schema)
        self.project_changed.emit(project)
        self.case_list_changed.emit()

    def save_schema(self, schema: ProjectSchema) -> None:
        if self.project is None:
            return
        self.schema = schema
        self.project.schema_json = json.dumps(schema.to_dict())
        self.repo.update_project(self.project, "Project schema updated.")
        self._engine = MeasurementEngine(schema)
        self.status_message.emit("Project schema saved.", 3000)
        self.recompute()

    # -- case lifecycle ------------------------------------------------------

    def open_case(self, case_id: str, kind: str = SetKind.PRIMARY, force_read_only: bool = False) -> bool:
        self.close_case()
        try:
            annotator_id = self.user.id if self.user else ""
            data = self.repo.load_case_data(case_id, annotator_id, kind)
        except KeyError as exc:
            self.error_raised.emit(
                "The case could not be opened", str(exc),
                "Refresh the case list and try again.",
            )
            return False

        read_only = force_read_only or not self.can(Permission.EDIT_ANNOTATIONS)
        if force_read_only:
            reason = "This case was opened read only."
        elif read_only and self.user is not None:
            # Saying only which role cannot annotate leaves the person stuck,
            # which is the state a solo administrator lands in after their
            # first import. The remedy belongs in the same sentence.
            reason = (
                f"The {self.user.role_display.lower()} role reads annotations "
                f"without editing them."
            )
            if self.can(Permission.MANAGE_USERS):
                reason += " " + self._annotator_account_hint()
            else:
                reason += " Ask an administrator for an annotator account."
        elif read_only:
            reason = "This account has read only access."
        else:
            reason = ""

        # An annotator who has not completed the calibration set is told so when
        # opening a case (FR 046). It is a notice rather than a block, because a
        # calibration set is itself annotated in this application.
        calibration_notice = ""
        if not read_only and self.user is not None:
            from ..security.auth import production_access_blocked

            calibration_notice = production_access_blocked(self.user, True)

        if not read_only:
            try:
                self.repo.acquire_lock(case_id, self.user)
            except CaseLockedError as exc:
                read_only = True
                reason = str(exc)
                self.status_message.emit(str(exc), 8000)

        self.case_data = data
        self._expected_counter = data.annotation_set.edit_counter
        self.read_only = read_only
        self.read_only_reason = reason

        try:
            self.image = self.importer().load_case_image(data.case)
        except (FileNotFoundError, ValueError, OSError) as exc:
            self.case_data = None
            self.repo.release_lock(case_id)
            self.error_raised.emit(
                "The image could not be loaded", str(exc),
                "Restore the retained source file from backup, or reimport the study.",
            )
            return False

        stored = self.repo.get_display_settings(case_id)
        self.display_settings = (
            DisplaySettings.from_dict(stored) if stored
            else self.image.default_display_settings()
        )

        if not read_only and data.annotation_set.state_enum is CaseState.ASSIGNED:
            try:
                self.repo.set_case_state(case_id, CaseState.IN_PROGRESS, "Annotation started.")
                data.case.state = CaseState.IN_PROGRESS.value
            except Exception:
                pass

        self.repo.record_case_view(case_id)
        self._lock_timer.start()

        self.case_opened.emit(data)
        self.image_loaded.emit(self.image)
        self.read_only_changed.emit(read_only, reason)
        self.recompute()
        self._refresh_undo_state()
        self._note_saved()
        if calibration_notice:
            self.status_message.emit(calibration_notice, 9000)
        return True

    def _annotator_account_hint(self) -> str:
        """Name the account to annotate from, or say how to make one."""
        names = [
            u.username for u in self.repo.list_users()
            if u.role in (Role.ANNOTATOR, Role.REVIEWER)
        ]
        if names:
            return (
                f"Sign out and sign in as an annotating account "
                f"({', '.join(names[:3])}) to draw on this case."
            )
        return (
            "Annotating needs an annotator account: create one in "
            "Administration, Accounts, then sign in as that account."
        )

    def close_case(self) -> None:
        if self.case_data is None:
            return
        case_id = self.case_data.case.id
        try:
            self.repo.set_display_settings(case_id, self.display_settings.to_dict())
        except Exception:
            pass
        self.repo.release_lock(case_id)
        self._lock_timer.stop()
        self.case_data = None
        self.image = None
        self.case_closed.emit()

    def reload_case(self) -> None:
        if self.case_data is None:
            return
        case_id = self.case_data.case.id
        kind = self.case_data.annotation_set.kind
        self.close_case()
        self.open_case(case_id, kind)

    def _heartbeat(self) -> None:
        if self.case_data is not None and not self.read_only:
            self.repo.heartbeat_lock(self.case_data.case.id)

    # -- annotations ---------------------------------------------------------

    def add_annotation(self, annotation: Annotation) -> bool:
        if not self._guard_editable():
            return False
        annotation.set_id = self.case_data.annotation_set.id
        annotation.created_by = self.user.id if self.user else ""
        annotation.updated_by = annotation.created_by

        cls = None
        try:
            cls = get_class(annotation.class_key)
        except KeyError:
            pass

        # A side specific class may hold only one live object per side, so a new
        # one replaces the previous rather than silently stacking two.
        replaced = None
        if cls is not None and cls.geometry.value in ("point", "line", "box", "mask"):
            existing = self.case_data.by_class(
                annotation.class_key, Side(annotation.side) if cls.side_scoped else None
            )
            if existing:
                replaced = existing[0]

        try:
            if replaced is not None:
                previous = asdict(replaced)
                replaced.coordinates = list(annotation.coordinates)
                replaced.geometry_type = annotation.geometry_type
                replaced.mask_rle = annotation.mask_rle
                replaced.mask_bbox = list(annotation.mask_bbox)
                replaced.presence = Presence.PRESENT.value
                self._expected_counter = self.repo.save_annotation(
                    replaced, self._expected_counter,
                    self.case_data.case.id, self.case_data.case.project_id,
                )
                self._push_undo(
                    "replace", {"annotation": previous},
                    {"annotation": asdict(replaced)},
                    f"Replace {cls.display_name if cls else annotation.class_key}",
                )
                self.annotation_updated.emit(replaced)
            else:
                self._expected_counter = self.repo.save_annotation(
                    annotation, self._expected_counter,
                    self.case_data.case.id, self.case_data.case.project_id,
                )
                self.case_data.annotations.append(annotation)
                self._push_undo(
                    "create", {"annotation_id": annotation.id},
                    {"annotation": asdict(annotation)},
                    f"Add {cls.display_name if cls else annotation.class_key}",
                )
                self.annotation_added.emit(annotation)
        except ConcurrentEditError as exc:
            self._handle_conflict(exc)
            return False

        self._after_change()
        return True

    def update_annotation_points(self, annotation_id: str, points, commit: bool = True) -> bool:
        annotation = self._find(annotation_id)
        if annotation is None:
            return False
        if not commit:
            annotation.coordinates = [v for p in points for v in p]
            self._mark_dirty(True)
            return True
        if not self._guard_editable():
            return False

        previous = list(annotation.coordinates)
        annotation.set_points(points)
        try:
            self._expected_counter = self.repo.save_annotation(
                annotation, self._expected_counter,
                self.case_data.case.id, self.case_data.case.project_id,
            )
        except ConcurrentEditError as exc:
            annotation.coordinates = previous
            self._handle_conflict(exc)
            return False

        self._push_undo(
            "geometry", {"id": annotation_id, "coordinates": previous},
            {"id": annotation_id, "coordinates": list(annotation.coordinates)},
            "Move geometry",
        )
        self.annotation_updated.emit(annotation)
        self._after_change()
        return True

    def delete_annotation(self, annotation_id: str) -> bool:
        if not self._guard_editable():
            return False
        annotation = self._find(annotation_id)
        if annotation is None:
            return False
        if annotation.locked:
            self.status_message.emit(
                "This object is locked. Unlock it before deleting.", 4000
            )
            return False
        snapshot = asdict(annotation)
        try:
            self._expected_counter = self.repo.delete_annotation(
                annotation_id, self._expected_counter,
                self.case_data.case.id, self.case_data.case.project_id,
            )
        except ConcurrentEditError as exc:
            self._handle_conflict(exc)
            return False

        annotation.deleted = True
        self._push_undo(
            "delete", {"annotation": snapshot}, {"annotation_id": annotation_id},
            "Delete object",
        )
        self.annotation_removed.emit(annotation_id)
        self._after_change()
        return True

    def set_presence(self, class_key: str, side: Side, presence: Presence, note: str = "") -> bool:
        """Record a structure as explicitly absent rather than leaving it blank.

        This is what stops missing anatomy from being encoded as a zero
        coordinate (FR 017).
        """
        if not self._guard_editable():
            return False
        existing = self.case_data.first(class_key, side if get_class(class_key).side_scoped else None)
        try:
            cls = get_class(class_key)
        except KeyError:
            return False

        if existing is None:
            annotation = Annotation(
                set_id=self.case_data.annotation_set.id,
                class_key=class_key,
                side=side.value if cls.side_scoped else Side.MIDLINE.value,
                geometry_type=cls.geometry.value,
                presence=presence.value,
                notes=note,
                created_by=self.user.id if self.user else "",
            )
            try:
                self._expected_counter = self.repo.save_annotation(
                    annotation, self._expected_counter,
                    self.case_data.case.id, self.case_data.case.project_id,
                )
            except ConcurrentEditError as exc:
                self._handle_conflict(exc)
                return False
            self.case_data.annotations.append(annotation)
            self.annotation_added.emit(annotation)
        else:
            previous = {"presence": existing.presence, "notes": existing.notes}
            existing.presence = presence.value
            existing.notes = note
            if presence is not Presence.PRESENT:
                existing.coordinates = []
                existing.mask_rle = ""
            try:
                self._expected_counter = self.repo.save_annotation(
                    existing, self._expected_counter,
                    self.case_data.case.id, self.case_data.case.project_id,
                )
            except ConcurrentEditError as exc:
                existing.presence = previous["presence"]
                self._handle_conflict(exc)
                return False
            self._push_undo(
                "presence", {"id": existing.id, **previous},
                {"id": existing.id, "presence": presence.value, "notes": note},
                f"Set {cls.display_name} to {presence.display}",
            )
            self.annotation_updated.emit(existing)

        self._after_change()
        self.status_message.emit(
            f"{cls.display_name} ({side.display}) recorded as {presence.display.lower()}.", 4000
        )
        return True

    def set_annotation_flags(
        self, annotation_id: str, hidden=None, locked=None, ambiguous=None,
        visibility_score=None, notes=None,
    ) -> bool:
        annotation = self._find(annotation_id)
        if annotation is None:
            return False
        if hidden is not None:
            annotation.hidden = bool(hidden)
        if locked is not None:
            annotation.locked = bool(locked)
        if ambiguous is not None:
            annotation.ambiguous = bool(ambiguous)
        if visibility_score is not None:
            annotation.visibility_score = int(visibility_score)
        if notes is not None:
            annotation.notes = notes
        if self.read_only:
            self.annotation_updated.emit(annotation)
            return True
        try:
            self._expected_counter = self.repo.save_annotation(
                annotation, self._expected_counter,
                self.case_data.case.id, self.case_data.case.project_id,
            )
        except ConcurrentEditError as exc:
            self._handle_conflict(exc)
            return False
        self.annotation_updated.emit(annotation)
        self._after_change()
        return True

    def duplicate_annotation(self, annotation_id: str, mirror: bool = False) -> bool:
        """Copy an object, optionally to the other side.

        Mirroring reflects the geometry about the image midline, which gives a
        starting position on the other side rather than a finished annotation.
        The annotator still adjusts it, and the object is flagged so a reviewer
        can see it began as a mirror.
        """
        source = self._find(annotation_id)
        if source is None or not self._guard_editable():
            return False
        from ..core.models import new_id

        copy = Annotation(
            id=new_id("ann_"),
            set_id=source.set_id,
            class_key=source.class_key,
            geometry_type=source.geometry_type,
            presence=source.presence,
            created_by=self.user.id if self.user else "",
        )
        points = source.points()
        if mirror and self.image is not None:
            width = float(self.image.columns)
            points = [(width - x, y) for x, y in points]
            copy.side = (
                Side.LEFT.value if source.side == Side.RIGHT.value else Side.RIGHT.value
            )
            copy.properties = {"origin": "mirrored", "mirrored_from": source.id}
            copy.notes = "Created by mirroring the other side. Adjust before submitting."
        else:
            copy.side = source.side
            copy.properties = {"origin": "copy", "copied_from": source.id}
            points = [(x + 12, y + 12) for x, y in points]
        copy.set_points(points)
        return self.add_annotation(copy)

    def _find(self, annotation_id: str):
        if self.case_data is None:
            return None
        for a in self.case_data.annotations:
            if a.id == annotation_id:
                return a
        return None

    # -- geometry assists ----------------------------------------------------

    def construct_index_line(self, class_key: str, side: Side) -> bool:
        """Compute a protocol defined construction line and offer it.

        This is deterministic geometry from the annotator's own contours, not a
        prediction. The result is written as an ordinary editable annotation,
        with a note recording how it was constructed.
        """
        if self.case_data is None or not self._guard_editable():
            return False
        from ..core import geometry as geo

        data = self.case_data
        peri = data.present("periosteal_border", side)
        endo = data.present("endosteal_border", side)
        if peri is None or endo is None:
            self.error_raised.emit(
                "Contours are needed first",
                "The periosteal and endosteal borders must be traced on this side "
                "before a construction line can be computed.",
                "Trace both borders, then try again.",
            )
            return False

        try:
            if class_key == "mcw_line":
                foramen = data.present("mental_foramen_centre", side)
                if foramen is None:
                    raise ValueError("The mental foramen centre is not marked on this side.")
                result = geo.construct_cortical_width(
                    foramen.points()[0], peri.points(), endo.points()
                )
            elif class_key in ("pmi_superior_line", "pmi_inferior_line"):
                margin_key = (
                    "mental_foramen_superior" if class_key == "pmi_superior_line"
                    else "mental_foramen_inferior"
                )
                margin = data.present(margin_key, side)
                mcw = data.present("mcw_line", side)
                if margin is None:
                    raise ValueError(f"{get_class(margin_key).display_name} is not marked.")
                if mcw is None:
                    raise ValueError(
                        "The cortical width line must exist first, because the "
                        "heights are measured along its axis."
                    )
                p0, p1 = mcw.points()[0], mcw.points()[-1]
                axis = geo.normalise(geo.vector(p0, p1))
                result = geo.construct_pmi_height(margin.points()[0], axis, peri.points())
            elif class_key == "antegonial_index_line":
                point = data.present("antegonial_point", side)
                if point is None:
                    raise ValueError("The antegonial point is not marked on this side.")
                result = geo.construct_antegonial_thickness(
                    point.points()[0], peri.points(), endo.points()
                )
            elif class_key == "gonial_index_line":
                gonion = data.present("gonion", side)
                ramus = data.present("posterior_ramus_border", side)
                if gonion is None:
                    raise ValueError("Gonion is not marked on this side.")
                if ramus is None:
                    raise ValueError(
                        "The posterior ramus border is needed, because the gonial "
                        "axis bisects the ramus tangent and the inferior border "
                        "tangent."
                    )
                result = geo.construct_gonial_thickness(
                    gonion.points()[0], ramus.points(), peri.points(), endo.points()
                )
            else:
                return False
        except (ValueError, IndexError) as exc:
            self.error_raised.emit(
                "The construction could not be computed", str(exc),
                "Complete the annotations it depends on, then try again.",
            )
            return False

        from ..core.models import new_id

        annotation = Annotation(
            id=new_id("ann_"),
            set_id=data.annotation_set.id,
            class_key=class_key,
            side=side.value,
            geometry_type="line",
            created_by=self.user.id if self.user else "",
            notes="Constructed from the traced contours. Review before submitting.",
            properties={
                "origin": "geometric_construction",
                "method": result.method,
                "notes": result.notes,
                "complete": result.complete,
            },
        )
        annotation.set_points([result.start, result.end])
        ok = self.add_annotation(annotation)
        if ok:
            message = f"{get_class(class_key).display_name} constructed."
            if not result.complete:
                message += " The construction is incomplete, so check it carefully."
            self.status_message.emit(message, 6000)
        return ok

    # -- grades and flags ----------------------------------------------------

    def set_grade(self, side: Side, grade: MCIGrade, rationale: str = "", region_id: str = "") -> bool:
        if not self._guard_editable():
            return False
        existing = self.case_data.grade("mci_grade", side)
        label = existing or CategoricalLabel(
            set_id=self.case_data.annotation_set.id, key="mci_grade", side=side.value,
            created_by=self.user.id if self.user else "",
        )
        previous = label.value if existing else None
        label.value = grade.value
        label.rationale = rationale
        if region_id:
            label.region_annotation_id = region_id
        elif self.case_data.present("mci_region", side) is not None:
            label.region_annotation_id = self.case_data.present("mci_region", side).id

        try:
            self._expected_counter = self.repo.set_grade(
                label, self._expected_counter,
                self.case_data.case.id, self.case_data.case.project_id,
            )
        except ConcurrentEditError as exc:
            self._handle_conflict(exc)
            return False

        if existing is None:
            self.case_data.categorical.append(label)
        self._push_undo(
            "grade", {"side": side.value, "value": previous},
            {"side": side.value, "value": grade.value}, f"Grade {side.display} as {grade.display}",
        )
        self.grades_changed.emit()
        self._after_change()
        return True

    def add_quality_flag(self, flag: str, comment: str = "", side: Side = Side.NONE) -> bool:
        if not self._guard_editable():
            return False
        record = QualityFlagRecord(
            set_id=self.case_data.annotation_set.id, flag=flag, side=side.value,
            comment=comment, created_by=self.user.id if self.user else "",
        )
        try:
            self._expected_counter = self.repo.add_quality_flag(
                record, self._expected_counter,
                self.case_data.case.id, self.case_data.case.project_id,
            )
        except ConcurrentEditError as exc:
            self._handle_conflict(exc)
            return False
        self.case_data.quality_flags.append(record)
        self.flags_changed.emit()
        self._after_change()
        return True

    def remove_quality_flag(self, flag_id: str) -> bool:
        if not self._guard_editable():
            return False
        try:
            self._expected_counter = self.repo.remove_quality_flag(
                flag_id, self._expected_counter,
                self.case_data.case.id, self.case_data.case.project_id,
            )
        except ConcurrentEditError as exc:
            self._handle_conflict(exc)
            return False
        self.case_data.quality_flags = [
            f for f in self.case_data.quality_flags if f.id != flag_id
        ]
        self.flags_changed.emit()
        self._after_change()
        return True

    # -- calibration and laterality -----------------------------------------

    def set_calibration(self, calibration: Calibration, detail: str = "") -> bool:
        if self.case_data is None:
            return False
        if not self.can(Permission.VALIDATE_CALIBRATION) and calibration.is_validated:
            self.error_raised.emit(
                "Not permitted",
                "Validating a calibration needs reviewer or administrator permission.",
                "Ask a reviewer to validate the calibration for this case.",
            )
            return False
        self.repo.set_calibration(self.case_data.case.id, calibration, detail)
        self.case_data.case.calibration = calibration
        self.calibration_changed.emit(calibration)
        self.recompute()
        return True

    def confirm_laterality(self, note: str = "") -> bool:
        if self.case_data is None:
            return False
        if not self.can(Permission.CONFIRM_LATERALITY):
            self.error_raised.emit(
                "Not permitted", "This account cannot confirm image orientation.",
                "Ask an annotator or reviewer to confirm the orientation.",
            )
            return False
        self.repo.confirm_laterality(
            self.case_data.case.id, self.user.id if self.user else "", note
        )
        self.case_data.case.laterality_confirmed = True
        self.case_data.case.laterality_note = note
        self.case_data.case.laterality_confirmed_at = utc_now()
        self.status_message.emit("Anatomical right and left confirmed.", 4000)
        self._run_validation()
        return True

    # -- measurements --------------------------------------------------------

    def recompute(self) -> list:
        if self.case_data is None:
            self.measurements_changed.emit([])
            return []
        measurements = self._engine.compute(self.case_data)
        grades = self._engine.grades(self.case_data)
        try:
            self.repo.store_measurements(
                self.case_data.annotation_set.id, measurements + grades
            )
        except Exception:
            pass
        self.measurements_changed.emit(measurements)
        self._validate_timer.start()
        return measurements

    def _run_validation(self) -> None:
        if self.case_data is None:
            self.validation_changed.emit(None)
            return
        result = validate_for_submission(self.case_data, self.schema)
        self.validation_changed.emit(result)

    def _after_change(self) -> None:
        self.annotations_changed.emit()
        self.recompute()
        self._note_saved()
        self._refresh_undo_state()

    # -- undo and redo -------------------------------------------------------

    def _push_undo(self, operation: str, undo: dict, redo: dict, label: str) -> None:
        if self.case_data is None:
            return
        try:
            self.repo.push_journal(
                self.case_data.annotation_set.id, operation, undo, redo, label
            )
        except Exception:
            pass

    def _refresh_undo_state(self) -> None:
        if self.case_data is None:
            self.undo_state_changed.emit(False, False)
            return
        undo, redo = self.repo.journal_depth(self.case_data.annotation_set.id)
        self.undo_state_changed.emit(undo > 0, redo > 0)

    def undo_label(self) -> str:
        if self.case_data is None:
            return ""
        entry = self.repo.peek_undo(self.case_data.annotation_set.id)
        return entry["label"] if entry else ""

    def redo_label(self) -> str:
        if self.case_data is None:
            return ""
        entry = self.repo.peek_redo(self.case_data.annotation_set.id)
        return entry["label"] if entry else ""

    def undo(self) -> bool:
        if self.case_data is None or not self._guard_editable():
            return False
        entry = self.repo.peek_undo(self.case_data.annotation_set.id)
        if entry is None:
            return False
        payload = json.loads(entry["undo_json"] or "{}")
        if not self._apply_journal(entry["operation"], payload, undoing=True):
            return False
        self.repo.mark_undone(entry["id"], True)
        self.status_message.emit(f"Undone: {entry['label']}", 3000)
        self._reload_content()
        return True

    def redo(self) -> bool:
        if self.case_data is None or not self._guard_editable():
            return False
        entry = self.repo.peek_redo(self.case_data.annotation_set.id)
        if entry is None:
            return False
        payload = json.loads(entry["redo_json"] or "{}")
        if not self._apply_journal(entry["operation"], payload, undoing=False):
            return False
        self.repo.mark_undone(entry["id"], False)
        self.status_message.emit(f"Redone: {entry['label']}", 3000)
        self._reload_content()
        return True

    def _apply_journal(self, operation: str, payload: dict, undoing: bool) -> bool:
        """Apply one journal entry in either direction."""
        try:
            case_id = self.case_data.case.id
            project_id = self.case_data.case.project_id

            if operation == "create":
                if undoing:
                    self._expected_counter = self.repo.delete_annotation(
                        payload["annotation_id"], self._expected_counter, case_id, project_id
                    )
                else:
                    annotation = Annotation.from_dict(payload["annotation"])
                    annotation.deleted = False
                    self._expected_counter = self.repo.save_annotation(
                        annotation, self._expected_counter, case_id, project_id
                    )
            elif operation == "delete":
                if undoing:
                    annotation = Annotation.from_dict(payload["annotation"])
                    annotation.deleted = False
                    self._expected_counter = self.repo.save_annotation(
                        annotation, self._expected_counter, case_id, project_id
                    )
                else:
                    self._expected_counter = self.repo.delete_annotation(
                        payload["annotation_id"], self._expected_counter, case_id, project_id
                    )
            elif operation in ("geometry", "presence", "replace"):
                if operation == "replace":
                    annotation = Annotation.from_dict(payload["annotation"])
                else:
                    annotation = self.repo.get_annotation(payload["id"])
                    if annotation is None:
                        return False
                    if "coordinates" in payload:
                        annotation.coordinates = payload["coordinates"]
                    if "presence" in payload:
                        annotation.presence = payload["presence"]
                    if "notes" in payload:
                        annotation.notes = payload["notes"]
                self._expected_counter = self.repo.save_annotation(
                    annotation, self._expected_counter, case_id, project_id
                )
            elif operation == "grade":
                side = Side(payload["side"])
                value = payload.get("value")
                if value is None:
                    return True
                label = self.case_data.grade("mci_grade", side) or CategoricalLabel(
                    set_id=self.case_data.annotation_set.id, key="mci_grade", side=side.value
                )
                label.value = value
                self._expected_counter = self.repo.set_grade(
                    label, self._expected_counter, case_id, project_id
                )
            else:
                return False
            return True
        except (ConcurrentEditError, KeyError, ValueError) as exc:
            self.error_raised.emit(
                "The change could not be reversed", str(exc),
                "Reload the case to see its current state.",
            )
            return False

    def _reload_content(self) -> None:
        """Reload annotation content from the store after an undo or redo."""
        if self.case_data is None:
            return
        set_id = self.case_data.annotation_set.id
        self.case_data.annotations = self.repo.list_annotations(set_id)
        self.case_data.categorical = self.repo.list_grades(set_id)
        self.case_data.quality_flags = self.repo.list_quality_flags(set_id)
        self.case_data.annotation_set = self.repo.get_set(set_id)
        self._expected_counter = self.case_data.annotation_set.edit_counter
        self.annotations_changed.emit()
        self.grades_changed.emit()
        self.flags_changed.emit()
        self.recompute()
        self._refresh_undo_state()

    # -- submission and review ----------------------------------------------

    def validate(self):
        if self.case_data is None:
            return None
        return validate_for_submission(self.case_data, self.schema)

    def submit(self, note: str = "") -> bool:
        if self.case_data is None:
            return False
        if not self.can(Permission.SUBMIT_ANNOTATIONS):
            self.error_raised.emit(
                "Not permitted", "This account cannot submit annotations.", "",
            )
            return False
        result = self.validate()
        if result is not None and not result.can_submit:
            lines = "\n".join(f"  {i.label()}" for i in result.blockers[:8])
            self.error_raised.emit(
                "Submission is blocked",
                f"{len(result.blockers)} items must be resolved first:\n{lines}",
                "Resolve each item listed in the Submission checks section.",
            )
            return False
        try:
            revision = self.repo.submit_set(
                self.case_data.annotation_set.id, note or "Submitted for review"
            )
        except Exception as exc:
            self.error_raised.emit("Submission failed", str(exc), "Try again, or reload the case.")
            return False
        self.status_message.emit(
            f"Submitted as revision {revision.revision_no}.", 6000
        )
        self.repo.release_lock(self.case_data.case.id)
        self.reload_case()
        self.case_list_changed.emit()
        return True

    def record_review(self, decision: str, summary: str, comments, new_state: CaseState) -> bool:
        if self.case_data is None:
            return False
        if not self.can(Permission.REVIEW_CASES):
            self.error_raised.emit(
                "Not permitted", "This account cannot review annotation sets.", "",
            )
            return False
        review = Review(
            set_id=self.case_data.annotation_set.id,
            reviewer_id=self.user.id if self.user else "",
            decision=decision,
            summary=summary,
            reviewed_revision=self.case_data.annotation_set.annotation_version,
            comments=list(comments),
        )
        try:
            self.repo.record_review(review, new_state)
        except Exception as exc:
            self.error_raised.emit("The review could not be saved", str(exc), "Try again.")
            return False
        self.status_message.emit(f"Review recorded: {decision}.", 5000)
        self.reload_case()
        self.case_list_changed.emit()
        return True

    def reopen_for_edit(self) -> bool:
        if self.case_data is None:
            return False
        self.repo.reopen_for_edit(self.case_data.annotation_set.id)
        self.reload_case()
        return True

    # -- conflicts -----------------------------------------------------------

    def _handle_conflict(self, exc: ConcurrentEditError) -> None:
        self.error_raised.emit(
            "This case changed elsewhere",
            str(exc),
            "Reload the case. Your unsaved change was not applied, so nothing was overwritten.",
        )
