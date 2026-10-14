"""
David's coding tools — read, write and execute code within his own project.

WHAT THIS FILE DOES
    Gives David three powers: reading his own source files, writing new ones,
    and running Python to test what he wrote. That turns him from someone who
    can only *suggest* code into someone who can actually build and verify it.

THE GUARDRAILS (why you can trust it)
    · Every path is forced to stay inside the David project folder
    · .env is completely blocked, so your API keys can never be read or damaged
    · Any overwrite makes a .bak copy first
    · The four files that could break David (david_core, main, values, server)
      cannot be written without you approving the change first
    · Code runs in a SEPARATE process with a timeout — a runaway loop can't
      hang David himself, and there is no shell access, only Python
"""

import os          # file paths, checking what exists
import shutil      # copying files (used for backups)
import subprocess  # running code in a separate process
import sys         # finding the current Python executable
import difflib     # generating the before/after diff you approve
from datetime import datetime   # timestamping backups


# The folder David is running from. Everything is measured against this —
# it's the fence that keeps file access inside the project.
PROJECT = os.path.abspath(os.getcwd())

# Files David must never touch, no matter what he's asked.
# .env holds your Anthropic, Finnhub and Twelve Data keys.
BLOCKED = {".env", ".env.local"}

# Files that ARE David. Breaking any of these breaks him, so writes to these
# are held back until you say yes.
CORE_FILES = {"david_core.py", "main.py", "values.py", "server.py"}

# Holding pen: proposed changes to core files sit here until approved.
# Key = filename, value = (full path, the new content, David's reason)
_PENDING = {}


def _safe(path):
    """Turn a requested path into a real one, and refuse it if it breaks a rule.

    Returns (resolved_path, "") when allowed, or (None, "reason") when blocked.
    This is the single choke point every file operation goes through.
    """
    # Resolve to an absolute path relative to the project folder.
    # abspath also flattens things like "../../" so escape attempts collapse.
    p = os.path.abspath(os.path.join(PROJECT, path))

    # THE FENCE: if the resolved path doesn't start with the project folder,
    # it's pointing somewhere else on your disk. Refuse.
    if not p.startswith(PROJECT):
        return None, "Blocked: path is outside the David project folder."

    # THE VAULT: credentials are off limits entirely, read or write.
    if os.path.basename(p).lower() in BLOCKED:
        return None, "Blocked: that file holds credentials and cannot be touched."

    return p, ""


