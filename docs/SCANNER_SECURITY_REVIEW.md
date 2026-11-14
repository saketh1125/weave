# Semgrep scanner dependency advisory review

> **Inherited Sentra record.** This document preserves earlier technical context,
> not a fresh Weave acceptance result. Original experiment artifacts and personal
> session records are excluded. See [Migration](MIGRATION.md) for current verification.

**Reviewed:** 2026-11-11

**Scope:** Semgrep CLI invoked by Sentra's background scan worker; five OSV findings in `requirements-semgrep.lock` (four `mcp` advisories and one `opentelemetry-api` advisory). This is a reachability assessment, not an exception or a claim that the dependencies are generally safe.

## Finding and actual path

The historical Sentra dependency audit recorded `mcp==1.29.0` and `opentelemetry-api==1.37.0` as affected; its artifact is not included in independent Weave. The Semgrep 1.179.0 CLI metadata pins those exact dependency lines; Semgrep 1.185.0's upstream `cli/pyproject.toml` still pins `mcp==1.29.0` and `opentelemetry-api~=1.37.0`. The patched versions listed by the audit (MCP 1.30.0; OpenTelemetry API 1.45.1) therefore do not satisfy upstream's current constraints. Do not force these pins in isolation.

Sentra calls `semgrep scan` as a subprocess with `--config p/default`, `--metrics=off`, and a repository path. The worker uses the container's `/app` working directory; the repository is passed as a scan target. The fixed Registry config is explicit, so repository-local Semgrep config is not selected by this command. `p/default` is remote configuration and requires Semgrep Registry access; metrics-off does not make that ruleset local or offline. Semgrep documents `--metrics=off` as disabling metrics, while its CLI source configures metrics from the explicit option. Command-line options take precedence over the inherited `SEMGREP_SEND_METRICS` environment setting.

The Semgrep distribution includes MCP and OpenTelemetry because it also offers a separate `semgrep mcp` command and telemetry support. In the observed Sentra path, the invoked command is `scan`; Sentra does not start the Semgrep MCP server or instantiate an MCP client/server. The four MCP findings cover HTTP redirects, Streamable HTTP session retention, remote `$ref` fetching during client output-schema validation, and server token-resource validation. Their described protocol operations are not part of this CLI scan path. `--metrics=off` additionally disables the documented metrics sending path, but is not itself evidence that OpenTelemetry code is never imported or used internally.

The OpenTelemetry finding concerns expensive parsing of untrusted W3C `tracestate` propagation headers by `opentelemetry-api`. Semgrep scan runs as a CLI process, not an HTTP service receiving client propagation headers. The inspected invocation does not pass repository-controlled headers into the API. This makes the advisory's documented trigger absent from the evidenced Sentra call path; it does not prove every Semgrep code path or every possible upstream/network behavior is immune.

Repository contents are input to Semgrep's parsers and rules, but the reviewed path contains no step that interprets repository files as MCP transport configuration, starts `semgrep mcp`, or changes the executable/config/metrics arguments. A malicious repository can still exercise Semgrep's general parser and rule-processing attack surface; that is outside these five dependency advisories and is not dismissed here.

## Reachability disposition

The primary agent subsequently inspected the installed Semgrep entry point and
package metadata in the candidate image. A real `run_semgrep` invocation with
the restricted environment, fixed working directory and public `p/default`
rules succeeded on a tiny synthetic Python repository: one finding, zero
scanner errors. This confirms that the restricted environment still supports
this registry-backed scan; it is not dynamic proof that the advisory triggers
are unreachable.

| Advisory set | Package/version | Finding | Sentra `semgrep scan` path |
|---|---|---|---|
| GHSA-5h93-6whr-6q8j; GHSA-84m7-p3x7-pcfv; GHSA-rwrf-2pqf-9j8j; GHSA-w4fh-qvv9-3v23 | `mcp` 1.29.0 | Four MCP SDK client/server transport/auth behaviors | No corresponding MCP client/server operation observed in static call-path review. Runtime reachability was not dynamically verified. |
| GHSA-xcq7-p3pf-p88h | `opentelemetry-api` 1.37.0 | Unbounded `tracestate` parsing | No inbound propagation-header handling observed in the CLI-only Sentra path; metrics are explicitly off. Not proven globally unreachable in Semgrep. |

