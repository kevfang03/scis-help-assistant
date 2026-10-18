# Open questions

Answer these in the architect session; the answers become decisions in ARCHITECTURE.md.

## Answered
- [x] **Current host of the public support site after the SCIS rename.**
  - **2026-10-18:** `https://support.cci.drexel.edu/` is live and current (titles still say "CCI Technology Services and Support"). `sitemap.xml` lists 692 pages; `robots.txt` returns 404. Details in ARCHITECTURE.md, "The source site".
- [x] **Owner OK on decisions D1–D10 and the payload plan.** **2026-10-18:** approved as proposed.
- [x] **Fallback contact wording.** **2026-10-18:** link to the support home page's "Talk to a person" section; no copied phone or email.
- [x] **Eval questions.** **2026-10-18:** the worker drafts ~30 from the public pages; the owner rewrites about 10 later in students' real phrasing, from memory and in general terms, never copied from tickets.
- [x] **API key.** **2026-10-18:** a new key for this project with its own spend limit. The owner stores it with `uv run scis-help llm set-key` (never in chat).
- [x] **Git identity.** **2026-10-18:** the owner set `user.name` / `user.email` in this repo's local config.

## Not blocking (answer before the payload that needs it)
- [ ] **Name.** "SCIS Help Assistant" for the repo and README; the widget header says "Help Assistant (unofficial)". Assumed OK unless the owner objects.
- [ ] **Licence.** MIT, as on enterprise-ai-ops-suite (payload 04).

## Later / optional
- [ ] **BookStack wiki source (desk mode only).** Needs (a) API access on the owner's BookStack account (check on shift) and (b) the manager's approval in writing, plus a separate OK for sending internal wiki text to the Anthropic API. Design is in ARCHITECTURE.md, "Sources". Not built before the deadline.
- [ ] **Courtesy note to the SCIS web/support team** that a student project crawls the public site at ≤ 1 page/1.5 s. Not required (public pages, no robots rules), but good practice and a good governance line for the README.
