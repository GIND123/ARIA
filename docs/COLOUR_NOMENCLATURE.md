# Colour and label nomenclature

Written for: annotators learning the scheme, and anyone reading an ARIA export.

Generated from `aria/core/schema.py`. Do not edit by hand; run `python scripts/make_docs.py`.

## The rule behind the scheme

Colour is never the only carrier of meaning. Every distinction ARIA draws is also carried by something that survives a monochrome print, a projector, or colour vision deficiency:

* **side** is carried by stroke pattern, solid for right and dashed for left, and by the side letter in the label chip,
* **class** is carried by a short code drawn beside the geometry,
* **state** is carried by a glyph and a word, not by colour alone.

Base colours come from the Okabe and Ito qualitative palette, which stays separable under the common forms of colour vision deficiency.

## Palette

| Name | Value | Contrast on the canvas | Used by |
| --- | --- | --- | --- |
| orange | `#E69F00` | 8.64:1 | `CRS`, `MF`, `MFi`, `MFs` |
| sky blue | `#56B4E9` | 8.44:1 | `ME`, `PMIs`, `SYM` |
| bluish green | `#009E73` | 5.69:1 | `AG`, `AI`, `TRB` |
| yellow | `#F0E442` | 14.72:1 | `PER` |
| blue | `#0072B2` | 3.75:1 | `PMIi` |
| vermillion | `#D55E00` | 5.03:1 | `MCIr`, `MCW` |
| reddish purple | `#CC79A7` | 6.36:1 | `GI`, `GO` |
| black | `#000000` | 1.08:1 | not used for labels |
| bone white | `#ECECEC` | 16.48:1 | `END` |
| slate | `#8C9BAB` | 6.85:1 | `PRB`, `USR` |
| teal | `#2FA3A3` | 6.39:1 | `HMN`, `MAN` |
| amber deep | `#B87400` | 5.13:1 | `CTX`, `MFb` |

## Label classes

Short codes are what appear beside the geometry on the image and in the object list.

### Landmarks

| Code | Label | Geometry | Colour | Sided | Required | Specification |
| --- | --- | --- | --- | --- | --- | --- |
| `MF` | Mental foramen centre | point | `#E69F00` | yes | yes | FR 018 |
| `MFs` | Mental foramen superior margin | point | `#E69F00` | yes | yes | FR 018, FR 025 |
| `MFi` | Mental foramen inferior margin | point | `#E69F00` | yes | yes | FR 018, FR 026 |
| `MFb` | Mental foramen bounding box | box | `#B87400` | yes | no | FR 018 |
| `AG` | Antegonial point | point | `#009E73` | yes | yes | FR 021, FR 023 |
| `GO` | Gonion | point | `#CC79A7` | yes | yes | FR 021, FR 024 |
| `ME` | Menton | point | `#56B4E9` | no | yes | FR 021 |

* **MF**, Mental foramen centre. Centre of the mental foramen on the annotated side. Anchors the perpendicular used for cortical width and the panoramic mandibular index.
* **MFs**, Mental foramen superior margin. Superior margin of the mental foramen outline. Depends on mental_foramen_centre.
* **MFi**, Mental foramen inferior margin. Inferior margin of the mental foramen outline. Depends on mental_foramen_centre.
* **MFb**, Mental foramen bounding box. Optional bounding box around the mental foramen.
* **AG**, Antegonial point. Deepest point of the antegonial notch on the inferior border of the mandible.
* **GO**, Gonion. Intersection of the tangent to the posterior border of the ramus and the tangent to the inferior border of the mandible.
* **ME**, Menton. Most inferior midline point of the mandibular symphysis.

### Contours

| Code | Label | Geometry | Colour | Sided | Required | Specification |
| --- | --- | --- | --- | --- | --- | --- |
| `PER` | Inferior cortex, periosteal border | polyline | `#F0E442` | yes | yes | FR 019 |
| `END` | Inferior cortex, endosteal border | polyline | `#ECECEC` | yes | yes | FR 019 |
| `PRB` | Posterior ramus border | polyline | `#8C9BAB` | yes | no | FR 024 |

