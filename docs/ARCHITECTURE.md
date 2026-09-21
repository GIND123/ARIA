# ARIA architecture

Written for: engineers joining the project or reviewing it before deployment.

## What this is

ARIA is a desktop application for annotating dental panoramic radiographs and
producing machine readable mandibular radiomorphometric data. It runs entirely
on the workstation. There is no server, no network layer and no model.

## Shape of the system

```
                       ┌──────────────────────────────────────────┐
                       │                 aria.ui                  │
                       │  main_window · controller · panels ·     │
                       │  viewer · dialogs · theme · icons        │
                       └───────────────────┬──────────────────────┘
                                           │  the only door to data
                       ┌───────────────────▼──────────────────────┐
                       │              aria.store                  │
                       │  repository (audit + locking) · db       │
                       └───────────────────┬──────────────────────┘
        ┌──────────────────────────────────┼──────────────────────────────┐
        │                                  │                              │
┌───────▼────────┐              ┌──────────▼─────────┐        ┌───────────▼────────┐
│   aria.core    │              │      aria.io       │        │  aria.security     │
│ schema         │              │ guards · readers   │        │ auth · crypto      │
│ geometry       │              │ deident · importer │        └────────────────────┘
│ units          │              │ image · exporters  │        ┌────────────────────┐
│ measurements   │              └────────────────────┘        │  aria.platform     │
│ texture        │                                            │ system_check       │
│ agreement      │                                            │ selftest           │
│ validation     │                                            └────────────────────┘
│ audit · models │
└────────────────┘
```

`aria.core` has no Qt and no database import. That is not tidiness for its own
sake: it is what lets the measurement rules be tested directly, and it is what
makes `reproduce_from_export` able to recompute every derived value from an
export document with nothing else present.

## Layers

### aria.core

Pure domain logic.

| Module | Responsibility |
| --- | --- |
| `schema.py` | The single registry of label classes, colours, sides, grades, states and project configuration. Everything else reads from it. |
| `geometry.py` | Distances, tangents, intersections, rasterisation, and the construction of the index lines the protocol defines. |
| `units.py` | Calibration, including the separation of detector spacing from anatomical magnification, and the rule that gates millimetre output. |
| `measurements.py` | The derived values and their provenance. Carries `CALC_VERSION`. |
| `texture.py` | Co-occurrence, fractal dimension, local binary patterns and run length features, implemented on numpy so results are reproducible from the recorded parameters. |
| `agreement.py` | Point, line, contour, mask and categorical agreement, plus intraclass correlation and Bland and Altman. |
| `validation.py` | The submission gate. Every issue carries a remedy. |
| `audit.py` | Event taxonomy and the hash chain. |
| `models.py` | Plain records shared by every layer. |

### aria.io

Everything that touches a file.

`guards.py` runs before any decoder. It checks extension, then magic bytes, then
the declared header dimensions, then available memory and disk. A file is only
decoded once it has passed all of that, which is why a malformed or hostile file
produces an explanation rather than an out of memory failure.

`importer.py` is the pipeline: inspect, checksum, retain the original unchanged,
decode, deidentify, write a working copy and a thumbnail, record the case. A
failure at any step rolls back what the earlier steps created.

`exporters/` holds the four output paths, deliberately separate: geometry as
JSON, tables as CSV with a data dictionary, masks as indexed PNG and COCO, and
derived DICOM objects behind an interoperability gate. `bundle.py` assembles
them into one verifiable archive.

### aria.store

`db.py` owns the SQLite connection. Two settings carry most of the weight:
`journal_mode = WAL` so a reader works while a writer commits, and
`synchronous = FULL` so a committed change reaches the device before it is
acknowledged. Autosave is durable because of the second one.

Append only history is enforced by triggers on `audit_log` and `revisions`, so
it is a property of the file rather than a convention the application is trusted
to follow.

`repository.py` is the only module that writes. Every state changing method
writes its audit record inside the same transaction as the change, and every
write to an annotation set checks the expected edit counter.

