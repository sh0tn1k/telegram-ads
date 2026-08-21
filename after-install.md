# After `hermes plugins install`

This plugin is persist-safe under `~/.hermes/plugins/telegram-ads`.

1. Install Python deps into the **Hermes venv** (not system Python):

```bash
~/.hermes/hermes-agent/venv/bin/pip install -e ~/.hermes/plugins/telegram-ads
~/.hermes/hermes-agent/venv/bin/python -m playwright install chromium
```

2. Put the Ads cabinet phone in `~/.hermes/.env` (`chmod 600`):

```
TELEGRAM_ADS_PHONE=+1...
```

3. Enable the plugin and grant watcher inject (needed so the read-only watcher can wake a Telegram session):

```yaml
plugins:
  enabled:
    - telegram-ads
  entries:
    telegram-ads:
      allow_gateway_injection: true
```

Or: `hermes plugins enable telegram-ads`

4. Restart the gateway. Ask the agent to call `telegram_ads_login_from_env`, then tap **Accept** in the Telegram app.

MCP: you do **not** start a server. Hermes uses in-process tools. Claude Code / Cursor spawn `python -m hermes_telegram_ads.mcp` as a local child (stdio). Do not bind `0.0.0.0`.
