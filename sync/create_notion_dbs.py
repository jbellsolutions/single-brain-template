#!/usr/bin/env python3
"""Create the Single Brain Notion databases.

Idempotent-safe: re-running prints existing DB IDs if titles already match
under the parent page (we just create new ones — Notion lets duplicates
exist; this script doesn't dedupe by title).

Usage:
    NOTION_TOKEN=... NOTION_PARENT_PAGE=... python3 create_notion_dbs.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


DBS = [
    {
        "icon": "💬",
        "title": "Sessions",
        "description": (
            "Every Hermes agent session — chat, slack, telegram, cron. "
            "Page body holds the full transcript."
        ),
        "properties": {
            "Title": {"title": {}},
            "Session ID": {"rich_text": {}},
            "Source": {
                "select": {
                    "options": [
                        {"name": "dashboard", "color": "blue"},
                        {"name": "slack", "color": "green"},
                        {"name": "telegram", "color": "purple"},
                        {"name": "cron", "color": "orange"},
                        {"name": "api", "color": "gray"},
                        {"name": "tui", "color": "pink"},
                        {"name": "other", "color": "default"},
                    ]
                }
            },
            "Started At": {"date": {}},
            "Ended At": {"date": {}},
            "Message Count": {"number": {"format": "number"}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "active", "color": "yellow"},
                        {"name": "ended", "color": "green"},
                        {"name": "error", "color": "red"},
                    ]
                }
            },
            "Model": {"rich_text": {}},
            "Vault Path": {"rich_text": {}},
        },
    },
    {
        "icon": "📋",
        "title": "Kanban Tasks",
        "description": "Mirror of Hermes kanban (kanban.db).",
        "properties": {
            "Title": {"title": {}},
            "Task ID": {"rich_text": {}},
            "Status": {
                "select": {
                    "options": [
                        {"name": "triage", "color": "gray"},
                        {"name": "todo", "color": "blue"},
                        {"name": "ready", "color": "yellow"},
                        {"name": "in_progress", "color": "orange"},
                        {"name": "blocked", "color": "red"},
                        {"name": "done", "color": "green"},
                    ]
                }
            },
            "Assignee": {"rich_text": {}},
            "Tenant": {"rich_text": {}},
            "Created At": {"date": {}},
            "Updated At": {"date": {}},
            "Vault Path": {"rich_text": {}},
        },
    },
    {
        "icon": "🧠",
        "title": "Memories",
        "description": (
            "Persistent agent memories. Mirror of /opt/data/memories/ — "
            "MEMORY.md, USER.md, etc."
        ),
        "properties": {
            "Title": {"title": {}},
            "File": {"rich_text": {}},
            "Type": {
                "select": {
                    "options": [
                        {"name": "persistent", "color": "purple"},
                        {"name": "session", "color": "blue"},
                        {"name": "user", "color": "green"},
                    ]
                }
            },
            "Updated At": {"date": {}},
            "Vault Path": {"rich_text": {}},
        },
    },
    {
        "icon": "🛠",
        "title": "Skills",
        "description": (
            "Skills inventory. Mirror of /opt/data/skills/ "
            "and /root/.hermes/skills/."
        ),
        "properties": {
            "Name": {"title": {}},
            "Description": {"rich_text": {}},
            "Source": {
                "select": {
                    "options": [
                        {"name": "bundled", "color": "blue"},
                        {"name": "user", "color": "green"},
                        {"name": "plugin", "color": "purple"},
                    ]
                }
            },
            "Status": {
                "select": {
                    "options": [
                        {"name": "active", "color": "green"},
                        {"name": "deprecated", "color": "gray"},
                    ]
                }
            },
            "Last Used": {"date": {}},
            "Vault Path": {"rich_text": {}},
        },
    },
    {
        "icon": "📡",
        "title": "Events",
        "description": (
            "Catch-all event log: config changes, hook fires, decisions, "
            "errors, anything that does not fit the other DBs."
        ),
        "properties": {
            "Title": {"title": {}},
            "Type": {
                "select": {
                    "options": [
                        {"name": "decision", "color": "yellow"},
                        {"name": "sop", "color": "blue"},
                        {"name": "config_change", "color": "purple"},
                        {"name": "error", "color": "red"},
                        {"name": "hook", "color": "orange"},
                        {"name": "info", "color": "gray"},
                    ]
                }
            },
            "Timestamp": {"date": {}},
            "Source": {"rich_text": {}},
            "Payload": {"rich_text": {}},
        },
    },
]


def main() -> int:
    token = os.environ.get("NOTION_TOKEN")
    parent = os.environ.get("NOTION_PARENT_PAGE")
    if not token or not parent:
        print("NOTION_TOKEN and NOTION_PARENT_PAGE must be set", file=sys.stderr)
        return 1

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": VERSION,
    }

    results = {}
    for spec in DBS:
        body = {
            "parent": {"type": "page_id", "page_id": parent},
            "icon": {"type": "emoji", "emoji": spec["icon"]},
            "title": [{"type": "text", "text": {"content": spec["title"]}}],
            "description": [
                {"type": "text", "text": {"content": spec["description"]}}
            ],
            "properties": spec["properties"],
            "is_inline": False,
        }
        req = urllib.request.Request(
            f"{API}/databases",
            method="POST",
            headers=headers,
            data=json.dumps(body).encode(),
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                results[spec["title"]] = data["id"]
                title = spec["title"].ljust(14)
                print(f"OK  {spec['icon']} {title} {data['id']}")
        except urllib.error.HTTPError as e:
            err_body = e.read()[:400]
            print(f"FAIL  {spec['title']}: {e.code} {err_body!r}")

    print()
    print("# Append to .env")
    for name, dbid in results.items():
        key = "NOTION_DB_" + name.upper().replace(" ", "_")
        print(f"{key}={dbid}")
    return 0 if len(results) == len(DBS) else 2


if __name__ == "__main__":
    sys.exit(main())
