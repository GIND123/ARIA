"""The import pipeline.

Order of operations, and why it is this order:

1. inspect with the guards, so nothing unsuitable reaches a decoder,
2. checksum the file, which both links the working copy to the original and
   detects a file already imported into this project,
3. retain the original bytes unchanged in content addressed storage (FR 002),
4. decode pixels and read metadata,
5. deidentify the stored metadata before the case becomes annotatable (FR 005),
6. write a working representation and a thumbnail,
7. create the case record and its audit entries.

If any step fails, the steps already completed are rolled back, so a failed
import leaves no half made case.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.models import Case, SourceImage, new_id
from ..core.schema import CaseState
from ..core.units import Calibration
from .deident import DeidProfile, deidentify_source_meta, get_profile, pseudonym_for
from .fsutil import atomic_write_bytes, copy_preserving, safe_filename, sha256_file
from .guards import InspectionResult, inspect_file
from .image import ImageData


@dataclass
class ImportOutcome:
    """Result of attempting to import one file."""

    path: str = ""
    succeeded: bool = False
    case: Case | None = None
    duplicate_of: str = ""
    error_code: str = ""
    error_message: str = ""
    remedy: str = ""
    warnings: list = field(default_factory=list)
    inspection: InspectionResult | None = None
    deid_report: dict = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return Path(self.path).name

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "filename": self.filename,
            "succeeded": self.succeeded,
            "case_id": self.case.id if self.case else "",
            "pseudonym": self.case.pseudonym if self.case else "",
            "duplicate_of": self.duplicate_of,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "remedy": self.remedy,
            "warnings": list(self.warnings),
        }


@dataclass
class ImportSummary:
    outcomes: list = field(default_factory=list)
    batch_issues: list = field(default_factory=list)

    @property
    def imported(self) -> list:
        return [o for o in self.outcomes if o.succeeded]

    @property
    def failed(self) -> list:
        return [o for o in self.outcomes if not o.succeeded and not o.duplicate_of]

    @property
    def duplicates(self) -> list:
        return [o for o in self.outcomes if o.duplicate_of]

    def summary_line(self) -> str:
        parts = [f"{len(self.imported)} imported"]
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} already present")
        if self.failed:
            parts.append(f"{len(self.failed)} could not be imported")
        return ", ".join(parts) + "."


def make_thumbnail(image: ImageData, max_edge: int = 480) -> np.ndarray:
    """Small preview for the case browser, rendered from the default window."""
    settings = image.default_display_settings()
    display = image.to_display(settings)
    h, w = display.shape
    scale = max(1, int(np.ceil(max(h, w) / max_edge)))
    if scale > 1:
        h2 = (h // scale) * scale
        w2 = (w // scale) * scale
        cropped = display[:h2, :w2]
        display = cropped.reshape(h2 // scale, scale, w2 // scale, scale).mean(axis=(1, 3))
        display = display.astype(np.uint8)
    return display


def write_thumbnail(image: ImageData, destination, max_edge: int = 480) -> None:
    from PIL import Image as PILImage

    thumb = make_thumbnail(image, max_edge)
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    PILImage.fromarray(thumb, mode="L").save(str(destination), format="PNG", optimize=True)


def write_working_copy(image: ImageData, destination) -> None:
    """Cache the decoded pixels so reopening a case does not re decode.

    The working copy is derived data. It can always be rebuilt from the retained
    original, and the checksum in the case record is what ties the two together.
    """
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"pixels": image.pixels}
    if image.original_channels is not None:
        payload["original_channels"] = image.original_channels
    import io

    buffer = io.BytesIO()
    np.savez_compressed(buffer, **payload)
    atomic_write_bytes(destination, buffer.getvalue())


def read_working_copy(path, meta: SourceImage) -> ImageData | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        with np.load(str(p), allow_pickle=False) as data:
            pixels = data["pixels"]
            channels = data["original_channels"] if "original_channels" in data else None
        return ImageData(pixels=pixels, meta=meta, original_channels=channels)
    except (OSError, ValueError, KeyError):
        return None


class Importer:
    """Imports files into a project."""

    def __init__(self, repository, paths, settings, salt: str | None = None):
        self.repo = repository
        self.paths = paths
        self.settings = settings
        #: Salt for pseudonym and identifier remapping. Held per installation so
        #: the same source always maps to the same pseudonym.
        self.salt = salt or self._installation_salt()

    def _installation_salt(self) -> str:
        existing = self.repo.db.get_meta("deid_salt")
        if existing:
            return existing
        value = secrets.token_hex(32)
        self.repo.db.set_meta("deid_salt", value)
        return value

    # -- single file ---------------------------------------------------------

    def import_file(
        self, path, project_id: str, profile: DeidProfile | None = None,
        imported_by: str = "", split: str = "", device_policy: dict | None = None,
        pseudonym: str = "",
    ) -> ImportOutcome:
        source_path = Path(path)
        outcome = ImportOutcome(path=str(source_path))

        # 1. Guards.
        inspection = inspect_file(
            source_path, limits=self.settings.import_limits(), destination=self.paths.data_dir
        )
        outcome.inspection = inspection
        outcome.warnings.extend(inspection.warnings)
        if not inspection.accepted:
            problem = inspection.first_problem()
            outcome.error_code = problem.code if problem else "rejected"
            outcome.error_message = problem.message if problem else "The file was rejected."
            outcome.remedy = problem.remedy if problem else ""
            self.repo.log(
                "import_rejected", "file", source_path.name,
                after={"reason": outcome.error_code, "message": outcome.error_message},
                detail=f"{source_path.name} was rejected: {outcome.error_message}",
                project_id=project_id,
            )
            return outcome

        self.repo.log(
            "import_started", "file", source_path.name,
            detail=f"Import started for {source_path.name}.", project_id=project_id,
        )

        # 2. Checksum and duplicate detection.
        try:
            checksum = sha256_file(source_path)
        except OSError as exc:
            outcome.error_code = "unreadable"
            outcome.error_message = f"The file could not be read: {exc}"
            outcome.remedy = "Check that the file is not locked by another application."
            return outcome

        existing = self.repo.find_case_by_checksum(project_id, checksum)
        if existing is not None:
            outcome.duplicate_of = existing.pseudonym
            outcome.error_code = "duplicate_content"
            outcome.error_message = (
                f"These exact pixels are already in this project as "
                f"{existing.pseudonym}."
            )
            outcome.remedy = "Nothing to do. The existing case holds the same image."
            return outcome

        retained_path = self.paths.source_path(
            checksum, safe_filename(source_path.name, "image")
        )
        working_path = None
        thumbnail_path = None
        wrote_retained = False

        try:
            # 3. Retain the original, unchanged and verified.
            if not retained_path.exists():
                copy_preserving(source_path, retained_path)
                wrote_retained = True

            # 4. Decode.
            from .png_reader import read_image

            image, calibration = read_image(source_path, device_policy)
            image.meta.sha256 = checksum
            image.meta.stored_path = str(retained_path)
            image.meta.byte_size = inspection.byte_size

            # 5. Deidentify what ARIA stores.
            profile = profile or get_profile("aria_default")
            source_identifier = (
                image.meta.sop_instance_uid or image.meta.study_instance_uid or checksum
            )
            deid_report = deidentify_source_meta(image.meta, profile, self.salt)
            outcome.deid_report = deid_report.to_dict()
            outcome.warnings.extend(deid_report.warnings)

            case_pseudonym = pseudonym or pseudonym_for(source_identifier, self.salt)
            case_pseudonym = self._unique_pseudonym(project_id, case_pseudonym)

            case = Case(
                project_id=project_id,
                pseudonym=case_pseudonym,
                source=image.meta,
                calibration=calibration,
                state=CaseState.UNASSIGNED.value,
                split=split,
                imported_by=imported_by,
            )

            # 6. Working representation and preview.
            working_path = self.paths.working_path(case.id)
            if self.settings.keep_working_copies:
                write_working_copy(image, working_path)
            thumbnail_path = self.paths.thumbnail_path(case.id)
            write_thumbnail(image, thumbnail_path, self.settings.thumbnail_max_edge)

            # 7. Record.
            self.repo.create_case(case, deid_report=outcome.deid_report)

            outcome.case = case
            outcome.succeeded = True
            for note in image.meta.notes:
                outcome.warnings.append(note)
            if calibration.warnings:
                outcome.warnings.extend(calibration.warnings)
            if not image.meta.image_laterality:
                outcome.warnings.append(
                    "The source does not carry image laterality. Anatomical right "
                    "and left must be confirmed before this case can be submitted."
                )
            return outcome

        except Exception as exc:
            # Roll back anything this import created.
            for created in (working_path, thumbnail_path):
                try:
                    if created and Path(created).exists():
                        Path(created).unlink()
                except OSError:
                    pass
            if wrote_retained:
                try:
                    if retained_path.exists() and not self.repo.db.query_one(
                        "SELECT id FROM cases WHERE source_sha256 = ?", (checksum,)
                    ):
                        retained_path.unlink()
                except OSError:
                    pass
            outcome.error_code = "import_failed"
            outcome.error_message = f"{source_path.name} could not be imported: {exc}"
            outcome.remedy = (
                "Check that the file opens in a DICOM or image viewer. If it "
                "does, report this with the diagnostics report from the Help menu."
            )
            self.repo.log(
                "import_rejected", "file", source_path.name,
                after={"reason": "exception", "message": str(exc)},
                detail=outcome.error_message, project_id=project_id,
            )
            return outcome

    def _unique_pseudonym(self, project_id: str, candidate: str) -> str:
        """Guarantee the pseudonym is unique inside the project."""
        base = candidate
        n = 1
        while self.repo.db.query_one(
            "SELECT id FROM cases WHERE project_id = ? AND pseudonym = ?",
            (project_id, candidate),
        ):
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    # -- batch ---------------------------------------------------------------

    def import_files(
        self, paths, project_id: str, profile: DeidProfile | None = None,
        imported_by: str = "", split: str = "", device_policy: dict | None = None,
        progress=None, should_cancel=None,
    ) -> ImportSummary:
        """Import many files, reporting progress and honouring cancellation."""
        from .guards import DEFAULT_LIMITS, GuardIssue, GuardCode, free_disk_bytes, human_bytes

        summary = ImportSummary()
        paths = [Path(p) for p in paths]
        limits = {**DEFAULT_LIMITS, **self.settings.import_limits()}

        if len(paths) > limits["max_batch_files"]:
            summary.batch_issues.append(
                GuardIssue(
                    GuardCode.BATCH_TOO_LARGE.value,
                    f"This selection contains {len(paths)} files, above the batch "
                    f"limit of {limits['max_batch_files']}.",
                    "Import in smaller batches, or raise the limit in Preferences, Import.",
                ).to_dict()
            )
            return summary

        total_bytes = 0
        for p in paths:
            try:
                total_bytes += p.stat().st_size
            except OSError:
                continue
        free = free_disk_bytes(self.paths.data_dir)
        needed = int(total_bytes * limits["working_copy_factor"]) + limits["disk_headroom_bytes"]
        if free is not None and free < needed:
            summary.batch_issues.append(
                GuardIssue(
                    GuardCode.INSUFFICIENT_DISK.value,
                    f"This import needs about {human_bytes(needed)} of free space "
                    f"and only {human_bytes(free)} is available.",
                    "Free space, import fewer files at a time, or move the ARIA "
                    "data folder to a larger drive.",
                ).to_dict()
            )
            return summary

        for index, p in enumerate(paths):
            if should_cancel is not None and should_cancel():
                break
            if progress is not None:
                progress(index, len(paths), p.name)
            summary.outcomes.append(
                self.import_file(
                    p, project_id, profile, imported_by, split, device_policy
                )
            )
        if progress is not None:
            progress(len(paths), len(paths), "")
        return summary

    # -- reload --------------------------------------------------------------

    def load_case_image(self, case: Case, device_policy: dict | None = None) -> ImageData:
        """Return the pixels for a case, from the working copy when available."""
        working = self.paths.working_path(case.id)
        image = read_working_copy(working, case.source)
        if image is not None:
            return image

        retained = Path(case.source.stored_path)
        if not retained.exists():
            retained = self.paths.source_path(
                case.source.sha256, safe_filename(case.source.original_filename, "image")
            )
        if not retained.exists():
            raise FileNotFoundError(
                f"The retained source file for {case.pseudonym} is missing. It "
                f"should be at {retained}. Restore it from backup, or reimport "
                f"the study."
            )

        actual = sha256_file(retained)
        if actual != case.source.sha256:
            raise ValueError(
                f"The retained source file for {case.pseudonym} no longer matches "
                f"the checksum recorded at import. The annotations were made "
                f"against different pixels, so the file must not be used. "
                f"Restore it from backup."
            )

        from .png_reader import read_image

        image, _ = read_image(retained, device_policy)
        image.meta = case.source
        if self.settings.keep_working_copies:
            try:
                write_working_copy(image, working)
            except OSError:
                pass
        return image

    def verify_case_integrity(self, case: Case) -> dict:
        """Confirm the retained original still matches its recorded checksum."""
        retained = Path(case.source.stored_path)
        if not retained.exists():
            retained = self.paths.source_path(
                case.source.sha256, safe_filename(case.source.original_filename, "image")
            )
        if not retained.exists():
            return {
                "ok": False, "reason": "missing",
                "message": f"The retained source file is missing at {retained}.",
            }
        actual = sha256_file(retained)
        if actual != case.source.sha256:
            return {
                "ok": False, "reason": "checksum_mismatch",
                "message": (
                    "The retained source file no longer matches the checksum "
                    "recorded at import."
                ),
                "expected": case.source.sha256, "actual": actual,
            }
        return {"ok": True, "reason": "", "message": "The source file matches its checksum."}
