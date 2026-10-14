"""Settings from environment variables or an optional config.json next to run.py.

Nothing is required: without an API key the copilot runs in offline (rules) mode,
and without Jira/Slack settings it writes tickets and notifications to local files.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "recommerce.db"

_file = {}
if (ROOT / "config.json").exists():
    _file = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))  # tolerate Notepad BOM


def get(key, default=None):
    return os.environ.get(key) or _file.get(key) or default


ANTHROPIC_API_KEY = get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = get("CLAUDE_MODEL", "claude-sonnet-4-5")
# USD per million tokens, used only for the cost-per-case counter. Set to your model's list price.
PRICE_IN_PER_MTOK = float(get("PRICE_IN_PER_MTOK", 3.0))
PRICE_OUT_PER_MTOK = float(get("PRICE_OUT_PER_MTOK", 15.0))

JIRA_BASE_URL = get("JIRA_BASE_URL")          # e.g. https://your-site.atlassian.net
JIRA_EMAIL = get("JIRA_EMAIL")
JIRA_API_TOKEN = get("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = get("JIRA_PROJECT_KEY")    # e.g. DISP
JIRA_ISSUE_TYPE = get("JIRA_ISSUE_TYPE", "Task")

SLACK_WEBHOOK_URL = get("SLACK_WEBHOOK_URL")
APP_URL = get("APP_URL", "http://localhost:8501")
PORT = int(get("PORT", 8501))


def llm_enabled():
    return bool(ANTHROPIC_API_KEY)


def jira_missing():
    names = {"JIRA_BASE_URL": JIRA_BASE_URL, "JIRA_EMAIL": JIRA_EMAIL,
             "JIRA_API_TOKEN": JIRA_API_TOKEN, "JIRA_PROJECT_KEY": JIRA_PROJECT_KEY}
    return [k for k, v in names.items() if not v]


def jira_enabled():
    return not jira_missing()


def slack_enabled():
    return bool(SLACK_WEBHOOK_URL)
