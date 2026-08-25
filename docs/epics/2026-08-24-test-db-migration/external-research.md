# External Best Practices: Migrating Tests from Embedded/In-Process DB to a Real DB Server

**Context:** Python project (tortoise) with ~6,000 tests currently running against `falkordblite` (a redislite-based embedded FalkorDB runtime). Target: real FalkorDB server via Docker, matching production.

**Method:** Perplexity web_search (sonar) + web_fetch of primary sources. Every claim below is attributed. Where the literature does not cover our specific situation, that is explicitly marked **[my synthesis]** — never dressed as "best practice."

**Date:** 2026-08-21

---

## Q1. Test isolation on a SHARED real database server

### Consensus best practice
**Do NOT spin up one server per test.** Start ONE server (or one container) per test run/session, then isolate per-test with one of three canonical strategies, in order of preference:

1. **Transaction rollback** — wrap each test in a transaction, roll back at the end. Fastest, but cannot observe committed state (breaks tests of `after_commit`-style behavior, uniqueness-on-commit, etc.).
2. **Truncate/delete-all between tests** — delete all data (FK-ordered) between tests, or truncate. Fast, works regardless of ORM, but requires FK-ordered cleanup (Respawn-style graph ordering).
3. **Per-test database/schema/namespace** — strongest isolation, enables parallelism; cost is per-test setup. With xdist, the community standard is **per-worker database/schema**, namespaced by `worker_id`/`PYTEST_XDIST_WORKER`, not per-test.

For Redis-family databases specifically, the pattern is: **dedicated test DB + FLUSHDB between tests, or unique key prefixes** (not per-test server).

For **FalkorDB specifically**: named graphs are the vendor-sanctioned isolation unit. The FalkorDB security guide explicitly recommends **per-graph isolation** ("use separate graph names for teams or tenants, pair with ACLs"), and every query targets a single graph key — so a **per-test graph** (or per-worker graph) is a first-class, supported isolation mechanism.

### Sources
| Source | URL | What it says |
|---|---|---|
| Jimmy Bogard, "Isolating database data in integration tests" (Los Techies) | https://lostechies.com/jimmybogard/2012/10/18/isolating-database-data-in-integration-tests/ | Canonical catalog of exactly 3 strategies: transaction rollback, drop-and-recreate DB per test ("dog slow"), and FK-ordered delete-all-data (fastest robust option; works straight against DB metadata). Guiding principle: "one test's data is completely isolated from other tests." |
| EF Core docs, "Testing against your production database system" (Microsoft Learn) | https://learn.microsoft.com/en-us/ef/core/testing/testing-with-the-database | "In most simple cases, your test suite has a **single database that's shared** between multiple tests... logic to make sure the database is created and seeded **exactly once** during the lifetime of the test run." Read-only tests can run in parallel; write tests should be wrapped in a rolled-back transaction; transactional tests are "the most problematic" and "may require disabling parallelization." |
| Rails Guides, "Testing Rails Applications" | https://guides.rubyonrails.org/testing.html | "Rails automatically wraps each test in a database transaction that is rolled back after the test finishes," making tests independent; `use_transactional_tests = false` is the documented per-case escape hatch. |
| Django docs, "Writing and running tests" | https://docs.djangoproject.com/en/6.1/topics/testing/overview/ | Uses a **separate blank test database** created per run and destroyed after the run (`--keepdb` to retain); `TestCase` wraps each test in a transaction with rollback. |
| pytest-django, "Database access" | https://pytest-django.readthedocs.io/en/stable/database.html | "Databases are created on first need, cached for later tests, and **transactions are rolled back** to isolate tests." |
| FalkorDB Security Guide | https://falkordb.github.io/docs-staging/operations/security/ | "**Per-graph isolation**: use separate graph names for teams or tenants; pair with ACLs to restrict access to those keys." |
| FalkorDB News, "Multigraph Topology" | https://www.falkordb.com/news-updates/multigraph-topology-isolation-linear-scale/ | "Every query targets a single graph key"; cross-graph queries within one execution are not supported — the graph key is the natural isolation boundary. |
| OneUptime, "How to Write Redis Integration Tests That Are Isolated" | https://oneuptime.com/blog/post/2026-03-31-redis-how-to-write-redis-integration-tests-that-are-isolated/view | For Redis-family: dedicated test database + `FLUSHDB` before each test via fixtures, or unique key prefixes; fresh container per module only for maximum isolation. |
| SQLAlchemy discussion #13109, "How to properly parallelize tests" | https://github.com/sqlalchemy/sqlalchemy/discussions/13109 | With pytest-xdist each worker is a separate process; use a **different database or schema per worker** keyed by `PYTEST_XDIST_WORKER` to avoid conflicts. |
| qaskills, "Create a PostgreSQL Database per Test with Testcontainers" | https://qaskills.sh/blog/testcontainers-postgres-per-test-database | Balanced guidance: one container per test process, fresh database per test only "when strong isolation is needed" — a reuse/isolation tradeoff, not a default. |

