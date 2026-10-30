#!/usr/bin/env python3
"""
Google Sheets & Tasks integration for Habit Wallpaper.

Handles:
  - OAuth2 credentials (token.json / credentials.json)
  - Fetching habit data from Google Sheets
  - Writing habit completions to Google Sheets
  - Fetching completed Google Tasks counts per calendar day (Pacific) for the wallpaper lookback

Setup:
  1. pip install google-api-python-client google-auth-oauthlib
  2. Google Cloud project → enable Sheets + Tasks APIs
  3. Download credentials.json to this directory
  4. Set SPREADSHEET_ID below
"""

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from gen_wallpaper import HABIT_TZ, LOOKBACK_DAYS, habit_now

log = logging.getLogger(__name__)

# Google Sheets date serials (same epoch as Excel): days since 1899-12-30.
_SHEETS_DATE_EPOCH = date(1899, 12, 30)


def _normalize_sheet_date(val):
    """
    Map column A (date) to YYYY-MM-DD so we match habit_now() and the wallpaper.
    Sheets may return ISO text, locale-formatted text, or a serial number.
    """
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        n = int(val)
        # Serial range covers ~1930–2090; avoids treating e.g. year 2026 as a serial.
        if 20000 <= n <= 80000:
            return (_SHEETS_DATE_EPOCH + timedelta(days=n)).strftime("%Y-%m-%d")
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _log_mark_column_a_scan(rows, today: str, limit: int = 15):
    """Log raw vs normalized column A for debugging date matching."""
    data_rows = [(r, row) for r, row in enumerate(rows) if r > 0 and len(row) > 0]
    total = len(data_rows)
    lines = []
    for r, row in data_rows[:limit]:
        raw_a = row[0]
        norm = _normalize_sheet_date(raw_a)
        hit = " <-- today" if norm == today else ""
        lines.append(
            f"  row{r + 1}: raw={raw_a!r} type={type(raw_a).__name__} -> {norm!r}{hit}"
        )
    if total > limit:
        lines.append(f"  ... ({total - limit} more data rows not shown)")
    if not lines:
        lines.append("  (no data rows)")
    log.info("mark_habit: column A scan (today target=%s):\n%s", today, "\n".join(lines))


def _parse_habit_cell(raw):
    """Sheet cell → int for habit columns.

    The Values API often returns numbers as JSON numbers or as strings like ``"1"``;
    both are normalized here. Checkbox / TRUE-FALSE text is handled too.
    """
    if raw is True or raw is False:
        return int(raw)
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        t = raw.strip()
        if not t:
            return 0
        tl = t.lower()
        if tl in ("true", "yes", "y", "x", "✓", "done"):
            return 1
        if tl in ("false", "no", "n"):
            return 0
        try:
            return int(float(t))
        except ValueError:
            return 0
    return 0

# =============================================================================
# CONFIG — edit these
# =============================================================================

SPREADSHEET_ID = "1Z4H6j4ILFXE5b51KhilNpQ6S2pxNPRTXjzgyk9nTPHQ"
SHEET_NAME = "Habits"
# A = date; B..K = ten habit columns (must match len(HABITS))
READ_RANGE = f"{SHEET_NAME}!A:K"


# =============================================================================
# AUTH
# =============================================================================

def get_google_creds(): # TODO: determine how often tokens need to get refreshed... is there a way to do never?
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/tasks.readonly",
    ]
    token_path = Path(__file__).parent / "token.json"
    creds_path = Path(__file__).parent / "credentials.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


# =============================================================================
# SHEETS — READ
# =============================================================================

def fetch_sheet_data(creds, habits):
    from googleapiclient.discovery import build

    svc = build("sheets", "v4", credentials=creds)
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=READ_RANGE
    ).execute()
    rows = result.get("values", [])
    data = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        key = _normalize_sheet_date(row[0])
        if key is None:
            continue
        vals = []
        for i in range(1, len(habits) + 1):
            raw = row[i] if i < len(row) else None
            vals.append(_parse_habit_cell(raw))

        if key in data:
            # Same calendar day may appear twice (e.g. locale date + appended ISO row).
            data[key] = [max(a, b) for a, b in zip(data[key], vals)]
        else:
            data[key] = vals

    return data


