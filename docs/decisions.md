# Design decisions

Short records of the decisions that shaped EPS. Each one says what I chose, what forced the choice, what I weighed it against, and what it costs me.

They are here because a list of technologies proves nothing. Anyone can install Flask. The interesting part is what the alternative was and why it lost, and writing that down is how I make sure I can still defend it in six months rather than half-remembering it.

Newest at the bottom. Decisions get superseded rather than deleted, so the reasoning survives even when the answer changes.

---

## 1. Flask, not FastAPI

**Decided:** 2026-07-22

**Context.** EPS needs a web framework. The two live candidates in Python are Flask and FastAPI, and FastAPI is the one currently attracting the attention.

**Alternatives.** FastAPI's strengths are validation at the boundary through Pydantic, an automatically generated OpenAPI documentation page, and async concurrency. Every one of those is aimed at a JSON API. EPS returns HTML fragments, so the generated docs page describes nothing and the response models never get used. Async would also mean either async database access, which is a harder pattern with more ways to accidentally block the event loop, or sync code running in threadpools, at which point the async was theatre. Concurrency demand here is one person. Django was ruled out for a different reason: it would replace the layers I specifically want to build by hand, its own ORM instead of SQLAlchemy and its own migrations instead of Alembic, so I would end up learning Django rather than the underlying patterns.

**Consequence.** No async, and no free OpenAPI documentation if EPS ever grows a real API. If that happens the web container is the only thing that would need to change, which is one of the reasons the tiers are split the way they are.

---

## 2. HTMX, not a single-page application

**Decided:** 2026-07-22

**Context.** The UI is a dashboard of forms and lists where essentially every interaction is a database write. Something has to make those interactions feel immediate rather than reloading the page each time.

**Alternatives.** A React front end would mean maintaining two applications, a JSON API and a client, plus the contract between them. It introduces client-side state, and client-side state that mirrors server state brings staleness, optimistic updates and cache invalidation with it. Plain HTML forms with no JavaScript at all were a genuine option and would work, but a journal page with fifteen checkboxes reloading on every tick gets old within a day of using it.

**Consequence.** Every interaction is a server round trip, which is fine here and would not be for a highly interactive UI. Rich client-side widgets like drag-and-drop would need an escape hatch. The single vendored `htmx.min.js` is the only JavaScript in the repository, and the quick-add forms and overdue menus use the native `<details>` element, so they work with no script at all.

---

## 3. Four containers

**Decided:** 2026-06-23

**Context.** The application has to be split into services somehow, and the number chosen is the thing an interviewer will push on.

**Alternatives.** One container running everything would be simpler to operate but puts the scheduler in the same process as the request path, which are genuinely different kinds of work with different failure modes. Going the other way and splitting a single-user application into per-feature services would be busywork I could not defend.

**Consequence.** Four tiers is enough to have a real network topology and real policy between the tiers without pretending the application is bigger than it is. It also means the Compose stack maps cleanly onto Kubernetes later, since each container becomes its own deployment with its own NetworkPolicy.

---

## 4. Postgres from the first commit

**Decided:** 2026-05-06

**Context.** The application needs a database on day one, and the tempting shortcut is SQLite because it needs no server.

**Alternatives.** SQLite is excellent and tiny, but it has no network layer, no user accounts and no concurrent writers, so a move off it later would be a migration rather than a configuration change. The ladder ends on RDS, which is managed Postgres, and the spec already uses Postgres-specific column types.

**Consequence.** A database server has to be running for local development, which Compose handles anyway. The test suite runs against in-memory SQLite for speed, using portable type variants so the same models compile on both, and CI runs the migrations against a real Postgres so the schema is proven on the engine that actually ships.

---

## 5. Recompute derived state in the application

**Decided:** 2026-05-06

**Context.** Streaks and trackers need a current value that the dashboard can read cheaply, but the truth is the underlying event history, and that history can be edited retroactively.

**Alternatives.** Database triggers would keep the cache fresh automatically but put the logic somewhere invisible, hard to unit test and hard to review, which is the right trade only when several different codebases write to the same database. There is exactly one writer here. Materialised views refresh on a schedule or on demand, so reads can be stale, and expressing the streak grace mechanic in SQL would be genuinely painful.

**Consequence.** Every write path has to remember to call the recompute function, which is a real footgun, so it is covered by tests that pin the behaviour. In exchange the logic is plain Python that can be read and stepped through, and retroactive editing needs no special-case code at all because past and present go through the same path.

---

## 6. Locked dependencies, not floating ones

**Decided:** 2026-07-22

**Context.** A dependency declared as "3.0 or newer" means a build today and a build in three months can install different code with no change on my side. That is the origin of the oldest complaint in software, which is that it worked on my machine.

**Alternatives.** Floating versions are the least work and were what I started from. Pinning exact versions by hand is nearly free but only pins what I named directly, leaving every transitive dependency still drifting, which is most of the tree. A lock file generated by `uv` pins the whole tree and records a hash for each package.

**Consequence.** Upgrades become a deliberate act rather than something that happens to me, and the lock file is one more thing to keep current. In exchange builds are reproducible, and the hashes mean a package altered on the index fails the install instead of silently entering the image. That also makes the Trivy scan meaningful, since a vulnerability report is only worth reading when you know exactly what is inside the image.