### Tradeoff summary (from the above)
- **Speed vs isolation vs setup cost** is the axis every source walks. Transaction rollback ≈ fastest but weakest for commit-sensitive code; truncate ≈ fast and robust; per-test DB ≈ strongest but adds setup cost per test and is generally **overkill per-test** — the community does per-*worker* or per-*process* DBs, not per-test, at 1,000+ test scale.

---

## Q2. Embedded/in-process test DB vs real server — known divergence risks

### Consensus best practice
**Test against the same database system production uses.** The canonical argument: an embedded substitute is a *test double* that can pass while production fails, because SQL/dialect, type coercion, concurrency, locking, and query-planning semantics all diverge. The standard recommendation (Testcontainers, Martin Fowler, Microsoft EF Core) is to run a real instance of the production DB.

**Known failure modes documented in the literature:**
- Type coercion differences (SQLite accepts a float in an INTEGER column; Postgres rejects it — test passes, prod crashes).
- Dialect differences (JSON operators, constraint enforcement, index behavior).
- Concurrency/locking semantics (in-process = single connection world; server = real concurrency, deadlocks, connection-pool behavior).

**Caveats:** embedded DBs are acknowledged as fine for pure unit tests and local dev. And in our specific case, the *vendor itself* sanctions FalkorDBLite for CI and claims API parity — but the vendor simultaneously says production workloads should run on a server, and the embedded runtime is single-process (network disabled by default), which means connection-pooling and multi-client concurrency paths are untested under FalkorDBLite.

### Sources
| Source | URL | What it says |
|---|---|---|
| Testcontainers, "Getting started with Testcontainers for Python" | https://testcontainers.com/guides/getting-started-with-testcontainers-for-python/ | "Write tests talking to **the same type of services you use in production without mocks or in-memory services**." |
| Neon, "The Dangers of Testing in SQLite as a Postgres User" (Brian Holt) | https://neon.com/blog/testing-sqlite-postgres | Concrete divergence catalog: SQLite type affinity silently coerces a float into an INTEGER column; Postgres raises an error for the same code. "The differences... can lead to **false confidence** in your tests." Also covers dialect and concurrency divergence. |
| EF Core docs (Microsoft) | https://learn.microsoft.com/en-us/ef/core/testing/testing-with-the-database | Explicit framing: "testing against a different database than what is used in production (e.g. Sqlite) is not covered here, **since the different database is used as a test double**." Also: LocalDB "doesn't support everything that SQL Server Developer Edition does" — even vendor-adjacent substitutes have feature gaps. |
| Martin Fowler, "Dependency Composition" | https://martinfowler.com/articles/dependency-composition.html | "I generally use Testcontainers to run dockerized dependent services" (databases) in tests. Fowler's Practical Test Pyramid: integration tests should spin up a local instance of the real dependency, not shared remote infra. |
| FalkorDB docs, "FalkorDBLite" | https://docs.falkordb.com/operations/falkordblite/ | Vendor statement: "The graph API and Cypher queries remain identical. Can I migrate from FalkorDBLite to a remote FalkorDB server? Yes. Simply swap the connection line." Also: FalkorDBLite = "Embedded Redis + FalkorDB server started by your app"; recommended for local dev/CI; "For production workloads or multi-user deployments, move to FalkorDB Cloud or self-hosted FalkorDB." |
| redislite docs, "What is redislite" | http://redislite.readthedocs.io/en/latest/topic/what_is_redislite.html | Embedded Redis "not accessible over the network by default," locked-down Unix domain sockets — i.e., no server-style multi-client access semantics. |
| StackOverflow, "When should SQLite not be used for testing in Django" | https://stackoverflow.com/questions/20531494/when-should-sqlite-not-be-used-for-testing-in-django-if-a-different-rdbmse-g-p | "Different engines can behave differently, causing queries to work on SQLite and fail on PostgreSQL" — test on the same DB as production before release. |