* **PER**, Inferior cortex, periosteal border. Outer surface of the mandibular inferior cortex, traced from the symphysis toward the gonial region.
* **END**, Inferior cortex, endosteal border. Inner surface of the mandibular inferior cortex, traced over the same span as the periosteal border.
* **PRB**, Posterior ramus border. Posterior border of the ramus. Supplies the ramus tangent used to construct the gonial bisector.

### Index lines

| Code | Label | Geometry | Colour | Sided | Required | Specification |
| --- | --- | --- | --- | --- | --- | --- |
| `MCW` | Mandibular cortical width | line | `#D55E00` | yes | yes | FR 022, FR 032 |
| `PMIs` | PMI superior height | line | `#56B4E9` | yes | yes | FR 025, FR 033 |
| `PMIi` | PMI inferior height | line | `#0072B2` | yes | yes | FR 026, FR 034 |
| `AI` | Antegonial index | line | `#009E73` | yes | yes | FR 023, FR 035 |
| `GI` | Gonial index | line | `#CC79A7` | yes | yes | FR 024, FR 035 |

* **MCW**, Mandibular cortical width. Two endpoint line from the periosteal to the endosteal cortex along the perpendicular through the mental foramen centre. Stored under the canonical names Mandibular Cortical Width and Mental Index. Also known as Cortical Width Index, CWI, Mental Index, MI. Depends on mental_foramen_centre, periosteal_border, endosteal_border.
* **PMIs**, PMI superior height. Height from the superior mental foramen margin to the inferior mandibular border along the cortical width axis. Depends on mental_foramen_superior, mcw_line.
* **PMIi**, PMI inferior height. Height from the inferior mental foramen margin to the inferior mandibular border along the same axis. Depends on mental_foramen_inferior, mcw_line.
* **AI**, Antegonial index. Cortical thickness line drawn perpendicular to the cortex at the antegonial point. Depends on antegonial_point.
* **GI**, Gonial index. Cortical thickness line through gonion along the bisector of the posterior ramus tangent and the inferior border tangent. Depends on gonion.

### Regions

| Code | Label | Geometry | Colour | Sided | Required | Specification |
| --- | --- | --- | --- | --- | --- | --- |
| `CTX` | Inferior cortical mask | mask | `#B87400` | yes | no | FR 019 |
| `MAN` | Mandible, whole | polygon | `#2FA3A3` | no | no | FR 020 |
| `HMN` | Hemimandible | polygon | `#2FA3A3` | yes | no | FR 020 |
| `MCIr` | MCI assessment region | box | `#D55E00` | yes | yes | FR 027 |

* **CTX**, Inferior cortical mask. Optional filled mask of the cortical band between the periosteal and endosteal borders. Depends on periosteal_border, endosteal_border.
* **MAN**, Mandible, whole. Whole mandible outline as a single polygon mask.
* **HMN**, Hemimandible. Paired hemimandible polygon mask for the annotated side.
* **MCIr**, MCI assessment region. Region of inferior cortex distal to the mental foramen that the Klemetti grade was assigned from. Depends on mental_foramen_centre.

### Optional

| Code | Label | Geometry | Colour | Sided | Required | Specification |
| --- | --- | --- | --- | --- | --- | --- |
| `TRB` | Trabecular bone region | roi_rect | `#009E73` | yes | no | FR 029, FR 030 |
| `CRS` | Alveolar crestal region | roi_rect | `#E69F00` | yes | no | FR 029 |
| `SYM` | Symphysis region | roi_rect | `#56B4E9` | no | no | FR 029 |
| `USR` | User defined landmark | point | `#8C9BAB` | yes | no | FR 029 |

* **TRB**, Trabecular bone region. Square analysis region over trabecular bone. Must exclude roots, the mandibular canal, the cortical border, lesions and obvious artefacts.
* **CRS**, Alveolar crestal region. Analysis region over the alveolar crest.
* **SYM**, Symphysis region. Analysis region over the mandibular symphysis.
* **USR**, User defined landmark. Project specific landmark. The free text name is stored in the annotation properties.

