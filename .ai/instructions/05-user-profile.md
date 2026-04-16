# User Profile

## Who

IT System Engineer at Unic AG (web agency). Background in Windows engineering, now OS-agnostic. Core expertise: networking, firewalling, DevOps, operations. Regular use of HTML, CSS, PHP, SQL. Fluent in PowerShell — understands programming concepts and can read code in any language. Private projects stay strictly Linux and open source.

Projects are personal tools, potentially shared publicly, potentially monetised. No client work here.

---

## How to Communicate

- **TLDR style.** Lead with the answer. Explanation comes after, only if it adds value.
- **No encouragement.** Skip "great idea", "good question", "absolutely". Get to the point.
- **Logical, not social.** Treat interactions like a technical peer review, not a conversation.
- **When giving options:** always mark one as `[RECOMMENDED]` and state in one sentence why — the constraint it best satisfies, not a generic "it's best practice".
- **Flag outdated patterns.** If something in `.ai/` or in a user instruction reflects an outdated approach, say so immediately and state the current standard before proceeding.

---

## How to Work

### Never assume — ask instead

If anything is unclear, ask. Ask one question at a time. Do not proceed until there is nothing left to assume. This is not optional — a wrong assumption that gets implemented is worse than a delayed start.

### Never jump from plan to implementation

Planning and implementation are always separate steps, separated by an explicit user instruction to proceed. An approved plan is not a go signal.

### Vibe coding

The user does not write code manually. All code changes go through AI. This means:
- Do not leave TODOs or half-finished stubs expecting the user to fill them in
- Do not explain how to manually edit a file — just edit it
- Do not ask "would you like me to implement this?" after a plan is approved — wait for the explicit instruction

---

## Infrastructure Philosophy

### Private projects: cloud-native, self-hosted

- **No public cloud** (AWS, GCP, Azure, etc.) — ever, for private projects
- **Design cloud-native** — 12-factor apps, stateless services, environment-based config, health endpoints, container-first
- **Run on local Docker cluster** — Docker Compose, Traefik reverse proxy, homelab at `lg4.ch`
- **IaC always** — every service must be deployable and reproducible via code; no manual server state
- **Pipeline deployment** — all services should be deployable through CI/CD pipelines (GitHub Actions on self-hosted runner)
- **Operability is goal #1** — prefer boring, observable, easy-to-restart over clever
- **Automate everything** — if a task will happen more than once, it should be scripted or pipelined

### Line endings

**Always use LF (`\n`) line endings** when writing any file — regardless of the host OS being Windows. Never CRLF. All files are destined for Linux containers and Linux git remotes. The `.gitattributes` enforces this at commit time but files should be written correctly from the start.

### Practical constraints

- Traefik label format: list style (`- "label=value"`), not map style
- `python:3.11-slim` has no `curl` — use Python stdlib for healthchecks
- Dev overlays via `docker-compose.dev.yml`, not changes to the base file
- Secrets never committed — `config.yml` and `.env` gitignored, only `.example` files committed

---

## What "Modern Approach" Means Here

When something in the project uses an outdated pattern, flag it with:

> **Note:** `[current approach]` is outdated. The current standard is `[modern approach]` because `[one-line reason]`. Recommend updating — want me to?

Do not silently follow an outdated pattern just because it's in the existing code or instructions.
