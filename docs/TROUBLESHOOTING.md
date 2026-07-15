# Troubleshooting & hard-won lessons

Runtime symptoms an operator actually hits, and the fix. The load-bearing *config*
fixes are already baked into `compose.yml` (read its comments); this covers
*operational* gotchas that bite after the stack is up. Ordered by how often they come up.

## The one meta-lesson: replicate the LIVE box, not the repo

The biggest time sink is copying an *architecture* from a GitHub repo that no longer
matches what's deployed. Before templating any working agent, inspect what actually
**runs** (`docker ps`, `docker inspect`, read the live `config.yaml`), not what's
committed. A repo can lag its own production box by an entire architecture. This
template tracks the **live** single-brain (Hermes-native gateway), which is why it
works out of the box.

## Symptom → cause → fix

| Symptom | Cause | Fix |
|---|---|---|
| Gateway `healthy`, but Slack messages never get a reply and don't appear in `hermes/data/logs/gateway.log` | Config isn't where you think. The gateway reads its FULL config from **`/opt/data/config.yaml`** (the `hermes/data` bind mount). A `config.yaml` under `/root/.hermes` is legacy and does nothing. | Confirm `hermes/data/config.yaml` exists and has your `model:` + `platforms:` blocks. The `gateway.log` `inbound message:` lines are the ground truth that events are arriving. |
| Container is `Up` but the **Chat tab renders blank** | TUI inherited `HOME=/root` on the gosu privilege drop and can't read it. | Already fixed in compose (`HOME: /opt/data`). If you edited compose, restore it. |
| Every Chat-tab connect hangs for minutes / websocket handshake times out | Upstream staleness check triggers a synchronous `npm` rebuild of the TUI bundle on each connect. | Already fixed (`HERMES_TUI_DIR: /opt/hermes/ui-tui`). Don't remove it. |
| All conversation history **disappears after `docker compose up --force-recreate`** | `/opt/data` is a declared VOLUME; without the bind mount Docker orphans an anonymous volume each recreate. | Already fixed (bind mount `${BASE_DIR}/hermes/data:/opt/data`). Never remove it. Never `docker compose down -v`. |
| Gateway **crash-loops on boot** | A channel is `enabled: true` in config but its token is missing/blank; the gateway exits if no enabled channel connects. | Only enable channels you have tokens for. `new-agent.sh` does this automatically from `agent.env`; if you hand-edited config, match `platforms.*.enabled` to the tokens present. |
| Sub-agent spawns all `401` | `delegation.model` defaulted to a provider you have no key for. | The seed pins delegation to the Fireworks endpoint — keep it, or point it at a provider whose key is in `.env`. |
| A **channel @mention** gets no reply (but DMs work) | The bot isn't a **member** of that channel. Slack only delivers channel `app_mention` events to member bots — this is normal Slack, not a bug. | Invite the bot: `/invite @your-agent`, or have it self-join public channels (needs `channels:join` scope). DMs never require membership. |
| Container is **`running` but stuck `unhealthy`** and never recovers | Docker's `restart:` policy only restarts on **exit**, not on a failing healthcheck. A wedged-but-alive gateway sits broken indefinitely. | The hardened `bin/watchdog.sh` restarts it after 3 consecutive unhealthy checks (~3 min). Confirm the watchdog cron is installed (`crontab -l`). |
| Rapid restarts / watchdog "fighting" a slow boot | Restarting during the normal cold-start window. | The watchdog only counts `unhealthy` (not `starting`), and the healthcheck has a `start_period` — leave both alone. Don't shorten `start_period`. |

## Fast diagnostics

```bash
# Is the gateway actually up and which platforms connected?
docker exec "$AGENT_NAME" /opt/hermes/.venv/bin/hermes gateway status
grep -E "connected|platform\(s\)" "$BASE_DIR/hermes/data/logs/gateway.log" | tail

# Are inbound messages arriving? (ground truth for channel wiring)
grep "inbound message:" "$BASE_DIR/hermes/data/logs/gateway.log" | tail

# Which model is primary / is the fallback chain wired?
docker exec "$AGENT_NAME" /opt/hermes/.venv/bin/hermes fallback list

# Watchdog history (restarts land in the vault daily log)
grep watchdog "$BASE_DIR/vault/daily-logs/$(date -u +%F).md"
```

## Adding tools (MCP) safely

Hermes stores `mcp_servers[].env` values as **literal strings** (no `${ENV}`
expansion), so anything you seed in `config.yaml` is a cleartext secret in that file.
Prefer adding MCP servers via the dashboard after boot, or keep `config.yaml` out of
git. An HTTP/streamable MCP server (e.g. Composio) is wired as:

```yaml
mcp_servers:
  composio:
    url: https://backend.composio.dev/v3/mcp/<server-id>/mcp?...&user_id=<user>
    enabled: true
    headers:
      x-api-key: <cleartext-key>   # ← handle carefully; not env-expanded
    timeout: 120
```
