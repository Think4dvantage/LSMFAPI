# Prompt: Update Human-Readable Files

> **Purpose**: Sync all human-readable documentation files from the current state of `.ai/`.
> **Use when**: After any session that changed architecture, shipped features, or updated the roadmap. Also called automatically by `sync.md`.
> **Source of truth**: `.ai/context/` and `.ai/instructions/` — never the other way around.

---

## Rule

Human-readable files are **derived output**. If they conflict with `.ai/`, `.ai/` wins. Rewrite the human-readable file to match.

---

## Step 1 — Identify Which Files Exist

Check for these files at the repo root and in `docs/`:

| File | Purpose |
|---|---|
| `README.md` | Primary project overview for humans and GitHub |
| `PLANNING.md` | Roadmap and current project status |
| `docs/*.md` | Any supplementary documentation |

Only update files that already exist. **Do not create new documentation files** unless explicitly asked.

---

## Step 2 — Update `README.md`

Read the current `README.md`. Then read:
- `.ai/instructions/01-project-overview.md` — project description, tech stack, layout
- `.ai/context/architecture.md` — API contracts, deployment notes
- `.ai/context/features.md` — shipped milestones

### What matters: process over tech stack

**The README is about how the project works, not what it is built with.**

The reader cares about:
- What the system does and why
- How data flows through it — what triggers what, what feeds what
- What a user can do and how they do it
- What has shipped and what is coming next
- How to get it running

The reader does not need to know:
- Which Python web framework handles routing
- Which ORM is used
- Which charting library renders graphs
- Which container runtime is used

**Tech stack belongs in a small, collapsed, or footnote-style section** — not the headline content. If a tech stack table already exists in the README, keep it but don't expand it. If it doesn't exist, don't add one.

Lead with behaviour and process. Describe the data flow in plain language. Explain what each part of the system does for the user, not how it is implemented.

### Section mapping

Update these sections if they exist (match the headings already present — do not impose a structure that isn't there):

| README section | Source in `.ai/` | Focus |
|---|---|---|
| Project description / "What is this?" | `01-project-overview.md` → "What This Is" | What it does, who uses it, why it exists |
| How it works / Data flow | `01-project-overview.md` → "Data Flow" | Plain-language walkthrough of the process |
| What you can do / Features | `context/features.md` → "Shipped Milestones" | User-facing capabilities, not internal mechanics |
| Getting started | `01-project-overview.md` + `context/architecture.md` deployment section | Steps to run it, nothing more |
| Roadmap / what's next | `context/features.md` → "Backlog" | What's coming, described as outcomes not tasks |
| Tech stack *(if already present)* | `01-project-overview.md` → "Tech Stack" | One compact table, no elaboration |

**Rules for editing README.md**:
- Preserve any human-written sections that have no `.ai/` equivalent (contributing guides, license, credits, etc.)
- Match the existing tone and heading style — do not reformat the whole file
- Keep it concise — README is a human overview, not a copy of the full `.ai/` content
- If the existing README is very tech-stack-heavy, gently rebalance it toward process and outcomes without deleting anything the owner clearly wrote intentionally

---

## Step 3 — Update `PLANNING.md` (if it exists)

Read the current `PLANNING.md`. Then read `.ai/context/features.md`.

`PLANNING.md` must always contain:
1. **Current Status** — one-line summary of where the project is right now
2. **Version History** — brief log of released versions
3. **Active Work** — what is currently in progress
4. **Roadmap** — phases with status icons (✅ Done, 🚧 In Progress, 🎯 Next, 📋 Planned)
5. **Next Steps** — the next 2–3 concrete actions to take
6. **Spec Index** — links to all specs in `specs/` (if the folder exists)

Write `PLANNING.md` for humans:
- No technical jargon without explanation
- Use concrete dates, not "next sprint"
- Keep it scannable — short bullets, not paragraphs

---

## Step 4 — Update `docs/` files (if any exist)

For each file found in `docs/`, check if its content has a corresponding section in `.ai/context/architecture.md`. If yes and the data has changed, update it. If the doc covers something not in `.ai/`, leave it alone.

---

## Step 5 — Report

List every file that was changed and briefly describe what was updated in each:

```
README.md       — updated tech stack table, added 2 new API routes
PLANNING.md     — ticked off v0.2 milestone, updated Next Steps
```

If nothing needed updating, say so explicitly: "All human-readable files are already in sync."
