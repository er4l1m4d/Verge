# Error Log

## Error: Resolution stopped after 1000 observations
Date: 2026-08-11
Step: Resolution / heartbeat
File(s): backend/db.py (`get_unresolved_window_outcomes`)

### Cause
Supabase has a project-level API setting that hard-caps query results at 1000 rows. The `get_unresolved_window_outcomes` function queried `window_observations` without an explicit row range. Once the observations table exceeded 1000 rows, newer windows were invisible to the resolver — it only saw the oldest 1000 rows (which already had outcomes), so it returned 0 unresolved.

This affected BOTH 1h and 15m resolution simultaneously.

### Fix
Replaced unbounded `.execute()` queries with `.range(offset, offset + page_size - 1)` pagination in `get_unresolved_window_outcomes`. Each page fetches 1000 rows; the function iterates until all rows are consumed. This bypasses the hard cap.

### Prevention
- Any query that needs to see all rows must use `.range()` pagination
- `.limit()` does NOT override the project-level cap
- Watch for this pattern: if resolution "stops working" after the table grows, it's likely this issue