## Stroke pattern by side

| Side | Stroke | Chip |
| --- | --- | --- |
| Right | solid | `CODE R` |
| Left | dashed | `CODE L` |
| Midline | solid | `CODE` |

A locked object is drawn dotted regardless of side. An object recorded as absent is drawn at reduced opacity, and an ambiguous one carries a dotted ring.

## Case states

| State | Glyph | Meaning |
| --- | --- | --- |
| Unassigned | `○` | Imported, not yet assigned to anyone |
| Assigned | `◔` | Assigned to an annotator, not yet started |
| In progress | `◑` | Being annotated |
| Submitted | `△` | Submitted for review |
| Returned | `↺` | Returned by a reviewer for changes |
| Accepted | `✓` | Accepted by a reviewer |
| Adjudicated | `✔` | Adjudicated between two annotations |

## Presence states

Absence is a recorded state with a reason. It is never a coordinate of zero.

| State | Meaning |
| --- | --- |
| Present | Annotated normally |
| Not visible | Not visible on this image |
| Not assessable | Visible but cannot be assessed reliably |
| Anatomy absent | The structure is not present in this patient |
| Uncertain | Not confident enough to record it |

## Cortical index grades

| Grade | Definition |
| --- | --- |
| C1 | Even and sharp endosteal margin of the inferior cortex on the assessed side. |
| C2 | Semilunar defects, or one to three layers of endosteal cortical residues. |
| C3 | Clearly porous cortical margin with more than three layers of endosteal cortical residues. |
| Not assessable | The inferior cortex distal to the mental foramen cannot be assessed on this image. |
| Uncertain | The cortex is visible but the grade cannot be decided with confidence. |

## Quality flags

| Flag | Notes |
| --- | --- |
| Not visible | A required structure is not visible |
| Ambiguous | The structure is visible but its boundary is unclear |
| Anatomical variant | Anatomy departs from the usual pattern |
| Artefact | Ghost image, metal, or other artefact |
| Cropped anatomy | Required anatomy falls outside the field |
| Poor positioning | Patient positioning affects the measurement |
| Motion | Movement during acquisition |
| Other | Anything else; requires a comment |

## Mask export indices

In an exported mask the palette index carries the class. Colour is for viewing only and must not be used to recover a class.

| Index | Class | Side |
| --- | --- | --- |
| 0 | background | |
| 1 | Mental foramen bounding box | Right |
| 2 | Mental foramen bounding box | Left |
| 3 | Inferior cortex, periosteal border | Right |
| 4 | Inferior cortex, periosteal border | Left |
| 5 | Inferior cortex, endosteal border | Right |
| 6 | Inferior cortex, endosteal border | Left |
| 7 | Inferior cortical mask | Right |
| 8 | Inferior cortical mask | Left |
| 9 | Posterior ramus border | Right |
| 10 | Posterior ramus border | Left |
| 11 | Mandible, whole | Midline |
| 12 | Hemimandible | Right |
| 13 | Hemimandible | Left |
| 14 | MCI assessment region | Right |
| 15 | MCI assessment region | Left |
| 16 | Mandibular cortical width | Right |
| 17 | Mandibular cortical width | Left |
| 18 | PMI superior height | Right |
| 19 | PMI superior height | Left |
| 20 | PMI inferior height | Right |
| 21 | PMI inferior height | Left |
| 22 | Antegonial index | Right |
| 23 | Antegonial index | Left |
| 24 | Gonial index | Right |
| 25 | Gonial index | Left |
| 26 | Trabecular bone region | Right |
| 27 | Trabecular bone region | Left |
| 28 | Alveolar crestal region | Right |
| 29 | Alveolar crestal region | Left |
| 30 | Symphysis region | Midline |

Where two classes overlap, the higher index takes the pixel, the overlap is recorded in `classmap.json`, and a class covered completely is marked as occluded there rather than silently dropped. Its geometry is always present in the annotation document.
