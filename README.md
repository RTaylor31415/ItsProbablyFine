# Part Study — offline batch inspection results

Select a **folder of PDF inspection reports**, one report per serial/run, and compile their measured results into a readable Excel part study. Runs locally on Windows with Node.js, Python, and Tesseract. No cloud APIs, document uploads to outside services, or telemetry. Internet is only needed to install prerequisites.

This redesign replaces the AS9102 workflow. The supplied `110780-001_MAJORS.xlsx` guides the layout and contributes **77 characteristic definitions**; its historical measurements are not bundled. The original AS9102 template is no longer used.

## Windows installation

Install **Node.js 22+**, **Python 3.11+ with the Windows `py` launcher**, and **Tesseract OCR 5+ with English data**. See the Tesseract project's [installation guidance](https://tesseract-ocr.github.io/tessdoc/Installation.html) for Windows installers.

Download this repository with **Code → Download ZIP**, extract it, then open PowerShell in the extracted folder:

```powershell
.\setup.ps1
.\start.ps1
```

Open **http://127.0.0.1:3210** in Edge or Chrome. Stop the server with Ctrl+C. The startup script detects Tesseract at `C:\Program Files\Tesseract-OCR\tesseract.exe`. For another location, set `TESSERACT_CMD` to the executable path. A `.venv` created for the previous prototype must be refreshed with `setup.ps1` to install the new Excel writer.

If your organization's PowerShell policy restricts scripts, run the equivalent commands manually without changing the policy:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FAI_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
node app/server.mjs
```

## Bulk workflow

1. Enter the study name, part/drawing, and unit label, then **Save details**. Compile one part and revision per study. Values are never converted between units.
2. For this example part, the 77 column definitions are already loaded. For another part, **Load reference .xlsx** before importing any PDFs. The reference needs rows labelled `PCD-ID`, `Nominal`, `U Tol`, and `L Tol`, with characteristics in columns. `Dimension ID` and `Axis` rows are recommended. Only definitions are read, never old serial results. Reference units default to mm; review the definitions before importing.
3. Click **Choose folder**. All PDFs in the folder and subfolders enter the queue in natural filename order. Alternatively select many PDFs with **Select PDFs**. Non-PDF files are skipped. You can append another folder to the same study.
4. **Serial results** shows per-file processing state and matched/missing counts. Uploading and extraction are separate: files queue quickly while one local extractor runs at a time to bound OCR memory. Pause stops new extraction jobs after the current file completes. Failed uploads/scans remain listed and can be retried individually.
5. Open a report, verify its serial number, and inspect source evidence next to the measurement list. Click a characteristic to view its source page and OCR text. Enter missing values or correct OCR mistakes; clear mapping flags only after checking the source. **Save & mark reviewed** confirms that report. Saving edits alone resets its review.
6. Once the queue finishes, **Export Excel workbook**. Unreviewed or incomplete reports can be exported, with their status recorded explicitly. Failed/excluded reports are listed but do not contribute measurements or statistics.

Serial numbers keep leading zeros. Reruns and duplicate serials remain separate rows, identified by source path; no results are silently overwritten. Identical PDF bytes are flagged. Exclude an unwanted duplicate in its review dialog. All included successful reports contribute to the statistics, so include/exclude reruns deliberately.

The active study, uploaded PDFs, queue, corrections, and reviews survive browser refresh and server restart. Interrupted processing is requeued; interrupted uploads require retry. The browser must remain open until uploading finishes, but already uploaded files continue processing while the server runs. There is one active study; **New study** changes the active workspace, preserving previous files on disk. There is not yet a history browser: export a study before switching.

## Excel layout

| Worksheet | Purpose |
|---|---|
| Study overview | Part, units, file counts, review coverage, calculation notes |
| Serial matrix | One row per included serial/run, 77 measurement columns for the example part, nominal/lower/upper reference rows, frozen serial and reference headers |
| Characteristic summary | Results and missing counts, reviewed count, average, minimum, maximum, range, sample standard deviation, and base-limit screening |
| Measurements | Filterable long-form results with PMin, PMax, bonus, assessment, page, extraction method, source text, and flags |
| Exceptions | Missing/unreviewed readings, mapping issues, geometric review, base-limit exceptions, file failures, exclusions, duplicates, and part mismatch warnings |
| Source reports | Complete input register, including excluded/failed files |

Alternating row colors, consistent number formats, readable column widths, filters on detail sheets, print headers, and freeze panes replace the original dense layout. The wide matrix is intended for screen use; the filterable long-form Measurements sheet is easier to print or inspect by characteristic.

Statistics use **only measured result values** from included successful reports, across the entire study. PMin/PMax are stored separately, never mixed into the population. Missing results are blank rather than zero. Standard deviation is blank for fewer than two readings. Statistics are calculated at export, so Excel can show them immediately without recalculation; regenerate the report if data changes. Base-limit highlighting ignores blank cells. There is no automatic pass/fail or capability certification, and no Cp/Cpk calculation.

Exports stream worksheet rows to disk to reduce memory use. Limits: **5,000 reports**, **50 MB and 200 pages per PDF**, **1,000 characteristic definitions**, and **950,000 report × characteristic combinations per workbook**. Each scan has a 15-minute timeout. Processing many raster reports may take hours; queue state is saved between files. OCR speed depends on page complexity and the workstation. These are supported input limits, not a promise that every PDF layout can be read automatically.

## Extraction accuracy

The parser targets the PC-DMIS/JEMCO CMM layout shown in the supplied PDF. It supports ordinary size, radius, profile, and position tables, with separate coordinates, PMin/PMax, and bonus where readable. Measurement matching requires dimension + feature + axis identifiers. It does **not** assign unidentified values by row order or fuzzy-match `ar3` to `CIR_3`. Unknown/missing rows remain available for review and manual correction. Serial identifiers are extracted from the report header; filename fallback is flagged.

Raster OCR can drop minus signs or misread labels. Nominal mismatches, repeated characteristics, and reversed PMin/PMax are flagged, but not every OCR error is detectable. Geometric tolerance, datum, MMC/LMC, bonus interpretation, manual checklists, and overall conformance remain human review tasks. The reference workbook supplies base limits; the app does not prove those definitions are appropriate for every report or revision. Reports from an unfamiliar layout may yield few or no matches.

## Development / tests

Linux/macOS also work with Node 22+, Python 3.11+, and Tesseract installed. Set `FAI_PYTHON` to your environment's Python if needed. Node has no npm dependencies.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
node --check app/server.mjs
node --check public/app.js
npm start
```

Tests cover measured-column selection, coordinate/position/bonus separation, profile PMin/PMax, strict feature identity, OCR sign flags, reference import, blank versus zero handling, duplicate serial retention, exclusion/failure handling, literal Excel strings, and statistics across 1,000 synthetic serials. Development/browser validation runs on Linux; Windows launch scripts need verification on a Windows workstation.

## Local storage

New studies are saved under ignored **`.part-study/`**; the previous prototype's `.local-fai/` is not imported. The server binds only to `127.0.0.1`, checks Host/Origin, uses subprocess argument lists rather than a shell, and writes document-derived text as literal Excel strings. Files and local JSON are not encrypted. Use a trusted single-user workstation and do not expose the port to the network. Back up `.part-study/` if you need to retain studies; stop the server before copying, restoring, or deleting that directory.
