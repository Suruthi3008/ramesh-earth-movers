"""Excel storage layer. The only module that touches openpyxl.

Concurrency: a process-wide re-entrant lock serialises all access. Run uvicorn
with a single worker (the default). Multiple processes would overwrite each other.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Sheet name -> column headers. The first column is always the record ID.
SHEETS: dict[str, list[str]] = {
    "Bills": [
        "bill_id", "bill_number", "bill_date", "customer_name", "customer_address",
        "project_name", "total_amount", "total_paid", "pending_amount", "status",
        "created_at", "updated_at",
    ],
    "Bill_Items": [
        "item_id", "bill_id", "date", "work_type", "description", "quantity",
        "unit", "rate", "bata", "amount",
    ],
    "Payments": [
        "payment_id", "bill_id", "bill_number", "customer_name", "payment_date",
        "amount", "payment_method", "reference_number", "notes", "created_at",
    ],
    "Expenses": [
        "expense_id", "date", "category", "description", "amount",
        "payment_method", "notes", "created_at", "machine",
    ],
    "Daily_Work": [
        "work_id", "date", "customer_name", "work_type", "description", "quantity",
        "unit", "rate", "bata", "amount", "notes", "created_at",
    ],
    "Drivers": ["driver_id", "name", "phone", "salary", "joining_date", "status"],
    "Driver_Payments": [
        "payment_id", "driver_id", "driver_name", "payment_type", "date", "month",
        "amount", "payment_date", "notes", "status",
    ],
    "Settings": ["key", "value"],
}

ID_PREFIXES: dict[str, str] = {
    "Bills": "BILL",
    "Bill_Items": "ITM",
    "Payments": "PAY",
    "Expenses": "EXP",
    "Daily_Work": "WRK",
    "Drivers": "DRV",
    "Driver_Payments": "DPY",
}


class StorageBusyError(Exception):
    """The workbook could not be saved, usually because it is open in Excel."""


def _to_cell(value: Any) -> Any:
    """Convert Python values into types openpyxl can store."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _headers(ws) -> list[Any]:
    return [cell.value for cell in ws[1]]


