"""Single source of truth for version identifiers used across ARIA.

Several of these version strings are written into exported records so that a
measurement can always be traced back to the exact rules that produced it
(FR 037, FR 053).
"""

APP_NAME = "ARIA"
APP_LONG_NAME = "ARIA: Anatomy aware Radiomorphometric Index Annotator"
APP_VENDOR = "DiceMed"
APP_VERSION = "1.0.0"

#: Version of the annotation label schema. Bumped when label classes change.
SCHEMA_VERSION = "1.0.0"

#: Version of the derived measurement rules in aria.core.measurements.
CALC_VERSION = "1.0.0"

#: Version of the texture feature implementations in aria.core.texture.
TEXTURE_VERSION = "1.0.0"

#: Version of the on disk SQLite structure. Drives migrations.
DB_SCHEMA_VERSION = 1

#: Version of the training bundle layout written by the bundle exporter.
BUNDLE_FORMAT_VERSION = "1.0.0"

#: Version of the deidentification profile definition format.
DEID_PROFILE_VERSION = "1.0.0"


def version_block() -> dict:
    """Return every version identifier, for stamping into exports."""
    return {
        "application": APP_NAME,
        "application_version": APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "calculation_version": CALC_VERSION,
        "texture_version": TEXTURE_VERSION,
        "database_schema_version": DB_SCHEMA_VERSION,
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "deid_profile_version": DEID_PROFILE_VERSION,
    }
