# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Concurrent fan-out delivery via `ThreadPoolExecutor` in `OutboxProcessor.publish()`.
  Deliveries to follower inboxes now run in parallel (default 10 workers) instead
  of sequentially.
- New `max_delivery_workers` parameter on `OutboxProcessor` and `ActivityPubHandler`
  to control the thread pool size.
