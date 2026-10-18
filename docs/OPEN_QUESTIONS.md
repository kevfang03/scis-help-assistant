# Open questions

Answer these in the architect session; the answers become decisions in ARCHITECTURE.md.

## Blocking payload 01
- [x] **Current host of the public support site after the SCIS rename.**
  - **2026-10-18:** `https://support.cci.drexel.edu/` is live and current (titles still say "CCI Technology Services and Support"). `sitemap.xml` lists 692 pages; `robots.txt` returns 404. Details in ARCHITECTURE.md, "The source site".
- [ ] **Owner OK on the proposed decisions** D1–D10 in ARCHITECTURE.md, mainly: local fastembed + BM25 hybrid (D1), FastAPI + vanilla-JS Shadow-DOM widget (D5), sitemap-only crawl at 1.5 s/page (D3), and the payload plan.

## Not blocking (answer before the payload that needs it)
- [ ] **Fallback contact wording (payload 02).** Proposed default: "Talk to a person at the CCI Commons help desk: https://support.cci.drexel.edu/ (\"Talk to a person\")." Alternative: copy the public phone and email from the home page into the setting. Which does the owner want? (Public information either way.)
- [ ] **Eval questions (payload 02).** The worker drafts ~30 questions from the public pages. The owner rewrites about 10 of them in the way students really ask at the desk, **from memory and in general terms, never copied from tickets**, and marks the out-of-scope ones. OK?
- [ ] **Name.** "SCIS Help Assistant" for the repo and README; the widget header says "Help Assistant (unofficial)". OK?
- [ ] **API key.** Reuse the ops-suite key or make a new one? A separate key named for this project, with its own spend limit in the Console, keeps the cost per project visible. The owner stores it with `uv run scis-help llm set-key` (never in chat).
- [ ] **Licence.** MIT, as on enterprise-ai-ops-suite (payload 04).

## Later / optional
- [ ] **BookStack wiki source (desk mode only).** Needs (a) API access on the owner's BookStack account (check on shift) and (b) the manager's approval in writing, plus a separate OK for sending internal wiki text to the Anthropic API. Design is in ARCHITECTURE.md, "Sources". Not built before the deadline.
- [ ] **Courtesy note to the SCIS web/support team** that a student project crawls the public site at ≤ 1 page/1.5 s. Not required (public pages, no robots rules), but good practice and a good governance line for the README.
