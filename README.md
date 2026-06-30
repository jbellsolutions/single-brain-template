# single-brain-template

Stamp out a new **Single-Brain agent** — a self-hosted [Hermes](https://github.com/NousResearch/hermes-agent) gateway with Slack + Telegram channels, a resilient LLM fallback chain, git-backed Obsidian memory, and an optional Notion mirror — from **one config file**.

This is the parameterized, secret-free distillation of the `single-brain` production agent. Every per-agent value lives in `agent.env`; the architecture (and its hard-won fixes) lives in the template.

## What you get

```
            Slack / Telegram ──▶ Hermes gateway (Docker) ──▶ Fireworks (primary)
                                      │                       └▶ Together (fallback)
                                      ├─ /opt/data  session DB, kanban, memories, auth
                                      └─ sync daemon ──▶ vault/ (Obsidian markdown, git)
                                                        └▶ Notion (5 mirrored DBs)   [opt-in]
```

- **Resilient inference** — Fireworks DeepSeek-V4-Flash primary, automatic Together fallback on rate-limit/5xx/auth/connection errors. No single provider can take the agent down.
- **Obsidian-native memory** — the sync daemon mirrors everything the agent does to a git-backed vault you can open in Obsidian, and to Notion.
- **Survives restarts** — `/opt/data` is bind-mounted, so `docker compose up --force-recreate` never wipes history.
- **Self-healing** — a watchdog restarts the container if it dies.

## Spin up a new agent (5 steps)

```bash
# 1. On a fresh Ubuntu 22.04+ VPS (installs Docker + clones this repo):
curl -fsSL https://raw.githubusercontent.com/jbellsolutions/single-brain-template/main/provision-vps.sh | bash
cd ~/single-brain-template

# 2. Describe the agent:
cp agent.example.env agent.env
nano agent.env            # AGENT_NAME, BASE_DIR, FIREWORKS_API_KEY, channel tokens, persona

# 3. Stamp it out:
./new-agent.sh agent.env

# 4. Launch:
cd "$BASE_DIR"            # the BASE_DIR you set
docker compose up -d                    # core agent
docker compose --profile sync up -d     # + Obsidian/Notion mirror (needs NOTION_* set)

# 5. Watchdog + verify:
(crontab -l 2>/dev/null; echo "* * * * * $BASE_DIR/bin/watchdog.sh") | crontab -
docker exec "$AGENT_NAME" /opt/hermes/.venv/bin/hermes fallback list   # primary should be Fireworks
```

That's it. Repeat steps 2–5 with a different `AGENT_NAME` / `BASE_DIR` / `HERMES_PORT` to run more agents — on the same VPS or a new one.

## Files

| Path | Role |
|---|---|
| `agent.example.env` | The one file you fill out per agent. Copy → `agent.env`. |
| `new-agent.sh` | Generator. Reads `agent.env`, lays down the stack, renders config + watchdog. |
| `provision-vps.sh` | Fresh-VPS bootstrap (Docker + clone). |
| `compose.yml` | Parameterized stack (`${ENV}` from `.env`). Hermes core + opt-in `sync` profile. |
| `hermes/config.template.yaml` | Hermes config seed (Fireworks primary, Together fallback, channels, persona). |
| `bin/init-chown.sh` | Root pre-entrypoint — fixes upstream EACCES/ownership bugs. **Load-bearing.** |
| `bin/start-hermes.sh` | Launches the dashboard + gateway. |
| `bin/watchdog.sh` | Cron self-healer (rendered per agent). |
| `sync/` | Obsidian/Notion mirror daemon (stdlib-only Python) + Notion DB creator. |

## Notion mirror (optional)

```bash
# create the 5 databases once under a page your Notion integration can edit:
NOTION_TOKEN=secret_... NOTION_PARENT_PAGE=<page-id> python3 sync/create_notion_dbs.py
# paste the printed NOTION_DB_* ids into agent.env, then:  docker compose --profile sync up -d
```

## Load-bearing fixes (why a naive `docker run hermes` fails)

These are baked into `compose.yml` / `bin/` — don't strip them:

- **`HOME=/opt/data`** — gosu keeps the parent HOME on privilege drop; otherwise the TUI can't read `/root` and the Chat tab is blank.
- **`/opt/data` bind mount** — the image declares `VOLUME /opt/data`; without a bind mount every recreate orphans an anonymous volume and wipes history.
- **`init-chown.sh`** — chowns `/opt/hermes/ui-tui` (ships root:root → EACCES) and `/opt/data` before the privilege drop.
- **`HERMES_TUI_DIR=/opt/hermes/ui-tui`** — bypasses a staleness check that otherwise triggers a synchronous npm rebuild on every Chat connect.
- **Custom primary is config-driven** — there is no `HERMES_INFERENCE_BASE_URL`; a custom OpenAI-compatible primary must be set in `config.yaml`'s `model:` block, and `HERMES_INFERENCE_*` env vars must not override it.

## Security

- `agent.env` / `.env` are gitignored. Never commit a filled-in copy.
- The template ships **zero** secrets — every key is `${ENV}` (compose) or `key_env` (Hermes).
- Hermes stores `mcp_servers[].env` values as **cleartext** in `config.yaml` — add tool keys via the dashboard or keep that file off git.
