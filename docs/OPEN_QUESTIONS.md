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

## Before payload 04 (asked 2026-10-21)
- [ ] **URLs inside answer text.** Today the engine strips every URL from the model's text, even host names copied exactly from a source (e.g. a test site the VPN page tells you to open), so some answers read "go to ." The judge marked two answers down for this. **Recommended:** keep a URL or host name only if it appears **verbatim in one of the chunks given to the model**, render it as plain code text (not a clickable link), and strip all others. Clickable links still come only from index metadata, so the CLAUDE.md grounding rule holds; it gets one added sentence. Alternative: keep stripping everything.
- [ ] **Owner-written eval split.** **Recommended:** instead of rewriting 10 drafted questions, the owner writes ~10 **new** ones (`split: owner`, `author: owner`) in students' real phrasing, from memory and in general terms, never from tickets, including 2 out-of-scope. They are run once after payload 04's fixes and reported as a third, untouched column in the README.

## Not blocking (answer before the payload that needs it)
- [ ] **Name.** "SCIS Help Assistant" for the repo and README; the widget header says "Help Assistant (unofficial)". Assumed OK unless the owner objects.
- [ ] **Licence.** MIT, as on enterprise-ai-ops-suite (payload 04).

## Later / optional
- [ ] **BookStack wiki source (desk mode only).** Needs (a) API access on the owner's BookStack account (check on shift) and (b) the manager's approval in writing, plus a separate OK for sending internal wiki text to the Anthropic API. Design is in ARCHITECTURE.md, "Sources". Not built before the deadline.
- [ ] **Courtesy note to the SCIS web/support team** that a student project crawls the public site at ≤ 1 page/1.5 s. Not required (public pages, no robots rules), but good practice and a good governance line for the README.
