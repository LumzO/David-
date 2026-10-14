"""
Document reading for David.

Handles PDF, Word, Excel, CSV and plain text. Extracts text so David can
analyse broker statements, research notes, course material, or anything else
the user hands him.

Guardrail: uses the same project-fence logic as the coding tools for relative
paths, but ALSO allows absolute paths, because documents legitimately live
elsewhere on disk (Downloads, Documents, etc). Credentials files stay blocked.
"""
import csv
import io
import os

BLOCKED_NAMES = {".env", ".env.local", "credentials.json", "secrets.json"}

TEXT_EXTS = (".txt", ".md", ".log", ".json", ".py", ".js", ".html", ".css", ".xml")


def _check(path):
    """Basic safety: file must exist and not be a credentials file."""
    if not path:
        return None, "No path given."
    p = os.path.abspath(os.path.expanduser(path.strip().strip('"').strip("'")))
    if os.path.basename(p).lower() in BLOCKED_NAMES:
        return None, "Blocked: that file holds credentials."
    if not os.path.exists(p):
        return None, f"Not found: {path}"
    if os.path.isdir(p):
        return None, f"That's a folder, not a file: {path}"
    return p, ""


def _read_pdf(p, max_pages):
    try:
        from pypdf import PdfReader
    except ImportError:
        return "pypdf not installed. Run: pip install pypdf"
    try:
        reader = PdfReader(p)
        total = len(reader.pages)
        limit = min(total, max_pages or total)
        chunks = []
        empty = 0
        for i in range(limit):
            txt = (reader.pages[i].extract_text() or "").strip()
            if not txt:
                empty += 1
            chunks.append(f"--- page {i+1} ---\n{txt}")
        body = "\n\n".join(chunks)
        head = f"PDF: {os.path.basename(p)} ({total} pages"
        head += f", showing {limit}" if limit < total else ""
        head += ")\n"
        # A PDF that yields no text is almost certainly scanned images
        if empty == limit and limit > 0:
            head += ("\n[No extractable text — this looks like a SCANNED pdf. "
                     "Ask the user to screenshot the pages and upload them as "
                     "images instead, so you can read them visually.]\n")
        return head + body
    except Exception as e:
        return f"Error reading PDF: {e}"


def _read_docx(p):
    try:
        import docx
    except ImportError:
        return "python-docx not installed. Run: pip install python-docx"
    try:
        d = docx.Document(p)
        parts = [para.text for para in d.paragraphs if para.text.strip()]
        # Tables often carry the actual data in reports
        for t_i, table in enumerate(d.tables, 1):
            rows = []
            for row in table.rows:
                rows.append(" | ".join(c.text.strip() for c in row.cells))
            if rows:
                parts.append(f"\n[table {t_i}]\n" + "\n".join(rows))
        return (f"DOCX: {os.path.basename(p)} "
                f"({len(d.paragraphs)} paragraphs, {len(d.tables)} tables)\n\n"
                + "\n".join(parts))
    except Exception as e:
        return f"Error reading Word document: {e}"


def _read_xlsx(p, max_rows):
    try:
        import openpyxl
    except ImportError:
        return "openpyxl not installed. Run: pip install openpyxl"
    try:
        wb = openpyxl.load_workbook(p, data_only=True)  # data_only = computed values
        out = [f"XLSX: {os.path.basename(p)} ({len(wb.sheetnames)} sheets)"]
        for name in wb.sheetnames:
            ws = wb[name]
            out.append(f"\n=== sheet: {name} ({ws.max_row} rows x {ws.max_column} cols) ===")
            for r_i, row in enumerate(ws.iter_rows(values_only=True), 1):
                if r_i > (max_rows or 200):
                    out.append(f"... ({ws.max_row - r_i + 1} more rows)")
                    break
                cells = ["" if c is None else str(c) for c in row]
                if any(cells):
                    out.append(" | ".join(cells))
        return "\n".join(out)
    except Exception as e:
        return f"Error reading spreadsheet: {e}"


def _read_csv(p, max_rows):
    try:
        with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except Exception:
                dialect = csv.excel
            reader = csv.reader(f, dialect)
            rows = []
            for i, row in enumerate(reader, 1):
                if i > (max_rows or 200):
                    rows.append("... (more rows not shown)")
                    break
                rows.append(" | ".join(row))
        return f"CSV: {os.path.basename(p)}\n\n" + "\n".join(rows)
    except Exception as e:
        return f"Error reading CSV: {e}"


def _read_text(p, max_chars):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        limit = max_chars or 20000
        head = f"{os.path.basename(p)} ({len(txt)} chars)\n\n"
        if len(txt) > limit:
            return head + txt[:limit] + f"\n\n... (truncated, {len(txt)-limit} chars remaining)"
        return head + txt
    except Exception as e:
        return f"Error reading file: {e}"


def read_document(path: str, max_pages: int = 0, max_rows: int = 0) -> str:
    """Read a document and return its text so you can analyse it.

    Supports PDF, Word (.docx), Excel (.xlsx/.xlsm), CSV, and plain text files
    (.txt, .md, .json, code files). Give the full path — the user will normally
    supply it via the upload dialog.

    max_pages limits PDF pages, max_rows limits spreadsheet/CSV rows. Leave at 0
    for sensible defaults. For a long document, read it once and summarise
    rather than re-reading.
    """
    p, err = _check(path)
    if err:
        return err

    ext = os.path.splitext(p)[1].lower()
    size_kb = os.path.getsize(p) / 1024

    if size_kb > 25000:
        return f"File is very large ({size_kb/1024:.1f} MB). Ask the user to split it."

    if ext == ".pdf":
        return _read_pdf(p, max_pages)
    if ext in (".docx", ".dotx"):
        return _read_docx(p)
    if ext in (".xlsx", ".xlsm", ".xltx"):
        return _read_xlsx(p, max_rows)
    if ext in (".csv", ".tsv"):
        return _read_csv(p, max_rows)
    if ext in TEXT_EXTS:
        return _read_text(p, 0)
    if ext == ".doc":
        return ("Old .doc format isn't supported. Ask the user to save it as "
                ".docx and try again.")

    # Unknown extension — try reading as text, it might work
    return _read_text(p, 0)


def list_documents(folder: str = "") -> str:
    """List readable documents in a folder, so you can see what's available
    before asking the user which one they mean."""
    target = os.path.abspath(os.path.expanduser(folder.strip() or os.getcwd()))
    if not os.path.isdir(target):
        return f"Not a folder: {folder}"
    exts = (".pdf", ".docx", ".xlsx", ".xlsm", ".csv", ".tsv") + TEXT_EXTS
    try:
        found = []
        for name in sorted(os.listdir(target)):
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            if os.path.isfile(full) and name.lower().endswith(exts):
                found.append(f"  {os.path.getsize(full)/1024:>8.1f} KB  {name}")
        if not found:
            return f"No readable documents in {target}"
        return f"Documents in {target}:\n" + "\n".join(found)
    except Exception as e:
        return f"Error listing folder: {e}"