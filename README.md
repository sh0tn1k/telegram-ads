# telegram-ads

Agent plugin for **ads.telegram.org**: typed tools, Playwright login, read-only watcher.

Give your agent the git URL. It installs itself. You do **not** host an MCP server.

## MCP is local

The MCP entrypoint is a **stdio child process** the agent starts:

```text
agent  --stdin/stdout JSON-RPC-->  python -m hermes_telegram_ads.mcp
```

No public port, no VPS daemon, no URL to keep alive. Chromium runs on the same machine as the agent. Same rule as Playwright MCP.

## Install

### Hermes Agent

```bash
hermes plugins install https://github.com/sh0tn1k/telegram-ads.git --enable
```

Then follow `after-install.md` (venv pip, `playwright install chromium`, `TELEGRAM_ADS_PHONE`, gateway restart, `telegram_ads_login_from_env` + Accept in Telegram).

Grant watcher inject so the read-only watcher can wake a chat:

```yaml
plugins:
  entries:
    telegram-ads:
      allow_gateway_injection: true
```

### Claude Code

```
/plugin marketplace add https://github.com/sh0tn1k/telegram-ads.git
/plugin install telegram-ads@telegram-ads
```

Set `TELEGRAM_ADS_PHONE` in the environment. First mutating call uses Claude Code's own permission prompt (Hermes Once/Session/Always cards exist only on Hermes).

### Other MCP agents (Cursor, …)

```bash
pip install -e '.[mcp]'
python -m playwright install chromium
```

Point the agent at this checkout:

```json
{
  "mcpServers": {
    "telegram-ads": {
      "command": "python",
      "args": ["-m", "hermes_telegram_ads.mcp"]
    }
  }
}
```

## First login (every host)

1. `TELEGRAM_ADS_PHONE` in the host env (never in git).
2. Call `telegram_ads_login_from_env`.
3. Tap Accept in the Telegram app.
4. Cookies stay in the persistent Chromium profile (`TELEGRAM_ADS_HOME`, or `~/.hermes/data/telegram_ads` if Hermes is present, else `~/.telegram-ads`).

## Safety

- Mutations require host approval (Hermes Once/Session/Always/Deny, or the MCP client's permission UI). Do not confirm by typing "yes".
- Watcher is **read-only**. Do not start a second Playwright on the same Ads profile.
- Do not start a second Telegram `getUpdates` poller. This plugin reuses the host bot.
- Do not bind `hermes serve` / MCP HTTP on `0.0.0.0`.

## Not in this plugin

Telegram research (Telethon), GrokBot identity, channel portfolio, `/sessions`. Those stay on the operator host.

## License

MIT