**Implication for us (with vendor parity claim on record):** FalkorDBLite is NOT a third-party substitute like SQLite — it is the vendor's own embedded build, and the vendor claims query parity. The risk profile is therefore lower than the SQLite/Postgres case, but the **concurrency/connection-pool/network** divergence remains real and untestable in-process. **[my synthesis]:** the untestable surface is multi-client concurrency, connection management, and anything behaviorally dependent on server process boundaries — not Cypher semantics, which the vendor warrants.

---

## Q3. Testcontainers / containerized test DBs for Python

### Consensus best practice
- **testcontainers-python is the current standard** for containerized test dependencies in Python.
- Recommended pattern: a **session- or module-scoped container fixture** (start once, stop at session end) + an **autouse function-scoped cleanup fixture** (delete/flush data between tests). Function-scoped containers are explicitly warned against as slow.
- Use **yield fixtures / finalizers** for setup/teardown, and set **env vars** (`DB_HOST`, `DB_PORT`, `DB_NAME`...) from the container so app code doesn't hardcode connection info.
- CI options: **GitHub Actions service containers** (pre-started per job, near-zero test-side startup) vs Testcontainers inside the job (adds ~1–3 s per suite, ~200 ms warm). Both are legitimate; service containers are the cheaper/CI-idiomatic option when the DB is the only dependency.
- Python community alternative: `pytest-postgresql` — session-scoped `postgresql_proc` server + per-test client fixture, using a **template DB clone per test** to get fresh-state speed.

### Sources
| Source | URL | What it says |
|---|---|---|
| Testcontainers Python guide | https://testcontainers.com/guides/getting-started-with-testcontainers-for-python/ | Shows module-scoped `PostgresContainer` fixture started once; connection params exported as env vars; autouse function-scoped fixture that `delete_all`-cleans before every test. Recommends yield fixtures and `request.addfinalizer`. |
| OneUptime, "How to Use Testcontainers with Redis in Python" | https://oneuptime.com/blog/post/2026-03-31-redis-testcontainers-python/view | "Session-scoped container for performance, or function-scoped for maximum isolation"; reset Redis state between tests. |
| Cadence, "How to run integration tests in CI" | https://cadence.withremote.ai/blog/integration-tests-ci | Direct comparison table: GitHub Actions service containers vs Testcontainers; Testcontainers adds **1–3 s startup per suite** (~200 ms warm reuse). |
| GitHub Docs, "Using containerized services" | https://docs.github.com/en/actions/using-jobs/running-jobs-in-a-container | Official pattern: service containers (databases, caches) defined per job with health checks. |
| pytest-postgresql (PyPI) | https://pypi.org/project/pytest-postgresql/ | "`postgresql_proc` — a session-scoped fixture that starts a PostgreSQL instance on its first use and stops it when all tests are finished"; client fixture clones a template DB per test. |
| Docker Blog, "Testcontainers Best Practices" | https://www.docker.com/blog/testcontainers-best-practices/ | "Use the same database version as production" — version parity is an explicit best practice. |

