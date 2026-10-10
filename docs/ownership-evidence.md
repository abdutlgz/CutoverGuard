# Why ownership checks matter

Primary sources checked October 10, 2026. The regulatory examples motivate
accurate customer records; they do not establish applicability to a particular
BNY business or certify this prototype. Our historical retail invoice values
are not client money, bank cash or settlement records.

## Exact regulatory hooks and their limits

| Provision | What it supports |
|---|---|
| FCA CASS 7.15.2R | Covered firms must distinguish one client's money from another's and the firm's own money. [FCA records rules](https://handbook.fca.org.uk/handbook/cass7/cass7s22) |
| FCA CASS 7.15.5R(1)–(2) | Records must permit prompt calculation of money that should be held for each client and explain client-money transactions/commitments. The ownership phrase is “for each of its clients.” [FCA records rules](https://handbook.fca.org.uk/handbook/cass7/cass7s22) |
| FCA CASS 7.16.8R | Client-money resource is an aggregate balance on client bank accounts; this does not replace the separate per-client record duty. [FCA reconciliation methods](https://handbook.fca.org.uk/handbook/cass7/cass7s23) |
| FCA CASS 7.16.16R(1), 7.16.21R | The individual-client-balance method uses client balances, with exclusions/adjustments; non-margined balances must capture what should be held for that client. Method eligibility and exceptions matter. [FCA reconciliation methods](https://handbook.fca.org.uk/handbook/cass7/cass7s23) |
| SEC 17 CFR 240.15c3-3(e)(1)–(2) | Covered broker-dealers maintain customer/PAB reserve accounts using the Exhibit A formula; excess credits over debits inform the reserve requirement. This is not an event-to-owner migration audit. [eCFR reserve clauses](https://www.ecfr.gov/current/title-17/section-240.15c3-3#p-240.15c3-3(e)) |
| SEC 17 CFR 240.15c3-3(c)(1) | In the specified central-handling custody arrangement, books/records must identify customers entitled to securities quantities/units. This supports ownership records in that securities context, not a universal cash-migration rule. [eCFR control clause](https://www.ecfr.gov/current/title-17/section-240.15c3-3#p-240.15c3-3(c)(1)) |

Inference: aggregate protection calculations and individual entitlement records
answer different questions. CutoverGuard demonstrates preservation of supplied
event-to-owner assignments. It implements neither the CASS segregation calculation
nor the SEC reserve formula, and passing its checks establishes no compliance.

## What the demonstration proves mathematically

Hold event ID, amount, currency and timestamp fixed. Permute owner assignments
among those events. Row count, currency totals and currency/UTC-day totals remain
unchanged because those expressions do not use ownership. Thus these aggregates
cannot determine whether the original event-to-owner relation was preserved.

An unweighted global checksum `SUM(h(account_id))` is also unchanged when the
owner-ID multiset is permuted. This holds even if `h` is truncated SHA-256:
no hash collision is needed. A checksum within a group has the same blind spot
when owners are permuted within that group. A hash over the complete relevant
tuple, including event ID and owner, can detect the change, subject to encoding
and collision assumptions. Keyed row/value comparison can also detect it.
Per-owner amount totals alone can still miss equal-value swaps.

The precise claim is **owner-insensitive controls miss owner permutations**,
not that every checksum fails or only CutoverGuard/row-by-row scans can catch them.

## Prior art and the project claim

Datafold already offers source/target comparisons that identify differing rows
and columns. Its official migration guidance describes investigating changed
values by key. Generic row diffing is established prior art, and CutoverGuard
has no measured accuracy or performance advantage over it. [Datafold Data Diff](https://www.datafold.com/data-diff/),
[Datafold migration workflow](https://www.datafold.com/blog/how-to-diff-your-data-during-a-data-migration/).

Our demonstrated contribution is a focused review workflow: explicit ownership
findings, exact minor-unit account effects, supplied-completeness checks and
human-review evidence on Databricks. Comparative usability and practitioner
demand remain hypotheses until evaluated.

## Practitioner question and evidence log

The [supplied guide](https://2026.knighthacks.org/dashboard/guide) schedules BNY Prompt Wars Saturday October 10, **8:15–9:15 PM,
BA1 119**. It is a chatbot workshop, not a confirmed migration-practitioner panel.
Follow current dashboard/Discord updates. The user can ask an appropriate sponsor
or mentor; no outreach has been sent by the agent.

Suggested question: “During a migration, which controls verify individual
customer/event ownership after aggregate totals match? Would a record-level
ownership discrepancy with signed account effects help your review?”

Ask before quoting: “May I note your response and quote it in our hackathon demo?
Would you prefer attribution by name/company, role only, or anonymity?”

| Field | Record after the conversation |
|---|---|
| Date/time, setting | — |
| Person/role; attribution preference | — |
| Exact question and verbatim response | — |
| Permission to quote and permission to name | — |
| Concrete existing control or pain described | — |
| Our interpretation, clearly separate from quotation | — |
| Follow-up or contradiction | — |

No practitioner validation is recorded yet. Do not turn an informal opinion into
a company endorsement or evidence of an observed production failure.
