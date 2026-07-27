# Design decisions

I wanted to get as much of my decision making process written down as possible especially when it came down to picking between different tech options. This document is intended as a list of them with it containing important details. Treat this more as a notebook for now than official documentation.

Newest at the bottom. Decisions get superseded rather than deleted, so the reasoning survives multiple iterations of changes.

---

## 1. No LLM in the app

The system EPS (Executive Productivity System) is rebuilt from a previous version, the DPS (Daily Productivity System) that runs fully using Claude Cowork on my machine. It reads freeform text and figures out what I meant, it decides when a grace day applies, it writes me a short daily review. So the first real decision of the project was what happens to all of that in a programmatic rebuild.

The answer I settled on: none of it comes along. Every fuzzy input gets replaced by a structured one. Instead of the system inferring from my journal that I worked out, there is a checkbox. Features that were purely AI prose, like the daily or weekly review, got cut entirely rather than replaced with some canned template text that would just be worse.

**Consequence.** Everything in the app is deterministic and testable, which is worth a lot. The cost is that some features of my original system simply do not exist here. If a narrative layer ever comes back it will be a separate deliberate addition, not a default part of the app.

---

## 2. What counts as a task

A surprising amount of design fell out of one rule: every task has to carry a date. A deadline, a scheduled day, or a remind-after date, but something. There are no dateless tasks, and the quick-add just defaults to today so this never gets in the way.

This decision basically cleaned the application up as previously, I had entire categories of tasks to sort them into the buckets of those without set dates and those that were just stale from not being touched for too long. Enforcing some kind of scheduling or rescheduling sanitized these. 

**Consequence.** One mental model instead of three. The system can always answer whether something belongs on today's list, because everything has a temporal handle it can be routed by.

---

## 3. Events as the truth, with a cache on top

The requirement that shaped the data model: editing the past. I want to open a day from three weeks ago, tick a box I forgot, and have every day since then update correctly.

So every time-stamped concept gets two tables. A log table that records every event and is the source of truth, and a state table that holds the current computed value, which is what the dashboard reads. After any write to the log, the app calls a recompute function that walks the events and rebuilds the state. The state table is never edited directly by anything.

I looked at two other ways of keeping the cache fresh. Database triggers would do the recompute inside Postgres itself, but then the logic lives somewhere I cannot easily test or debug. Materialized views refresh on a schedule, so reads can be stale, and writing some of the rules in SQL did not sound like a good time. Plain Python functions won.

**Consequence.** Every write path has to remember to call recompute. I am going to make it a point to extensively test these so as to make sure it works. In exchange, editing the past needs no special handling at all. An edit from three weeks ago and a tick from today go through the exact same code.

---

## 4. Postgres from the first commit

The application will have to do a significant amount of work with databases. This involves many different types of data, states, tracking and so on.

The main choice that existed was the one between SQLite and PostgreSQL. If this application was intended to be just the application itself, meaning there was never a plan of hosting it and it would be just the app on whatever device it would run on, then SQLite would be the better choice. Given the totality of the project, in terms of the future and the fact that this is eventually going to be hosted on the cloud, usage of Postgres from the get go was the more future proof choice.

**Consequence.** Local development needs a running Postgres, which the Compose setup provides anyway. Alembic comes in from the first commit as well, so every schema change is a versioned migration in git rather than something done by hand.

---

## 5. Single user, on purpose

EPS is built for exactly one user, me. No accounts, no login, no multi-tenancy.

I could have built user handling "while I am at it" but that is work for an audience that does not exist, and this project's actual point is the infrastructure ladder, not a SaaS product. I am not going to rule out a hypothetical future where I somehow do get real users but in that case, this project would need a proper rework. 

---

## 6. Four containers

My initial idea was to basically just build everything on a singular Linux VM and then containerise out of that, but this way felt a lot better in so far as getting the structure right from the get go. The separation of services is just good as a habit in setting up system architecture.

I did not want to go too crazy with microservices and whatnot. While a singular monolith structure was out of the question, the goal was a middle ground that kept things separate without overcomplication.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/topology-dark.svg">
  <img alt="The four containers: nginx in front, the web app and the worker behind it, Postgres at the bottom, all inside one private network." src="img/topology-light.svg">
</picture>

Four tiers is enough to have a real network topology and real policy between the tiers without pretending the application is bigger than it is. nginx is the only container exposed to the outside, the worker takes no inbound traffic at all, and the scheduled jobs live in their own process rather than inside the web app (APScheduler now, Kubernetes CronJobs later). Additionally, containerising early on makes the later stages of the roadmap easier. It maps more cleanly onto Kubernetes, since each container becomes its own deployment with its own NetworkPolicy.

---

## 7. Choice of Flask

**Context.** EPS needs a web framework. While the obvious choice from the start was just Flask, I wanted to see if there were perhaps better options that could be used. Given the long horizon nature of the project, some way of ensuring I did not have to repeat work down the line was a priority. I began to evaluate options such as FastAPI and Django.

This choice was quite important because it more or less colours the entire architecture of the application. Is this a webapp served to the user? Or is this an API?

I personally leaned on the webapp aspect because for the vast majority of the project it would basically just be interacted with by a single user. I would rather develop it through and get it right instead of developing it for scale from the get go and adding unnecessary complexity. Flask won out in the end.

**Consequence.** This choice locks in a significant part of our stack and the structure of the application. In the future it is a limitation to be worked around, although the benefits far outweigh the drawbacks.

---

## 8. HTMX, not a single-page application

**Context.** The UI is a dashboard of forms and lists where essentially every interaction is a database write. Something has to make those interactions feel immediate rather than reloading the page each time.

My initial instincts were pointing me towards having a client/server split, in so far as having the user interact with their own end which writes to and updates the backend. This added more complexity in the form of the maintenance of a JSON API, the different sides involved and the updates between them. How would things sync? How do we deal with states not matching each other?

I realised that the user interface I had envisioned for this application was not intended to be that complicated or rich and interactive. I opted for the simpler choice in the matter.

**Consequence.** Every interaction is a server round trip, which is fine here but would not be for a highly interactive UI. Richer client-side widgets like a drag-and-drop would need specific considerations.

---

## 9. Secrets stay out of git, and no Vault

The rule from the start: code goes in git, secret values never. Locally that means a gitignored `.env` file with a committed `.env.example` documenting what the app needs, and a gitleaks hook so nothing slips into a commit by accident.

The decision worth writing down is further up the ladder. I looked at running HashiCorp Vault and decided against it. It is a whole extra service to operate, and for this project it buys the same result that a managed secrets store gives me without the operational weight. So the progression is Ansible Vault when a VM enters the picture, and AWS Secrets Manager once the cloud does.

---

## 10. Dependencies are locked, not floating

I originally had dependencies declared loosely, along the lines of "this version or newer". The problem with that is quiet drift: a build today and a build in three months can pull different package versions with zero changes on my side, and things break or change behaviour with nothing in git to explain why.

Pinning the versions I name directly is not enough either, because each package brings its own dependencies and those would still float. So the repo uses `uv` with a committed lock file: every package in the whole tree at an exact version, each with a checksum. Installs are the same everywhere, and a package that got tampered with on the index fails the install instead of silently ending up in the image.

**Consequence.** Upgrading a dependency is now a deliberate act instead of something that happens to me, which is the point. It also makes the image scanning in CI actually mean something, since a vulnerability scan is only as good as your certainty about what is in the image.