---

## Q4. The "redirect" question: switching test DB construction from embedded to server

### Consensus best practice
- **One seam, env-var-driven.** Connection construction should read from env vars / config (`DATABASE_URL`, `DB_HOST`, ...) at a single factory/fixture point. This is exactly the pattern the Testcontainers Python guide itself uses (`os.getenv("DB_HOST", ...)`) and the `pytest-env` plugin codifies (`DATABASE_URL` set in `pyproject.toml`). pytest fixtures are the dependency-injection mechanism for choosing the resource per test.
- **Incremental over big-bang** where risk is high: the **strangler fig pattern** (Azure/AWS) — introduce a facade, shift consumers progressively, decommission the legacy path only when the new one is proven. Applied to test infra: redirect test-by-test or module-by-module, keeping the embedded path live until the server lane is green, then remove it.
- There is **no canonical "test-infra strangler" literature**; the strangler citations are the general pattern, applied by analogy. **[my synthesis].**

### Sources
| Source | URL | What it says |
|---|---|---|
| Testcontainers Python guide (connection seam) | https://testcontainers.com/guides/getting-started-with-testcontainers-for-python/ | "Instead of hard-coding the database connection parameters, we are using environment variables to get the database connection parameters. This will help us to run the application in different environments without changing the code." |
| pytest-env (PyPI) | https://pypi.org/project/pytest-env/ | Lets you define env vars in `pyproject.toml` for tests, "including `DATABASE_URL`" — the standard way to flip which DB tests use. |
| pytest docs, "How to use fixtures" | https://docs.pytest.org/en/stable/how-to/fixtures.html | Fixtures are pytest's dependency-injection mechanism — "test functions request fixtures by declaring them as arguments," the canonical way to resolve per-test resources. |
| Azure Architecture Center, "Strangler Fig pattern" | https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig | Introduce a facade, "incrementally shift requests to the new system until the legacy system can be decommissioned." |
| AWS Prescriptive Guidance, "Strangler fig pattern" | https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/strangler-fig.html | "Gradual extraction... users progressively consume migrated features as the legacy is replaced" — incremental migration by routing, not big-bang. |

---

## Q5. Fail-closed / fail-open test semantics when the test DB is unavailable

### Consensus best practice
**Fail loud. Missing Docker/DB must FAIL the run in CI, not skip.** The Testcontainers maintainers' own position (issue #343): "letting the test fail is the correct behavior." Testcontainers does a pre-flight Docker check and **errors** ("Could not find a valid Docker environment") rather than silently proceeding. Skipping (JUnit `Assume`, `skipif`) is acceptable only for **local developer convenience**, on the condition that CI guarantees the environment exists. The failure mode of skip-when-unavailable is the **vacuous pass / false green** — the pipeline reports success while the integration path was never exercised.

Supporting principle from pytest-django: tests that access the DB **fail by default** if no DB is available, and DB access must be explicitly requested — there is no silent pass.

### Sources
| Source | URL | What it says |
|---|---|---|
| Testcontainers Java issue #343 | https://github.com/testcontainers/testcontainers-java/issues/343 | Maintainer: "IMO **letting the test fail is the correct behavior**"; CI should be where Docker is guaranteed; `Assume`-skipping is for local machines. |
| Testcontainers pre-flight / error behavior (guide) | https://perun.au/insights/testcontainers-production/ | Testcontainers performs a pre-flight Docker environment check and throws an error ("Could not find a valid Docker environment") if no reachable daemon — fail-closed by design. |
| Testcontainers GitLab CI docs | https://java.testcontainers.org/supported_docker_environment/continuous_integration/gitlab_ci/ | The recommended CI setup is to **provide Docker explicitly** (dind, `DOCKER_HOST`) so tests RUN — not to disable them. |
| pytest-django, "Database access" | https://pytest-django.readthedocs.io/en/stable/database.html | "Tests **fail by default** if they try to access the database" without explicit opt-in (`@pytest.mark.django_db` / `db` fixture) — explicit request, no silent pass. |
| Cadence, "How to run integration tests in CI" | https://cadence.withremote.ai/blog/integration-tests-ci | Service containers vs Testcontainers comparison; both assume the DB is a hard requirement of the job, not optional. |

