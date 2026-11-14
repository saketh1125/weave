# Disposable worker recovery rehearsal

Run from PowerShell with Docker running and the locally built candidate image and `pgvector/pgvector:pg17` available:

```powershell
./scripts/verify_recovery.ps1 -Image sentra-candidate:login-fix
```

The script creates a uniquely named, labeled internal network with no external connectivity, PostgreSQL volume and containers, without published ports, host credentials, the project `.env`, or existing services. It deletes only resources bearing its own random sentinel label. It uses the real worker loop, child process, job leasing, PostgreSQL advisory locks and persisted job result. Two competing workers are started while the first holds the repository lock. The fixture expires its lease while that lock is held, checks no contender runs it, kills the first container with SIGKILL, conditionally expires the original token again in case a heartbeat renewed it, checks exactly one contender reclaims it with a new token, then releases it and verifies the stored result and cleared lease.

For speed the lease is **artificially expired in the disposable database**, so this does not validate natural two-minute expiry or heartbeat timing. A mounted test-only `embed.py` records entries and waits for release; no embedding model, provider, network repository, or real embedding operation is exercised. The database schema is created from current ORM metadata, so migrations are not covered. The rehearsal does not establish production recovery performance or behavior of real scans, Git operations or external services. An interrupted PowerShell process may leave labeled resources; inspect their unique `sentra-recovery-...` names and labels before manual cleanup.
