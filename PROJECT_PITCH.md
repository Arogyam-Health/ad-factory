# Ad Factory — Project Pitch (What to Speak)

> Use this when someone says: **“Tell me about your project.”**  
> Speak top-to-bottom. Each section is one beat. Don’t jump around.  
> Deep tech dump lives in [`INTERVIEW_README.md`](INTERVIEW_README.md) — only go there if they ask follow-ups.

---

## How to use this

| Situation | What to say |
|-----------|-------------|
| First ask / 60–90 sec | Steps **1 → 4** only |
| They want more depth | Continue **5 → 7** |
| They ask “how does it work technically?” | Step **8** (architecture) |
| They dig into design / tradeoffs | Step **9** |
| Closing | Step **10** |

---

## Step 1 — One-line opener (5 seconds)

> “I built **Ad Factory** — a multi-user platform that turns product truth and buyer personas into **on-brand static ad creatives at scale**, with a rules engine so ads stay compliant and diverse.”

Stop. Let them nod or ask.

---

## Step 2 — The problem (15–20 seconds)

> “In DTC marketing — we were working on an Ayurvedic weight-management kit — the team needed **hundreds** of static ads across **five formats**, **three languages**, and many personas.  
> Doing that by hand is slow. Blindly calling an image model is worse: you get repeated visuals, weak copy, and **non-compliant health claims**.  
> We also couldn’t put all creative bytes and browser automation on a free cloud host — no durable disk, and image tools like ChatGPT/Gemini need a **real logged-in Chrome**.”

**Problem in three bullets (if they interrupt):**
1. Scale of creatives (formats × languages × personas)
2. Quality + compliance + diversity (not random AI slop)
3. Cloud constraints (cost + Chrome/browser reality)

---

## Step 3 — What we built (15 seconds)

> “So Ad Factory is an end-to-end system:  
> **configure product rules → generate ad copy → assemble structured image prompts → generate images → review in a dashboard**,  
> with Google login, orgs/teams, encrypted provider keys, and a super-admin ops panel.”

---

## Step 4 — How we solve it (the product flow) (30–40 seconds)

Speak this as a **pipeline**, not a feature list:

> “The flow is deliberate:  
> **First**, we encode creative rules — product master claims, personas, copy architecture, background slots, and a playbook with validation checks.  
> **Second**, an LLM generates **ad copy** under those rules — headline, support, CTA — not free-form spam.  
> **Third**, we assemble a **nine-section image prompt** — subject, background, composition, style, lighting, color, typography, mood, technical — so every image request is structured.  
> **Fourth**, we render images either via provider APIs or by automating ChatGPT/Gemini in a real browser.  
> **Fifth**, a registry and hypothesis controls help us avoid duplicates and run A/B-style creative tests.”

**If they only want business value:** stop after “structured prompts + compliant copy + scalable formats.”

---

## Step 5 — What makes it non-trivial (20–30 seconds)

> “The interesting part isn’t ‘call DALL·E.’ It’s three hard pieces together:  
> 1. A **creative rules engine** so outputs stay on-brand and claim-safe.  
> 2. A **multi-tenant product** — auth, orgs with roles, shared vs individual configs, versioning and rollback.  
> 3. A **local-first architecture** — the cloud holds control metadata; the user’s machine holds uploads, prompts, images, and runs the browser automation.”

---

## Step 6 — Architecture in plain English (30–45 seconds)

Only if they ask “how is it designed?” or you have time:

> “Production is split into two planes.  
> **Render** is a **stateless control plane**: Google OAuth, MongoDB for users/orgs/jobs, and eight small config files so people can edit rules after login.  
> The **laptop** is the **data plane**: a local agent stores content, serves the dashboard over localhost, calls providers, and drives Chrome via CDP.  
> The browser talks to Render for metadata, and to `localhost` for actual files — after a pairing challenge, so another machine can’t steal that content.  
> Old content APIs on Render return **410**, so the boundary is enforced, not just documented. We deliberately skip Cloudinary, Redis, and paid disks because of free-tier and privacy constraints.”

**Analogy you can use:**
> “Think of Render as the **air traffic control tower** — who’s flying, what job is assigned — and the laptop as the **hangar** where the planes and cargo actually live.”

---