---

## Q6. Per-test vs per-file carve-out granularity

### Consensus best practice
**Both mechanisms are first-class; which is "the norm" depends on how localized the special behavior is.**
- **Per-test / per-class marking** is the norm when the carve-out is localized: pytest-django requires per-test `@pytest.mark.django_db` for ANY DB access; Rails documents `use_transactional_tests = false` on individual test cases/classes (not globally) when specific tests must observe transactions; Dataverse groups DB-backed tests with `@Tag("testcontainers")`.
- **Whole-file marking** (`pytestmark = pytest.mark.<name>` at module level) is the norm when the ENTIRE module shares the property — and pytest explicitly supports selecting by marker expressions (`-m "markname and not other"`).
- The literature contains no rule that says "never mark per-test inside a mostly-unchanged file"; the guidance is to mark at the granularity that matches the scope of behavioral difference. **[my synthesis]** — this is an inference from the above sources, not a stated rule.

### Sources
| Source | URL | What it says |
|---|---|---|
| pytest docs, "Marking test functions with attributes" | https://docs.pytest.org/en/stable/how-to/mark.html | "You can apply custom markers to **individual tests, whole classes, or entire modules**" (`pytestmark` for module-level) and select with `-m "mark1 and not mark2"`. |
| pytest-django, "Helpers" | https://pytest-django.readthedocs.io/en/stable/helpers.html | Tests needing DB must use `@pytest.mark.django_db` or the `db`/`transactional_db` fixtures — per-test granularity as the default contract. |
| Rails Guides, "Testing Rails Applications" | https://guides.rubyonrails.org/testing.html | Transactional tests are per-test default; `self.use_transactional_tests = false` is set on specific cases/classes, and Viget's writeup adds: disable it "for specific test cases that need to observe transaction behavior, rather than turning it off globally" (https://www.viget.com/articles/testing-transactions-in-rails). |
| Dataverse, "Testing" | https://guides.dataverse.org/en/6.0/developers/testing.html | DB-backed integration tests carry `@Tag("testcontainers")` — a marker used to group/select them, demonstrating marker-based DB-test segregation. |

---

## What this means for our 4 decisions

Ground rules used below: (1) evidence-backed consensus outranks convenience; (2) vendor parity claims are credited but scoped; (3) where literature is silent, labeled **[my synthesis]**.

### D-1 — Redirect scope (how broadly to point tests at the real server)
**Defensible choice: full-suite redirect through a single connection seam, with the embedded path retained behind the same seam for local use — not a partial redirect.**
- Evidence FOR broad redirect: every authoritative source says test on the production DB (Q2: Testcontainers, EF Core, Fowler, Neon). The vendor's own migration story is "simply swap the connection line" (FalkorDBLite docs), i.e., the seam is trivially broad by design.
- Evidence AGAINST big-bang: strangler-fig guidance favors incremental when risk is high. With 6,000 tests, the pragmatic middle is: **the seam flips everything at once (single factory), but the *verification* of the flip is staged** — land the server lane as the gate, keep the embedded lane running as a non-gating lane during the transition (see D-3), then remove it. That is strangler discipline applied to *acceptance*, not to *code scope*.

### D-2 — Per-test vs whole-file carve-out
**Defensible choice: whole-suite default = server; carve out per-test (or per-class) with an explicit marker for the minority of embedded-only tests; use file-level markers only for files that are wholly embedded-specific.**
- Q6 evidence: per-test marking is the documented norm for localized differences (pytest-django, Rails); whole-file is for module-wide properties. Since our hypothesis is that embedded-only behavior is the exception, per-test markers match the evidence. If the carve-out set turns out large (>~10–20% of tests), that is a signal the divergence is broader than expected — reconsider whether the carve-outs are actually *necessary* versus *accidental* (e.g., tests accidentally relying on embedded quirks). **[my synthesis]** on the threshold; the mechanism guidance is sourced.