def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read a file from the David project. Optionally read only a line range
    (1-indexed, inclusive) — useful for large files."""

    # Run the request through the safety check first.
    p, err = _safe(path)
    if err:
        return err

    if not os.path.exists(p):
        return f"Not found: {path}"

    try:
        # Read the whole file as a list of lines so we can slice and number it.
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total = len(lines)

        # If a range was requested, clamp it to what actually exists so a bad
        # range can't cause an error — it just returns what's there.
        if start_line or end_line:
            a = max(1, start_line or 1)          # never below line 1
            b = min(total, end_line or total)    # never past the end
            chunk = lines[a - 1:b]               # -1 because lists start at 0
            head = f"{path}  (lines {a}-{b} of {total})\n"
        else:
            chunk = lines
            head = f"{path}  ({total} lines)\n"

        # Prefix each line with its number, so David can refer to "line 47"
        # precisely when discussing or editing the file.
        body = "".join(f"{i:5d} | {l}" for i, l in
                       enumerate(chunk, start=(start_line or 1)))
        return head + body

    except Exception as e:
        return f"Error reading {path}: {e}"


def list_files(subfolder: str = "") -> str:
    """List the files in the project (or a subfolder) so David can see what
    exists before deciding what to read or change."""

    p, err = _safe(subfolder or ".")
    if err:
        return err
    if not os.path.isdir(p):
        return f"Not a folder: {subfolder}"

    try:
        out = []
        for name in sorted(os.listdir(p)):
            full = os.path.join(p, name)

            # Skip hidden files (.env, .git) and Python's cache clutter.
            if name.startswith(".") or name == "__pycache__":
                continue

            if os.path.isdir(full):
                out.append(f"  [dir]  {name}/")
            else:
                size = os.path.getsize(full)
                # Flag the protected files so David knows which need approval.
                flag = "  *CORE*" if name in CORE_FILES else ""
                out.append(f"  {size:>7} b  {name}{flag}")

        return f"{subfolder or 'project root'}:\n" + "\n".join(out)

    except Exception as e:
        return f"Error listing: {e}"


def _diff(old, new, path):
    """Build a readable before/after comparison, so you can see exactly what
    would change before approving it. Only used for core files."""

    # unified_diff is the standard "- old line / + new line" format.
    d = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                  fromfile=f"{path} (current)",
                                  tofile=f"{path} (proposed)",
                                  lineterm="", n=2))  # n=2 = 2 lines of context
    if not d:
        return "(no changes)"

    # Keep it readable — a 900-line diff helps nobody.
    if len(d) > 60:
        d = d[:60] + [f"... ({len(d) - 60} more diff lines)"]

    return "\n".join(d)


def write_file(path: str, content: str, reason: str = "") -> str:
    """Write or overwrite a file in the David project.

    NEW and TOOL files      → written straight away, with a .bak backup
    CORE files              → NOT written. The diff is returned instead and
                              you must approve it via approve_write().
    """

    p, err = _safe(path)
    if err:
        return err

    name = os.path.basename(p)

    # Grab the current contents (if any) so we can diff against them and,
    # for normal files, so we know whether a backup is needed.
    existing = ""
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                existing = f.read()
        except Exception:
            existing = ""

    # ---- THE APPROVAL GATE ----
    # If this is one of David's own vital organs, don't write. Park the change
    # and hand back a diff for you to look at.
    if name in CORE_FILES:
        _PENDING[name] = (p, content, reason)
        return ("APPROVAL REQUIRED — this is a core file.\n"
                f"File: {path}\nReason: {reason or '(none given)'}\n\n"
                f"{_diff(existing, content, path)}\n\n"
                "Show this to the user and ask them to confirm. If they agree, "
                f"call approve_write('{name}').")

    # ---- NORMAL WRITE PATH ----
    try:
        # Back up first. If the new version is wrong, the .bak has your old one.
        if existing:
            shutil.copy2(p, p + ".bak")

        # Create any missing folders in the path (e.g. writing to tools/new.py
        # when tools/ somehow didn't exist).
        os.makedirs(os.path.dirname(p), exist_ok=True)

        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

        note = " (backup saved as .bak)" if existing else " (new file)"
        return f"Written: {path}{note}\nReason: {reason or '(none)'}"

    except Exception as e:
        return f"Error writing {path}: {e}"


def approve_write(filename: str) -> str:
    """Apply a pending change to a core file.

    David should only call this AFTER you have seen the diff and said yes.
    """

    key = os.path.basename(filename)

    # Nothing waiting? Then there's nothing to approve — refuse rather than
    # guess. This stops a stray call writing something unexpected.
    if key not in _PENDING:
        return f"No pending write for {key}."

    # pop() both retrieves it and clears it, so the same change can't be
    # applied twice by accident.
    p, content, reason = _PENDING.pop(key)

    try:
        # Timestamped backup — core files get a dated copy, not just one .bak,
        # so you can roll back through several changes if needed.
        if os.path.exists(p):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(p, f"{p}.{stamp}.bak")

        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

        return f"Approved and written: {key} (timestamped backup saved)\nReason: {reason}"

    except Exception as e:
        return f"Error writing {key}: {e}"


def run_python(code: str, timeout: int = 30) -> str:
    """Run Python code in a SEPARATE process and return whatever it printed.

    This is how David tests his own work before claiming it functions.
    Separate process matters: an infinite loop or crash kills only the test,
    never David himself.
    """
    try:
        # sys.executable = the same Python that's running David, so the test
        # sees all the same installed packages.
        # cwd=PROJECT so the code can do "from tools.candles import ..." etc.
        # capture_output collects whatever it printed instead of losing it.
        # timeout is the safety net against code that never finishes.
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True,
                           timeout=timeout, cwd=PROJECT)

        out = (r.stdout or "").strip()   # normal printed output
        err = (r.stderr or "").strip()   # errors and tracebacks

        parts = []

        # Truncate long output so a runaway print doesn't flood David's context
        # (and cost you tokens).
        if out:
            parts.append("OUTPUT:\n" + (out[:3000] + "\n...(truncated)"
                                        if len(out) > 3000 else out))
        if err:
            parts.append("STDERR:\n" + (err[:2000] + "\n...(truncated)"
                                        if len(err) > 2000 else err))
        if not parts:
            parts.append("(ran with no output)")

        # exit code 0 = success, anything else = something went wrong.
        parts.append(f"exit code: {r.returncode}")
        return "\n\n".join(parts)

    except subprocess.TimeoutExpired:
        return (f"Timed out after {timeout}s — the code may have been waiting "
                "for input or stuck in a loop.")
    except Exception as e:
        return f"Error running code: {e}"