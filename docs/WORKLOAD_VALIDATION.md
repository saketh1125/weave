# Documentation workload validation

Two provider-free PostgreSQL integration rehearsals live in `tests/test_documentation_scale.py`. They use a disposable database selected by `SENTRA_TEST_DATABASE_URL`.

The three-section fixture exercises the actual reviewer tool conversation, persisted assignments, invocation pauses and resumes through a fake provider. Each claim is marked insufficient because no source evidence is supplied. It verifies recovery, not documentation quality.

The 165-section fixture replaces both planner and reviewer with deterministic test functions. It verifies unique assignments, persisted claims, configured budget accounting (660 simulated calls under a 1,000-call ceiling), and that completed sections are not rerun. It does not establish real-provider throughput, investigation accuracy, or the number of calls an actual model requires.

Run after applying migrations: `pytest -q tests/test_documentation_scale.py`. Without the disposable database URL, these tests are skipped. The configured 1,000-call ceiling is a test setting; the application default remains 200.

The combined candidate passed 477 backend tests and 19 frontend contract tests on 2026-11-14, and its frontend production build passed. This includes these rehearsals, not a completed review of a real 165-section repository. Later changes require their own verification.