# =============================================================================
# SHEETS — WRITE
# =============================================================================

def _resolve_habit(habits, habit_name):
    """
    Resolve habit_name against the habits list.
    Tries exact match first, then unique substring match.
    Returns (index, habit_dict) or raises ValueError.
    """
    query = habit_name.lower()

    # Exact match
    for i, h in enumerate(habits):
        if h["name"].lower() == query:
            return i, h

    # Substring / prefix match — must be unambiguous
    matches = [(i, h) for i, h in enumerate(habits) if query in h["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = [h["name"] for _, h in matches]
        raise ValueError(
            f"Ambiguous habit '{habit_name}' matches {names}. Be more specific."
        )

    available = [h["name"] for h in habits]
    raise ValueError(f"Unknown habit '{habit_name}'. Available: {available}")


def mark_habit(creds, habits, habit_name):
    """
    Mark a habit for today in Google Sheets.
    If today's row doesn't exist, creates it.
    For non-'count' habits: toggles 0↔1 (if already 1, sets back to 0).
    For 'count' type habits, increments by 1 each time.
    Supports partial/substring habit names (must be unambiguous).

    Args:
        creds: Google OAuth2 credentials
        habits: list of habit dicts
        habit_name: name of the habit to mark (case-insensitive, substring ok)

    Returns:
        dict with status info, including ``previous`` (parsed int before write) and
        ``raw_cell`` (value returned by Sheets for that cell, if row existed).

    Raises:
        ValueError: if habit_name not found or ambiguous
    """
    from googleapiclient.discovery import build

    habit_idx, habit = _resolve_habit(habits, habit_name)
    habit_type = habit["type"]
    resolved_name = habit["name"]
    log.info("mark_habit: resolved %r -> %r (idx=%s)", habit_name, resolved_name, habit_idx)

    svc = build("sheets", "v4", credentials=creds)
    today = habit_now().strftime("%Y-%m-%d")
    now_local = habit_now()

    log.info(
        "mark_habit: start habit=%r idx=%s type=%s today=%s now_local=%s tz=%s",
        habit_name,
        habit_idx,
        habit_type,
        today,
        now_local.isoformat(),
        HABIT_TZ,
    )
    log.info(
        "mark_habit: spreadsheet_id=%s sheet=%s read_range=%s",
        SPREADSHEET_ID,
        SHEET_NAME,
        READ_RANGE,
    )

    # Read current data to find today's row
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=READ_RANGE
    ).execute()
    rows = result.get("values", [])
    log.info("mark_habit: fetched %s total rows (incl. header)", len(rows))

    _log_mark_column_a_scan(rows, today)

    # Find today's row number (1-indexed, row 1 is header)
    row_num = None
    for r, row in enumerate(rows):
        if r == 0:
            continue  # skip header
        if len(row) > 0 and _normalize_sheet_date(row[0]) == today:
            row_num = r + 1  # sheets is 1-indexed
            break

    col_letter = chr(ord('B') + habit_idx)  # B=first habit, C=second, etc.

    if row_num:
        log.info(
            "mark_habit: matched today at sheet row %s column %s (%s)",
            row_num,
            col_letter,
            habit_name,
        )
        # Row exists — read current value
        cell = f"{SHEET_NAME}!{col_letter}{row_num}"
        current = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=cell
        ).execute().get("values", [[0]])

        try:
            raw = current[0][0]
        except IndexError:
            raw = None
        current_val = _parse_habit_cell(raw)

        if habit_type == "count":
            new_val = current_val + 1
        else:
            # Toggle: any "done" state (1, TRUE, 2 from bad data) → 0; else → 1
            new_val = 0 if current_val >= 1 else 1

        log.info(
            "mark_habit: cell=%s raw=%r parsed=%s -> new_val=%s (toggle=%s)",
            cell,
            raw,
            current_val,
            new_val,
            habit_type != "count",
        )

        upd = svc.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=cell,
            valueInputOption="RAW",
            body={"values": [[int(new_val)]]},
        ).execute()
        log.info("mark_habit: sheets update ok: %s", upd)

        return {
            "habit": resolved_name,
            "date": today,
            "value": new_val,
            "action": "updated",
            "previous": current_val,
            "raw_cell": raw,
        }
    else:
        log.warning(
            "mark_habit: no sheet row matched today=%s — appending new row (see column A scan above)",
            today,
        )
        # No row for today — create one
        new_row = [today] + [0] * len(habits)
        if habit_type == "count":
            new_row[habit_idx + 1] = 1
        else:
            new_row[habit_idx + 1] = 1

        log.info("mark_habit: append new_row habit col index=%s -> %s", habit_idx + 1, new_row)

        resp = svc.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=READ_RANGE,
            valueInputOption="RAW",
            body={"values": [new_row]}
        ).execute()
        log.info("mark_habit: sheets append ok: %s", resp)

        return {
            "habit": resolved_name,
            "date": today,
            "value": 1,
            "action": "created_row",
            "previous": None,
            "raw_cell": None,
        }


