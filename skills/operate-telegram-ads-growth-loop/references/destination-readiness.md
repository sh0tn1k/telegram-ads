# Destination Readiness Audit

## Required Checks (REQUIRED_DESTINATION_CHECKS)

1. **Existence** — destination (@channel or @bot) exists and is accessible
2. **Language match** — destination language = campaign/ad language
3. **Mobile + Desktop** — bot must respond on both mobile and desktop
4. **Content freshness** — channel has ≥10 posts in last 30 days
5. **Start flow** — bot /start flow is functional
6. **Inline start** — bot accepts start parameter
7. **URL consistency** — primary URL and text link lead to same destination

## Result

`DestinationReadinessResult`:
- `overall: "ready" | "needs_fix" | "blocked"`
- `checks: list[CheckResult]` — per-check pass/fail with reason
- `hard_failures: list[str]` — blockers that prevent launch

## Implementation

Module: `agent/telegram_ads_operator/destination_readiness.py`
Function: `audit_destination(target: str, entity_type: EntityType) -> DestinationReadinessResult`
