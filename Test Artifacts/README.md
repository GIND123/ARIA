# Test fixtures

Four sample images used by the test suite and by `scripts/ui_smoke.py`.

| File | What it is |
| --- | --- |
| `fixture_panoramic_01.dcm` | Full panoramic radiograph, DICOM Part 10, 2868 by 1504, 14 bit stored in 16 bit words, MONOCHROME2, with pixel spacing present |
| `fixture_panoramic_02.dcm` | A second panoramic radiograph with the same characteristics |
| `fixture_cropped_01.png` | Cropped region as an 8 bit RGB PNG, which exercises the colour to grey display conversion |
| `fixture_cropped_02.png` | A second cropped region |

## The metadata is fabricated

These are real radiographs, and their **pixel data is unchanged**. Everything
else about them is not what it says it is.

`scripts/sanitise_fixtures.py` replaced, before publication:

* every instance, series, study and frame of reference identifier, rewritten
  under `1.2.826.0.1.3680043.10.9999`, a synthetic root registered to nobody,
* every date and time, set to 15 January 2020 at 10:15,
* the device manufacturer, model and software version, set to
  "Example Imaging Systems" and "Example Panoramic Unit",
* the device serial number, removed,
* all vendor private tags, removed,
* the patient's age and sex, set to 42 and female.

None of those values describe anything real. They are there so the files
behave like ordinary DICOM images and exercise every code path, while leading
nowhere if anyone follows them.

The files carry `PatientIdentityRemoved = YES` and a
`DeidentificationMethod` describing this.

**Do not cite the acquisition date, the device or the patient characteristics
of these files for any purpose.** They are test fixtures, not data.

## They are optional

The test suite skips the fixtures that need these files when the folder is
absent, so a checkout without them still runs. To use your own images, drop
any DICOM Part 10 or PNG panoramic radiographs into this folder.

## Reproducing the sanitisation

```bash
python scripts/sanitise_fixtures.py --check     # report what would change
python scripts/sanitise_fixtures.py --apply     # rewrite in place
```

The script verifies its own output and refuses to report success if anything
traceable survives.
