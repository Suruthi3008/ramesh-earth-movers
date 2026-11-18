"""Bill rules: numbering, line calculation, totals, status and saving to Excel."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.schemas.bill import DEFAULT_UNITS, BillIn, BillOut, BillSummary, WorkItemIn, WorkItemOut
from app.services.errors import BusinessRuleError, NotFoundError
from app.services.excel_service import get_excel_service
from app.services.settings_service import get_text
from app.utils.calculations import (
    bill_status,
    bill_total,
    line_amount,
    money,
    pending_amount,
    to_decimal,
)

BILLS = "Bills"
ITEMS = "Bill_Items"
PAYMENTS = "Payments"
ZERO = Decimal("0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _paid_by_bill() -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for payment in get_excel_service().list_rows(PAYMENTS):
        bill_id = payment.get("bill_id")
        if bill_id:
            totals[bill_id] = totals.get(bill_id, ZERO) + money(payment.get("amount"))
    return totals


def _summary(row: dict[str, Any], paid: Decimal) -> BillSummary:
    total = money(row.get("total_amount"))
    paid = money(paid)
    return BillSummary(
        bill_id=row["bill_id"],
        bill_number=str(row["bill_number"]),
        bill_date=_to_date(row["bill_date"]),
        customer_name=row["customer_name"],
        project_name=row.get("project_name") or "",
        total_amount=float(total),
        total_paid=float(paid),
        pending_amount=float(pending_amount(total, paid)),
        status=bill_status(total, paid),
    )


def _item_out(row: dict[str, Any]) -> WorkItemOut:
    return WorkItemOut(
        item_id=row["item_id"],
        date=_to_date(row["date"]),
        work_type=row["work_type"],
        description=row.get("description") or "",
        quantity=float(to_decimal(row.get("quantity"))),
        unit=row.get("unit") or "",
        rate=float(money(row.get("rate"))),
        bata=float(money(row.get("bata"))),
        amount=float(money(row.get("amount"))),
    )


def _items_for(bill_id: str) -> list[dict[str, Any]]:
    rows = [r for r in get_excel_service().list_rows(ITEMS) if r.get("bill_id") == bill_id]
    return sorted(rows, key=lambda r: (str(r.get("date")), str(r.get("item_id"))))


def _remove_items(bill_id: str) -> None:
    excel = get_excel_service()
    for row in _items_for(bill_id):
        excel.delete(ITEMS, row["item_id"])


def _next_bill_number(prefix: str) -> str:
    highest = 0
    for row in get_excel_service().list_rows(BILLS):
        raw = str(row.get("bill_number") or "")
        if raw.startswith(prefix):
            digits = raw[len(prefix):]
            if digits.isdigit():
                highest = max(highest, int(digits))
    return f"{prefix}{highest + 1:02d}"


def _prepare_items(items: list[WorkItemIn]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if item.work_type == "Lorry Service":
            description = item.material  # the material is the description on the bill
        else:
            description = item.description.strip()
            if not description:
                if item.work_type == "Other Work":
                    raise BusinessRuleError("Enter a description for each Other Work item.")
                description = item.work_type

        rows.append(
            {
                "date": item.date.isoformat(),
                "work_type": item.work_type,
                "description": description,
                "quantity": item.quantity,
                "unit": item.unit.strip() or DEFAULT_UNITS[item.work_type],
                "rate": item.rate,
                "bata": item.bata,
                "amount": line_amount(item.quantity, item.rate, item.bata),
            }
        )
    return rows


def list_bills(query: str | None = None, status: str | None = None) -> list[BillSummary]:
    excel = get_excel_service()
    paid_map = _paid_by_bill()
    rows = sorted(excel.list_rows(BILLS), key=lambda r: str(r.get("created_at") or ""), reverse=True)

    needle = (query or "").strip().lower()
    result: list[BillSummary] = []
    for row in rows:
        summary = _summary(row, paid_map.get(row["bill_id"], ZERO))
        if needle and needle not in summary.customer_name.lower() and needle not in summary.bill_number.lower():
            continue
        if status and summary.status != status:
            continue
        result.append(summary)
    return result


def get_bill(bill_id: str) -> BillOut:
    row = get_excel_service().get(BILLS, bill_id)
    if row is None:
        raise NotFoundError("Bill not found.")

    summary = _summary(row, _paid_by_bill().get(bill_id, ZERO))
    return BillOut(
        **summary.model_dump(),
        customer_address=row.get("customer_address") or "",
        items=[_item_out(r) for r in _items_for(bill_id)],
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def create_bill(data: BillIn) -> BillOut:
    excel = get_excel_service()
    rows = _prepare_items(data.items)
    total = bill_total(r["amount"] for r in rows)
    if total <= 0:
        raise BusinessRuleError("The bill total must be more than zero.")

    now = _now()
    bill = excel.create(
        BILLS,
        {
            "bill_number": _next_bill_number(get_text("bill_number_prefix")),
            "bill_date": data.bill_date.isoformat(),
            "customer_name": data.customer_name,
            "customer_address": data.customer_address.strip(),
            "project_name": data.project_name.strip(),
            "total_amount": total,
            "total_paid": ZERO,
            "pending_amount": total,
            "status": "Pending",
            "created_at": now,
            "updated_at": now,
        },
    )

    # Excel has no transactions, so roll back by hand if an item fails to save
    try:
        for row in rows:
            excel.create(ITEMS, {"bill_id": bill["bill_id"], **row})
    except Exception:
        _remove_items(bill["bill_id"])
        excel.delete(BILLS, bill["bill_id"])
        raise

    return get_bill(bill["bill_id"])


def update_bill(bill_id: str, data: BillIn) -> BillOut:
    excel = get_excel_service()
    if excel.get(BILLS, bill_id) is None:
        raise NotFoundError("Bill not found.")

    rows = _prepare_items(data.items)
    total = bill_total(r["amount"] for r in rows)
    if total <= 0:
        raise BusinessRuleError("The bill total must be more than zero.")

    paid = _paid_by_bill().get(bill_id, ZERO)
    if total < paid:
        raise BusinessRuleError(
            f"The total cannot be less than the ₹{paid:,.2f} already received for this bill."
        )

    # Save new items first, then remove the old ones, so a failure never leaves the bill empty
    old_items = _items_for(bill_id)
    for row in rows:
        excel.create(ITEMS, {"bill_id": bill_id, **row})
    for old in old_items:
        excel.delete(ITEMS, old["item_id"])

    excel.update(
        BILLS,
        bill_id,
        {
            "bill_date": data.bill_date.isoformat(),
            "customer_name": data.customer_name,
            "customer_address": data.customer_address.strip(),
            "project_name": data.project_name.strip(),
            "total_amount": total,
            "total_paid": paid,
            "pending_amount": pending_amount(total, paid),
            "status": bill_status(total, paid),
            "updated_at": _now(),
        },
    )
    return get_bill(bill_id)


def delete_bill(bill_id: str) -> None:
    excel = get_excel_service()
    if excel.get(BILLS, bill_id) is None:
        raise NotFoundError("Bill not found.")
    if bill_id in _paid_by_bill():
        raise BusinessRuleError(
            "This bill has payments recorded. Remove the payments before deleting the bill."
        )

    _remove_items(bill_id)
    excel.delete(BILLS, bill_id)
