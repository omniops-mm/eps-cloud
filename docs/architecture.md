# EPS Architecture

This document covers the technical shape of EPS in the form of components, data model, patterns, and how I'm building it over time. For less detailed/more surface level information on what EPS *is* and why it exists, the [README](../README.md) is the starting point.

This is a living document. EPS is pre-v0.1 at the moment so a lot will change. I'll update this as decisions land and code lands.

---

## Overview

I have gone through an insane amount of productivity applications, systems and methods in my attempts to increase the amount of work I could get done. Some have succeeded far more than others but they all shared the same fly in the ointment, in that the systems themselves, require daily maintenance work in order for them to function. The Executive Productivity System, which I used to call my Daily Productivity System, is my effort to change this.

The core idea is that you concentrate your effort on adding in the important tasks and things you need to do while leaving all the maintenance work to the application itself. Habits, trackers, tasks, daily metrics, etc. You must first set them up when entering into the application and then it will be handled automatically. Less time spent deciding and more time spent doing.

The current version of this system is working off of Claude Cowork by Anthropic. I essentially utilised the AI in order to organise my days for me. While this has worked, I realised that there are many components that could just be implemented programmatically either directly or with a few tweaks.

And so we have arrived to this project. This will be my attempt to build this application, step by step, while utilising the correct tools and technologies, in order to create a version of my application that is functional on its own. 

As a note, this whole project is moreso a learning project than a product. I am using it as a substrate to layer in practice for a career in DevOps or Cloud Engineering, with me driving the changes towards those technologies to better develop my understanding of them. The version ladder is intentionally built: as each tag adds a real piece of infrastructure rather than just shipping features.

**Status**: design phase. The spec is locked; code starts next.

---

## Roadmap

EPS evolves through tagged versions. Each tag adds a substantial layer to either the product, the infrastructure, or both.

| Version  | What it adds                                                                                                                                                                                                                                                                                   |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **v0.1** | Foundation: local Python webapp with a Postgres database, scheduled background jobs, a working dashboard with streaks / trackers / tasks / daily metrics, Google Calendar + weather integration. Runs on a single Ubuntu machine via systemd.                                                  |
| **v0.2** | Multi-distro reproducibility. A Bash bootstrap script that sets up a fresh Ubuntu / Debian / Fedora VM end-to-end. Demonstrates Linux fluency across distros.                                                                                                                                  |
| **v0.3** | Ansible roles replace the Bash bootstrap. Same deployment, idempotent, configuration-management style. Secrets handled via Ansible Vault.                                                                                                                                                      |
| **v0.4** | Terraform provisions the local VM (via libvirt or Vagrant); Ansible deploys onto it. Infrastructure-as-code locally.                                                                                                                                                                           |
| **v1.0** | AWS deployment. Terraform builds a custom VPC with multi-AZ subnets, an internet gateway and NAT gateway, route tables, security groups, and NACLs. RDS for Postgres. EC2 for the webapp. Hand-rolled networking on purpose -> practical AWS skill development, not the default-VPC easy path. |
| **v1.x** | Kubernetes migration. EKS with Helm charts per service tier, NetworkPolicies, persistent volumes, ingress controllers, Prometheus + Grafana observability, optional ArgoCD for GitOps. The portfolio centerpiece.                                                                              |

This is sequenced so each version is a meaningful learning step, not just a tooling check-the-box. The commit history is part of the story. 

---

## Tech Stack

| Layer             | Choice                                                                                         | Why                                                                                                                                                                                                                                    |
| ----------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Language          | Python 3.x                                                                                     | Already comfortable with it. Fits both the webapp and the schedulers. Broad ecosystem.                                                                                                                                                 |
| Web framework     | Flask or FastAPI (settling in implementation)                                                  | Both mature, both work. Picking when I start coding.                                                                                                                                                                                   |
| Database          | PostgreSQL                                                                                     | Real production database from day one. Same engine all the way through v1.0 RDS -> no SQLite-to-Postgres migration tax. JSONB and array support cover all my schema needs.                                                             |
| Schema migrations | Alembic                                                                                        | Industry standard for Python + Postgres. Versioned, ordered, reversible migrations checked into git.                                                                                                                                   |
| Scheduler         | APScheduler (v0.1 → v0.4), then AWS EventBridge (v1.0), then Kubernetes CronJob (v1.x)         | Each version uses the simplest scheduler that makes sense for that substrate. APScheduler runs in-process, no infrastructure. EventBridge is cloud-native and managed. CronJob is the K8s primitive. Same problem, evolving solutions. |
| Frontend          | Web app (framework TBD)                                                                        | Picking when the design phase wraps. Probably something boring -> server-rendered with Tailwind, or React + shadcn if SPA-shaped behavior is needed.                                                                                   |
| Deployment        | systemd → Bash → Ansible → Terraform → AWS → EKS                                               | The version ladder above.                                                                                                                                                                                                              |
| Secrets           | `.env` (local) → Ansible Vault (v0.3) → AWS Secrets Manager (v1.0) → Kubernetes Secrets (v1.x) | Evolves with the substrate.                                                                                                                                                                                                            |
| CI                | GitHub Actions from v0.1 (lint + test), expanding to build + deploy at v1.0                    | Same tool throughout.                                                                                                                                                                                                                  |

