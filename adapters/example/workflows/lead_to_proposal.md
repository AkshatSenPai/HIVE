---
name: lead_to_proposal
version: 1
trigger: lead.new
description: New inbound lead -> researched, drafted proposal package, one approval at the end.
sla_hours: 24
budget:
  max_steps: 20
  max_tokens: 150000
  max_usd: 1.0
steps:
  - id: enrich
    agent: research
    action: build_context_brief
    action_kind: internal
    inputs: []
    output: brief
    done_when: "Brief covers who the lead is, what they need, and relevant context."
  - id: draft
    agent: maker
    action: draft_proposal
    action_kind: internal
    inputs: [brief]
    output: proposal
    done_when: "Proposal follows the structure below and quotes per-sq-yd pricing."
  - id: send
    agent: coordinator
    action: send_proposal
    action_kind: send
    inputs: [proposal]
    output: sent_confirmation
    checkpoint: true
    done_when: "Owner approved; proposal delivered to the lead's email."
---

# SOP: Lead → Proposal package

A smart intern following only this document must be able to do the job.

## enrich (Research)
Read the lead message. Identify: company, contact person, space size, location,
timeline, budget signals, and anything referencing our past work. Note what is
MISSING — do not invent facts. If space size or location is absent, flag it as
missing information rather than assuming.

## draft (Maker)
Using the brief and the business profile, draft a proposal with exactly these
sections: (1) What you told us, (2) Our approach, (3) Indicative pricing —
always lead with price-per-sq-yd, (4) Timeline, (5) Next step (a 30-minute
call). Match the voice in profile.md. Keep it under 500 words.

## send (Coordinator — GATED)
Assemble brief + proposal into the package. This step always blocks on an
owner approval card. On approval, the package goes to the lead; on edit,
apply the owner's note and it goes out; on reject, the job is cancelled and
the rejection reason is recorded for reflection.
