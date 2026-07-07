/* HIVE API client — shared across all dashboard pages.
 * Tries the real relative endpoint first; if no live `hive serve` backend is
 * reachable it transparently falls back to a self-contained in-memory mock so
 * the design is fully reviewable AND still works live. All mock data mirrors
 * the exact shapes in DESIGN_PROMPT.md. window.HiveAPI.req(path, opts) ->
 *   { ok, status, data, live }
 */
(function () {
  'use strict';

  var ADAPTER = 'offline-stub';
  var NOW = Date.now();
  var iso = function (ms) { return new Date(ms).toISOString(); };
  var mins = function (m) { return NOW - m * 60000; };
  var secs = function (s) { return NOW - s * 1000; };
  var r2 = function (n) { return Math.round(n * 100) / 100; };
  var ri = function (a, b) { return a + Math.floor(Math.random() * (b - a + 1)); };
  var pick = function (a) { return a[Math.floor(Math.random() * a.length)]; };
  var id4 = function () { return Math.random().toString(16).slice(2, 6); };

  /* ---------------- static config ---------------- */
  var agents = [
    { name: 'coordinator', tier: 'coordinator', model: 'claude-opus-4-8',
      tools: ['plan', 'review', 'delegate', 'escalate'], steps: [] },
    { name: 'scout', tier: 'specialist', model: 'claude-sonnet-5',
      tools: ['web_search', 'crm_lookup', 'read_vault'],
      steps: ['lead_to_proposal.enrich', 'support_triage.classify'] },
    { name: 'scribe', tier: 'specialist', model: 'claude-sonnet-5',
      tools: ['write_draft', 'read_vault'],
      steps: ['lead_to_proposal.draft', 'content_pipeline.write', 'support_triage.respond'] },
    { name: 'ledger', tier: 'specialist', model: 'claude-haiku-4-5',
      tools: ['read_ledger', 'price_quote', 'charge_card'],
      steps: ['lead_to_proposal.price', 'invoice_run.compile', 'invoice_run.charge'] },
    { name: 'envoy', tier: 'specialist', model: 'claude-sonnet-5',
      tools: ['send_email', 'publish_cms'],
      steps: ['lead_to_proposal.send', 'content_pipeline.publish'] },
    { name: 'sentinel', tier: 'frontier', model: 'claude-fable-5',
      tools: ['deep_research', 'synthesize'],
      steps: ['content_pipeline.research'] }
  ];

  var workflows = [
    { name: 'lead_to_proposal', version: 3, trigger: 'owner.request',
      description: 'Enrich an inbound lead, draft a tailored proposal, price it, and send once approved.',
      keywords: ['proposal', 'lead', 'quote', 'pitch', 'deal', 'prospect', 'northwind'],
      steps: [
        { id: 'enrich', agent: 'scout', action_kind: 'internal', checkpoint: false },
        { id: 'draft', agent: 'scribe', action_kind: 'internal', checkpoint: false },
        { id: 'price', agent: 'ledger', action_kind: 'internal', checkpoint: false },
        { id: 'send', agent: 'envoy', action_kind: 'send', checkpoint: true }
      ] },
    { name: 'content_pipeline', version: 2, trigger: 'owner.request',
      description: 'Research a topic, write a post, and publish it to the CMS after review.',
      keywords: ['blog', 'post', 'content', 'article', 'write', 'publish', 'playbook'],
      steps: [
        { id: 'research', agent: 'sentinel', action_kind: 'internal', checkpoint: false },
        { id: 'write', agent: 'scribe', action_kind: 'internal', checkpoint: false },
        { id: 'publish', agent: 'envoy', action_kind: 'publish', checkpoint: true }
      ] },
    { name: 'invoice_run', version: 1, trigger: 'schedule.daily',
      description: "Compile the day's billable work and charge cards on file.",
      keywords: ['invoice', 'bill', 'charge', 'payment', 'collect', 'receivable'],
      steps: [
        { id: 'compile', agent: 'ledger', action_kind: 'internal', checkpoint: false },
        { id: 'charge', agent: 'ledger', action_kind: 'spend', checkpoint: true }
      ] },
    { name: 'support_triage', version: 2, trigger: 'inbox.message',
      description: 'Classify an inbound support message and draft a reply for approval.',
      keywords: ['support', 'ticket', 'bug', 'issue', 'customer', 'reply', 'refund'],
      steps: [
        { id: 'classify', agent: 'scout', action_kind: 'internal', checkpoint: false },
        { id: 'respond', agent: 'scribe', action_kind: 'send', checkpoint: true }
      ] }
  ];

  var LIFE = ['queued', 'planning', 'executing', 'reviewing'];

  /* ---------------- semantic vault (Obsidian-compatible markdown) ---------------- */
  var VAULT_ROOT = '/Users/akshat/hive/vault';
  var vaultFiles = {
    'README.md': [
      '# HIVE Vault', '',
      'The shared memory for the hive — plain markdown, Obsidian-compatible. Agents read from here to ground their work and write notes back as they learn.', '',
      '## Structure',
      '- **clients/** — one note per account: context, contacts, deal state',
      '- **playbooks/** — how we run recurring work',
      '- **finance/** — pricing and billing reference',
      '- **notes/** — reusable guidance (tone, objections)', '',
      'Keep entries short and factual. Link between notes where it helps, e.g. the [pricing sheet](finance/pricing.md).'
    ].join('\n'),
    'clients/northwind.md': [
      '# Northwind Trading', '',
      '**Status:** active lead · proposal drafted',
      '**Primary contact:** Dana Reyes — procurement@northwind.example',
      '**Team size:** ~40', '',
      '## Context',
      'Inbound lead from the June webinar. Wants to automate lead intake, proposal generation, and invoicing. Asked for a same-week turnaround on a quote.', '',
      '## Scope discussed',
      '- Lead enrichment from the CRM',
      '- Proposal drafting with pricing',
      '- Monthly invoice runs', '',
      '## Pricing',
      'Quoted **$18,000** setup + **$2,400/mo**. Anchored to the [proposals playbook](playbooks/proposals.md).', '',
      '## Open questions',
      '- Confirm the ops lead who signs off',
      '- Data residency requirements?'
    ].join('\n'),
    'clients/acme.md': [
      '# Acme Corp', '',
      '**Status:** customer · live 3 months',
      '**Primary contact:** Sam Okonkwo', '',
      '## Context',
      'First customer on the content pipeline. Publishes two posts a week. Low-touch, pays on time.', '',
      '## Notes',
      '- Prefers a formal tone — see [tone of voice](notes/tone-of-voice.md)',
      '- Invoice `INV-1043` outstanding this cycle'
    ].join('\n'),
    'clients/delta-labs.md': [
      '# Delta Labs', '',
      '**Status:** customer · support-heavy',
      '**Primary contact:** Priya Nair', '',
      '## Context',
      'Uses HIVE for support triage. Filed a refund request this week, handled via `support_triage`. Watch for churn signals.', '',
      '## Recent',
      '- Refund request — draft reply pending approval',
      '- Escalation last month resolved in 2 days'
    ].join('\n'),
    'playbooks/proposals.md': [
      '# Playbook: Proposals', '',
      'How `lead_to_proposal` should read and behave.', '',
      '## Principles',
      '1. Enrich before drafting — never quote blind.',
      '2. Anchor pricing to the [pricing sheet](finance/pricing.md).',
      '3. Always end with a single clear next step.', '',
      '## Structure',
      '- Opening: reference the last conversation',
      '- Scope: 3–5 bullets, their words not ours',
      '- Pricing: setup + monthly, no hidden lines',
      '- Close: propose a specific time', '',
      'The **send** step is always owner-approved.'
    ].join('\n'),
    'playbooks/content.md': [
      '# Playbook: Content', '',
      'How `content_pipeline` researches, writes, and publishes.', '',
      '## Research',
      'Pull from at least five sources. Prefer primary sources over blog roundups.', '',
      '## Writing',
      '- Lead with the reader\u2019s problem',
      '- Short paragraphs, concrete examples',
      '- One idea per section', '',
      '## Publishing',
      'The **publish** step is gated — nothing goes live without owner approval.'
    ].join('\n'),
    'playbooks/support.md': [
      '# Playbook: Support Triage', '',
      'How `support_triage` classifies and responds.', '',
      '## Classify',
      'Tag each message: question, bug, billing, or refund.', '',
      '## Respond',
      '- Acknowledge fast, resolve clearly',
      '- For refunds, state the amount and the timeline',
      '- Never promise what we can\u2019t ship', '',
      'Replies are owner-approved before they send.'
    ].join('\n'),
    'finance/pricing.md': [
      '# Pricing Sheet', '',
      '**Setup:** from $12,000 (scales with workflow count)',
      '**Monthly:** from $1,800/mo', '',
      '## Tiers',
      '- Starter — 1 workflow — $12,000 + $1,800/mo',
      '- Growth — 3 workflows — $18,000 + $2,400/mo',
      '- Scale — custom — talk to the owner', '',
      'Discounts above 15% are **owner-approved only**.'
    ].join('\n'),
    'finance/invoicing.md': [
      '# Invoicing', '',
      '`invoice_run` compiles the day\u2019s billable work and charges cards on file.', '',
      '## Rules',
      '- Charge only after work is delivered',
      '- The **charge** step is always gated',
      '- Receipts email automatically on success', '',
      'Failed charges retry three times, then escalate.'
    ].join('\n'),
    'notes/tone-of-voice.md': [
      '# Tone of Voice', '',
      'Calm, direct, competent. We sound like a senior operator, not a chatbot.', '',
      '## Do',
      '- Short sentences',
      '- Plain words',
      '- Say the number', '',
      '## Don\u2019t',
      '- Hype or exclamation marks',
      '- Filler like \u201cjust\u201d and \u201csimply\u201d'
    ].join('\n'),
    'notes/objections.md': [
      '# Common Objections', '',
      '## \u201cToo expensive\u201d',
      'Reframe on time saved. One workflow usually pays back in a month.', '',
      '## \u201cWe\u2019ll build it ourselves\u201d',
      'Fine — offer the playbooks as a starting point, keep the door open.', '',
      '## \u201cNot sure it\u2019s safe\u201d',
      'Every consequential action is owner-approved. Point to the [support playbook](playbooks/support.md).'
    ].join('\n')
  };

  /* ---------------- mutable state ---------------- */
  var jobs = [
    j('job_4a71', 'content_pipeline', 'executing', 3, 9200, 0.31, 0, null, secs(2), 1),
    j('job_9c2f', 'lead_to_proposal', 'awaiting_approval', 8, 18400, 0.62, 0, null, mins(4), 3),
    j('job_a1e4', 'support_triage', 'reviewing', 2, 3100, 0.09, 0, null, secs(38), 1),
    j('job_b3d7', 'lead_to_proposal', 'planning', 1, 900, 0.03, 0, null, secs(20), 3),
    j('job_c8f2', 'invoice_run', 'queued', 0, 0, 0.00, 0, null, secs(6), 0),
    j('job_7be3', 'invoice_run', 'awaiting_approval', 22, 4200, 0.12, 0, null, mins(19), 1),
    j('job_1d08', 'support_triage', 'awaiting_approval', 14, 6100, 0.18, 1, null, mins(11), 1),
    j('job_2f55', 'lead_to_proposal', 'done', 51, 41200, 1.38, 2, null, mins(44), 3),
    j('job_8ea0', 'content_pipeline', 'done', 74, 63800, 2.11, 1, null, mins(69), 2),
    j('job_3c19', 'support_triage', 'done', 96, 5200, 0.16, 1, null, mins(93), 1),
    j('job_5b7c', 'lead_to_proposal', 'escalated', 133, 22800, 0.76, 1,
      'conflicting pricing signals — the CRM and the ledger disagree on the account tier', mins(129), 2),
    j('job_6d42', 'invoice_run', 'failed', 120, 2100, 0.07, 0, 'payment gateway timeout (503) after 3 retries', mins(118), 0),
    j('job_0af9', 'content_pipeline', 'cancelled', 165, 1400, 0.05, 1, null, mins(160), 2)
  ];
  function j(id, wf, state, ago, tok, usd, touches, err, updatedMs, stepIdx) {
    var w = workflows.filter(function (x) { return x.name === wf; })[0];
    return {
      id: id, workflow: wf, adapter: ADAPTER, state: state,
      created_at: iso(mins(ago)), updated_at: iso(updatedMs),
      context: { subject: subjectFor(wf) },
      artifact_ids: [], spend_tokens: tok, spend_usd: usd,
      owner_touches: touches, error: err || null,
      _step: stepIdx, _steps: w ? w.steps.length : 1, _wf: w
    };
  }
  function subjectFor(wf) {
    return {
      lead_to_proposal: 'Prepare a proposal for the Northwind Trading lead',
      content_pipeline: 'Draft the Q3 automation playbook post',
      invoice_run: "Run today's invoice batch",
      support_triage: 'Handle the refund request from Delta Labs'
    }[wf] || wf;
  }

  var traces = {
    job_4a71: [
      t(secs(180), 'job_created', 'coordinator', 140, 0.004, { subject: 'Draft the Q3 automation playbook post' }),
      t(secs(176), 'plan', 'coordinator', 260, 0.008, { steps: ['research', 'write', 'publish'] }),
      t(secs(150), 'model_call', 'sentinel', 5400, 0.18, { step: 'research', tool: 'deep_research' }),
      t(secs(120), 'tool_result', 'sentinel', 0, 0.0, { sources: 7 }),
      t(secs(70), 'model_call', 'scribe', 3100, 0.10, { step: 'write' }),
      t(secs(12), 'model_call', 'scribe', 600, 0.02, { step: 'write', note: 'revising the intro' })
    ]
  };
  function t(ms, type, agent, tokens, usd, payload) {
    return { ts: iso(ms), type: type, agent: agent, tokens: tokens, usd: usd, payload: payload || {} };
  }

  var cards = [
    { id: 'card_p1', job_id: 'job_9c2f', step_key: 'lead_to_proposal.send', action_kind: 'send',
      title: 'Send proposal to Northwind Trading', artifact_id: 'art_1',
      cost_so_far_usd: 0.62, status: 'pending', created_at: iso(mins(4)), decided_at: null, owner_note: '',
      reasoning: 'The lead is enriched and the proposal is drafted and priced at $18,000. Northwind requested a quote two days ago and asked for a same-week turnaround, so sending now keeps us inside their stated window.',
      downstream_effects: [
        'Emails proposal.pdf to procurement@northwind.example',
        'Logs the send in the CRM and starts a 5-day follow-up timer',
        'Commits $18,000 as the official quoted price'
      ],
      artifact_preview: { artifact_type: 'Draft', kind: 'email', title: 'Proposal — Northwind Trading',
        body: 'Subject: Your automation proposal from HIVE\n\nHi Dana,\n\nThanks for the call on Tuesday. Based on your team of 40 and the three workflows we scoped, here is a proposal to automate lead intake, proposal generation, and invoicing.\n\nScope & pricing: $18,000 one-time setup, then $2,400/mo.\nTimeline: live in 3 weeks.\n\nThe full breakdown is attached. Happy to walk your ops lead through it this week.\n\n— Sent on behalf of Akshat, HIVE' } },
    { id: 'card_p2', job_id: 'job_8ea0b', step_key: 'content_pipeline.publish', action_kind: 'publish',
      title: "Publish 'The Automation Playbook' to the blog", artifact_id: 'art_2',
      cost_so_far_usd: 0.88, status: 'pending', created_at: iso(mins(6)), decided_at: null, owner_note: '',
      reasoning: 'The post is researched from 7 sources, written, and passed review. It aligns with the Q3 content calendar slot for Thursday and is ready to ship.',
      downstream_effects: [
        'Publishes to /blog, live for all visitors immediately',
        'Triggers the newsletter send to 4,120 subscribers',
        'Cannot be fully pulled from CDN caches right away'
      ],
      artifact_preview: { artifact_type: 'Draft', kind: 'article', title: 'The Automation Playbook',
        body: '# The Automation Playbook\n\nMost teams automate the wrong things first. They chase the flashy demo instead of the boring, repeated task that quietly eats a day a week.\n\n## Start where the pain repeats\n\nThe best first automation is the one you have done by hand more than fifty times. You already know the edge cases, so you can judge the output.\n\n## Keep a human on the consequential steps\n\nAutomate the drafting. Gate the sending. The owner should approve anything that spends money, contacts a customer, or ships in public.' } },
    { id: 'card_p3', job_id: 'job_7be3', step_key: 'invoice_run.charge', action_kind: 'spend',
      title: 'Charge $2,400 across 3 cards on file', artifact_id: 'art_3',
      cost_so_far_usd: 0.15, status: 'pending', created_at: iso(mins(19)), decided_at: null, owner_note: '',
      reasoning: "Today's invoice batch compiled cleanly. Three invoices are due and the cards on file are valid. Total to charge is $2,400.00.",
      downstream_effects: [
        'Charges 3 customer cards totaling $2,400.00',
        'Emails receipts automatically to each customer',
        'Marks 3 invoices as paid in the ledger'
      ],
      artifact_preview: { artifact_type: 'Deliverable', title: 'Invoice batch — 2026-07-06',
        summary: '3 invoices due · $2,400.00 total',
        parts: ['INV-1043 · Acme Corp · $900.00', 'INV-1044 · Delta Labs · $750.00', 'INV-1045 · Northwind · $750.00'] } }
  ];

  /* ---------------- artifacts + trace generators (Tasks page detail) ---------------- */
  var PLAN_GOAL = {
    lead_to_proposal: 'Win the Northwind Trading account with a tailored, priced proposal.',
    content_pipeline: 'Research, write, and publish the Q3 automation playbook post.',
    invoice_run: "Compile today's due invoices and collect payment.",
    support_triage: 'Resolve the Delta Labs refund request with a customer reply.'
  };
  var PLAN_NOTES = {
    lead_to_proposal: 'The send step is gated — the proposal will not leave until you approve it.',
    content_pipeline: 'The publish step is gated — nothing goes live without your approval.',
    invoice_run: 'The charge step is gated — no cards are charged until you approve.',
    support_triage: 'The reply is gated — the message is held for your approval before sending.'
  };
  var ESCALATION_Q = {
    lead_to_proposal: 'The CRM lists Northwind as Tier 2, but the ledger shows Tier 1 pricing. Which tier should the proposal use?'
  };
  // one entry per workflow step, in order; null where a step produces no new artifact
  var ARTS = {
    lead_to_proposal: [
      { artifact_type: 'Brief', subject: 'Northwind Trading — lead enrichment',
        summary: 'Mid-market logistics firm (~40 staff) evaluating automation for lead intake, proposals, and invoicing. Warm inbound via the website.',
        findings: ['Team of ~40; ops-led buying committee', 'Runs on spreadsheets + manual email today', 'Budget signal: $15k–$25k setup range', 'Timeline: wants to decide this quarter', 'Champion: Dana Reyes, Head of Operations', 'Evaluated two competitors — both judged too heavy'], confidence: 0.82 },
      { artifact_type: 'Draft', kind: 'email', title: 'Proposal — Northwind Trading',
        body: 'Subject: Your automation proposal from HIVE\n\nHi Dana,\n\nThanks for the call on Tuesday. Based on your team of 40 and the three workflows we scoped, here is a proposal to automate lead intake, proposal generation, and invoicing.\n\nScope & pricing: $18,000 one-time setup, then $2,400/mo.\nTimeline: live in 3 weeks.\n\nThe full breakdown is attached. Happy to walk your ops lead through it this week.\n\n— Sent on behalf of Akshat, HIVE' },
      { artifact_type: 'Deliverable', title: 'Pricing — Northwind Trading',
        summary: '$18,000 one-time setup + $2,400/mo · go-live in 3 weeks',
        parts: ['Setup — 3 workflows automated: $18,000', 'Monthly automation & support: $2,400/mo', 'Estimated go-live: 3 weeks', 'Assumes 1 CRM + 1 mailbox integration'] },
      null
    ],
    content_pipeline: [
      { artifact_type: 'Brief', subject: 'Research — the automation playbook',
        summary: 'Synthesized 7 sources on where teams get the most from early automation.',
        findings: ['Teams over-automate flashy demos and under-automate repetitive work', 'Best first target: a task done by hand 50+ times', 'Human-in-the-loop on consequential steps is the trust unlock', 'ROI shows up as reclaimed hours, not headcount cuts', 'Publishing cadence matters more than post length'], confidence: 0.76 },
      { artifact_type: 'Draft', kind: 'article', title: 'The Automation Playbook',
        body: '# The Automation Playbook\n\nMost teams automate the wrong things first. They chase the flashy demo instead of the boring, repeated task that quietly eats a day a week.\n\n## Start where the pain repeats\n\nThe best first automation is the one you have done by hand more than fifty times. You already know the edge cases, so you can judge the output.\n\n## Keep a human on the consequential steps\n\nAutomate the drafting. Gate the sending. The owner should approve anything that spends money, contacts a customer, or ships in public.' },
      null
    ],
    invoice_run: [
      { artifact_type: 'Deliverable', title: 'Invoice batch — today',
        summary: '3 invoices due · $2,400.00 total',
        parts: ['INV-1043 · Acme Corp · $900.00', 'INV-1044 · Delta Labs · $750.00', 'INV-1045 · Northwind · $750.00'] },
      null
    ],
    support_triage: [
      { artifact_type: 'Brief', subject: 'Triage — Delta Labs refund request',
        summary: 'Billing complaint: customer reports a duplicate charge on their annual renewal.',
        findings: ['Sentiment: frustrated but civil', 'Category: billing / refund', 'Account in good standing for 2 years', 'Duplicate charge confirmed in the ledger', 'Recommended resolution: full refund of the duplicate'], confidence: 0.88 },
      { artifact_type: 'Draft', kind: 'email', title: 'Reply — Delta Labs refund',
        body: 'Subject: Re: Double charge on renewal\n\nHi Sam,\n\nYou’re right — I can see two charges for your annual renewal on the same day, and I’m sorry for the trouble. I’ve queued a full refund of the duplicate ($750.00); it should land in 3–5 business days.\n\nNothing else on your account is affected, and your renewal is active through next year.\n\n— Sent on behalf of Akshat, HIVE' }
    ]
  };

  function artifactsFor(job) {
    var w = job._wf, stepArts = ARTS[job.workflow] || [], out = [], len = stepArts.length;
    var done = (job.state === 'queued' || job.state === 'planning') ? 0
      : (job.state === 'done') ? len
      : Math.min(len, (job._step || 0) + 1);
    if (job.state !== 'queued') {
      out.push({ artifact_type: 'Plan', goal: PLAN_GOAL[job.workflow] || (job.context && job.context.subject) || job.workflow,
        steps: (w ? w.steps : []).map(function (s) { return { id: s.id, agent: s.agent, action: s.action_kind, checkpoint: !!(s.checkpoint || s.action_kind !== 'internal') }; }),
        notes: PLAN_NOTES[job.workflow] || '' });
    }
    for (var i = 0; i < done; i++) { if (stepArts[i]) out.push(clone(stepArts[i])); }
    if (job.state === 'escalated') out.push({ artifact_type: 'Escalation', reason: job.error || 'Review needs owner input.', question: ESCALATION_Q[job.workflow] || 'How should the agents proceed?' });
    out.forEach(function (a, idx) { a.id = 'art_' + job.id + '_' + idx; });
    return out;
  }

  function traceFor(job) {
    if (traces[job.id] && traces[job.id].length) return traces[job.id];
    var w = job._wf, steps = w ? w.steps : [], arr = [];
    var start = new Date(job.created_at).getTime();
    var end = Math.max(start + 1000, new Date(job.updated_at).getTime());
    var slots = 3 + steps.length, span = (end - start) / (slots + 1), ti = start;
    var at = function () { ti += span; return ti; };
    arr.push(t(at(), 'job_created', 'coordinator', 140, 0.004, { subject: (job.context && job.context.subject) || '' }));
    arr.push(t(at(), 'plan', 'coordinator', ri(200, 320), 0.008, { workflow: job.workflow }));
    var done = (job.state === 'queued') ? 0 : (job.state === 'done') ? steps.length : Math.min(steps.length, (job._step || 0) + 1);
    for (var i = 0; i < done; i++) {
      var s = steps[i];
      arr.push(t(at(), 'model_call', s.agent, ri(600, 4200), r2(ri(3, 18) / 100), { step: s.id }));
      if (s.action_kind !== 'internal') arr.push(t(at(), 'checkpoint', 'coordinator', 0, 0, { step: s.id, action_kind: s.action_kind }));
    }
    if (job.state === 'done') arr.push(t(at(), 'job_done', 'coordinator', ri(80, 200), 0.004, {}));
    else if (job.state === 'escalated') arr.push(t(at(), 'job_escalated', 'coordinator', ri(80, 200), 0.004, { reason: job.error }));
    else if (job.state === 'failed') arr.push(t(at(), 'job_failed', 'coordinator', ri(60, 160), 0.003, { error: job.error }));
    else if (job.state === 'cancelled') arr.push(t(at(), 'job_cancelled', 'coordinator', 40, 0.001, {}));
    traces[job.id] = arr;
    return arr;
  }

  var killSwitch = false;

  /* ---------------- simulator (keeps the dashboard alive) ---------------- */
  function activeJobs() { return jobs.filter(function (x) { return LIFE.indexOf(x.state) >= 0; }); }
  function pendingCards() { return cards.filter(function (c) { return c.status === 'pending'; }); }

  function advance(job) {
    var w = job._wf; if (!w) { job.state = 'done'; return; }
    if (job.state === 'queued') { job.state = 'planning'; job._step = 0; pushTrace(job, 'plan', 'coordinator', ri(180, 320), 0.008); return; }
    if (job.state === 'planning') { job.state = 'executing'; job._step = 0; return; }
    if (job.state === 'executing') {
      var step = w.steps[job._step] || w.steps[w.steps.length - 1];
      pushTrace(job, 'model_call', step.agent, ri(600, 4200), r2(ri(2, 18) / 100), { step: step.id });
      var gated = step.checkpoint || step.action_kind !== 'internal';
      if (gated) {
        if (pendingCards().length < 5) { job.state = 'awaiting_approval'; spawnCard(job, step); }
        else { job.updated_at = iso(Date.now()); } // gate is full, wait
        return;
      }
      job._step++;
      if (job._step >= w.steps.length) { job.state = 'reviewing'; pushTrace(job, 'review', 'coordinator', ri(200, 500), 0.01); }
      return;
    }
    if (job.state === 'reviewing') {
      var roll = Math.random();
      if (roll < 0.08) { job.state = 'escalated'; job.error = 'review found an inconsistency that needs owner input'; }
      else if (roll < 0.12) { job.state = 'failed'; job.error = 'a downstream tool returned an unexpected error'; }
      else { job.state = 'done'; }
      pushTrace(job, 'job_' + job.state, 'coordinator', ri(80, 200), 0.004);
    }
  }

  function pushTrace(job, type, agent, tokens, usd, payload) {
    if (!traces[job.id]) traces[job.id] = [];
    traces[job.id].push(t(Date.now(), type, agent, tokens, usd, payload));
    if (traces[job.id].length > 40) traces[job.id].shift();
    job.spend_tokens += tokens; job.spend_usd = r2(job.spend_usd + usd);
    job.updated_at = iso(Date.now());
  }

  function spawnCard(job, step) {
    var titles = {
      'lead_to_proposal.send': 'Send the drafted proposal to the client',
      'content_pipeline.publish': 'Publish the finished post to the blog',
      'invoice_run.charge': 'Charge the compiled invoice batch',
      'support_triage.respond': 'Send the drafted support reply'
    };
    cards.unshift({
      id: 'card_' + id4(), job_id: job.id, step_key: step.agent + ':' + job.workflow + '.' + step.id,
      action_kind: step.action_kind, title: titles[job.workflow + '.' + step.id] || ('Approve ' + job.workflow),
      artifact_id: null, cost_so_far_usd: job.spend_usd, status: 'pending',
      created_at: iso(Date.now()), decided_at: null, owner_note: '',
      reasoning: 'The workflow reached a gated step and is waiting on your decision before it acts.',
      downstream_effects: ['This action is consequential and blocks on your approval.'],
      artifact_preview: { artifact_type: 'Draft', kind: 'text', title: job.context.subject,
        body: 'Preview of the content that would go out for "' + job.context.subject + '".' }
    });
  }

  function spawnInbound() {
    var w = pick(workflows.filter(function (x) { return x.trigger !== 'owner.request'; }));
    var nj = j('job_' + id4(), w.name, 'queued', 0, 0, 0, 0, null, Date.now(), 0);
    nj.context = { subject: subjectFor(w.name) };
    jobs.unshift(nj);
  }

  function buildDigest() {
    var ft = function (n) { return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n); };
    var sh = function (s) { s = s || ''; return s.length > 46 ? s.slice(0, 44) + '\u2026' : s; };
    var by = {}; jobs.forEach(function (j) { by[j.state] = (by[j.state] || 0) + 1; });
    var done = jobs.filter(function (j) { return j.state === 'done'; });
    var flight = jobs.filter(function (j) { return ['queued', 'planning', 'executing', 'reviewing'].indexOf(j.state) >= 0; });
    var attn = jobs.filter(function (j) { return ['escalated', 'failed'].indexOf(j.state) >= 0; });
    var pend = cards.filter(function (c) { return c.status === 'pending'; });
    var usd = jobs.reduce(function (s, j) { return s + (j.spend_usd || 0); }, 0);
    var tok = jobs.reduce(function (s, j) { return s + (j.spend_tokens || 0); }, 0);
    var d = new Date(), stamp = d.toISOString().slice(0, 10) + ' ' + d.toTimeString().slice(0, 8);
    var L = [];
    L.push('=== HIVE DAILY DIGEST ===');
    L.push(stamp + '  \u00b7  adapter: ' + ADAPTER);
    L.push('');
    L.push('OVERVIEW');
    L.push('  ' + jobs.length + ' jobs tracked');
    L.push('  ' + done.length + ' done \u00b7 ' + flight.length + ' in flight \u00b7 ' + (by.escalated || 0) + ' escalated \u00b7 ' + (by.failed || 0) + ' failed');
    L.push('  $' + usd.toFixed(2) + ' spent \u00b7 ' + ft(tok) + ' tokens');
    L.push('  ' + pend.length + ' approval' + (pend.length === 1 ? '' : 's') + ' waiting on you');
    L.push('');
    if (attn.length || pend.length) {
      L.push('NEEDS YOU');
      attn.forEach(function (j) { L.push('  ! ' + j.id + '  ' + j.workflow + ' \u2014 ' + j.state); if (j.error) L.push('      ' + j.error); });
      pend.slice(0, 4).forEach(function (c) { L.push('  \u00b7 approve: ' + c.title + '  ($' + (c.cost_so_far_usd || 0).toFixed(2) + ')'); });
      if (pend.length > 4) L.push('    \u2026and ' + (pend.length - 4) + ' more in Approvals');
      L.push('');
    }
    if (done.length) {
      L.push('RECENTLY DONE');
      done.slice(0, 5).forEach(function (j) { L.push('  \u2713 ' + j.id + '  ' + j.workflow + ' \u2014 ' + sh(j.context && j.context.subject)); });
      L.push('');
    }
    if (flight.length) {
      L.push('IN FLIGHT');
      flight.slice(0, 6).forEach(function (j) { L.push('  \u2192 ' + j.id + '  ' + j.workflow + ' \u2014 ' + j.state); });
      L.push('');
    }
    L.push('MONEY');
    L.push('  today   $' + usd.toFixed(2) + ' of $120.00 daily cap');
    var top = jobs.slice().sort(function (a, b) { return b.spend_usd - a.spend_usd; }).slice(0, 3).filter(function (j) { return j.spend_usd > 0; });
    if (top.length) L.push('  top     ' + top.map(function (j) { return j.id + ' $' + j.spend_usd.toFixed(2); }).join(' \u00b7 '));
    L.push('');
    L.push('\u2014 end of digest \u2014');
    return L.join('\n');
  }

  function tick() {
    if (killSwitch) return;                 // frozen while the kill switch is engaged
    jobs.forEach(function (job) {           // accrue spend on anything running
      if (['executing', 'reviewing', 'planning'].indexOf(job.state) >= 0) {
        job.spend_tokens += ri(40, 260); job.spend_usd = r2(job.spend_usd + ri(1, 22) / 1000);
        job.updated_at = iso(Date.now());
      }
    });
    var act = activeJobs();
    if (act.length) advance(pick(act));
    if (act.length < 2) spawnInbound();     // keep at least a couple of jobs moving
    if (Math.random() < 0.12 && jobs.length < 22) spawnInbound();
    // trim old terminal jobs
    if (jobs.length > 22) {
      var term = jobs.filter(function (x) { return ['done', 'failed', 'cancelled'].indexOf(x.state) >= 0; });
      if (term.length) { var old = term[term.length - 1]; jobs = jobs.filter(function (x) { return x !== old; }); }
    }
  }
  setInterval(tick, 2400);

  /* ---------------- mock router ---------------- */
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function publicJob(job) {
    return { id: job.id, workflow: job.workflow, adapter: job.adapter, state: job.state,
      created_at: job.created_at, updated_at: job.updated_at, context: clone(job.context),
      artifact_ids: job.artifact_ids.slice(), spend_tokens: job.spend_tokens,
      spend_usd: r2(job.spend_usd), owner_touches: job.owner_touches, error: job.error };
  }
  function findJob(id) { return jobs.filter(function (x) { return x.id === id; })[0]; }

  function matchWorkflow(subject) {
    var s = (subject || '').toLowerCase(), best = null, score = 0;
    workflows.forEach(function (w) {
      var sc = 0; w.keywords.forEach(function (k) { if (s.indexOf(k) >= 0) sc++; });
      if (sc > score) { score = sc; best = w; }
    });
    return score > 0 ? best : null;
  }

  function mock(path, opts) {
    opts = opts || {};
    var method = (opts.method || 'GET').toUpperCase();
    var url = path.split('?')[0];
    var qs = {};
    (path.split('?')[1] || '').split('&').forEach(function (p) { if (p) { var kv = p.split('='); qs[kv[0]] = decodeURIComponent(kv[1] || ''); } });
    var body = {};
    try { body = opts.body ? JSON.parse(opts.body) : {}; } catch (e) {}
    var ok = function (data, status) { return { ok: true, status: status || 200, data: data, live: false }; };
    var err = function (status, detail) { return { ok: false, status: status, data: { detail: detail }, live: false }; };

    if (url === '/health') return ok({ status: 'ok', adapter: ADAPTER, kill_switch: killSwitch });
    if (url === '/adapter') return ok({ agents: clone(agents), workflows: clone(workflows) });
    if (url === '/killswitch') {
      if (method === 'POST') { killSwitch = !!body.engaged; return ok({ engaged: killSwitch }); }
      return ok({ engaged: killSwitch });
    }
    if (url === '/jobs') {
      if (method === 'POST') {
        var w = matchWorkflow(body.subject);
        if (!w) return err(422, 'no workflow matched that request');
        var nj = j('job_' + id4(), w.name, 'planning', 0, 320, 0.01, 0, null, Date.now(), 0);
        nj.context = { subject: body.subject };
        jobs.unshift(nj);
        traces[nj.id] = [
          t(Date.now(), 'job_created', 'coordinator', 140, 0.004, { subject: body.subject }),
          t(Date.now(), 'plan', 'coordinator', 240, 0.008, { workflow: w.name })
        ];
        var pend = [];
        // support_triage gates almost immediately -> demonstrates the "needs approval" path
        if (w.name === 'support_triage') {
          nj.state = 'awaiting_approval';
          spawnCard(nj, w.steps[w.steps.length - 1]);
          pend = [clone(cards[0])];
        }
        return ok({ job: publicJob(nj), pending_cards: pend });
      }
      var list = jobs.slice();
      if (qs.state) list = list.filter(function (x) { return x.state === qs.state; });
      list.sort(function (a, b) { return new Date(b.updated_at) - new Date(a.updated_at); });
      return ok({ jobs: list.map(publicJob) });
    }
    var mJobTrace = url.match(/^\/jobs\/([^\/]+)\/trace$/);
    if (mJobTrace) { var jt = findJob(mJobTrace[1]); return ok({ trace: clone(jt ? traceFor(jt) : (traces[mJobTrace[1]] || [])) }); }
    var mJob = url.match(/^\/jobs\/([^\/]+)$/);
    if (mJob) {
      var job = findJob(mJob[1]); if (!job) return err(404, 'job not found');
      var arts = artifactsFor(job);
      var pj = publicJob(job); pj.artifact_ids = arts.map(function (a) { return a.id; });
      return ok({ job: pj, artifacts: arts });
    }
    if (url === '/approvals') {
      var cl = cards.slice();
      if (qs.status) cl = cl.filter(function (c) { return c.status === qs.status; });
      else cl = cl.filter(function (c) { return c.status === 'pending'; });
      cl.sort(function (a, b) { return new Date(b.created_at) - new Date(a.created_at); });
      return ok({ cards: clone(cl) });
    }
    var mDec = url.match(/^\/approvals\/([^\/]+)\/decision$/);
    if (mDec && method === 'POST') {
      var card = cards.filter(function (c) { return c.id === mDec[1]; })[0];
      if (!card || card.status !== 'pending') return err(404, 'card already decided');
      card.status = body.decision === 'approve' ? 'approved' : body.decision === 'edit' ? 'edited' : 'rejected';
      card.decided_at = iso(Date.now()); card.owner_note = body.note || '';
      var jb = findJob(card.job_id);
      if (jb) {
        jb.owner_touches += 1;
        if (card.status === 'rejected') { jb.state = 'cancelled'; }
        else { jb.state = 'executing'; jb._step = (jb._step || 0) + 1; if (jb._step >= (jb._steps || 1)) jb.state = 'done'; }
        jb.updated_at = iso(Date.now());
      }
      return ok({ job: jb ? publicJob(jb) : null, card: clone(card) });
    }
    // ---- stubs for the other pages (kept consistent for later) ----
    if (url === '/system') return ok({
      adapter: ADAPTER, use_llm: false,
      model_routing: { planner: 'claude-opus-4-8', specialist: 'claude-sonnet-5', extractor: 'claude-haiku-4-5', frontier: 'claude-fable-5' },
      policies: {
        gated_actions: ['send', 'spend', 'publish', 'contract', 'live_settings'],
        budgets: { per_job: { steps: 12, tokens: 120000, usd: 5 }, global_daily: { tokens: 4000000, usd: 120 } },
        autonomy: { default: 'plan_then_ask', internal_steps: 'auto' },
        escalation: { on: ['budget_exceeded', 'low_confidence', 'conflicting_data'] }
      },
      kill_switch: killSwitch
    });
    if (url === '/digest') return ok({ text: buildDigest() });
    if (url === '/vault') return ok({ root: VAULT_ROOT, files: Object.keys(vaultFiles) });
    if (url === '/vault/file') {
      var vp = qs.path || '';
      if (!vp || vp.indexOf('..') >= 0 || vp.charAt(0) === '/') return err(400, 'invalid path');
      if (!Object.prototype.hasOwnProperty.call(vaultFiles, vp)) return err(404, 'file not found: ' + vp);
      return ok({ path: vp, content: vaultFiles[vp] });
    }
    return err(404, 'not found');
  }

  /* ---------------- transport (real -> mock fallback) ---------------- */
  var mode = 'unknown'; // 'unknown' | 'live' | 'offline'

  function realFetch(path, opts) {
    var ctrl = new AbortController();
    var to = setTimeout(function () { ctrl.abort(); }, 2500);
    return fetch(path, Object.assign({ signal: ctrl.signal, headers: { 'Content-Type': 'application/json' } }, opts || {}))
      .then(function (res) {
        clearTimeout(to);
        var ct = res.headers.get('content-type') || '';
        if (ct.indexOf('json') < 0) throw new Error('non-json');
        return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data, live: true }; });
      })
      .catch(function (e) { clearTimeout(to); throw e; });
  }

  function req(path, opts) {
    if (mode === 'offline') return Promise.resolve(mock(path, opts));
    return realFetch(path, opts).then(
      function (r) { mode = 'live'; return r; },
      function () { mode = 'offline'; return mock(path, opts); }
    );
  }

  window.HiveAPI = {
    req: req,
    isLive: function () { return mode === 'live'; },
    mode: function () { return mode; }
  };
})();
