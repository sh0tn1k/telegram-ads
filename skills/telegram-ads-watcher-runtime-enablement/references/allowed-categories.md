# Mini-report allowed categories — exact match contract

The V2.7 `MiniReportRouter` accepts **12** watcher event
categories. This set is **closed and exhaustive** for the
runtime enablement scope. Any drift between the approval's list
and the router's `allowed_categories` is a **P0** defect
that blocks runtime enablement.

## The 12 categories (canonical order)

```
1.  login_required
2.  session_lost
3.  session_restored
4.  ad_approved
5.  ad_rejected
6.  ad_delivering
7.  ad_not_delivering
8.  spend_threshold_reached
9.  budget_near_limit
10. cpm_threshold_exceeded
11. post_action_verification_failed
12. watch_error
```

## Source of truth

- **Module-level**:
  `gateway/ads_watcher_v2/report.py:ALLOWED_REPORT_CATEGORIES`
  (frozen at module import).
- **Router instance**:
  `gateway.ads_watcher_v2/report_router.py:RouterConfig.allowed_categories`
  (frozen at `MiniReportRouter.__init__` time).

The two MUST be in sync. The router reads the module-level
constant at init, copies it into `RouterConfig`, and never
re-reads it. To extend the set: edit the module-level constant
**first**, then update tests, then update the approval.

## Drift detection script (run during runtime enablement)

```python
import os
os.environ['HERMES_ADS_WATCHER_REPORTS_ENABLED'] = '1'  # for accurate load

APPROVED = {
    'login_required', 'session_lost', 'session_restored',
    'ad_approved', 'ad_rejected', 'ad_delivering', 'ad_not_delivering',
    'spend_threshold_reached', 'budget_near_limit',
    'cpm_threshold_exceeded', 'post_action_verification_failed',
    'watch_error',
}

from gateway.ads_watcher_v2.report_router import load_router_config
cfg = load_router_config()

missing = APPROVED - cfg.allowed_categories
extra = cfg.allowed_categories - APPROVED

if missing:
    raise SystemExit(f'P0 DRIFT: missing from router: {missing}')
if extra:
    raise SystemExit(f'P0 DRIFT: extra in router: {extra}')

print('OK: 12/12 categories exact match.')
```

## Why this matters

`MiniReportRouter.route(event)` does:

```python
if event.category not in self._config.allowed_categories:
    return MiniReportRouteResult(
        status='category_not_allowed',
        category=event.category,
        body=None,
        error='category not in allowed_categories',
    )
```

A category that is in the **approval** but not in the
**router** → events are silently dropped (`status=category_not_allowed`).
The user thinks the watcher is broken; in reality the router
is over-strict.

A category that is in the **router** but not in the
**approval** → events are sent, possibly leaking operational
detail the operator didn't approve. This is a compliance failure.

Either case is a **P0** blocker for runtime enablement.

## Adding a new category (future)

If the operator approves a 13th category (e.g. `ad_scheduled`):

1. Update `gateway/ads_watcher_v2/report.py:ALLOWED_REPORT_CATEGORIES`
   (add the string).
2. Update the V2 event emitter (if the category maps to a new
   V2 event type, add it to
   `gateway/ads_watcher_v2/wiring.py:V2_EVENT_TYPE_TO_STORE_EVENT_TYPE`
   too).
3. Update `tests/gateway/test_ads_watcher_vN.py` to cover the
   new category (positive case: allowed; negative case: route
   returns `category_not_allowed` when explicitly excluded).
4. Update this reference with the new category in the canonical
   order (alphabetical is preferred, but the existing 12 follow
   no strong order — preserve insertion order for readability).
5. Update the approval template to include the new category in
   the "Allowed mini-report categories" section.
6. Bump V-N drop version in `CHANGELOG.md` of the umbrella
   skill.

## Removing a category (rare)

If the operator decides a category should be **dropped** (e.g. it
generated too much noise), the same process as adding, but
in reverse. The 12-category list should **only shrink** if
the operator explicitly approves the removal — never shrink it
silently.

## Verifying in post-restart check

Add to the post-restart verification table:

```
| Mini-report route config | enabled=True, 12 categories exact match | ✅ |
```

If the exact-match check fails, **stop and report**. Do not
patch the router during runtime enablement; that's a code
change and requires a separate approval.
