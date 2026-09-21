"""The ARIA training bundle: one archive holding raw images, labels and metadata.

The bundle exists so a dataset can be handed to whoever will train a model later
without them having to reconstruct context from a folder of loose files. It is
self describing: a manifest states what is inside and how it was produced, a
data dictionary documents every column, and a checksum file lets a recipient
prove nothing changed in transit.

Layout
------
::

    manifest.json              what this bundle is, how it was made, what it holds
    README.md                  how to read the bundle, written for the recipient
    checksums.sha256           digest of every file, in the standard tool format
    privacy_scan.json          result of the identifier scan run before writing
    metadata/                  tabular metadata and the data dictionary
    raw/<pseudonym>/           the retained original image, bit for bit
    annotations/<pseudonym>/   geometry and derived measurements, kept separate
    masks/<pseudonym>/         indexed mask and class map
    coco/instances.json        COCO compatible segmentation for the whole set

Raw images are the retained originals, unchanged (FR 002). The deidentification
that applies to them is the profile applied at import, and the manifest records
which profile that was.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ...version import APP_NAME, APP_VERSION, BUNDLE_FORMAT_VERSION, version_block
from ...core.models import utc_now
from ...core.schema import LABEL_CLASSES, MCI_DEFINITIONS, ProjectSchema
from ..deident import date_findings, scan_payload


@dataclass
class BundleOptions:
    include_raw: bool = True
    include_masks: bool = True
    include_coco: bool = True
    include_texture: bool = True
    include_agreement: bool = False
    include_dicom_output: bool = False
    compression_level: int = 6
    #: Refuse to write when the identifier scan finds anything.
    fail_on_privacy_finding: bool = True

    def to_dict(self) -> dict:
        return {
            "include_raw": self.include_raw,
            "include_masks": self.include_masks,
            "include_coco": self.include_coco,
            "include_texture": self.include_texture,
            "include_agreement": self.include_agreement,
            "include_dicom_output": self.include_dicom_output,
            "compression_level": self.compression_level,
            "fail_on_privacy_finding": self.fail_on_privacy_finding,
        }


@dataclass
class BundleResult:
    path: Path | None = None
    n_cases: int = 0
    n_files: int = 0
    bytes_written: int = 0
    sha256: str = ""
    privacy_findings: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    manifest: dict = field(default_factory=dict)
    cancelled: bool = False

    def summary(self) -> str:
        if self.cancelled:
            return "The bundle was cancelled before it was written."
        size_mb = self.bytes_written / (1024 * 1024)
        return (
            f"{self.n_cases} {'case' if self.n_cases == 1 else 'cases'} written to "
            f"{self.path.name if self.path else 'the bundle'}, "
            f"{self.n_files} files, {size_mb:.1f} MB."
        )


class PrivacyScanFailed(RuntimeError):
    """Raised when the identifier scan finds something before writing."""

    def __init__(self, findings):
        self.findings = findings
        lines = "\n".join(
            f"  {f['path']}: {f['message']}" for f in findings[:10]
        )
        super().__init__(
            f"The bundle was not written because the identifier scan found "
            f"{len(findings)} {'item' if len(findings) == 1 else 'items'} that "
            f"must not leave the institution:\n{lines}"
        )


README_TEMPLATE = """# {project_name} annotation bundle

Produced by {app} {version} on {generated}.

This archive holds the annotated panoramic radiographs for {n_cases} cases,
together with the labels, the derived measurements and the metadata needed to
interpret them.

## What is in here

| Folder | Contents |
| --- | --- |
| `metadata/` | Tabular metadata, one documented variable per column, and `data_dictionary.csv` describing every one of them. |
| `raw/` | The retained original image for each case, unchanged from import. |
| `annotations/` | Per case geometry in `annotations.json` and derived values in `measurements.json`. |
| `masks/` | An indexed PNG per case plus the class map that gives each palette index its meaning. |
| `coco/` | COCO compatible segmentation for the classes that have area. |

## Reading the labels

Coordinates are in original image pixels. The origin is the top left corner of
the top left pixel, x increases to the right and y increases downward. Nothing
in the viewer, including zoom, windowing and inversion, affects them.

Geometry and derived measurements are kept in separate files on purpose. Every
derived value names the annotations it came from and the version of the rules
that produced it, so the measurements can be recomputed from the geometry and
checked against what is recorded here.

