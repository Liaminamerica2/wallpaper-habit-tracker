#!/usr/bin/env python3
"""
Google Sheets & Tasks integration for Habit Wallpaper.

Handles:
  - OAuth2 credentials (token.json / credentials.json)
  - Fetching habit data from Google Sheets
  - Writing habit completions to Google Sheets
  - Fetching completed Google Tasks count for today

Setup:
  1. pip install google-api-python-client google-auth-oauthlib
  2. Google Cloud project → enable Sheets + Tasks APIs
  3. Download credentials.json to this directory
  4. Set SPREADSHEET_ID below
"""

from datetime import datetime
from pathlib import Path

# =============================================================================
# CONFIG — edit these
# =============================================================================

SPREADSHEET_ID = "1Z4H6j4ILFXE5b51KhilNpQ6S2pxNPRTXjzgyk9nTPHQ"
SHEET_NAME = "Habits"
READ_RANGE = f"{SHEET_NAME}!A:F"


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
        vals = []
        for i in range(1, len(habits) + 1):
            try:
                vals.append(int(row[i]) if i < len(row) else 0)
            except (ValueError, TypeError):
                vals.append(0)
        data[row[0]] = vals
    return data


# =============================================================================
# SHEETS — WRITE
# =============================================================================

def mark_habit(creds, habits, habit_name):
    """
    Mark a habit as done (set to 1) for today in Google Sheets.
    If today's row doesn't exist, creates it.
    For 'count' type habits, increments by 1 instead of setting to 1.

    Args:
        creds: Google OAuth2 credentials
        habits: list of habit dicts
        habit_name: name of the habit to mark (case-insensitive)

    Returns:
        dict with status info

    Raises:
        ValueError: if habit_name not found
    """
    from googleapiclient.discovery import build

    # Find habit index
    habit_idx = None
    habit_type = None
    for i, h in enumerate(habits):
        if h["name"].lower() == habit_name.lower():
            habit_idx = i
            habit_type = h["type"]
            break

    if habit_idx is None:
        available = [h["name"] for h in habits]
        raise ValueError(f"Unknown habit '{habit_name}'. Available: {available}")

    svc = build("sheets", "v4", credentials=creds)
    today = datetime.now().strftime("%Y-%m-%d")

    # Read current data to find today's row
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=READ_RANGE
    ).execute()
    rows = result.get("values", [])

    # Find today's row number (1-indexed, row 1 is header)
    row_num = None
    for r, row in enumerate(rows):
        if r == 0:
            continue  # skip header
        if len(row) > 0 and row[0] == today:
            row_num = r + 1  # sheets is 1-indexed
            break

    col_letter = chr(ord('B') + habit_idx)  # B=first habit, C=second, etc.

    if row_num:
        # Row exists — read current value
        cell = f"{SHEET_NAME}!{col_letter}{row_num}"
        current = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=cell
        ).execute().get("values", [[0]])

        try:
            current_val = int(current[0][0])
        except (IndexError, ValueError, TypeError):
            current_val = 0

        if habit_type == "count":
            new_val = current_val + 1
        else:
            new_val = 1

        svc.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=cell,
            valueInputOption="RAW",
            body={"values": [[new_val]]}
        ).execute()

        return {"habit": habit_name, "date": today, "value": new_val, "action": "updated"}
    else:
        # No row for today — create one
        new_row = [today] + [0] * len(habits)
        if habit_type == "count":
            new_row[habit_idx + 1] = 1
        else:
            new_row[habit_idx + 1] = 1

        svc.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=READ_RANGE,
            valueInputOption="RAW",
            body={"values": [new_row]}
        ).execute()

        return {"habit": habit_name, "date": today, "value": 1, "action": "created_row"}


# =============================================================================
# TASKS
# =============================================================================

def fetch_google_tasks_today(creds): # TODO: check that this gives you tasks completed today
    from googleapiclient.discovery import build

    svc = build("tasks", "v1", credentials=creds)
    lists = svc.tasklists().list().execute().get("items", [])
    today_rfc = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    count = 0
    for tl in lists:
        tasks = svc.tasks().list(
            tasklist=tl["id"], showCompleted=True,
            showHidden=True, completedMin=today_rfc,
        ).execute().get("items", [])
        count += sum(1 for t in tasks if t.get("status") == "completed")
    return count


# =============================================================================
# CONVENIENCE
# =============================================================================

def fetch_all(habits):
    creds = get_google_creds()
    data = fetch_sheet_data(creds, habits)
    tasks = fetch_google_tasks_today(creds)
    key = datetime.now().strftime("%Y-%m-%d")
    if key in data:
        data[key][-1] = tasks
    else:
        data[key] = [-1] * (len(habits) - 1) + [tasks]
    return data