### D-3 — Matrix split (embedded lane + server lane in CI)
**Defensible choice: yes to a temporary dual lane, with the SERVER lane as the gating (fail-closed) lane and the embedded lane as non-gating, dropped once the server lane is green and stable.**
- Q3 evidence: the community pattern is a single production-like DB in CI (service container or Testcontainers); there is no canon endorsing running two DB lanes permanently. A dual lane is a **transition** artifact — the strangler "both paths live until the new is proven" state (Q4). Run server = gate, embedded = informational canary so regression surface stays visible during migration; delete the embedded lane after N consecutive green runs. **[my synthesis]** on the canary mechanics; the "test on prod DB, not two DBs" consensus is sourced.

### D-4 — Fail-closed wipe
**Defensible choice: fail closed, and wipe at session/graph granularity.**
- **Fail-closed:** missing/unreachable server = suite FAILURE, never skip. Directly supported by Testcontainers #343 ("letting the test fail is the correct behavior"), Testcontainers' pre-flight error, and pytest-django's fail-by-default DB access. Guard pattern to avoid: `skipif(no-docker)` at the top of the suite — that is exactly the vacuous-pass failure mode Q5 documents. If local devs without Docker need relief, gate ONLY local runs (e.g., a `--local-no-docker` opt-in), never CI. **[my synthesis]** on the opt-in flag mechanics.
- **Wipe:** the sourced patterns are (a) drop/recreate the test DB per run (EF Core `EnsureDeleted/EnsureCreated`, Django per-run test DB), and (b) Redis-family `FLUSHDB`/dedicated-DB between tests (Q1). For FalkorDB the analogous unit is the **graph**: wipe per run at graph granularity (delete/flush the test graphs at session start — matching the vendor's per-graph isolation model), plus per-test or per-worker graph namespacing for isolation (Q1). **[my synthesis]** that graph-level flush is the FalkorDB analog of FLUSHDB; the per-graph isolation model itself is vendor-documented.

---

## Where the literature is THIN or our situation is unusual

1. **FalkorDB-specific test isolation literature is nearly nonexistent.** RedisGraph was EOL'd (migration to FalkorDB), so RedisGraph-era test guidance is stale, and FalkorDB's own docs cover security/deployment, not testing patterns. The only vendor-documented pieces we have: per-graph isolation (security guide) and "swap the connection line" parity claim (FalkorDBLite docs). Everything beyond that (graph-flush fixtures, per-worker graph naming) is **[my synthesis]** grounded in the Redis-family and general DB patterns above.
2. **redislite-vs-server divergence is undocumented in the literature.** The SQLite/Postgres divergence literature is rich but only analogous — redislite is not a dialect substitute, it's the same code in a different process topology. The concrete divergence we can assert: network disabled by default, single-process concurrency (redislite docs) → connection-pooling and multi-client paths untested under FalkorDBLite. **[my synthesis]** for anything beyond that.
3. **Vendor-sanctioned embedded DBs are unusual.** Most embedded-DB cautionary literature assumes a *third-party* substitute (SQLite for Postgres). Here the vendor ships and recommends the embedded runtime for CI and warrants API parity — so the "false confidence" argument weakens on the *semantics* axis but stands on the *topology* axis (concurrency, connections, process boundaries). No external source addresses this hybrid case.
4. **Dual-matrix testing (two DB lanes permanently) has no supporting canon.** All guidance converges on one production-like DB. A permanent dual lane would be an anti-pattern by implication; as a bounded transition it's consistent with strangler guidance.
5. **Scale.** Most published examples are hundreds of tests. At 6,000 tests, per-test-DB and even per-class-DB patterns are clearly too slow per the sources' own tradeoff framing; the sourced scalable pattern is per-worker DB/schema (SQLAlchemy discussion, pytest-postgresql template cloning) or truncate/flush between tests.
