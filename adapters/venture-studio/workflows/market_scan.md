---
name: market_scan
version: 1
trigger: owner.request
description: Owner asks for market opportunities -> researched, ranked shortlist, owner picks at the checkpoint.
sla_hours: 24
budget:
  max_steps: 20
  max_tokens: 150000
  max_usd: 1.0
steps:
  - id: scan
    agent: research
    action: scan_market
    action_kind: internal
    inputs: []
    output: brief
    done_when: "Brief lists candidate opportunities, each with a demand signal and a rough effort guess."
  - id: shortlist
    agent: maker
    action: rank_opportunities
    action_kind: internal
    inputs: [brief]
    output: shortlist
    done_when: "Ranked shortlist document follows the structure below."
  - id: review
    agent: coordinator
    action: owner_review
    action_kind: internal
    inputs: [shortlist]
    output: decision
    checkpoint: true
    done_when: "Owner reviewed the shortlist and picked a candidate (approve) or discarded the scan (reject)."
---

# SOP: Market scan -> opportunity shortlist

A smart intern following only this document must be able to do the job.

## scan (Research)
Take the owner's request as scope (e.g. "viable apps we can make"). Collect
candidate opportunities. For EACH candidate record: the problem, who has it,
the demand signal (where you saw evidence people want it), existing
alternatives and their weakness, and a rough build effort (S/M/L). Candidates
without a demand signal do not make the list. If the request is too vague to
scope (no category, platform, or audience hints), escalate instead of
guessing.

You have the web_search tool — use it. Every demand signal should come from
something you actually found, with the source noted. Anything you could not
verify with a search gets marked "unverified" in the brief. (On the offline
stub model, searches don't run and output is canned — that's a dev mode, not
a real scan.)

## shortlist (Maker)
Rank the candidates. Output a shortlist document with exactly these sections
per candidate, best first: (1) One-liner, (2) Target user, (3) Demand signal,
(4) Why now, (5) Build effort S/M/L, (6) Monetization, (7) Kill criteria —
what result within 30 days of launch means we drop it. Maximum 5 candidates;
fewer strong ones beat many weak ones.

## review (Coordinator — CHECKPOINT)
Present the shortlist to the owner as an approval card. Approve = owner picked
a direction and the follow-on build workflow (future SOP: ship_product) takes
over from here. Reject = scan discarded; record the owner's note for the next
scan. Nothing external is sent either way — this checkpoint exists because
choosing what to build is an owner decision, not an agent decision.
