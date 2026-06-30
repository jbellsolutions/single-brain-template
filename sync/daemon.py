#!/usr/bin/env python3
"""Single Brain sync daemon.

Long-running service. Polls Hermes's state.db, kanban.db, memories/, skills/
on a regular cadence; emits markdown to /vault and creates / updates rows
in the Notion databases for anything that has changed.

Decoupled from Hermes — runs in its own container with read-only access
to /opt/data so a Notion outage or sync bug can't slow the agent.

Checkpoint state lives at /vault/.sync-checkpoint.json so progress survives
container restarts.

Env:
    NOTION_TOKEN
    NOTION_DB_SESSIONS
    NOTION_DB_KANBAN_TASKS
    NOTION_DB_MEMORIES
    NOTION_DB_SKILLS
    NOTION_DB_EVENTS
    SYNC_INTERVAL_SECONDS  (default 60)
    SYNC_DATA_DIR          (default /opt/data)
    SYNC_VAULT_DIR         (default /vault)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
RATE = float(os.environ.get("SYNC_RATE_SECONDS", "0.4"))

DATA = Path(os.environ.get("SYNC_DATA_DIR", "/opt/data"))
VAULT = Path(os.environ.get("SYNC_VAULT_DIR", "/vault"))
INTERVAL = int(os.environ.get("SYNC_INTERVAL_SECONDS", "60"))

CHECKPOINT_PATH = VAULT / ".sync-checkpoint.json"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=os.environ.get("SYNC_LOG_LEVEL", "INFO"),
)
log = logging.getLogger("single-brain-sync")


# ---------- Helpers ---------------------------------------------------------


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def truncate(s: str, n: int = 1900) -> str:
    return s if len(s) <= n else s[: n - 30] + "\n\n…[truncated for Notion]"


def load_checkpoint() -> dict:
    try:
        return json.loads(CHECKPOINT_PATH.read_text())
    except Exception:
        return {
            "sessions_max_started_at": 0.0,
            "messages_max_id": 0,
            "memories_mtime": {},
            "skills_mtime": {},
            "kanban_max_updated_at": 0.0,
        }


def save_checkpoint(cp: dict) -> None:
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, indent=2))
    tmp.rename(CHECKPOINT_PATH)


@dataclass
class Notion:
    token: str
    sessions_db: str
    kanban_db: str
    memories_db: str
    skills_db: str
    events_db: str

    @classmethod
    def from_env(cls) -> "Notion":
        return cls(
            token=os.environ["NOTION_TOKEN"],
            sessions_db=os.environ["NOTION_DB_SESSIONS"],
            kanban_db=os.environ["NOTION_DB_KANBAN_TASKS"],
            memories_db=os.environ["NOTION_DB_MEMORIES"],
            skills_db=os.environ["NOTION_DB_SKILLS"],
            events_db=os.environ["NOTION_DB_EVENTS"],
        )

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{NOTION_API}{path}", method=method, headers=headers, data=data
        )
        for _ in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After", "5"))
                    log.warning("notion: rate-limited, sleeping %ss", wait)
                    time.sleep(wait)
                    continue
                if 500 <= e.code < 600:
                    log.warning("notion: %s on %s; retrying in 2s", e.code, path)
                    time.sleep(2)
                    continue
                err_body = e.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(f"{method} {path} -> {e.code} {err_body}")
            except (urllib.error.URLError, TimeoutError) as e:
                log.warning("notion: %s on %s; retrying in 2s", e, path)
                time.sleep(2)
        raise RuntimeError(f"notion: {method} {path} failed after retries")

    def find(self, db_id: str, prop: str, value: str) -> str | None:
        body = {
            "filter": {"property": prop, "rich_text": {"equals": value}},
            "page_size": 1,
        }
        res = self._request("POST", f"/databases/{db_id}/query", body=body)
        rows = res.get("results", [])
        return rows[0]["id"] if rows else None

    def create(self, body: dict) -> str:
        return self._request("POST", "/pages", body=body)["id"]

    def update_props(self, page_id: str, properties: dict) -> None:
        self._request("PATCH", f"/pages/{page_id}", body={"properties": properties})


# ---------- Markdown writers -----------------------------------------------


def write_session_md(session: dict, messages: list[dict]) -> Path:
    started = session.get("started_at") or 0
    date_dir = VAULT / "sessions" / datetime.fromtimestamp(started, tz=timezone.utc).strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_id = session["id"].replace("/", "_").replace(" ", "_")
    path = date_dir / f"{safe_id}.md"

    title = (session.get("title") or "").strip() or session["id"]
    front = [
        "---",
        f"session_id: {session['id']}",
        f"source: {session.get('source') or ''}",
        f"started_at: {iso(session.get('started_at'))}",
        f"ended_at: {iso(session.get('ended_at'))}",
        f"model: {session.get('model') or ''}",
        f"message_count: {session.get('message_count') or 0}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    body = []
    for m in messages:
        role = m.get("role") or "?"
        ts = iso(m.get("timestamp"))
        body.append(f"## {role}  · {ts}")
        body.append("")
        body.append(m.get("content") or "")
        body.append("")
    path.write_text("\n".join(front) + "\n".join(body), encoding="utf-8")
    return path


def write_memory_md(name: str, content: str) -> Path:
    target = VAULT / "memories"
    target.mkdir(parents=True, exist_ok=True)
    p = target / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------- Sync passes -----------------------------------------------------


def sync_sessions(notion: Notion, cp: dict) -> int:
    """Sync sessions newer than the checkpoint.

    Strategy: find sessions whose started_at > checkpoint, OR sessions that
    have new messages since checkpoint. For each, re-emit markdown and
    upsert Notion row (create if Session ID not found, otherwise update
    status/ended_at/message_count properties).
    """
    db = DATA / "state.db"
    if not db.exists():
        return 0

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    last_started = cp.get("sessions_max_started_at", 0.0)
    last_msg_id = cp.get("messages_max_id", 0)

    # Sessions touched since last run: started after, OR have a message > last_msg_id
    rows = list(
        conn.execute(
            """
            SELECT DISTINCT s.* FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.started_at > ? OR m.id > ?
            ORDER BY s.started_at ASC, s.id ASC
            """,
            (last_started, last_msg_id),
        )
    )
    if not rows:
        return 0

    log.info("sessions: %d to sync", len(rows))
    new_max_started = last_started
    new_max_msg = last_msg_id
    synced = 0

    for r in rows:
        s = dict(r)
        msgs = [
            dict(m)
            for m in conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
                (s["id"],),
            )
        ]
        try:
            path = write_session_md(s, msgs)
        except Exception as e:
            log.error("sessions: md write failed for %s: %s", s["id"], e)
            continue

        if msgs:
            new_max_msg = max(new_max_msg, msgs[-1]["id"])
        if s.get("started_at"):
            new_max_started = max(new_max_started, s["started_at"])

        try:
            existing = notion.find(notion.sessions_db, "Session ID", s["id"])
            time.sleep(RATE)

            title = (s.get("title") or "").strip() or s["id"]
            source = (s.get("source") or "other").lower()
            if source not in {"dashboard", "slack", "telegram", "cron", "api", "tui"}:
                source = "other"
            status = "ended" if s.get("ended_at") else "active"

            props: dict[str, Any] = {
                "Title": {"title": [{"text": {"content": title[:1900]}}]},
                "Session ID": {"rich_text": [{"text": {"content": s["id"][:1900]}}]},
                "Source": {"select": {"name": source}},
                "Status": {"select": {"name": status}},
                "Message Count": {"number": int(s.get("message_count") or 0)},
                "Model": {"rich_text": [{"text": {"content": (s.get("model") or "")[:1900]}}]},
                "Vault Path": {"rich_text": [{"text": {"content": str(path)[:1900]}}]},
            }
            if s.get("started_at"):
                props["Started At"] = {"date": {"start": iso(s["started_at"])}}
            if s.get("ended_at"):
                props["Ended At"] = {"date": {"start": iso(s["ended_at"])}}

            if existing:
                notion.update_props(existing, props)
            else:
                # Build short transcript preview (first 20 messages, truncated)
                children: list[dict] = [
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": f"Vault: {path}\nFull transcript on disk."
                                    },
                                }
                            ],
                            "icon": {"type": "emoji", "emoji": "🔗"},
                            "color": "gray_background",
                        },
                    }
                ]
                for m in msgs[:20]:
                    role = (m.get("role") or "?").upper()
                    content = (m.get("content") or "").strip()
                    if not content:
                        continue
                    children.append(
                        {
                            "object": "block",
                            "type": "heading_3",
                            "heading_3": {
                                "rich_text": [{"type": "text", "text": {"content": role[:90]}}]
                            },
                        }
                    )
                    children.append(
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [
                                    {"type": "text", "text": {"content": truncate(content)}}
                                ]
                            },
                        }
                    )
                body = {
                    "parent": {"database_id": notion.sessions_db},
                    "icon": {"type": "emoji", "emoji": "💬"},
                    "properties": props,
                    "children": children[:100],
                }
                notion.create(body)
            time.sleep(RATE)
            synced += 1
        except Exception as e:
            log.error("sessions: notion sync failed for %s: %s", s["id"], e)

    cp["sessions_max_started_at"] = new_max_started
    cp["messages_max_id"] = new_max_msg
    return synced


def sync_memories(notion: Notion, cp: dict) -> int:
    src = DATA / "memories"
    if not src.exists():
        return 0
    state = cp.setdefault("memories_mtime", {})
    synced = 0
    for p in sorted(src.iterdir()):
        if p.is_dir() or p.name.endswith(".lock"):
            continue
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue
        if state.get(p.name) == mtime:
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            out = write_memory_md(p.name, content)
        except Exception as e:
            log.error("memories: md write failed for %s: %s", p.name, e)
            continue

        try:
            existing = notion.find(notion.memories_db, "File", p.name)
            time.sleep(RATE)

            title = p.stem
            mem_type = (
                "user" if "user" in title.lower()
                else "session" if "session" in title.lower()
                else "persistent"
            )
            props = {
                "Title": {"title": [{"text": {"content": title[:1900]}}]},
                "File": {"rich_text": [{"text": {"content": p.name[:1900]}}]},
                "Type": {"select": {"name": mem_type}},
                "Updated At": {"date": {"start": iso(mtime)}},
                "Vault Path": {"rich_text": [{"text": {"content": str(out)[:1900]}}]},
            }
            if existing:
                notion.update_props(existing, props)
            else:
                children = []
                chunk = content[: 1900 * 50]
                for i in range(0, len(chunk), 1900):
                    piece = chunk[i : i + 1900]
                    children.append(
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{"type": "text", "text": {"content": piece}}]
                            },
                        }
                    )
                body = {
                    "parent": {"database_id": notion.memories_db},
                    "icon": {"type": "emoji", "emoji": "🧠"},
                    "properties": props,
                    "children": children[:100],
                }
                notion.create(body)
            time.sleep(RATE)
            state[p.name] = mtime
            synced += 1
        except Exception as e:
            log.error("memories: notion sync failed for %s: %s", p.name, e)
    return synced


def sync_skills(notion: Notion, cp: dict) -> int:
    src = DATA / "skills"
    if not src.exists():
        return 0
    state = cp.setdefault("skills_mtime", {})
    synced = 0
    for d in sorted(src.iterdir()):
        if not d.is_dir():
            continue
        # Track latest mtime among files within
        try:
            mtime = max((f.stat().st_mtime for f in d.rglob("*") if f.is_file()), default=0)
        except FileNotFoundError:
            continue
        if state.get(d.name) == mtime:
            continue

        desc = ""
        for fname in ("SKILL.md", "skill.md", "README.md", "readme.md"):
            f = d / fname
            if f.exists():
                desc = f.read_text(encoding="utf-8", errors="replace")
                break

        out = VAULT / "skills" / f"{d.name}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            f"---\nskill: {d.name}\nsource: bundled\n---\n\n# {d.name}\n\n{desc}\n",
            encoding="utf-8",
        )

        try:
            existing = notion.find(notion.skills_db, "Vault Path", str(out))
            time.sleep(RATE)
            props = {
                "Name": {"title": [{"text": {"content": d.name[:1900]}}]},
                "Description": {"rich_text": [{"text": {"content": truncate(desc) or d.name}}]},
                "Source": {"select": {"name": "bundled"}},
                "Status": {"select": {"name": "active"}},
                "Last Used": {"date": {"start": iso(mtime)}},
                "Vault Path": {"rich_text": [{"text": {"content": str(out)[:1900]}}]},
            }
            if existing:
                notion.update_props(existing, props)
            else:
                body = {
                    "parent": {"database_id": notion.skills_db},
                    "icon": {"type": "emoji", "emoji": "🛠"},
                    "properties": props,
                    "children": [
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [{"type": "text", "text": {"content": truncate(desc) or d.name}}]
                            },
                        }
                    ],
                }
                notion.create(body)
            time.sleep(RATE)
            state[d.name] = mtime
            synced += 1
        except Exception as e:
            log.error("skills: notion sync failed for %s: %s", d.name, e)
    return synced


# ---------- Main loop -------------------------------------------------------


def loop_once(notion: Notion, cp: dict) -> dict:
    counts = {
        "sessions": sync_sessions(notion, cp),
        "memories": sync_memories(notion, cp),
        "skills": sync_skills(notion, cp),
    }
    save_checkpoint(cp)
    return counts


def main() -> int:
    notion = Notion.from_env()
    log.info(
        "starting; data=%s vault=%s interval=%ss",
        DATA, VAULT, INTERVAL,
    )

    while True:
        cp = load_checkpoint()
        try:
            counts = loop_once(notion, cp)
            if any(counts.values()):
                log.info("synced: %s", counts)
            else:
                log.debug("idle")
        except Exception as e:
            log.exception("sync pass failed: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main() or 0)
