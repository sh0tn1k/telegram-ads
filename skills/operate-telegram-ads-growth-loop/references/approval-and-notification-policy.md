# Approval and Notification Policy

## Approval Boundary

### Read-only — Never requires approval
- All `telegram_ads_*` read-only tools (list, get, stats, status, validate)
- Operator: validate_query, validate_target, validate_brief, audit_destination
- Operator: evaluate_campaign (recommends, never mutates)
- Draft preparation: campaign brief, watcher policy, attribution snapshot

### Approval Required — Always requires explicit operator approval
- Creating, launching, stopping ads
- Changing CPM, budget, targeting
- Editing ad creative
- Publishing materials
- Sending user-facing messages
- Deploy, restart, production DB/config changes
- Using credentials
- Spending budget

## Notification Policy

### Material Events (notify)

- Moderation approved
- Moderation declined
- No-delivery anomaly
- Evidence checkpoint with classification change
- Budget 80% utilization
- Budget 95% utilization
- Maximum approved loss reached
- Material performance deterioration
- Watcher failure
- Attribution failure
- Action requiring approval

### Suppressed (do NOT notify)

- Routine poll
- Unchanged metrics
- Normal metric growth
- Repeated identical event
- Insufficient data (without anomaly)
- Internal retry
- Unchanged classification

## Bounded Notification Authorization

```yaml
notification_authorization:
  authorization_id: "uuid"
  destination: "telegram:chat_id"
  allowed_projects: []
  allowed_event_types: []
  minimum_severity: "warning"
  max_messages_per_hour: 5
  max_messages_per_day: 20
  valid_from: "ISO8601"
  valid_until: "ISO8601"
  revocable: true
  redact_secrets: true
  excluded_actions: []
  audit_log_required: true
```

- Authorization grants notification only — NOT ad mutation
- Authorization does NOT authorize spend
- Authorization is project-scoped — not inherited
- Authorization has TTL
- Authorization can be revoked

## Report Format

```
Campaign / Ad / Query or Target Cluster

Facts:
- impressions, clicks, CTR, actions, spend, CPA, budget utilization

Assessment:
- classification, confidence, baseline comparison, attribution quality

Hermes decision:
- recommended action, next checkpoint

Operator:
- "Продолжаю наблюдение. Ваше действие не требуется."
  OR
- Exact scoped approval request
```