## Step 7 — Stack (10 seconds)

> “Backend is **FastAPI**, DB is **MongoDB Atlas**, frontend is **vanilla JS** served by FastAPI — no React build. Auth is Google OAuth with HttpOnly session cookies. Secrets use Fernet encryption. Automation is Playwright plus Chrome DevTools Protocol.”

---

## Step 8 — One concrete walkthrough (optional, 40 seconds)

Use when they say “walk me through a user journey”:

> “User logs in with Google. On the same machine they start the local agent and pair the dashboard.  
> They allocate a run on Render — that’s just metadata.  
> They upload product images to localhost, not through the cloud.  
> They kick off **structured copy**: Render plans and validates; the agent makes the provider HTTPS call; final prompts are delivered encrypted to the laptop and decrypted locally.  
> Then they enqueue **image generation**: the agent claims a device-pinned job, opens Chrome, pastes prompts, downloads PNGs locally.  
> The UI shows images by fetching from localhost as Blob URLs. Render never stored the creative bytes.”

---

## Step 9 — Design decisions / tradeoffs (if asked “why?”)

Pick 2–3, don’t recite all:

| They ask | You say |
|----------|---------|
| Why local-first? | Free Render has no durable content disk; browser UIs need real Chrome; keep creative bytes off the cloud. |
| Why not just S3/Cloudinary? | Cost + policy. Metadata and hashes in Mongo are enough for listing; bytes stay on-device. |
| Why Mongo? | Flexible docs for configs/orgs/jobs; TTL for sessions/jobs; free Atlas. |
| Why vanilla JS? | Zero build, FastAPI static mount, small ops UI; modules already split by domain. |
| Why agents separate? | Automation can’t run on Render; needs logged-in Chrome + local disk. |
| Biggest risk? | UI automation fragility — mitigated with readiness gates, idempotent jobs, resume from local state. |
| What you’d improve? | If multi-device cloud sync became a product requirement, add object storage with a new threat model; React only if UI complexity forces it. |

---

## Step 10 — Close (10 seconds)

> “So in short: Ad Factory solves **scaled, compliant ad creative generation** by combining a **rules-heavy pipeline** with a **multi-user control plane** and a **local data plane** that owns content and browser automation.”

Then stop. Wait for questions.

---

## 60-second script (read aloud practice)

> “I built Ad Factory for DTC ad creatives. The problem was generating many static ads across formats and languages without inventing illegal claims or repeating the same creative.  
> The system encodes product rules and personas, generates structured ad copy, assembles nine-section image prompts, then renders images via APIs or browser automation.  
> Technically it’s FastAPI and MongoDB on Render for auth, orgs, and job metadata, plus a local agent on the user’s laptop for files and Chrome automation — because free cloud hosting can’t own that content or run that browser.  
> Teams get Google login, shared configs, versioning, and an admin readiness dashboard. The hard part was making the creative rules and the cloud/local split both production-safe.”

---

## 2-minute script (if they give you room)

1. Opener (Step 1)  
2. Problem (Step 2)  
3. Solution pipeline (Step 4)  
4. Why it’s hard (Step 5)  
5. Architecture sketch (Step 6)  
6. Close (Step 10)

Do **not** list every API, collection, or file unless they ask.

---

## Questions to expect next (and where to go)

| Follow-up | Answer from |
|-----------|-------------|
| Auth / sessions / orgs | `INTERVIEW_README.md` → Auth, Organizations |
| Data flow / relay / pairing | `INTERVIEW_README.md` → Architecture, Pipeline |
| Security | `INTERVIEW_README.md` → Security Topics |
| Creative rules / formats | Playbook + Creative Rules section |
| Deploy / free tier | Local-first ops + Tradeoffs |

---

## Anti-patterns (don’t do these)

- Don’t start with folder structure or “I used FastAPI and MongoDB…”
- Don’t explain Cloudinary / Redis — that’s **not** the production design
- Don’t dump every endpoint
- Don’t say “AI generates ads” without mentioning **rules + compliance + diversity**
- Don’t skip the **problem** — interviewers grade clarity of problem framing first

---

*Companion to [`INTERVIEW_README.md`](INTERVIEW_README.md). This file is for speaking; that file is for answering deep follow-ups.*
