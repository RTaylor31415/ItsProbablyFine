# Local FAI

A Windows-focused prototype: batch PDF import → local OCR → human review → AS9102 Excel export using the supplied JEMCO template. Node.js serves the browser interface; Python handles PDFs and Excel; Tesseract performs OCR locally. No cloud APIs or external document processing are used. Runtime works offline after installation.

## Windows setup

Install **Node.js 22+**, **Python 3.11+** (with the `py` launcher), and **Tesseract OCR 5+ with English language data**. Windows installers are documented by the Tesseract project at <https://tesseract-ocr.github.io/tessdoc/Installation.html>. The default location `C:\Program Files\Tesseract-OCR\tesseract.exe` is detected by the launch script. Otherwise add Tesseract to PATH or set `TESSERACT_CMD` to its executable path.

From this folder in PowerShell:

```powershell
.\setup.ps1
.\start.ps1
```

Follow your organization's PowerShell policy if scripts are restricted. Alternatively run these commands manually without changing that policy:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FAI_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
node app/server.mjs
```

Open **http://127.0.0.1:3210** locally. Ctrl+C stops the server. Internet is needed to obtain prerequisites and Python packages, but not for processing afterward. For air-gapped deployment, download compatible Windows Python wheels on a connected machine and install from a local directory with pip's `--no-index --find-links` options.

## Workflow

1. Import PDFs for **one part**. Files process sequentially, limited to 50 MB / 200 pages each and 15 minutes per scan. Split very large inputs.
2. Enter verified report identifiers. They are intentionally not inferred from filenames or OCR.
3. Compare each candidate with the original PDF. Correct characteristic numbers, requirements, units, quantity, and limits. Add omitted characteristics manually; exclude duplicates. A source caption such as “JEMCO 13,14” is not automatically treated as a verified characteristic number.
4. Enter inspection results after checking their source. They are **not automatically copied from OCR**. Confirm each included row, then confirm every source page was checked for omitted characteristics.
5. Export a draft `.xlsx`. Forms 1, 2, 3, and CofC are retained. Additional Form 3 sheets are created after 99 rows. An Extraction review sheet records source text, filename, page, and requirements.
6. Complete materials, processes, testing, nonconformance information, and authorization in Excel. No signatures, dates, acceptance decisions, or certifications are generated. The template's blanket CofC assertion is replaced with a draft notice. Excel recalculates retained cross-sheet formulas on opening.

**Save review** downloads editable JSON. Closing or refreshing loses unsaved browser changes. **Open review** restores rows and source text; use **Reconnect PDF** in the evidence panel to view original PDFs again. PDFs are not embedded in review files. Reconnection does not validate file identity; choose the matching original.

Your browser may request permission for multiple downloads when saving a review and exporting Excel in the same session.

## Extraction scope

- PDF text extraction, with local OCR for pages containing images or little text.
- Explicit symmetric (`±`, `+/-`) and asymmetric (`+a/-b`) tolerances.
- A conservative subset of ordinary CMM size rows: nominal, positive/negative tolerance, measurement, deviation, out-of-tolerance columns.
- Recognizable checklist items such as surface texture and burr removal.

This is **not a general GD&T interpreter or completeness guarantee**. OCR can lose signs, symbols, table boundaries, and digits. It does not resolve MMC/LMC, datum references, geometric tolerance frames, basic/reference dimensions, general tolerances, thread definitions, or unit conversions. Position/profile results are not interpreted as ordinary size dimensions. Raw evidence is available for manual transcription. Review every source page. The example is primarily a raster CMM report, rather than a vector engineering drawing.

The exporter targets this template's layout: Form1 identifier cells and Form3 rows 8–106, columns B–M. Other templates need separately validated mappings. Form2 and CofC require manual completion. Export does not establish AS9102 compliance.

## Development

Linux/macOS: install Node 22+, Python 3.11+, and Tesseract. Create a virtual environment, install `requirements.txt`, set `FAI_PYTHON` to its Python executable, then run `npm start`. Node has no third-party runtime packages.

```sh
python -m unittest discover -s tests -v
node --check app/server.mjs
node --check public/app.js
```

Tests cover tolerance extraction, position/size separation, review gates, invalid numeric limits, template layout, overflow, blank results, and literal Excel text. Windows scripts require validation on a Windows machine; development validation here uses Linux.

## Local data

The server binds only to `127.0.0.1`, validates Host and Origin, starts subprocesses without a shell, and writes document-derived text as literal Excel strings. It makes no outbound requests. PDFs, export inputs, and workbooks remain in ignored `.local-fai/` until you delete that directory with the server stopped. Extracted text also resides in browser memory and review JSON. Files are not encrypted. This prototype assumes a trusted single-user workstation; do not expose the port to a network.

The supplied Excel template is in `assets/as9102-template.xltx`. Keep its use within your permitted scope. The sample PDF is not included in the repository.
