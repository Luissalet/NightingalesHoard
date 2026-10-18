"""A small dataset catalogue over local files, queried with read-only SQL.

DuckDB materialises each registered file into `data/catalog.duckdb` (or, for
files bigger than `LINK_THRESHOLD_BYTES`, keeps a lazy `VIEW` over the file
instead — "linked").

Connection model. DuckDB does not let one process hold two differently
configured connections to the same file, and `enable_external_access`
cannot be switched back on once it is off. So the catalogue keeps exactly
one connection open at a time, under one lock:

- the **query connection** (the normal state): `read_only=True` and
  `enable_external_access=false`, so a query can neither write to the
  catalogue nor read arbitrary files from disk;
- a short-lived **read-write connection** with file access, opened only
  while a dataset is being registered and closed straight after.

Linked datasets (views over big files) stay queryable because the query
connection allows exactly their source paths (`allowed_paths` /
`allowed_directories`) before file access is switched off.

Every `query()` also goes through a statement-type gate (`gate_sql`)
before it reaches DuckDB at all.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import UUID

class _LazyDuckDB:
    def __getattr__(self, name):
        import importlib
        return getattr(importlib.import_module("duckdb"), name)

duckdb = _LazyDuckDB()
import openpyxl

__all__ = ["Catalog", "DataError", "SQLGateError", "gate_sql", "slugify_name"]

LINK_THRESHOLD_BYTES = 1_000_000_000  # 1 GB
DEFAULT_QUERY_TIMEOUT_S = 20
MAX_QUERY_LIMIT = 1000
MAX_CELL_CHARS = 500
TOTAL_COUNT_CAP = 1_000_000

_ALLOWED_SOLO_TYPES = {"SELECT", "EXPLAIN"}
_BANNED_LEADING_KEYWORDS = {
    "ATTACH", "COPY", "INSTALL", "LOAD", "SET", "PRAGMA", "CREATE", "INSERT",
    "UPDATE", "DELETE", "EXPORT", "CALL", "DROP", "ALTER", "VACUUM",
    "ANALYZE", "BEGIN", "COMMIT", "ROLLBACK", "GRANT", "REVOKE",
    "CHECKPOINT", "USE", "RESET", "DETACH", "IMPORT", "TRUNCATE",
}
_NUMERIC_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT",
    "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT", "FLOAT", "DOUBLE",
    "REAL", "DECIMAL", "NUMERIC",
}
_TEMPORAL_PREFIXES = ("DATE", "TIMESTAMP", "TIME")


class DataError(ValueError):
    pass


class SQLGateError(DataError):
    pass


def _leading_keyword(sql: str) -> str:
    # skip leading comments so "-- x\nATTACH ..." is still recognised
    stripped = re.sub(r"^(\s*(--[^\n]*\n|/\*.*?\*/))*", "", sql, flags=re.S)
    m = re.match(r"\s*\(*\s*([A-Za-z]+)", stripped)
    return m.group(1).upper() if m else ""


def gate_sql(sql: str) -> None:
    """Raise `SQLGateError` unless `sql` is exactly one read-only statement.

    Allowed: SELECT / WITH (a select) / DESCRIBE / SUMMARIZE / EXPLAIN, and
    a single PIVOT/UNPIVOT (DuckDB's parser expands one PIVOT/UNPIVOT
    statement into an internal CREATE+SELECT pair, which is the one
    documented exception to "exactly one statement").
    Rejected explicitly, regardless of how DuckDB classifies them: ATTACH,
    COPY, INSTALL, LOAD, SET, PRAGMA, CREATE, INSERT, UPDATE, DELETE,
    EXPORT, CALL and friends, plus any multi-statement input.
    """
    if not sql or not sql.strip():
        raise SQLGateError("empty query")
    leading = _leading_keyword(sql)
    try:
        statements = duckdb.extract_statements(sql)
    except Exception as exc:  # noqa: BLE001
        raise SQLGateError(f"could not parse SQL: {_first_lines(str(exc))}") from exc
    if not statements:
        raise SQLGateError("no statement found")
    types = [s.type.name for s in statements]

    if leading in ("PIVOT", "UNPIVOT"):
        if types == ["CREATE", "SELECT"] or types == ["SELECT"]:
            return
        raise SQLGateError("PIVOT/UNPIVOT must be a single statement")

    if len(statements) != 1:
        raise SQLGateError(
            f"only one statement is allowed, found {len(statements)}; remove the ';' and send one query per call"
        )
    if leading in _BANNED_LEADING_KEYWORDS:
        raise SQLGateError(
            f"statement type not allowed: {leading}. Only read-only SELECT / WITH / DESCRIBE / "
            "SUMMARIZE / EXPLAIN / PIVOT queries can run here"
        )
    if types[0] not in _ALLOWED_SOLO_TYPES:
        raise SQLGateError(
            f"statement type not allowed: {types[0]}. Only read-only SELECT / WITH / DESCRIBE / "
            "SUMMARIZE / EXPLAIN / PIVOT queries can run here"
        )


def slugify_name(name: str) -> str:
    """Turn a file stem / sheet / table name into a safe SQL identifier.

    "Ventas 2024" -> "Ventas_2024", "Año-fiscal" -> "Ano_fiscal",
    "2024 sales" -> "t_2024_sales". Idempotent on names that are already safe.
    """
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    text = re.sub(r"_{3,}", "__", text).strip("_")
    if not text:
        text = "dataset"
    if text[0].isdigit():
        text = "t_" + text
    return text[:63]


def _safe_view_name(name: str) -> str:
    if not isinstance(name, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise DataError(f"invalid dataset name: {name!r}")
    return name


def _first_lines(text: str, max_chars: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else text[:max_chars] + "…"


def _json_safe(value: Any, *, cap: Optional[int] = None, list_cap: Optional[int] = None) -> Any:
    """Make a DuckDB value JSON-safe: decimals as strings, dates ISO, NaN as string.

    `cap` bounds every string; when it is set (a result meant for a person or
    a model), a BLOB is shown as "<binary, N bytes>" instead of its bytes,
    which say nothing to either. `list_cap` bounds
    how many elements of a nested LIST/STRUCT-array are kept, so one huge
    nested value (a JSON export's array column, a wide BLOB) cannot blow up a
    small sample or query result — pass it wherever the result is meant for a
    model with a finite context (sample rows, public query results), and
    leave it `None` for internal full-data reads (stats, charts).
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        if cap is not None:
            return f"<binary, {len(value):,} bytes>"
        return value.hex()
    if isinstance(value, str):
        if cap is not None and len(value) > cap:
            return value[:cap] + "…"
        return value
    if isinstance(value, dict):
        items = list(value.items())
        if list_cap is not None and len(items) > list_cap:
            out = {str(k): _json_safe(v, cap=cap, list_cap=list_cap) for k, v in items[:list_cap]}
            out["…"] = f"{len(items) - list_cap} more field(s) omitted"
            return out
        return {str(k): _json_safe(v, cap=cap, list_cap=list_cap) for k, v in items}
    if isinstance(value, (list, tuple)):
        if list_cap is not None and len(value) > list_cap:
            return [_json_safe(v, cap=cap, list_cap=list_cap) for v in value[:list_cap]] + [
                f"… {len(value) - list_cap} more item(s) omitted"
            ]
        return [_json_safe(v, cap=cap, list_cap=list_cap) for v in value]
    return str(value)


# Cap on how many elements of a nested list/struct are kept in a sample row or
# a public query result — a single JSON row with a large nested array (an
# activity-log export, say) must not blow up to hundreds of thousands of
# characters just to preview it.
MAX_LIST_ITEMS = 20
# ...and fewer still in describe's 5 sample rows, which are only a preview
SAMPLE_LIST_ITEMS = 3


def _base_type(type_name: str) -> str:
    return type_name.upper().split("(")[0].strip()


def is_numeric_type(type_name: str) -> bool:
    return _base_type(type_name) in _NUMERIC_TYPES


def is_temporal_type(type_name: str) -> bool:
    return _base_type(type_name).startswith(_TEMPORAL_PREFIXES)


@dataclass
class DatasetMeta:
    name: str
    source_path: str
    kind: str
    linked: bool
    row_count: int
    columns: list[dict]
    profile: dict
    mtime: float
    size: int
    updated_at: str
    options: dict


class Catalog:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "catalog.duckdb"
        self._lock = threading.RLock()
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._conn_mode: Optional[str] = None
        self._count_cache: Optional[int] = None
        with self._writable() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS _lh_datasets (
                    name VARCHAR PRIMARY KEY,
                    source_path VARCHAR,
                    kind VARCHAR,
                    linked BOOLEAN,
                    row_count BIGINT,
                    columns_json VARCHAR,
                    profile_json VARCHAR,
                    mtime DOUBLE,
                    size BIGINT,
                    updated_at VARCHAR
                )
                """
            )
            conn.execute("ALTER TABLE _lh_datasets ADD COLUMN IF NOT EXISTS options_json VARCHAR")

    # -- connections ----------------------------------------------------------

    def _open(self, mode: str) -> duckdb.DuckDBPyConnection:
        """Return the single open connection in `mode` ("ro" or "rw")."""
        if self._conn is not None and self._conn_mode == mode:
            return self._conn
        self._close_conn()
        # never download extensions: a query naming e.g. sqlite_scan() would
        # otherwise make DuckDB fetch one from the internet
        offline = {"autoinstall_known_extensions": False, "autoload_known_extensions": False}
        if mode == "rw":
            conn = duckdb.connect(str(self.db_path), config=offline)
        else:
            conn = duckdb.connect(str(self.db_path), read_only=True, config=offline)
            try:
                # Linked datasets are views over files, so exactly their sources
                # (and our own cache) stay readable; then file access is switched
                # off for good - DuckDB refuses to widen either setting afterwards.
                files, dirs = self._linked_sources(conn)
                if files:
                    conn.execute("SET allowed_paths = ?", [files])
                conn.execute("SET allowed_directories = ?", [dirs])
                conn.execute("SET enable_external_access = false")
            except Exception:
                conn.close()
                raise
        self._conn, self._conn_mode = conn, mode
        return conn

    def _linked_sources(self, conn) -> tuple[list[str], list[str]]:
        files: list[str] = []
        dirs: list[str] = [str(self.cache_dir) + ("\\" if "\\" in str(self.cache_dir) else "/")]
        rows = conn.execute(
            "SELECT kind, source_path, options_json FROM _lh_datasets WHERE linked = TRUE"
        ).fetchall()
        for kind, source, _options in rows:
            if kind == "folder":
                dirs.append(source + ("\\" if "\\" in source else "/"))
            elif kind in ("csv", "parquet", "json"):
                files.append(source)
        return files, dirs

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn, self._conn_mode = None, None

    @contextmanager
    def _writable(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self._lock:
            conn = self._open("rw")
            try:
                yield conn
            finally:
                # never leave the file open read-write between calls
                self._close_conn()

    def _reader(self) -> duckdb.DuckDBPyConnection:
        return self._open("ro")

    def close(self) -> None:
        with self._lock:
            self._close_conn()

    # -- registration ------------------------------------------------------

    def dataset_count(self, wait_s: float = 0.2) -> Optional[int]:
        """Number of datasets, without waiting behind a long registration.

        /api/health must answer instantly (Faustus polls it to decide whether
        the app is alive), so this returns the last known count when the
        catalogue is busy."""
        if self._lock.acquire(timeout=wait_s):
            try:
                self._count_cache = int(
                    self._reader().execute("SELECT COUNT(*) FROM _lh_datasets").fetchone()[0]
                )
            finally:
                self._lock.release()
        return self._count_cache

    def list_datasets(self) -> list[dict]:
        with self._lock:
            rows = self._reader().execute(
                "SELECT name, source_path, kind, linked, row_count, columns_json, updated_at "
                "FROM _lh_datasets ORDER BY name"
            ).fetchall()
            cols = ["name", "source_path", "kind", "linked", "row_count", "columns", "updated_at"]
            out = []
            for r in rows:
                d = dict(zip(cols, r))
                d["columns"] = json.loads(d["columns"]) if d.get("columns") else []
                out.append(d)
            return out

    def register(self, path: str, name: Optional[str] = None, options: Optional[dict] = None) -> dict:
        options = dict(options or {})
        if not path or not str(path).strip():
            raise DataError("path is empty: pass the full path of a CSV/TSV/Parquet/JSON/Excel/SQLite file or folder")
        p = Path(str(path).strip().strip('"')).expanduser()
        if not p.is_absolute():
            p = (self.data_dir / p).resolve()
        else:
            p = p.resolve()
        if not p.exists():
            raise DataError(f"path does not exist: {p}. Pass an absolute path to a file on this computer")

        with self._lock:
            try:
                if p.is_dir():
                    return self._register_folder(p, name, options)
                suffix = p.suffix.lower()
                if suffix in (".csv", ".tsv", ".txt"):
                    return self._register_csv(p, name, options)
                if suffix == ".parquet":
                    return self._register_parquet(p, name, options)
                if suffix in (".json", ".ndjson", ".jsonl"):
                    return self._register_json(p, name, options)
                if suffix in (".xlsx", ".xlsm"):
                    return self._register_excel(p, name, options)
                if suffix in (".sqlite", ".db", ".sqlite3"):
                    return self._register_sqlite(p, name, options)
            except DataError:
                raise
            except (duckdb.Error, sqlite3.Error, OSError, KeyError, ValueError) as exc:
                raise DataError(f"could not read {p.name}: {_first_lines(str(exc))}") from exc
            raise DataError(
                f"unsupported file type: {suffix or '(none)'}; supported: .csv .tsv .parquet .json .ndjson "
                ".jsonl .xlsx .sqlite .db, or a folder of CSV/Parquet files"
            )

    def _put_meta(self, conn, meta: DatasetMeta) -> None:
        conn.execute(
            "INSERT INTO _lh_datasets (name, source_path, kind, linked, row_count, "
            "columns_json, profile_json, mtime, size, updated_at, options_json) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET source_path=excluded.source_path, kind=excluded.kind, "
            "linked=excluded.linked, row_count=excluded.row_count, columns_json=excluded.columns_json, "
            "profile_json=excluded.profile_json, mtime=excluded.mtime, size=excluded.size, "
            "updated_at=excluded.updated_at, options_json=excluded.options_json",
            (
                meta.name, meta.source_path, meta.kind, meta.linked, meta.row_count,
                json.dumps(meta.columns), json.dumps(meta.profile), meta.mtime, meta.size,
                meta.updated_at, json.dumps(meta.options),
            ),
        )

    def _existing_name(self, conn, name: str) -> Optional[str]:
        row = conn.execute("SELECT name FROM _lh_datasets WHERE lower(name) = lower(?)", [name]).fetchone()
        return row[0] if row else None

    def _materialize(
        self, view_name: str, select_sql: str, size: int, source: Path, kind: str, options: dict,
        *, locale_numeric: Optional[tuple[Optional[str], Optional[str]]] = None,
    ) -> dict:
        """Create TABLE (materialized) or VIEW (linked, for big files), profile it, store metadata."""
        with self._writable() as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                existing = self._existing_name(conn, view_name)
                if existing and existing != view_name:
                    view_name = existing  # DuckDB identifiers are case-insensitive: keep the stored spelling
                object_row = conn.execute(
                    "SELECT table_type FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND lower(table_name) = lower(?)",
                    [view_name],
                ).fetchone()
                if object_row:
                    object_kind = "VIEW" if object_row[0] == "VIEW" else "TABLE"
                    conn.execute(f'DROP {object_kind} "{view_name}"')
                linked = size > LINK_THRESHOLD_BYTES
                numbers_converted: list[str] = []
                if locale_numeric is not None:
                    dec, thou = locale_numeric
                    auto = not (dec or thou)
                    # a linked (>1 GB) file is not scanned for automatic detection
                    if not (auto and linked):
                        select_sql, numbers_converted = _apply_locale_numbers(conn, select_sql, dec, thou, auto=auto)
                kind_sql = "VIEW" if linked else "TABLE"
                conn.execute(f'CREATE {kind_sql} "{view_name}" AS {select_sql}')
                row_count = conn.execute(f'SELECT COUNT(*) FROM "{view_name}"').fetchone()[0]
                columns = [{"name": r[0], "type": r[1]} for r in conn.execute(f'DESCRIBE "{view_name}"').fetchall()]
                profile = _profile(conn, view_name, columns, row_count)
                stat = source.stat() if source.exists() else None
                meta = DatasetMeta(
                    name=view_name,
                    source_path=str(source),
                    kind=kind,
                    linked=linked,
                    row_count=row_count,
                    columns=columns,
                    profile=profile,
                    mtime=stat.st_mtime if stat else 0.0,
                    size=stat.st_size if stat else 0,
                    updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    options=options,
                )
                self._put_meta(conn, meta)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        result = self.describe(view_name)
        if numbers_converted:
            sep = "decimal ',' and thousands '.'" if not (locale_numeric and any(locale_numeric)) else "the given separators"
            result["numbers_converted"] = {
                "columns": numbers_converted,
                "note": f"read as numbers with {sep}, e.g. '-1.150,00' = -1150.00; "
                        "pass options.decimal_separator='.' to keep them as text",
            }
        return result

    def _register_csv(self, p: Path, name: Optional[str], options: dict) -> dict:
        view_name = slugify_name(name or p.stem)
        delim = options.get("delimiter")
        header = options.get("header", True)
        encoding = options.get("encoding")
        date_format = options.get("date_format")
        decimal_sep = options.get("decimal_separator")
        thousands_sep = options.get("thousands_separator")
        if encoding and str(encoding).lower() not in _CSV_ENCODINGS:
            raise DataError(
                f"unsupported encoding: {encoding!r}; use one of utf-8, utf-16, latin-1 "
                "(latin-1 also reads Windows-1252 files correctly for Spanish/French/German text)"
            )

        date_fmt = date_format or _sniff_dayfirst_format(p, delim, bool(header), str(encoding or "utf-8"))

        def build_select(enc: Optional[str]) -> str:
            args = [f"'{_esc(str(p))}'", f"header={str(bool(header)).lower()}"]
            if delim:
                args.append(f"delim='{_esc(str(delim))}'")
            elif p.suffix.lower() == ".tsv":
                args.append("delim='\\t'")
            if enc:
                args.append(f"encoding='{_esc(str(enc))}'")
            if date_fmt:
                args.append(f"dateformat='{_esc(date_fmt)}'")
            return f"SELECT * FROM read_csv_auto({', '.join(args)})"

        # (None, None) means "detect Spanish-style numbers automatically"
        locale_numeric = (decimal_sep, thousands_sep)
        size = p.stat().st_size
        # No explicit encoding: try UTF-8 first, then the common Windows
        # fallbacks, so a Windows-1252 export from a Spanish machine "just
        # works" instead of failing with a confusing "try UTF-16" message.
        candidates: list[Optional[str]] = [encoding] if encoding else [None, *_CSV_ENCODING_FALLBACKS]
        last_exc: Optional[Exception] = None
        for enc in candidates:
            try:
                result = self._materialize(
                    view_name, build_select(enc), size, p, "csv",
                    {**options, **({"encoding": enc} if enc else {})},
                    locale_numeric=locale_numeric,
                )
            except duckdb.Error as exc:
                if encoding or not _looks_like_encoding_error(exc):
                    raise
                last_exc = exc
                continue
            if enc and not encoding:
                result["encoding_detected"] = enc
            return result
        raise DataError(
            f"could not read {p.name}: it is not valid UTF-8, and reading it as latin-1 (the usual "
            f"stand-in for a Windows-1252 export) did not work either; pass options.encoding "
            f"explicitly, e.g. {{\"encoding\": \"latin-1\"}} or {{\"encoding\": \"utf-16\"}} "
            f"({_first_lines(str(last_exc))})"
        ) from last_exc

    def _register_parquet(self, p: Path, name: Optional[str], options: dict) -> dict:
        view_name = slugify_name(name or p.stem)
        select_sql = f"SELECT * FROM read_parquet('{_esc(str(p))}')"
        return self._materialize(view_name, select_sql, p.stat().st_size, p, "parquet", options)

    def _register_json(self, p: Path, name: Optional[str], options: dict) -> dict:
        view_name = slugify_name(name or p.stem)
        select_sql = f"SELECT * FROM read_json_auto('{_esc(str(p))}')"
        return self._materialize(view_name, select_sql, p.stat().st_size, p, "json", options)

    def _register_folder(self, p: Path, name: Optional[str], options: dict) -> dict:
        pattern = str(options.get("glob", "*.csv"))
        if "/" in pattern or "\\" in pattern or ".." in pattern:
            raise DataError("glob must be a file pattern inside the folder, e.g. '*.csv' or '*.parquet'")
        view_name = slugify_name(name or p.name)
        files = [f for f in p.glob(pattern) if f.is_file()]
        if not files:
            raise DataError(f"no files match {pattern!r} in {p}")
        glob_path = str(p / pattern)
        if pattern.endswith(".parquet"):
            select_sql = f"SELECT * FROM read_parquet('{_esc(glob_path)}')"
        else:
            select_sql = f"SELECT * FROM read_csv_auto('{_esc(glob_path)}')"
        size = sum(f.stat().st_size for f in files)
        return self._materialize(view_name, select_sql, size, p, "folder", {**options, "glob": pattern})

    def _register_excel(self, p: Path, name: Optional[str], options: dict) -> dict:
        base = slugify_name(name or p.stem)
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        try:
            wanted = options.get("sheets") or ([options["sheet"]] if options.get("sheet") else None)
            sheets = wanted or wb.sheetnames
            missing = [s for s in sheets if s not in wb.sheetnames]
            if missing:
                raise DataError(f"sheet(s) not found: {missing}; the workbook has {wb.sheetnames}")
            explicit_skip = options.get("skip_rows")
            results = []
            for sheet_name in sheets:
                ws = wb[sheet_name]
                skip_rows = _resolve_excel_skip_rows(ws, explicit_skip)
                rows_iter = ws.iter_rows(min_row=skip_rows + 1, values_only=True)
                try:
                    header = next(rows_iter)
                except StopIteration:
                    continue
                header = [str(h) if h is not None else f"col{i}" for i, h in enumerate(header)]
                view_name = slugify_name(f"{base}__{slugify_name(sheet_name)}")
                csv_path = self.cache_dir / f"__xlsx_{view_name}.csv"
                with csv_path.open("w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(header)
                    for row in rows_iter:
                        writer.writerow(["" if v is None else v for v in row])
                parquet_path = self.cache_dir / f"{view_name}.parquet"
                with self._writable() as conn:
                    conn.execute(
                        f"COPY (SELECT * FROM read_csv_auto('{_esc(str(csv_path))}', header=true)) "
                        f"TO '{_esc(str(parquet_path))}' (FORMAT PARQUET)"
                    )
                csv_path.unlink(missing_ok=True)
                select_sql = f"SELECT * FROM read_parquet('{_esc(str(parquet_path))}')"
                sheet_options = {**options, "sheet": sheet_name}
                if explicit_skip is None and skip_rows:
                    sheet_options["skip_rows_detected"] = skip_rows
                results.append(
                    self._materialize(view_name, select_sql, parquet_path.stat().st_size, p, "excel",
                                      sheet_options)
                )
        finally:
            wb.close()
        if not results:
            raise DataError("workbook has no readable sheets")
        return {"name": results[0]["name"], "sheets": [r["name"] for r in results],
                "datasets": [_compact_meta(r) for r in results]}

    def _register_sqlite(self, p: Path, name: Optional[str], options: dict) -> dict:
        base = slugify_name(name or p.stem)
        src = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True)
        try:
            all_tables = [
                r[0]
                for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            tables = options.get("tables") or all_tables
            missing = [t for t in tables if t not in all_tables]
            if missing:
                raise DataError(f"table(s) not found: {missing}; the database has {all_tables}")
            results = []
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                cur = src.execute(f"SELECT * FROM {quoted}")
                cols = [d[0] for d in cur.description]
                view_name = slugify_name(f"{base}__{slugify_name(table)}")
                csv_path = self.cache_dir / f"__sqlite_{view_name}.csv"
                blob_cols: set[int] = set()
                with csv_path.open("w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(cols)
                    for row in cur:
                        out_row = list(row)
                        for i, v in enumerate(out_row):
                            if isinstance(v, (bytes, bytearray)):
                                # DuckDB reads "\xHH" escapes back into the same bytes
                                out_row[i] = "".join(f"\\x{b:02X}" for b in v)
                                blob_cols.add(i)
                        writer.writerow(out_row)
                types = ", ".join(f"'{_esc(cols[i])}': 'BLOB'" for i in sorted(blob_cols))
                types_arg = f", types={{{types}}}" if types else ""
                parquet_path = self.cache_dir / f"{view_name}.parquet"
                with self._writable() as conn:
                    conn.execute(
                        f"COPY (SELECT * FROM read_csv_auto('{_esc(str(csv_path))}', header=true{types_arg})) "
                        f"TO '{_esc(str(parquet_path))}' (FORMAT PARQUET)"
                    )
                csv_path.unlink(missing_ok=True)
                select_sql = f"SELECT * FROM read_parquet('{_esc(str(parquet_path))}')"
                results.append(
                    self._materialize(view_name, select_sql, parquet_path.stat().st_size, p, "sqlite",
                                      {**options, "table": table})
                )
        finally:
            src.close()
        if not results:
            raise DataError("sqlite file has no tables")
        return {"name": results[0]["name"], "tables": [r["name"] for r in results],
                "datasets": [_compact_meta(r) for r in results]}

    # -- describe ------------------------------------------------------------

    def _get_meta_row(self, conn, name: str) -> Optional[dict]:
        cur = conn.execute("SELECT * FROM _lh_datasets WHERE lower(name) = lower(?)", [name])
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def _unknown(self, name: str) -> DataError:
        names = [d["name"] for d in self.list_datasets()]
        if names:
            return DataError(f"unknown dataset: {name!r}. Registered datasets: {', '.join(names[:20])}")
        return DataError(f"unknown dataset: {name!r}. No datasets are registered yet: call data_register with a file path first")

    def describe(self, name: str) -> dict:
        with self._lock:
            lookup = slugify_name(name) if isinstance(name, str) and name.strip() else ""
            if not lookup:
                raise DataError("dataset name is empty")
            meta = self._get_meta_row(self._reader(), lookup)
            if meta is None:
                raise self._unknown(name)
            name = meta["name"]
            source = Path(meta["source_path"])
            stale = False
            if source.exists() and source.is_file():
                stat = source.stat()
                if stat.st_mtime != meta["mtime"] or stat.st_size != meta["size"]:
                    if meta["kind"] in ("csv", "parquet", "json"):
                        options = json.loads(meta.get("options_json") or "{}")
                        self.register(str(source), name=name, options=options)
                        meta = self._get_meta_row(self._reader(), name)
                    else:
                        stale = True
            columns = json.loads(meta["columns_json"]) if meta["columns_json"] else []
            profile = json.loads(meta["profile_json"]) if meta["profile_json"] else {}
            conn = self._reader()
            sample = conn.execute(f'SELECT * FROM "{name}" LIMIT 5').fetchall()
            col_names = [c["name"] for c in columns]
            sample_rows = [
                {col_names[i]: _json_safe(v, cap=120, list_cap=SAMPLE_LIST_ITEMS) for i, v in enumerate(row)}
                for row in sample
            ]
            out = {
                "name": name,
                "source_path": meta["source_path"],
                "kind": meta["kind"],
                "linked": bool(meta["linked"]),
                "row_count": meta["row_count"],
                "column_count": len(columns),
                "columns": columns,
                "profile": profile,
                "sample_rows": sample_rows,
                "updated_at": meta["updated_at"],
            }
            if stale:
                out["stale"] = True
                out["stale_hint"] = "the source file changed since registration; call data_register on it again"
            nested = [c["name"] for c in columns if _is_nested_type(c["type"])]
            if nested and meta["row_count"] <= 5:
                out["hint"] = (
                    f"this dataset has only {meta['row_count']} row(s) with nested column(s) "
                    f"{', '.join(nested)}; if it is really one record per exported item, expand it with "
                    f"e.g. SELECT UNNEST({nested[0]}) FROM \"{name}\" in data_query"
                )
            return out

    # -- query ---------------------------------------------------------------

    def query(self, sql: str, limit: int = 50, timeout_s: int = DEFAULT_QUERY_TIMEOUT_S) -> dict:
        """Run one gated, read-only statement and return up to `limit` rows (max 1000).

        `row_count` is the number of rows returned; `total_rows` is the size
        of the whole result (counted up to 1,000,000; `None` beyond that).
        """
        limit = max(1, min(int(limit), MAX_QUERY_LIMIT))
        return self._run(sql, limit=limit, timeout_s=timeout_s, cell_cap=MAX_CELL_CHARS)

    def query_all(self, sql: str, max_rows: int = 5_000_000, timeout_s: int = DEFAULT_QUERY_TIMEOUT_S) -> dict:
        """Internal: the same gated read-only query, returning every row (for stats/charts)."""
        return self._run(sql, limit=max_rows, timeout_s=timeout_s, cell_cap=None)

    def _run(self, sql: str, *, limit: int, timeout_s: int, cell_cap: Optional[int]) -> dict:
        gate_sql(sql)
        with self._lock:
            conn = self._reader()
            timer = threading.Timer(timeout_s, conn.interrupt)
            timer.daemon = True
            start = time.monotonic()
            timer.start()
            total: Optional[int]
            try:
                rel = conn.execute(sql)
                if rel.description is None:
                    raise DataError("the statement returned no result set")
                columns = [{"name": d[0], "type": str(d[1])} for d in rel.description]
                rows = rel.fetchmany(limit + 1)
                total = len(rows)
                if total > limit:
                    while total <= TOTAL_COUNT_CAP:
                        chunk = rel.fetchmany(10_000)
                        if not chunk:
                            break
                        total += len(chunk)
                    if total > TOTAL_COUNT_CAP:
                        total = None
                del rel
            except duckdb.InterruptException as exc:
                raise DataError(
                    f"query exceeded {timeout_s}s and was stopped; aggregate or filter in SQL instead of selecting everything"
                ) from exc
            except duckdb.PermissionException as exc:
                raise DataError(
                    "this query tried to read a file directly; register the file with data_register "
                    "and query it by its dataset name instead"
                ) from exc
            except duckdb.Error as exc:
                raise DataError(f"SQL error: {_first_lines(str(exc))}") from exc
            finally:
                timer.cancel()
            elapsed_ms = (time.monotonic() - start) * 1000

        truncated = len(rows) > limit
        rows = rows[:limit]
        col_names = [c["name"] for c in columns]
        # cell_cap is only set on the public query() path (query_all(), used
        # internally by stats/charts, passes None): cap nested list/struct
        # values there too, for the same reason long text cells are capped.
        list_cap = MAX_LIST_ITEMS if cell_cap is not None else None
        json_rows = [
            {col_names[i]: _json_safe(v, cap=cell_cap, list_cap=list_cap) for i, v in enumerate(row)}
            for row in rows
        ]
        return {
            "columns": columns,
            "rows": json_rows,
            "row_count": len(json_rows),
            "total_rows": total,
            "truncated": truncated,
            "elapsed_ms": round(elapsed_ms, 2),
        }


def _is_nested_type(type_name: str) -> bool:
    t = type_name.upper().rstrip()
    return _base_type(t) in ("STRUCT", "MAP", "UNION") or t.endswith("]")


def _compact_meta(d: dict) -> dict:
    return {"name": d["name"], "row_count": d["row_count"], "columns": [c["name"] for c in d["columns"]]}


def _profile(conn, view_name: str, columns: list[dict], total: int) -> dict:
    profile = {}
    for col in columns:
        cname = col["name"]
        q = '"' + cname.replace('"', '""') + '"'
        nulls, distinct = conn.execute(
            f'SELECT COUNT(*) - COUNT({q}), APPROX_COUNT_DISTINCT({q}) FROM "{view_name}"'
        ).fetchone()
        entry: dict[str, Any] = {
            "type": col["type"],
            "nulls_pct": round(100.0 * nulls / total, 2) if total else 0.0,
            "distinct_approx": int(distinct) if distinct is not None else 0,
        }
        if is_numeric_type(col["type"]):
            stats_row = conn.execute(
                f'SELECT MIN({q}), MAX({q}), AVG({q}), STDDEV_SAMP({q}) FROM "{view_name}"'
            ).fetchone()
            entry.update(
                {
                    "min": _json_safe(stats_row[0]),
                    "max": _json_safe(stats_row[1]),
                    "mean": _json_safe(stats_row[2]),
                    "sd": _json_safe(stats_row[3]),
                }
            )
            try:
                hist = conn.execute(
                    f'SELECT bin, COUNT(*) FROM (SELECT LEAST(9, FLOOR(({q} - m.lo) / NULLIF(m.hi - m.lo, 0) * 10))::INT AS bin '
                    f'FROM "{view_name}", (SELECT MIN({q}) AS lo, MAX({q}) AS hi FROM "{view_name}") m WHERE {q} IS NOT NULL) '
                    f'GROUP BY bin ORDER BY bin'
                ).fetchall()
                counts = [0] * 10
                for b, n in hist:
                    counts[int(b) if b is not None else 0] += int(n)
                entry["histogram"] = counts
            except duckdb.Error:
                pass
        elif is_temporal_type(col["type"]):
            stats_row = conn.execute(f'SELECT MIN({q}), MAX({q}) FROM "{view_name}"').fetchone()
            entry.update({"min": _json_safe(stats_row[0]), "max": _json_safe(stats_row[1])})
        elif _base_type(col["type"]) == "BLOB":
            size_row = conn.execute(f'SELECT MIN(octet_length({q})), MAX(octet_length({q})) FROM "{view_name}"').fetchone()
            entry.update({"bytes_min": size_row[0], "bytes_max": size_row[1],
                          "note": "binary data (e.g. a thumbnail); query octet_length() rather than its contents"})
        elif _is_nested_type(col["type"]):
            # top values of a whole nested list/struct would be the full value
            # repeated (hundreds of KB for an export's array column)
            entry["note"] = "nested value: expand it with UNNEST in data_query to profile its fields"
        else:
            try:
                top = conn.execute(
                    f'SELECT {q}, COUNT(*) AS n FROM "{view_name}" WHERE {q} IS NOT NULL '
                    f'GROUP BY {q} ORDER BY n DESC, 1 LIMIT 5'
                ).fetchall()
                entry["top_values"] = [{"value": _json_safe(v, cap=80), "count": int(n)} for v, n in top]
            except duckdb.Error:
                entry["top_values"] = []
        profile[cname] = entry
    return profile


def _esc(text: str) -> str:
    return text.replace("'", "''")


# -- CSV locale handling: encoding, day-first dates, decimal/thousands seps --

# DuckDB's CSV reader only accepts these encoding names without an extra
# extension (verified against duckdb 1.5.x) — and this catalogue never
# auto-installs extensions (see the module docstring), so cp1252 itself is
# not usable even though DuckDB's error message mentions it. 'latin-1' is
# used as the practical stand-in for Windows-1252: they only differ in the
# 0x80-0x9F range (curly quotes, em-dash, €...), and are identical for every
# Spanish/French/German accented letter, which is what a "Windows-1252 CSV"
# almost always means in practice.
_CSV_ENCODINGS = {"utf-8", "utf8", "utf-16", "latin-1", "latin1"}
_CSV_ENCODING_FALLBACKS = ("latin-1",)


def _looks_like_encoding_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "not utf-8 encoded" in text or "invalid unicode" in text


_DATE_TOKEN_RE = re.compile(r"^(\d{1,2})([/-])(\d{1,2})\2(\d{2}|\d{4})$")


def _sniff_dayfirst_format(p: Path, delimiter: Optional[str], header: bool, encoding: str = "utf-8") -> Optional[str]:
    """Find unambiguous day-first evidence in a slash/dash-separated date
    column, e.g. "13/02/25" (13 can only be a day) — DuckDB's own sniffer
    otherwise reads it as 2013-02-25 (day and year swapped), silently.

    Returns a `dateformat` string for `read_csv_auto` when a component > 12
    is found in a consistent position across the sampled rows; returns
    `None` (no override, DuckDB's own sniffer decides as before) when there
    is no such evidence, or when it is contradictory (mixed formats in the
    same file) — guessing wrong would be worse than not guessing.
    """
    sep_guess = delimiter or ("\t" if p.suffix.lower() == ".tsv" else ",")
    dayfirst: Optional[bool] = None
    sep = "/"
    year_len4 = True
    try:
        with p.open("r", encoding=encoding, errors="replace", newline="") as fh:
            reader = csv.reader(fh, delimiter=sep_guess)
            for i, row in enumerate(reader):
                if i == 0 and header:
                    continue
                if i > 500:
                    break
                for cell in row:
                    m = _DATE_TOKEN_RE.match((cell or "").strip())
                    if not m:
                        continue
                    a_s, s, b_s, y = m.groups()
                    a, b = int(a_s), int(b_s)
                    if a > 31 or b > 31 or a == 0 or b == 0:
                        continue
                    if a > 12 and b <= 12:
                        if dayfirst is False:
                            return None  # contradictory evidence: leave it alone
                        dayfirst, sep, year_len4 = True, s, len(y) == 4
                    elif b > 12 and a <= 12:
                        if dayfirst is True:
                            return None
                        dayfirst, sep, year_len4 = False, s, len(y) == 4
    except OSError:
        return None
    if dayfirst:
        return f"%d{sep}%m{sep}" + ("%Y" if year_len4 else "%y")
    return None


_MAX_TITLE_ROWS = 10


def _resolve_excel_skip_rows(ws, explicit: Any) -> int:
    """How many leading rows to skip before the header row.

    An explicit `options.skip_rows` always wins. Otherwise, detect a title
    block above the real header: a first row with a single filled cell (a
    title like "Q1 2024 report" in a merged cell), then any further rows
    with at most one filled cell (blank spacer rows, a subtitle), then a row
    with several filled cells - the actual column headers. Without this the
    title becomes the header, producing columns named col1, col2...
    """
    if explicit is not None:
        try:
            return max(0, int(explicit))
        except (TypeError, ValueError):
            raise DataError(f"skip_rows must be a whole number, got {explicit!r}")
    try:
        peek = list(ws.iter_rows(min_row=1, max_row=_MAX_TITLE_ROWS + 1, values_only=True))
    except Exception:  # noqa: BLE001 - never let the heuristic itself break registration
        return 0
    nonempty = lambda row: sum(1 for v in row if v not in (None, "") and str(v).strip())  # noqa: E731
    if not peek or nonempty(peek[0]) != 1:
        return 0
    for i, row in enumerate(peek[1:], start=1):
        filled = nonempty(row)
        if filled >= 2:
            return i
    return 0


# A value written the Spanish/continental way: "-1.150,00", "7.262,37",
# "51,05", "1.234" (thousands only) or "42". Used by the automatic detection
# below, which only fires when *every* non-blank value of a text column
# matches this and at least one of them is unmistakably a decimal comma.
_COMMA_NUMBER_RE = r"[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?"
# Unambiguous decimal-comma evidence: a comma followed by 1-2 or 4+ digits
# ("51,05", "3,5", "40,4168"), or dot-thousands together with a comma
# ("1.150,00"). "1,234" alone is not evidence: it could be English 1234.
_COMMA_DECIMAL_EVIDENCE_RE = r"[+-]?(\d+,(\d{1,2}|\d{4,})|\d{1,3}(\.\d{3})+,\d+)"
_MAX_EXACT_SCALE = 6


def _apply_locale_numbers(
    conn, base_sql: str, decimal_sep: Optional[str], thousands_sep: Optional[str], *, auto: bool = False,
) -> tuple[str, list[str]]:
    """Turn VARCHAR columns that hold locale-formatted numbers into numbers.

    DuckDB's CSV sniffer only ever considers '.' a decimal point, so a
    Spanish export like "-1.150,00" is left as text and SUM/AVG fail on it.

    Explicit mode (`decimal_sep` / `thousands_sep` given): every VARCHAR
    column is tested - strip the thousands separator, turn the decimal
    separator into '.', TRY_CAST to DOUBLE - and rewritten when at least 90%
    of its non-blank values cast cleanly.

    Automatic mode (`auto=True`, nothing given): a VARCHAR column is
    rewritten only when *all* its non-blank values look like "1.234,56" /
    "51,05" / "42" and at least one has an unambiguous decimal comma, so no
    value is ever lost to a NULL and English-looking "1,234" is left alone.
    Those columns become DECIMAL with the column's own number of decimals
    (exact sums for money), or DOUBLE beyond 6 decimals.

    Returns the (possibly rewritten) SELECT and the names of the columns it
    converted.
    """
    try:
        described = conn.execute(f"DESCRIBE ({base_sql})").fetchall()
    except duckdb.Error:
        return base_sql, []
    if auto:
        decimal_sep, thousands_sep = ",", "."
    decimal_sep = decimal_sep or "."
    parts: list[str] = []
    converted: list[str] = []
    for row in described:
        cname, ctype = row[0], row[1]
        q = '"' + cname.replace('"', '""') + '"'
        if _base_type(ctype) != "VARCHAR" or (decimal_sep == "." and not thousands_sep):
            parts.append(q)
            continue
        norm = f"TRIM({q})"
        if thousands_sep:
            norm = f"REPLACE({norm}, '{_esc(thousands_sep)}', '')"
        if decimal_sep != ".":
            norm = f"REPLACE({norm}, '{_esc(decimal_sep)}', '.')"
        blank = f"({q} IS NULL OR TRIM({q}) = '')"
        try:
            if auto:
                non_blank, matching, evidence, scale = conn.execute(
                    f"SELECT COUNT(*) FILTER (WHERE NOT {blank}), "
                    f"COUNT(*) FILTER (WHERE NOT {blank} AND regexp_full_match(TRIM({q}), '{_COMMA_NUMBER_RE}')), "
                    f"COUNT(*) FILTER (WHERE NOT {blank} AND regexp_full_match(TRIM({q}), '{_COMMA_DECIMAL_EVIDENCE_RE}')), "
                    f"MAX(CASE WHEN strpos(TRIM({q}), ',') > 0 THEN length(split_part(TRIM({q}), ',', 2)) ELSE 0 END) "
                    f"FROM ({base_sql})"
                ).fetchone()
                ok = bool(non_blank) and matching == non_blank and evidence > 0
                target = f"DECIMAL(18, {int(scale or 0)})" if (scale or 0) <= _MAX_EXACT_SCALE else "DOUBLE"
            else:
                probe = f"TRY_CAST(NULLIF({norm}, '') AS DOUBLE)"
                non_blank, castable = conn.execute(
                    f"SELECT COUNT(*) FILTER (WHERE NOT {blank}), "
                    f"COUNT(*) FILTER (WHERE NOT {blank} AND {probe} IS NOT NULL) FROM ({base_sql})"
                ).fetchone()
                ok = bool(non_blank) and castable / non_blank >= 0.9
                target = "DOUBLE"
        except duckdb.Error:
            parts.append(q)
            continue
        if ok:
            parts.append(f"TRY_CAST(NULLIF({norm}, '') AS {target}) AS {q}")
            converted.append(cname)
        else:
            parts.append(q)
    if not converted:
        return base_sql, []
    return f"SELECT {', '.join(parts)} FROM ({base_sql}) t", converted
