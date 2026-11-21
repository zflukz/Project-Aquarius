---
name: aquarius-env-safety
description: Rules for working with Aquarius Docker / compose configuration without touching the user's private env files. Use before any task that involves docker/.env, compose variables, image registry / tags, passwords, PX4_* / NUM_DRONES / HEADLESS settings, or restarting the stack with different settings.
---

# Aquarius: never read or change the user's `.env`

`docker/.env` (and any other `.env`, `secret/`) is private to the user: passwords, registry, IPs, local choices.
**Never read, grep, cat, print, copy, diff or edit it** — not even "just to check one key". This is enforced by
deny rules in `.claude/settings.json`, but also don't work around them (no `type`, `Get-Content`, `python open()`,
`docker cp`, `env_file` tricks, `docker inspect` / `docker exec … env` dumps, `docker compose config` without `-q`
— all of these print the values).

## What to use instead

| Need | Do this |
| :--- | :--- |
| Which keys exist / their defaults | read `docker/.env.example` (template, committed) and the `${VAR:-default}` in `docker/compose.*.yml` |
| Which value is active right now | ask the user, or look at behaviour that is safe to show (e.g. `docker logs px4-sim` prints "Starting N x model in world W") |
| Validate compose files | `docker compose -f compose.<mode>.yml config -q` (quiet: no values printed) |
| Test with other settings | pass them for one command only: `HEADLESS=1 docker compose … up -d px4-sim` or a compose override file in the scratchpad — never write them into `.env` |
| A setting must change permanently | tell the user the exact line(s) to put in `docker/.env` (or the `aquarius drones N` / `aquarius path …` command that edits it) and let them do it |
| Grafana / DB login for a check | ask the user, or use checks that need no secret (`/api/health`, `docker exec timescaledb psql -U aquarius …` inside the container) |

## When you added a new key

Add it to `docker/.env.example` and `docker/.env.prod` (templates) and both compose files with a safe default, and
tell the user the key exists — `aquarius update` lists keys that are new in `.env.example`.
