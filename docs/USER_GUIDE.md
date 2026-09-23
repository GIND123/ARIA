# ARIA user guide

Written for: annotators, reviewers and administrators using ARIA.

ARIA is an annotation and research data tool for dental panoramic radiographs.
It records what you measure and how you measured it. It does not diagnose
osteoporosis, it does not estimate bone mineral density, and it does not
recommend treatment.

---

## Contents

1. [First run](#first-run)
2. [Signing in](#signing-in)
3. [The workspace](#the-workspace)
4. [Importing images](#importing-images)
5. [Calibration and units](#calibration-and-units)
6. [Annotating a case](#annotating-a-case)
7. [Recording what is absent](#recording-what-is-absent)
8. [Grading the cortex](#grading-the-cortex)
9. [Quality flags](#quality-flags)
10. [Texture features](#texture-features)
11. [Submitting](#submitting)
12. [Reviewing](#reviewing)
13. [Agreement](#agreement)
14. [Exporting](#exporting)
15. [Administration](#administration)
16. [Keeping your work safe](#keeping-your-work-safe)
17. [When something goes wrong](#when-something-goes-wrong)

---

## First run

The first time ARIA opens it does three things.

**It checks this workstation.** Processor, memory, free space, display,
storage durability, encryption and image preparation speed are measured against
the requirements. Every row says what was measured, what the requirement is and,
where it is not met, what to do about it. You can continue past a failed
requirement deliberately, and that choice is recorded.

**It creates an administrator account.** Everything you do in ARIA is recorded
against an account, which is what makes the history meaningful. The password is
stored as a memory hard digest, never as text, so there is no way to recover it.
Create a second administrator account afterwards.

An administrator imports images, manages the project and reads annotations, but
does not draw them: annotation is the annotator's and the reviewer's work. If
you are running the study on your own, tick **I will also be annotating in this
study** on that page. It creates an annotator account alongside the
administrator one, and you sign in as that account to draw. You can add the same
account later from Administration, Accounts.

**It creates a project.** A project holds its own cases, label schema,
tolerances and privacy profile.

The guided tour then runs. It takes about two minutes, points at the real
controls, and can be skipped. Help, Guided tour brings it back at any time.

---

## Signing in

Enter your username and password. After the number of failed attempts your
administrator has configured, the account locks for a set period.

If you were given a temporary password you will be asked to change it before you
can continue.

---

## The workspace

```
  File  Edit  View  Annotate  Review  Tools  Help
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ← →  Module [Annotate ▾]   import export   undo redo    case  tour  │
 │ ▸ tools · snap · construct · fit · reset · invert · layouts         │
 │ Side [Right ▾]  Label [Mental foramen centre ▾]      Ready to submit│
 ├──────────────┬──────────────────────────────────────────────────────┤
 │              │ M  Main                                  zoom  44%   │
 │   Module     │                                                      │
 │   panel      │                    the image                         │
 │              │                                                      │
 ├──────────────┴──────────────────────────────────────────────────────┤
 │ Position   Stored   Value   Millimetres   Zoom   Window   Region    │
 │ status                             Editing   Assigned   Saved  you  │
 └─────────────────────────────────────────────────────────────────────┘
```

**Modules** group the work. Cases finds and opens an image, Annotate is where
you draw, Measure holds calibration and derived values, Review is for checking
submissions, Export produces files, Administration manages the project and
Audit reads the history. The arrows beside the module selector take you back to
where you were.

**The data probe** along the bottom reports what is under the cursor: the pixel
position, the stored value, the value after rescaling, and the position in
millimetres when a validated calibration exists. When there is no validated
calibration it says so rather than showing a number.

**Layouts.** One pane is the default. Four panes gives the whole image with a
magnified right side, a magnified left side and an overview. Two panes is for
comparing revisions. Any pane can be maximised from the button in its header.

### Mouse and keyboard

| Input | Action |
| --- | --- |
| Left | the active tool |
| Middle drag | pan |
| Right drag | window and level, horizontal is width, vertical is centre |
| Wheel | zoom about the cursor |
| Space and drag | pan without changing tool |
| Arrow keys | nudge the selection by one image pixel |
| Escape | cancel the shape being drawn |
| Enter | finish a contour or region |

Help, Keyboard shortcuts lists everything.

---

## Importing images

File, Import images, or File, Import folder.

ARIA reads DICOM Part 10 files and PNG images. Anything else is refused with an
explanation and a next step.

Every file is checked before anything is imported, so you see exactly what will
be accepted and what will not before you commit. The check looks at the
extension, then the actual content, then the dimensions declared in the header,
then whether there is enough memory and disk. Nothing is decoded until it has
passed all of that.

**The original file is kept unchanged.** ARIA copies it, verifies the copy by
checksum and links your annotations to those exact bytes. If the retained file
is ever altered, ARIA refuses to open it rather than showing you different
pixels from the ones you annotated.

**Identifiers are removed at import**, according to the privacy profile the
project uses. Study and instance identifiers are remapped consistently, so
images of one study stay linked, while the mapping cannot be reversed.

A colour PNG is converted to grey for display only. The colour channels are kept
and the original file is untouched.

**After the import, the Cases list holds what arrived**, with the newest import
selected. Choose **Open** there, or double click the case, to take it into the
Annotate module. If Open is greyed out, nothing is selected: click a case in the
list first.

---

## Calibration and units

This is the part that most affects whether your millimetre values mean anything.

**A DICOM header gives detector spacing, not anatomical scale.** A panoramic
unit magnifies the patient, and it magnifies differently in the vertical and the
horizontal direction. ARIA therefore reads the header spacing, shows it, and
marks it *present, not validated*. Millimetre values stay unavailable until a
reviewer validates it.

To validate: open Measure, read the values shown, and choose Validate. ARIA
refuses values outside the plausible range for a panoramic detector rather than
accepting an obviously wrong scale.

**To calibrate manually**, choose Manual calibration, click the two ends of
something whose true length you know, and enter that length.

**Magnification correction** is switched off until an administrator has approved
a device policy for the project. When it is on you can enter separate vertical
and horizontal factors, and the correction applied is shown beside every value.

**Without a validated calibration** you still get pixel measurements and
dimensionless ratios such as the panoramic mandibular index, because a ratio does
not need a scale. The millimetre column says unavailable. It is never guessed.

---

## Annotating a case

Open a case from Cases, then work in this order. It is quicker than any other
order because the later steps are computed from the earlier ones.

### 1. Confirm the orientation

If the image does not carry laterality, ARIA blocks submission until you confirm
that the patient's anatomical right is displayed on the left of the image. The
letters R and L on the view are there to keep it straight.

### 2. Trace the contours

Choose the periosteal border, press 3 for the contour tool, and click along the
outer surface of the inferior cortex from the symphysis toward the gonial
region. Press Enter or double click to finish.

Do the same for the endosteal border, over the same span.

Repeat for the other side. Press Tab to switch sides.

Right is drawn with a solid stroke and left with a dashed one, so you can tell
them apart without relying on colour.

### 3. Mark the mental foramen

Place the centre, then the superior margin, then the inferior margin. Press 1
for the point tool, or just pick the label and click.

### 4. Construct the index lines

With the contours and the foramen in place, select a label such as Mandibular
cortical width and choose Construct from contours, or press Control and G.

ARIA computes the line the protocol defines: for cortical width, the
perpendicular to the periosteal border at the point nearest the foramen centre,
from the periosteal to the endosteal border. It tells you how it built the line,
and if the construction is incomplete it says so.

This is ordinary geometry from the contours you traced. It is not a prediction
and there is no model involved. The result is an ordinary editable annotation
that is marked as constructed, so a reviewer can see which objects began that
way. Check it and adjust it.

Do the same for the panoramic mandibular index heights, the antegonial index and
the gonial index. The gonial index also needs the posterior ramus border, since
its axis bisects the ramus tangent and the inferior border tangent.

### 5. Place the remaining landmarks

Antegonial point and gonion on each side, and menton on the midline.

### 6. Mark the grading region

Draw the region of inferior cortex distal to the mental foramen that you will
grade from.

### Editing

Press S for the select tool.

* Drag a handle to move a point.
* Hold Shift and drag to move a whole object.
* Hold Alt and click a handle to remove that vertex.
* Double click a contour to add a vertex there.
* Use the arrow keys to nudge by exactly one image pixel, which is the only way
  to place a point precisely at low zoom.

**Snapping** is on by default, so a new point lands on a traced contour rather
than near it. That matters: the cortical width endpoints are supposed to sit on
the borders they are measured between. Press N to turn it off.

---

## Recording what is absent

When a structure cannot be annotated, say so. Open Record as absent, choose the
reason, and apply it to the selected label.

| State | Meaning |
| --- | --- |
| Not visible | The structure is not visible on this image |
| Not assessable | It is visible but cannot be assessed reliably |
| Anatomy absent | The structure is not present in this patient |
| Uncertain | You are not confident enough to record it |

Absence is stored as a state with a reason. It is never written as a coordinate
of zero, so nothing downstream can mistake it for a measurement at the corner of
the image. A side recorded as absent is excluded from bilateral means, and the
exclusion and its reason travel with the mean.

---

## Grading the cortex

Assign a Klemetti grade per side in Cortical index grading.

| Grade | Definition |
| --- | --- |
| C1 | Even and sharp endosteal margin |
| C2 | Semilunar defects, or one to three layers of endosteal cortical residues |
| C3 | Clearly porous margin with more than three layers of residues |
| Not assessable | The cortex distal to the foramen cannot be assessed |
| Uncertain | Visible, but the grade cannot be decided confidently |

Grade from the region you marked. If you choose Uncertain, record why.

The View menu has a Cortex readability filter that lifts local contrast across
the endosteal margin. It changes the display only.

---

## Quality flags

Record anything about the image that affects the annotation: not visible,
ambiguous, anatomical variant, artefact, cropped anatomy, poor positioning,
motion, or other. Other requires a comment.

Flags travel with the case into every export, so someone analysing the dataset
later can see which cases had problems.

---

## Texture features

Place a trabecular, crestal or symphysis region, then choose Compute for this
case in Measure.

Keep a trabecular region clear of roots, the mandibular canal, the cortical
border, lesions and obvious artefacts. ARIA warns if a region appears to overlap
the cortex.

Regions are a fixed size set by the project, because region size changes the
feature values. The size used, the box size ladder, the quantisation levels and
the method are recorded with every result, so the numbers can be reproduced.

Features are computed after annotation, from the region you drew. They are
derived values, never something you type in.

---

## Submitting

The Submission checks section lists what still blocks submission and what to do
about each item. Hover any entry for the full explanation. The toolbar shows the
same state as you work.

Submission is blocked until the mandatory annotations, the side labels, the
orientation confirmation and the required flags are complete.

When you submit, the whole set is frozen as a revision. Later edits become a new
revision; the earlier one is never altered.

---

## Reviewing

Reviewers work in the Review module.

**Submission** shows the set as it stands, with the annotator, the revision and
the counts.

**Comments on labels** attaches a comment to one label, so the annotator knows
exactly what to change. Returning a case requires a summary or at least one
comment.

**Revisions** compares any two revisions, or a revision against the current
working state, and lists what changed: added, removed, moved with the largest
shift, or a changed state.

**Decisions** are Accept, Return or Adjudicate. A returned case keeps its prior
submission and the annotator's changes become a new revision.

---

## Agreement

When a case has been annotated independently by two people, Agreement compares
them.

The second set stays hidden until both have been submitted. This is enforced,
not just convention: you cannot see the other annotator's work early even if you
go looking for it.

The report gives point distance error, line endpoint error, contour distance,
Dice and intersection over union for regions, absolute measurement differences,
and grade agreement. Across several cases it adds intraclass correlation,
Bland and Altman bias and limits, and Cohen or weighted kappa.

**ARIA does not apply a pass mark.** Any threshold shown comes from the project
configuration your protocol approved. A value outside tolerance is highlighted;
what that means is a decision for the protocol, not the software.

---

## Exporting

The Export module filters by state, split and annotator, then writes whichever
formats you choose.

| Format | Contents |
| --- | --- |
| JSON | Geometry in original pixel coordinates, with full provenance |
| CSV | Tables with one documented variable per column, plus a data dictionary |
| Indexed PNG | Masks whose palette index is the class index, with a class map |
| COCO | Segmentation for the classes that have area |
| DICOM | Structured Report and Segmentation, once interoperability is accepted |

Geometry and derived measurements are always separate files, so the
measurements can be recomputed from the geometry and checked.

### The training bundle

One archive holding the raw images, the labels, the derived measurements and a
metadata sheet, with a manifest, a data dictionary and a checksum file.

It is meant to be handed to whoever will train a model later, without them
having to reconstruct context from a folder of loose files. The README inside
explains the coordinate system, the units, how absence is recorded and what the
grades mean.

Before anything is written, every record is scanned for direct identifiers. By
default a finding stops the export rather than being reported afterwards.

Verify an existing bundle at any time with Verify an existing bundle, or from a
terminal with `aria --verify-bundle path`.

---

## Administration

**Projects** creates and renames projects. Archiving a project keeps its cases
and annotations readable.

**Label schema and tolerances** sets which labels are required, the acceptance
tolerances, the analysis region size and the duplicate annotation fraction.
Changing the schema is versioned and existing annotations stay readable.

**Screening thresholds** are project configuration, never label content. A rule
stays inactive until both enabled and recorded as clinically approved, with the
name of the clinician who approved it. Its outcome is always labelled as a
screening rule, never as a diagnosis.

**Accounts** creates users, resets passwords and records that an annotator has
completed the calibration set.

**Privacy profile** chooses the deidentification profile and lists terms the
institution declares prohibited, which every export is scanned for.

**DICOM derived output** stays off until you record the validator result and the
result for each target viewer. An object a receiving system mis-reads is worse
than no object, because the numbers look official.

**Decisions needing clinical sign off** lists the decisions that belong to the
clinical team, with their current state.

---

## Keeping your work safe

Every completed change is written immediately, and the database is configured so
a committed change reaches the device before it is acknowledged. A power loss
cannot lose work you have finished. The status bar shows when the last save
happened.

Undo and redo survive a restart, because the edit history is stored rather than
held in memory.

If two people open the same case, the second gets it read only and is told who
has it. A lock left by a crashed session is reclaimed automatically.

A database backup is taken at startup and kept for the configured number of
copies. File, Back up database takes one on demand.

Uninstalling ARIA does not remove annotation data.

---

## When something goes wrong

**Tools, Run diagnostics** runs the self tests against the installed code and
reports what each one checked. Save the report and include it when asking for
help.

**Tools, Compatibility check** re-runs the workstation check at any time.

**Tools, Verify audit history** recomputes the audit chain and reports whether it
is intact.

**Tools, Check database integrity** runs the storage level checks.

**Help, About, Versions** lists every version identifier, which are the same ones
written into exports.

Logs are in the data folder, under `logs`. Tools, Open data folder takes you
there.

### From a terminal

```
aria --self-test                 run the self tests
aria --system-check              check this workstation
aria --verify-bundle PATH        verify a training bundle
aria --verify-audit              verify the audit chain
aria --version                   print the version block
```

Add `--json` to any of them for machine readable output.
