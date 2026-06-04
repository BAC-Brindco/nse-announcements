import logging
from typing import Any

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def bulk_upsert(table: str, records: list[dict], conflict_columns: list[str]) -> int:
    if not records:
        return 0
    client = get_client()
    chunk_size = 500
    total = 0
    for i in range(0, len(records), chunk_size):
        chunk = records[i:i + chunk_size]
        resp = (
            client.table(table)
            .upsert(chunk, on_conflict=",".join(conflict_columns))
            .execute()
        )
        total += len(resp.data or [])
    return total


def fetch_for_date(table: str, date_col: str, date_val: str) -> list[dict]:
    client = get_client()
    page, page_size, out = 0, 1000, []
    while True:
        resp = (
            client.table(table).select("*")
            .eq(date_col, date_val)
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return out


def fetch_from_date(table: str, date_col: str, from_date: str) -> list[dict]:
    """Fetch all rows where date_col >= from_date."""
    client = get_client()
    page, page_size, out = 0, 1000, []
    while True:
        resp = (
            client.table(table).select("*")
            .gte(date_col, from_date)
            .order(date_col)
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        chunk = resp.data or []
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return out