## Units

A `value_mm` column that is empty means the case had no validated spatial
calibration. Millimetre values are never inferred from an unvalidated scale.
Pixel values and dimensionless ratios such as the panoramic mandibular index are
present for every case regardless of calibration.

## Absent structures

A structure that was not annotated is not recorded as a zero coordinate. It
appears in `metadata/omissions.csv` and in the `omissions` block of each case
document, with the reason it is absent: not visible, not assessable, anatomy
absent, uncertain, or simply not recorded.

## Cortical index grades

{mci_definitions}

## Integrity

`checksums.sha256` lists a digest for every file. Verify the archive with:

    sha256sum -c checksums.sha256

## Intended use

This is a research annotation dataset. It does not contain a diagnosis, it does
not estimate bone mineral density, and any screening rule outcome recorded here
is a configurable project rule rather than a clinical determination.
"""


class BundleExporter:
    """Builds the training bundle."""

    def __init__(self, repository, paths, settings, schema: ProjectSchema | None = None):
        self.repo = repository
        self.paths = paths
        self.settings = settings
        self.schema = schema or ProjectSchema()

    def build(
        self, destination, project, case_bundles, options: BundleOptions | None = None,
        filters: dict | None = None, progress=None, should_cancel=None,
    ) -> BundleResult:
        """Write the bundle.

        ``case_bundles`` is a list of ``(CaseData, annotator, reviewer, texture)``.
        """
        from .csv_export import export_tables
        from .json_export import geometry_document, measurements_document
        from .mask_export import build_class_index, class_map_document, coco_document
        from .mask_export import rasterise_case, verify_round_trip, write_indexed_png

        options = options or BundleOptions()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = BundleResult(path=destination)

        import tempfile

        staging = Path(tempfile.mkdtemp(prefix="aria_bundle_"))
        try:
            total_steps = max(1, len(case_bundles) + 4)
            step = 0

            def report(message: str) -> None:
                if progress is not None:
                    progress(step, total_steps, message)

            report("Preparing")

            # -- per case documents ---------------------------------------
            geometry_documents: list = []
            class_index = build_class_index()
            mask_checks: list = []

            for data, annotator, reviewer, texture in case_bundles:
                if should_cancel is not None and should_cancel():
                    result.cancelled = True
                    return result
                step += 1
                report(f"Writing {data.case.pseudonym}")

                pseudonym = data.case.pseudonym
                geometry = geometry_document(data, project, annotator, reviewer)
                geometry_documents.append(geometry)
                measurements = measurements_document(
                    data, project, self.schema, annotator, texture
                )

                case_dir = staging / "annotations" / pseudonym
                case_dir.mkdir(parents=True, exist_ok=True)
                (case_dir / "annotations.json").write_text(
                    json.dumps(geometry, indent=2, default=str), encoding="utf-8"
                )
                (case_dir / "measurements.json").write_text(
                    json.dumps(measurements, indent=2, default=str), encoding="utf-8"
                )

                if options.include_raw:
                    source = Path(data.case.source.stored_path)
                    if not source.exists():
                        from ..fsutil import safe_filename

                        source = self.paths.source_path(
                            data.case.source.sha256,
                            safe_filename(data.case.source.original_filename, "image"),
                        )
                    if source.exists():
                        raw_dir = staging / "raw" / pseudonym
                        raw_dir.mkdir(parents=True, exist_ok=True)
                        suffix = ".dcm" if data.case.source.source_format == "dicom" else ".png"
                        import shutil

                        shutil.copyfile(source, raw_dir / f"{pseudonym}{suffix}")
                    else:
                        result.warnings.append(
                            f"The retained original for {pseudonym} was not found, "
                            f"so it is not in the bundle."
                        )

                if options.include_masks:
                    shape = (data.case.source.rows, data.case.source.columns)
                    if shape[0] > 0 and shape[1] > 0:
                        label, written, overlaps = rasterise_case(data, shape, class_index)
                        mask_dir = staging / "masks" / pseudonym
                        mask_dir.mkdir(parents=True, exist_ok=True)
                        mask_path = mask_dir / "mask.png"
                        write_indexed_png(mask_path, label, class_index)
                        check = verify_round_trip(mask_path, label)
                        mask_checks.append({"case": pseudonym, **check})
                        if not check["ok"]:
                            result.warnings.append(
                                f"The mask for {pseudonym} did not round trip: "
                                f"{check.get('reason', '')}"
                            )
                        document = class_map_document(class_index)
                        document["written_classes"] = written
                        document["overlaps"] = overlaps
                        document["image_shape"] = {"rows": shape[0], "columns": shape[1]}
                        document["round_trip_check"] = check
                        (mask_dir / "classmap.json").write_text(
                            json.dumps(document, indent=2, default=str), encoding="utf-8"
                        )

            # -- tabular metadata ------------------------------------------
            step += 1
            report("Writing metadata tables")
            export_tables(staging / "metadata", case_bundles, project, self.schema)

            if options.include_coco:
                step += 1
                report("Writing segmentation index")
                coco_dir = staging / "coco"
                coco_dir.mkdir(parents=True, exist_ok=True)
                (coco_dir / "instances.json").write_text(
                    json.dumps(coco_document(case_bundles, class_index), indent=2, default=str),
                    encoding="utf-8",
                )

            # -- privacy scan ----------------------------------------------
            step += 1
            report("Scanning for identifiers")
            findings: list = []
            for doc in geometry_documents:
                findings.extend(
                    f.to_dict()
                    for f in scan_payload(doc, self.settings.prohibited_terms)
                )
            date_notes = []
            for doc in geometry_documents:
                date_notes.extend(f.to_dict() for f in date_findings(doc))
            result.privacy_findings = findings

            scan_document = {
                "generated_at": utc_now(),
                "n_documents_scanned": len(geometry_documents),
                "prohibited_terms_configured": len(self.settings.prohibited_terms),
                "findings": findings,
                "date_notes": date_notes,
                "result": "clean" if not findings else "findings present",
                "note": (
                    "This scan is a safety net behind the deidentification applied "
                    "at import. A clean result means no direct identifier was "
                    "found in the exported records."
                ),
            }
            (staging / "privacy_scan.json").write_text(
                json.dumps(scan_document, indent=2, default=str), encoding="utf-8"
            )

            if findings and options.fail_on_privacy_finding:
                raise PrivacyScanFailed(findings)

            # -- manifest and readme ---------------------------------------
            manifest = self._manifest(project, case_bundles, options, filters, mask_checks)
            manifest["privacy_scan"] = {
                "result": scan_document["result"],
                "n_findings": len(findings),
            }
            result.manifest = manifest
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2, default=str), encoding="utf-8"
            )
            (staging / "README.md").write_text(
                README_TEMPLATE.format(
                    project_name=project.name if project else "ARIA",
                    app=APP_NAME, version=APP_VERSION, generated=utc_now(),
                    n_cases=len(case_bundles),
                    mci_definitions="\n".join(
                        f"* **{g.display}**: {text}" for g, text in MCI_DEFINITIONS.items()
                    ),
                ),
                encoding="utf-8",
            )

            # -- checksums --------------------------------------------------
            step += 1
            report("Computing checksums")
            self._write_checksums(staging)

            # -- archive ----------------------------------------------------
            report("Compressing")
            n_files, written_bytes = self._zip_directory(
                staging, destination, options.compression_level, should_cancel
            )
            if should_cancel is not None and should_cancel():
                result.cancelled = True
                if destination.exists():
                    destination.unlink()
                return result

            result.n_cases = len(case_bundles)
            result.n_files = n_files
            result.bytes_written = destination.stat().st_size
            result.sha256 = self._sha256(destination)
            report("Finished")
            return result

        finally:
            import shutil

            shutil.rmtree(staging, ignore_errors=True)

    # -- helpers -------------------------------------------------------------

    def _manifest(self, project, case_bundles, options, filters, mask_checks) -> dict:
        from ...core.schema import CLASS_BY_KEY

        by_state: dict = {}
        by_split: dict = {}
        calibrated = 0
        for data, *_rest in case_bundles:
            by_state[data.case.state] = by_state.get(data.case.state, 0) + 1
            split = data.case.split or "unassigned"
            by_split[split] = by_split.get(split, 0) + 1
            if data.case.calibration.millimetres_available:
                calibrated += 1

        return {
            "bundle_format_version": BUNDLE_FORMAT_VERSION,
            "generated_at": utc_now(),
            "generated_by": APP_NAME,
            "application_version": APP_VERSION,
            "project": {
                "id": project.id if project else "",
                "name": project.name if project else "",
                "deid_profile": project.deid_profile if project else "",
            },
            "versions": version_block(),
            "options": options.to_dict(),
            "filters": dict(filters or {}),
            "counts": {
                "cases": len(case_bundles),
                "by_state": by_state,
                "by_split": by_split,
                "with_validated_calibration": calibrated,
                "without_validated_calibration": len(case_bundles) - calibrated,
            },
            "schema": self.schema.to_dict(),
            "label_classes": [
                {
                    "key": c.key,
                    "display_name": c.display_name,
                    "short_code": c.short_code,
                    "geometry": c.geometry.value,
                    "category": c.category.value,
                    "colour": c.colour,
                    "side_scoped": c.side_scoped,
                    "aliases": list(c.aliases),
                    "description": c.description,
                }
                for c in LABEL_CLASSES
            ],
            "mask_round_trip_checks": mask_checks,
            "layout": {
                "metadata/": "Tabular metadata and the data dictionary",
                "raw/": "Retained original images, unchanged from import",
                "annotations/": "Per case geometry and derived measurements",
                "masks/": "Indexed masks with class maps",
                "coco/": "COCO compatible segmentation",
                "checksums.sha256": "Digest of every file in the bundle",
                "privacy_scan.json": "Identifier scan run before the bundle was written",
            },
            "intended_use": (
                "Research annotation dataset. Not a diagnosis, not a bone "
                "mineral density estimate, and not a treatment recommendation."
            ),
        }

    @staticmethod
    def _sha256(path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _write_checksums(self, root: Path) -> None:
        lines: list = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "checksums.sha256":
                relative = path.relative_to(root).as_posix()
                lines.append(f"{self._sha256(path)}  {relative}")
        (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _zip_directory(root: Path, destination: Path, level: int, should_cancel) -> tuple:
        files = [p for p in sorted(root.rglob("*")) if p.is_file()]
        total = 0
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level
        ) as archive:
            for path in files:
                if should_cancel is not None and should_cancel():
                    break
                archive.write(path, path.relative_to(root).as_posix())
                total += path.stat().st_size
        return len(files), total


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_bundle(path) -> dict:
    """Check a bundle is complete and that every checksum matches.

    Run against a bundle after it has been copied or transferred, which is when
    a truncated file usually shows up.
    """
    path = Path(path)
    report = {
        "path": str(path), "ok": False, "n_files": 0, "verified": 0,
        "mismatched": [], "missing": [], "issues": [],
    }
    if not path.exists():
        report["issues"].append("The bundle file does not exist.")
        return report

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            report["n_files"] = len(names)

            for required in ("manifest.json", "README.md", "checksums.sha256"):
                if required not in names:
                    report["issues"].append(f"{required} is missing from the bundle.")

            broken = archive.testzip()
            if broken:
                report["issues"].append(f"The archive entry {broken} is corrupt.")
                return report

            if "manifest.json" in names:
                manifest = json.loads(archive.read("manifest.json"))
                report["manifest"] = {
                    "bundle_format_version": manifest.get("bundle_format_version"),
                    "generated_at": manifest.get("generated_at"),
                    "n_cases": manifest.get("counts", {}).get("cases"),
                    "project": manifest.get("project", {}).get("name"),
                }

            if "checksums.sha256" not in names:
                report["issues"].append("No checksum file, so content cannot be verified.")
                return report

            expected: dict = {}
            for line in archive.read("checksums.sha256").decode("utf-8").splitlines():
                if not line.strip():
                    continue
                digest, _, name = line.partition("  ")
                expected[name.strip()] = digest.strip()

            for name, digest in expected.items():
                if name not in names:
                    report["missing"].append(name)
                    continue
                actual = hashlib.sha256(archive.read(name)).hexdigest()
                if actual != digest:
                    report["mismatched"].append(name)
                else:
                    report["verified"] += 1

    except zipfile.BadZipFile as exc:
        report["issues"].append(f"The file is not a readable archive: {exc}")
        return report
    except (KeyError, ValueError) as exc:
        report["issues"].append(f"The bundle could not be read: {exc}")
        return report

    report["ok"] = not (report["issues"] or report["mismatched"] or report["missing"])
    report["summary"] = (
        f"{report['verified']} of {len(expected)} files verified."
        if report["ok"]
        else "The bundle did not verify."
    )
    return report