# =============================================================================
# TASKS
# =============================================================================

def _completed_rfc_to_pacific_date(completed_rfc: str):
    """Parse Tasks API `completed` timestamp → date in Pacific."""
    s = completed_rfc.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(HABIT_TZ).date()


def _list_completed_tasks_since(svc, tasklist_id: str, completed_min_rfc: str):
    """Paginate tasks.list — a single page maxes at 100 tasks."""
    out = []
    page_token = None
    while True:
        kwargs = dict(
            tasklist=tasklist_id,
            showCompleted=True,
            showHidden=True,
            completedMin=completed_min_rfc,
            maxResults=100,
        )
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.tasks().list(**kwargs).execute()
        out.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def fetch_google_task_counts_by_pacific_date(creds, lookback_days: int):
    """
    Count tasks completed on each Pacific calendar day from (today - lookback_days + 1) through today.
    Uses each task's `completed` RFC3339 time converted to America/Los_Angeles.
    """
    from googleapiclient.discovery import build

    svc = build("tasks", "v1", credentials=creds)
    lists = svc.tasklists().list().execute().get("items", []) or []

    today = habit_now().date()
    oldest = today - timedelta(days=lookback_days - 1)
    start_local = datetime.combine(oldest, time.min, tzinfo=HABIT_TZ)
    completed_min_rfc = start_local.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )

    counts: dict[str, int] = defaultdict(int)
    for tl in lists:
        for t in _list_completed_tasks_since(svc, tl["id"], completed_min_rfc):
            if t.get("status") != "completed":
                continue
            cr = t.get("completed")
            if not cr:
                continue
            try:
                d = _completed_rfc_to_pacific_date(cr)
            except (ValueError, TypeError):
                continue
            if d < oldest or d > today:
                continue
            counts[d.strftime("%Y-%m-%d")] += 1

    return dict(counts)


# =============================================================================
# CONVENIENCE
# =============================================================================

def fetch_all(habits):
    creds = get_google_creds()
    data = fetch_sheet_data(creds, habits)
    tasks_by_date = fetch_google_task_counts_by_pacific_date(creds, LOOKBACK_DAYS)
    today_str = habit_now().strftime("%Y-%m-%d")
    today = habit_now().date()

    for i in range(LOOKBACK_DAYS):
        d = today - timedelta(days=LOOKBACK_DAYS - 1 - i)
        key = d.strftime("%Y-%m-%d")
        n = tasks_by_date.get(key, 0)
        if key not in data:
            if key == today_str:
                data[key] = [-1] * (len(habits) - 1) + [n]
            else:
                data[key] = [None] * (len(habits) - 1) + [n]
        else:
            data[key][-1] = n
    return data