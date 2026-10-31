# Part Study — offline PDF batch results

Import a folder of serial-number CMM inspection PDFs and export a readable Excel part study. Runs locally on Windows with Node.js, Python, and Tesseract. No cloud AI, telemetry, or external document processing.

**All measurement fields now come from the PDFs. No Excel reference or bundled characteristic list is used.** The Excel file supplied during design was only a layout example.

## Install / update on Windows

Install Node.js 22+, Python 3.11+ with the `py` launcher, and Tesseract OCR 5+ with English data. Tesseract's [installation guidance](https://tesseract-ocr.github.io/tessdoc/Installation.html) lists Windows options.

Download the latest repository, open PowerShell in the extracted folder, and run:

```powershell
.\setup.ps1
.\start.ps1
```

Open `http://127.0.0.1:3210` locally. Ctrl+C stops the server. **Rerun setup.ps1 when updating**: this version adds Pillow for table/cell OCR preprocessing. The startup script detects Tesseract at `C:\Program Files\Tesseract-OCR\tesseract.exe`. For another location, set `TESSERACT_CMD` to its executable path.

If script execution is restricted by your organization's policy, run the equivalent commands manually without changing that policy:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FAI_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
node app/server.mjs
```

Internet is needed to install prerequisites, but not to process reports afterward. Windows launch scripts require validation on a Windows workstation; development and browser tests run on Linux.

## Existing studies: re-extract the saved PDFs

Older versions stored Excel-derived definitions. Those extractions are now labelled **outdated** and cannot be exported as PDF-sourced results. Click **Re-extract PDFs** in Study overview. The app queues the PDFs already stored locally; uploading them again is unnecessary.

Re-extraction replaces measurements and any manual corrections, and saves a backup of the previous extraction JSON beside the source PDF. It does not delete the PDFs. Exclusion choices are retained. No review/approval step is required before export.

## Bulk workflow

1. Choose a folder, including subfolders, or select many PDFs. Non-PDF files are skipped. Use one part/revision per study.
2. Files upload into a saved queue and process one at a time to bound local OCR memory. Uploading requires the browser to stay open; already uploaded files continue processing while the server runs. Pause takes effect after the current scan finishes. Failed files can be retried individually.
3. The app discovers the characteristic columns from the reports. It reads dimension/feature/axis identifiers, units, nominal, printed +TOL/-TOL, measured result, DEV, OUTTOL, MIN, MAX, and BONUS where those fields are present.
4. Serial results shows extracted and missing counts. Opening a report shows its own nominal, tolerances, and reported MIN/MAX beside the measurement and source PDF. Corrections are optional; **Save changes** updates the results immediately.
5. Export when processing finishes. Exceptions contains only **Missing** or **Outside base limits**. File failures, duplicates, extraction notes, and annotation-without-table warnings remain in Source reports or Measurements.

The queue, source PDFs, corrections, and active study persist across browser refresh and server restart. Interrupted extraction resumes; interrupted uploads need retry. New study changes the active workspace but leaves previous files on disk; export before switching because there is no history browser yet.

Serial numbers retain leading zeros. Duplicate serials/reruns remain separate rows with their source paths. Identical PDF bytes are flagged; exclude unwanted duplicates in report details.

## What each field means

- **PDF nominal / +TOL / -TOL:** extracted from that report's table, not copied from another serial or a template.
- **Lower/upper limits:** calculated as that PDF's nominal plus its printed tolerance. A blank tolerance stays blank; the app never substitutes zero or an Excel default.
- **PDF MIN / MAX:** the values printed under MIN/MAX in the CMM report. These are separate from tolerance limits and from statistics across serials.
- **Observed min / max:** calculated across included measured results in the study, not read from the PDF MIN/MAX columns.
- **Unspecified limits:** a measured coordinate can have a nominal but blank tolerance cells. Its result is retained and its state is “Limits not specified.” That is not a missing measurement or an automatic failure.

Every report keeps its own definitions. When specifications differ for the same identifier, shared workbook headers show **Varies by PDF**. Measurements retains the per-file fields, and outside-limit screening/highlighting uses each file's own limits. Bonus is retained but not automatically applied to conformance calculations. No pass/fail certification or Cp/Cpk is generated.

Missing measurements are blank, never zero. Missing means either a row was unreadable, or an identifier found in another included PDF is absent from this report. An identifier absent from every PDF cannot be known automatically; an annotation with no readable table is reported as a source warning rather than supplied with invented fields.

## Extraction improvements

The parser targets the PC-DMIS/JEMCO CMM layout shown in the sample. It uses positioned text for vector PDFs. For raster tables it detects header columns, reads enlarged numeric/label cells, strips grid rules without erasing text strokes, recovers visible minus signs, and retries difficult axis labels/fields. Separate image-strip OCR finds table headings that whole-page OCR suppresses.

Coordinates are associated with their printed feature within a table block. New identifiers are accepted directly from the PDF instead of rejected for not appearing in an Excel list. A truncated feature label can be resolved against a unique full label printed elsewhere in the **same PDF**, with a note retaining that provenance.

OCR is not perfect. Source text and page references are retained. Unreadable fields stay blank; nominal/measured/deviation inconsistencies are noted. Manual checklist requirements without measured numeric results are not fabricated as CMM results. The parser does not interpret arbitrary GD&T drawings, infer missing general tolerances, convert units, or certify completeness across unfamiliar layouts.

## Excel workbook

| Sheet | Contents |
|---|---|
| Study overview | File counts, part, PDF-source rules and calculation notes |
| Serial matrix | One row per included serial/run; shared PDF nominals/limits or “Varies by PDF”; frozen headers; own-limit highlighting |
| Characteristic summary | Counts, missing, mean, observed min/max, range, sample deviation, and own-limit exception count |
| Measurements | Per-report PDF fields, calculated limits, reported MIN/MAX, DEV/OUTTOL/BONUS, units, page, source text and notes |
| Exceptions | Only missing measurements and results outside their PDF's available limits |
| Source reports | Every input, including failed/excluded files and input warnings |

Only included successful reports contribute results/statistics. PDF MIN/MAX are not mixed into population statistics. Standard deviation is blank with fewer than two results. Statistics are calculated at export, so Excel does not need to recalculate them. Regenerate the workbook after editing results.

Limits: 5,000 reports per study; 50 MB and 200 pages per PDF; 1,000 discovered characteristics; 950,000 report × characteristic combinations per workbook; 15-minute timeout per scan. Very large raster batches can take hours. Excel worksheet rows stream to disk to reduce memory use.

## Development and validation

Linux/macOS also work with Node 22+, Python 3.11+, and Tesseract. Set `FAI_PYTHON` if your virtual environment's Python is not the default. There are no npm dependencies.

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
node --check app/server.mjs
node --check public/app.js
npm start
```

Tests create PDFs with previously unknown IDs and verify extraction without a template, blank tolerances, negative numbers, report-specific limits, differing specifications, reported versus observed MIN/MAX, missing versus zero results, no review gating, safe literal Excel text, and statistics across 1,000 serials. Old template-based extractions are explicitly rejected until re-extracted.

## Local files

Data is stored in ignored `.part-study/`. Back it up with the server stopped. Re-extraction backups are named `*.before-reextract-*.json`. The old `.local-fai/` prototype data is not imported automatically.

The server binds only to `127.0.0.1`, checks Host/Origin, uses subprocess argument lists, and writes document text as literal Excel strings. Local files are not encrypted; use a trusted single-user workstation and do not expose the port to a network.
