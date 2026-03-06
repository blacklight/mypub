# 01 — Concurrent Delivery — Follow-ups

- [ ] Add an optional `shutdown()` / context-manager (`__enter__`/`__exit__`) to `OutboxProcessor` for clean pool teardown in long-running apps.
- [ ] Consider a persistent delivery queue (e.g. SQLite-backed) for crash-resilient retries — currently retries are in-memory only.
- [ ] Expose delivery metrics/callbacks (success count, failure count, per-inbox latency) for monitoring.
- [ ] Evaluate async delivery (aiohttp) as a future alternative for apps already running an async event loop.
