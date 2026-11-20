"""Business settings. Values saved in the Settings sheet override the defaults below."""
from decimal import Decimal

from app.schemas.settings import SettingsIn
from app.services.excel_service import get_excel_service

DEFAULT_SETTINGS: dict[str, str] = {
    "business_name": "RAMESH EARTH MOVERS",
    "business_tagline": "& BUILDER (ARROW GROUPS)",
    "address": "34 Indira Gandhi Street, Kanathur, ECR, Chengalpattu District, Tamil Nadu",
    "phone": "9944878713, 7200338060",
    "email": "rameshearthmovers19@gmail.com",
    "gstin": "33DDZPP6166H1ZS",
    "bank_account_name": "RAMESH EARTH MOVERS",
    "bank_account_number": "1293115000008305",
    "bank_ifsc": "KVBL0001293",
    "bank_account_type": "Current",
    "gpay": "9944878713",
    "jcb_rate": "900",
    "default_bata": "500",
    "min_hours_price": "4500",
    "min_hours_threshold": "3",
    "day_rate": "8000",
    "bill_number_prefix": "",
}


def get_all() -> dict[str, str]:
    """Every setting as text. Stored values override defaults; blanks use the default."""
    values = dict(DEFAULT_SETTINGS)
    for row in get_excel_service().list_rows("Settings"):
        key = row.get("key")
        value = row.get("value")
        if key in values and value not in (None, ""):
            values[key] = str(value)
    return values


def get_text(key: str) -> str:
    return get_all()[key]


def _text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value).strip()


def save(data: SettingsIn) -> dict[str, str]:
    values = {key: _text(value) for key, value in data.model_dump().items()}
    get_excel_service().upsert_settings(values)
    return get_all()