---

## Components

EPS is broken up into components. Each one handles a small, distinct piece of the system. Together they cover the daily tracking surface.

### Streaks

These are essentially for forming good habits or minimizing bad ones. The idea is, the user defines a streak that they wish to track like sleeping on time or practicing a specific skill or even avoiding drinking too much coffee and then attempts to maintain it for as long a streak as possible.

I have also added a grace mechanic. If a user reaches 7 days on a streak, they get 1 free miss which gets replenished after 7 more days on that streak. This is primarily for things like staying below a calorie amount, where you might splurge a little at times but don't want to punish yourself. This can be disabled though.

### Trackers

Days-since-last-done counters for tasks that are recurring. Some tasks are things that need to be done frequently. Doing the dishes, grocery shopping, working out, etc. A tracker is essentially a recurring task where you set a threshold after which it begins to surface. So if you set it so that you must workout every 3 days, then the tracker will not show the workout task in your daily to do list until it has been 3 days since you last did it.

Trackers can also be configured to only surface on specific weekdays. So a "weekly report" tracker can be set to only show up on Fridays. Same mechanism, just constrained by day of the week.

### Tasks

Here is the main gimmick I suppose of the app. Tasks are individual activities that you have to do. Unlike other to do lists, a task has to have some kind of a scheduled time or a date attached to it. If you use the quick add to create a task, the date of that task will just default to that specific day.

A task can carry one or more of three kinds of dates. A deadline, which is when it has to be done by. The task will show up repeatedly until the day of the deadline, after which it will keep showing but demand input to resolve. A scheduled date, for tasks that you intend to do on a specific day. And a "remind after" date, which hides the task until that date arrives and only then surfaces it.

Recurring activities do not live here. If you want to do something every 3 days or every Friday, that is a tracker, not a task. Tasks are one-shot. You do them, mark them done, and they go away.

Tasks route into one of two sections on the dashboard. Priorities pulls in tasks that are vital, due today or tomorrow, or already overdue. Scheduled pulls in tasks you have scheduled for today and tasks with deadlines 3 to 5 days out.

Past their deadline, tasks get an inline maintenance menu right where they show up. You can mark them done, reconfigure them, or delete them. No need to dig into a settings page to deal with overdue stuff.

If a task has been sitting on your list untouched for over a week, it surfaces with a similar prompt asking if you still want to do it. This is to prevent the task list from clumping up over time when you forget about things.

### Daily Metrics

User-defined per-day quantified data. Two shapes:
- **Scale**: a 1-5 picker. Good for subjective ratings.
- **Numeric**: a number with an optional unit. Good for measurements or counts.

I was thinking of creating specific trackers for sleep or weight or what have you but I realised creating the infrastructure to allow the user to create whichever tracker they wanted would be idea. You may scale your mood, energy, sleep duration, sleep quality or how many red cars you saw on that day. 🚗

### Daily State

A small table holding per-day boolean flags. I am planning in the future to add specific statuses to days like vacation or a rest day which impact how the app displays tasks. This is something that is a bit more tricky to translate from my current AI based system. 

For now there is only one flag: `bad_day`. When you mark a day as bad, any habit checkboxes you didn't tick for that day stop counting as failures. The point is to protect streaks during rough days without forcing you to go back and log everything manually.

### Daily Notes

A freeform text field for each day. Write whatever you want. It's stored as text and nothing parses it. Think of it as a journal for your future self.

### Calendar Integration

This pulls events from Google Calendar for the upcoming week. The data gets cached daily at a time you set, and you can also manually trigger a refresh from the dashboard. Events show up in the Scheduled section with a 📅 marker.

It is intended to be read only. The app does not write anything into the calendar.

To connect it, you go through Google's OAuth flow once during setup. The app stores the refresh and access tokens in its own database and refreshes them automatically before any API call.

I am planning on integrating more calendars once this is handled.

### Weather Integration

This pulls a daily forecast from the BrightSky API. The API is free, requires no authentication, and works off DWD data. The forecast gets cached per day.

A small rule-based engine takes the raw API data and derives a one-line practical recommendation. The rules look like this:

- Rain probability above 50% → "bring an umbrella"
- Max temperature below 5°C → "dress warmly, cold day"
- Max temperature above 25°C → "hot day, stay hydrated"
- Wind speed above 25 km/h → "windy, secure loose items"
- Clear weather with comfortable temperatures → "good weather for a walk"

The rules are tunable in code. There's no AI prose generation here.

### Settings / User Profile

A single-row table holding global preferences. Things like the timezone (default Europe/Berlin), the fetch times for calendar and weather, and the location coordinates for the weather lookup.

### Audit Log

Every time something modifies event-shaped data (habit log entries, tracker events, task changes, metric updates, etc.), a row gets written to the audit log. Captured: the timestamp, the table, the row, the field that changed, the old value, and the new value.

Retention is 180 days. A daily cleanup job deletes anything older. Journal text is exempted from this since it's high-churn and low debug value.

The audit log is being captured even though v0.1 doesn't surface it in the UI. It's there for debugging now and as a ready foundation for a future "edit history" feature.

---

*This document evolves as the project evolves. Last updated 2026-05-XX (design phase complete).*