class ExcelService:
    def __init__(self, path: Path, backup_dir: Path, backup_keep: int) -> None:
        self.path = Path(path)
        self.backup_dir = Path(backup_dir)
        self.backup_keep = backup_keep
        self._lock = threading.RLock()
        self._wb: Workbook | None = None
        self._mtime: float = 0.0
        self._initialise()

    # ------------------------------------------------------------------ setup

    def _initialise(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                wb = Workbook()
                wb.remove(wb.active)
                for name, headers in SHEETS.items():
                    ws = wb.create_sheet(name)
                    ws.append(headers)
                    ws.freeze_panes = "A2"
                wb.save(self.path)
                logger.info("Created new workbook at %s", self.path)

            self._load()
            self._migrate_schema()

    def _load(self) -> None:
        self._wb = load_workbook(self.path)
        self._mtime = self.path.stat().st_mtime

    def _migrate_schema(self) -> None:
        """Add missing sheets and missing columns without touching existing data."""
        changed = False
        for name, headers in SHEETS.items():
            if name not in self._wb.sheetnames:
                ws = self._wb.create_sheet(name)
                ws.append(headers)
                ws.freeze_panes = "A2"
                changed = True
                continue

            ws = self._wb[name]
            existing = [h for h in _headers(ws) if h is not None]
            for header in headers:
                if header not in existing:
                    existing.append(header)
                    ws.cell(row=1, column=len(existing), value=header)
                    changed = True

        if changed:
            self._commit(backup=False)

    # ------------------------------------------------------------- internals

    def _ws(self, sheet: str):
        if sheet not in SHEETS:
            raise KeyError(f"Unknown sheet: {sheet}")
        return self._wb[sheet]

    def _reload_if_changed(self) -> None:
        """Pick up edits made by another program (e.g. after a cloud sync)."""
        if self.path.exists() and self.path.stat().st_mtime != self._mtime:
            self._load()

    def _find_row_index(self, ws, record_id: Any) -> int | None:
        for idx, row in enumerate(ws.iter_rows(min_row=2, max_col=1, values_only=True), start=2):
            if row[0] == record_id:
                return idx
        return None

    def _next_id(self, sheet: str) -> str:
        prefix = ID_PREFIXES[sheet]
        id_column = SHEETS[sheet][0]
        highest = 0
        for row in self.list_rows(sheet):
            value = str(row.get(id_column, ""))
            if value.startswith(f"{prefix}-"):
                try:
                    highest = max(highest, int(value.rsplit("-", 1)[-1]))
                except ValueError:
                    continue
        return f"{prefix}-{highest + 1:05d}"

    def _commit(self, backup: bool = True) -> None:
        """Save atomically: write a temp file, then replace the real file."""
        tmp_path = self.path.with_name(f"{self.path.stem}.tmp.xlsx")
        try:
            self._wb.save(tmp_path)
            if backup and self.path.exists():
                self._backup()
            os.replace(tmp_path, self.path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            self._load()  # discard the unsaved in-memory change
            logger.exception("Workbook save failed (file may be open in Excel)")
            raise StorageBusyError(
                "The data file is open or locked. Close it and try again."
            ) from exc
        self._mtime = self.path.stat().st_mtime

    def _backup(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(self.path, self.backup_dir / f"business_data-{stamp}.xlsx")

        backups = sorted(self.backup_dir.glob("business_data-*.xlsx"))
        for old in backups[: -self.backup_keep] if self.backup_keep else []:
            old.unlink(missing_ok=True)

    # ---------------------------------------------------------- public API

    def list_rows(self, sheet: str) -> list[dict[str, Any]]:
        """Return every non-empty row of a sheet as a dict keyed by header."""
        with self._lock:
            self._reload_if_changed()
            ws = self._ws(sheet)
            headers = _headers(ws)
            rows: list[dict[str, Any]] = []
            for values in ws.iter_rows(min_row=2, values_only=True):
                if values[0] in (None, ""):
                    continue
                rows.append({h: v for h, v in zip(headers, values) if h is not None})
            return rows

    def get(self, sheet: str, record_id: str) -> dict[str, Any] | None:
        id_column = SHEETS[sheet][0]
        for row in self.list_rows(sheet):
            if row.get(id_column) == record_id:
                return row
        return None

    def create(self, sheet: str, data: dict[str, Any]) -> dict[str, Any]:
        """Add a record, generating its ID. Returns the stored record."""
        with self._lock:
            self._reload_if_changed()
            ws = self._ws(sheet)
            id_column = SHEETS[sheet][0]

            record = dict(data)
            record[id_column] = self._next_id(sheet)
            headers = _headers(ws)
            ws.append([_to_cell(record.get(h)) if h is not None else None for h in headers])
            self._commit()
            return record

    def update(self, sheet: str, record_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update the given fields of a record. Returns the updated record, or None if missing."""
        with self._lock:
            self._reload_if_changed()
            ws = self._ws(sheet)
            idx = self._find_row_index(ws, record_id)
            if idx is None:
                return None

            for col, header in enumerate(_headers(ws), start=1):
                if header is not None and header in updates:
                    ws.cell(row=idx, column=col, value=_to_cell(updates[header]))
            self._commit()
            return self.get(sheet, record_id)

    def delete(self, sheet: str, record_id: str) -> bool:
        with self._lock:
            self._reload_if_changed()
            ws = self._ws(sheet)
            idx = self._find_row_index(ws, record_id)
            if idx is None:
                return False
            ws.delete_rows(idx)
            self._commit()
            return True

    def snapshot(self, dest: Path) -> Path:
        """Save a copy of the current data to dest. The live file is never handed out."""
        with self._lock:
            self._reload_if_changed()
            self._wb.save(dest)
        return Path(dest)

    def upsert_settings(self, values: dict[str, str]) -> None:
        """Create or update several Settings rows, then save the workbook once."""
        with self._lock:
            self._reload_if_changed()
            ws = self._ws("Settings")
            for key, value in values.items():
                idx = self._find_row_index(ws, key)
                if idx is None:
                    ws.append([key, _to_cell(value)])
                else:
                    ws.cell(row=idx, column=2, value=_to_cell(value))
            self._commit()


@lru_cache
def get_excel_service() -> ExcelService:
    settings = get_settings()
    return ExcelService(
        path=settings.EXCEL_FILE_PATH,
        backup_dir=settings.BACKUP_DIR,
        backup_keep=settings.BACKUP_KEEP,
    )