The package audit finding remains valid and should remain visible in dependency reporting. Reachability evidence narrows the current workflow exposure; it does not remove the packages, repair the code, or establish non-exploitability for other commands or future changes.

## Remediation options

1. **Preferred clean fix:** move to a Semgrep release whose upstream dependency constraints permit patched `mcp` and `opentelemetry-api`, regenerate the full scanner lock with hashes, and verify the exact `semgrep scan` workflow. As of this review, the audited latest published Semgrep version (1.185.0) still has the vulnerable constraints, so this option is not currently available from that release.
2. **If scanner release is blocked:** obtain an upstream-supported dependency change or patched Semgrep build. Do not hand-edit pins or suppress the findings without a tested, reviewed compatibility basis.
3. **Temporary, explicit risk decision:** keep the findings open and document that the deployed workflow is limited to the reviewed CLI command, fixed Registry config, and metrics-off arguments. Reassess whenever Semgrep version, command, environment, or worker role changes. This is risk acceptance, not remediation.

The scanner subprocess now runs with `cwd=REPO_ROOT` and a narrow environment allowlist: executable lookup, home/temp paths, locale, trusted CA and proxy settings, and Windows process-start variables. It omits application credentials and Semgrep app tokens, and sets `SEMGREP_SEND_METRICS=off` in addition to the explicit CLI flag. This reduces ambient configuration drift and credential inheritance; it does not patch the flagged libraries, make the remote ruleset offline, or establish process isolation.

## Evidence and limits

- Local code and lock reviewed: `app/semgrep.py`, `app/worker.py`, `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `requirements-semgrep.lock`, and the historical Sentra dependency audit artifact (not included in independent Weave).
- Primary upstream sources: [Semgrep 1.179.0 dependency metadata](https://github.com/semgrep/semgrep/blob/v1.179.0/cli/pyproject.toml), [Semgrep 1.185.0 dependency metadata](https://github.com/semgrep/semgrep/blob/v1.185.0/cli/pyproject.toml), [Semgrep 1.179.0 scan command](https://github.com/semgrep/semgrep/blob/v1.179.0/cli/src/semgrep/commands/scan.py), [Semgrep metrics behavior](https://github.com/semgrep/semgrep/blob/develop/metrics.md), [MCP SDK security advisories](https://github.com/modelcontextprotocol/python-sdk/security/advisories), and [OpenTelemetry advisory GHSA-xcq7-p3pf-p88h](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-xcq7-p3pf-p88h).
- Docker Engine access was denied during the delegated source review, so that review used upstream tagged source and the checked-in lock/config. The primary agent's subsequent image inspection and synthetic runtime smoke are recorded above; no exploitability test was performed.
- The dependency audit itself states that it is a package advisory lookup, not a base-image/OS audit, and that reachability was not established. This note adds source-based reachability reasoning but is not a dynamic exploitability test. Registry content/network trust, Semgrep parser vulnerabilities, OS packages, and later changes remain outside scope.


## Weave dependency lookup — 2026-11-14

A current OSV batch lookup checked all 150 pinned entries across
`requirements.lock` and `requirements-semgrep.lock`. The runtime lock returned
zero advisory matches. The scanner lock returned six moderate advisory matches:
four for `mcp` 1.29.0 and two for `opentelemetry-api` 1.37.0. Five match the
inherited record; the additional finding is
[GHSA-mf5v-w3w3-f6cg](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-mf5v-w3w3-f6cg),
a trace-context parsing denial of service fixed in 1.45.2. Reachability in Weave
has not been established. No dependency versions were changed during this sprint.

This is a package-version advisory lookup, not a container/OS scan, exploit test,
or production risk acceptance. Production promotion remains blocked pending
compatible scanner dependency remediation or reviewed mitigation, image/OS audit,
staging and restore exercises, access controls, and deployment/account decisions.

A compatibility check on 2026-11-14 confirmed that the fixed versions `mcp` 1.30.0 and `opentelemetry-api` 1.45.2 do not satisfy the constraints in the latest published [Semgrep 1.185.0 tagged metadata](https://github.com/semgrep/semgrep/blob/v1.185.0/cli/pyproject.toml): `mcp==1.29.0` and `opentelemetry-api~=1.37.0`. A supported lock-only remediation is therefore blocked until upstream changes its constraints or provides a supported patched build. The six findings remain open.
