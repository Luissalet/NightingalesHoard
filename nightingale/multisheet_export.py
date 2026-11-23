"""Write several datasets into one new .xlsx, one sheet per dataset.

The caller (``Services.export(mode="sheets")``) has already resolved datasets and versions; this module only
names the sheets, lays them out and saves the file. Layout of every sheet: bold header row frozen under the
top edge and, when asked, a final row of column totals marked in bold with a top border and a fill.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .workbench.engine import DataError

MAX_SHEET_NAME = 31
MAX_ROWS = 1_048_576  # worksheet rows in the .xlsx format, header and totals included
MAX_COLUMNS = 16_384
_BAD_NAME_CHARS = re.compile(r"[\[\]:*?/\\]")
_NATIVE = (bool, int, float, Decimal, str, dt.datetime, dt.date, dt.time, dt.timedelta)
_BOLD = Font(bold=True)
_TOTALS_FILL = PatternFill("solid", fgColor="E8EEF3")
_TOTALS_BORDER = Border(top=Side(style="thin"))


def check_sheet_name(name: str) -> str:
    """Validate a sheet name the caller chose; the name is used exactly as given or refused."""
    if not isinstance(name, str) or not name.strip():
        raise DataError("sheet_name must not be empty")
    if name != name.strip():
        raise DataError(f"sheet name {name!r} must not start or end with spaces")
    if len(name) > MAX_SHEET_NAME:
        raise DataError(f"sheet name {name!r} is {len(name)} characters; Excel allows {MAX_SHEET_NAME}")
    if _BAD_NAME_CHARS.search(name):
        raise DataError(f"sheet name {name!r} cannot contain any of [ ] : * ? / \\")
    if name.startswith("'") or name.endswith("'"):
        raise DataError(f"sheet name {name!r} cannot start or end with an apostrophe")
    if name.lower() == "history":
        raise DataError("'History' is reserved by Excel and cannot be a sheet name")
    return name


def default_sheet_name(dataset: str, version: Optional[int], taken: set[str]) -> str:
    """A free name for a sheet the caller did not name: the dataset name, then with its version, then numbered."""
    base = _BAD_NAME_CHARS.sub("_", dataset).strip().strip("'") or "sheet"
    candidates = [base[:MAX_SHEET_NAME]]
    if version is not None:
        candidates.append(f"{base[:MAX_SHEET_NAME - len(str(version)) - 2]}_v{version}")
    for candidate in candidates:
        if candidate.lower() not in taken and candidate.lower() != "history":
            return candidate
    counter = 2
    while True:
        suffix = f"_{counter}"
        candidate = f"{base[:MAX_SHEET_NAME - len(suffix)]}{suffix}"
        if candidate.lower() not in taken:
            return candidate
        counter += 1


def _cell_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # NaN and infinities are not valid worksheet numbers; keep them as text
    if isinstance(value, _NATIVE):
        return value
    return ILLEGAL_CHARACTERS_RE.sub("", str(value))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _column_total(values: list[Any]) -> Any:
    """The sum of a numeric column, or None when the column is not numeric or holds a non-finite number."""
    present = [value for value in values if value is not None]
    if not present or not all(_is_number(value) for value in present):
        return None
    if any(isinstance(value, float) and not math.isfinite(value) for value in present):
        return None
    if any(isinstance(value, float) for value in present):
        return math.fsum(float(value) for value in present)
    return sum(present)


def write_sheets(path: Path, sheets: list[dict], totals_label: str) -> list[dict]:
    """Save ``sheets`` (``name``, ``columns``, ``rows``, ``totals``) as one new workbook at ``path``.

    Returns, per sheet, what was written: name, row count and the totals row (``None`` when not asked for).
    """
    for sheet in sheets:
        extra = 1 + (1 if sheet["totals"] else 0)
        if len(sheet["columns"]) > MAX_COLUMNS:
            raise DataError(f"{sheet['name']!r} has {len(sheet['columns'])} columns; a worksheet holds {MAX_COLUMNS}")
        if len(sheet["rows"]) + extra > MAX_ROWS:
            raise DataError(f"{sheet['name']!r} has {len(sheet['rows'])} rows; a worksheet holds {MAX_ROWS - extra}")
    workbook = Workbook()
    workbook.remove(workbook.active)
    written = []
    for sheet in sheets:
        columns, rows = sheet["columns"], sheet["rows"]
        ws = workbook.create_sheet(sheet["name"])
        ws.append([_cell_value(name) for name in columns])
        for cell in ws[1]:
            cell.data_type = "s"  # a header such as "=x" is a name, never a formula
            cell.font = _BOLD
        for row_number, row in enumerate(rows, start=2):
            ws.append([_cell_value(value) for value in row])
            for column, value in enumerate(row, start=1):
                if isinstance(value, str):
                    ws.cell(row_number, column).data_type = "s"
        ws.freeze_panes = "A2"
        totals = None
        if sheet["totals"]:
            sums = [_column_total([row[index] for row in rows]) for index in range(len(columns))]
            label_at = next((index for index, total in enumerate(sums)
                             if total is None and not any(_is_number(row[index]) for row in rows)), 0)
            sums[label_at] = None
            totals_row = len(rows) + 2
            for index, total in enumerate(sums):
                cell = ws.cell(totals_row, index + 1)
                if index == label_at:
                    cell.value, cell.data_type = totals_label, "s"
                elif total is not None:
                    cell.value = total
                cell.font, cell.fill, cell.border = _BOLD, _TOTALS_FILL, _TOTALS_BORDER
            totals = {"row": totals_row, "label_column": columns[label_at] if columns else None,
                      "sums": {columns[index]: _cell_value(total) for index, total in enumerate(sums) if total is not None}}
        for index, name in enumerate(columns, start=1):
            longest = max([len(str(name))] + [len(str(row[index - 1])) for row in rows[:200] if row[index - 1] is not None])
            ws.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 8), 50)
        written.append({"sheet": sheet["name"], "row_count": len(rows), "totals": totals})
    workbook.save(path)
    return written
