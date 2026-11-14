# Hookbox events

Hookbox receives signed event notifications for a developer's connected
services. It checks the event envelope and acknowledges accepted deliveries so
integrations can report whether a notification was processed.

## Local workflow

Run the event server, send a signed sample notification, and inspect the
delivery receipt. Replayed delivery identifiers are acknowledged without
running the handler a second time.