### aria.ui

`controller.py` is the only place the interface talks to the store. Panels emit
intent, the controller applies it, and signals carry the result back. Autosave,
the undo journal, the audit trail and optimistic locking all live in that one
place instead of being repeated in a dozen widgets.

`viewer/canvas.py` sets the rule the whole product rests on: the graphics scene
*is* the image pixel coordinate system. One scene unit is one image pixel, and
zoom and pan change only the view transform. A stored coordinate therefore
cannot move when the display changes, which is structural rather than something
the code has to remember.

### aria.platform

`system_check.py` is the compatibility check. `selftest.py` is the self test
suite, which also runs under pytest, so the build pipeline and the installed
application check exactly the same things.

## Key decisions

**One indexed plane for masks.** A mask export is a single indexed PNG whose
palette index is the class index. Colour is never used to recover a class. Where
two classes overlap, the higher index wins, the overlap is recorded, and any
class that ends up completely covered is marked as occluded in `classmap.json`
rather than silently dropped.

**Geometry assists are not predictions.** Constructing the cortical width line
is deterministic geometry from the annotator's own contours. It is offered as an
editable proposal that states how it was built, and it is stored with
`origin: geometric_construction` so a reviewer can see which objects began that
way.

**Millimetres are gated, ratios are not.** A millimetre value is produced only
from a validated calibration. A dimensionless ratio such as the panoramic
mandibular index is produced regardless, because a ratio does not need a scale,
and the basis used is recorded on the value.

**Detector spacing is not anatomical scale.** A panoramic unit magnifies
differently in the vertical and the horizontal direction. `Calibration` keeps
the detector scale and the magnification correction as separate fields, and the
correction is refused unless the project has enabled it against an approved
device policy.

**Texture parameters travel with the result.** Region size changes fractal
dimension values, so the size, the box size ladder, the quantisation levels and
the method name are written with every feature set.

**Absence is a state, not a coordinate.** A structure that cannot be annotated
carries a presence state and a reason. Nothing downstream can mistake it for a
measurement at the origin.

## Data flow for one annotation

```
 annotator drags a handle
   → HandleItem moves within the graphics scene (image pixel coordinates)
   → canvas emits annotation_edited, repeatedly, during the gesture
   → gesture ends, canvas emits annotation_edit_finished
   → Controller.update_annotation_points(commit=True)
       → Repository.save_annotation
           ├─ checks the expected edit counter, refusing a stale write
           ├─ writes the annotation row
           └─ writes the audit record, in the same transaction
       → pushes one entry onto the persisted undo journal
   → Controller.recompute
       → MeasurementEngine produces values with their source identifiers
       → validation runs, the panel and the toolbar update
```

One database transaction per gesture, not per mouse move. That is what makes
autosave both durable and meaningful to undo.

## Storage

```
<data>/aria.db              SQLite, WAL, full synchronous
<data>/sources/ab/cd/…      retained originals, content addressed, never modified
<data>/working/<case>.npz   decoded pixels, derived and rebuildable
<data>/exports/             exports and bundles
<data>/backups/             rolling database backups
<data>/journal/             crash recovery journal
<config>/settings.json      preferences and policy
<config>/vault.key          encryption key, owner readable only
```

## Testing

* `tests/test_acceptance.py` is the specification's acceptance criteria as
  executable tests, one group per criterion.
* `tests/test_functional.py` covers the requirements outside those criteria.
* `tests/test_smoke.py` runs the in application self tests under pytest.
* `scripts/ui_smoke.py` builds the real window and drives it end to end
  offscreen, which catches interface errors a unit test cannot reach.

## Packaging

PyInstaller, one folder rather than one file, because a single file executable
unpacks itself on every launch and that both slows startup and provokes endpoint
protection software. Windows gets an Inno Setup installer; macOS gets a signed
and notarised bundle in a disk image. The build scripts run the tests first and
stop on failure, then run the packaged binary's own `--self-test` before
producing an installer.
