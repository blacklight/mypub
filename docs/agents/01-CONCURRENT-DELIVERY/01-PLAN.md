# 01 — Concurrent Delivery

## Problem

`OutboxProcessor.publish()` delivers activities to follower inboxes **sequentially**. Each delivery includes up to 3 retries with exponential backoff (10s, 20s, 40s). For N inboxes, worst-case wall time is `N × (15s timeout × 3 retries + backoff delays)` — a blog with 50 followers could block for minutes on a single publish.

## Solution

Use `concurrent.futures.ThreadPoolExecutor` to deliver to all inboxes in parallel.

## Changes

### `src/python/pubby/handlers/_outbox.py`

1. **New constructor parameter:** `max_delivery_workers: int = 10` — max threads for concurrent delivery. Configurable because operators may want to limit outgoing connections.

2. **Create the pool lazily** (or on init) as `self._delivery_pool = ThreadPoolExecutor(max_workers=max_delivery_workers)`.

3. **Replace the sequential loop in `publish()`:**

   Current:
   ```python
   for inbox_url in inboxes:
       self._deliver_with_retry(inbox_url, activity)
   ```

   New:
   ```python
   futures = {
       self._delivery_pool.submit(self._deliver_with_retry, inbox_url, activity): inbox_url
       for inbox_url in inboxes
   }

   for future in as_completed(futures):
       inbox_url = futures[future]
       try:
           future.result()
       except Exception:
           logger.error("Delivery to %s raised an unexpected exception", inbox_url, exc_info=True)
   ```

4. **No changes to `_deliver_with_retry` or `_deliver`** — they're already self-contained and thread-safe (no shared mutable state).

5. **`time.sleep()` in retries is fine** — it blocks only its own thread in the pool, not the caller.

### `src/python/pubby/handlers/_handler.py`

Pass `max_delivery_workers` through from `ActivityPubHandler` constructor to `OutboxProcessor`.

### Tests

- **`tests/test_outbox.py`:** Add a test that verifies concurrent delivery actually happens in parallel (mock `_deliver` with a short sleep, assert total wall time is ≪ sequential time for N deliveries).
- **Existing tests** should continue to pass — the pool is transparent to the delivery logic.

## Non-goals

- Async/await (would be a much larger refactor and break the sync API surface).
- Persistent delivery queue (deferred to a later task).
- The pool is not shut down explicitly — it's daemon-threaded and cleaned up on process exit. A `shutdown()` method could be added later if needed.
