# Revenue Model — WhatsApp Ordering Bot

> **Status:** Draft — for internal discussion
> **Last Updated:** 2026-03-01

---

## Recommended Model: One-Time Setup + Monthly Retainer + Usage Tier

### 1. One-Time Setup Fee

Covers the cost of onboarding each new client:

| Activity | Details |
|----------|---------|
| WhatsApp Business API configuration | Phone number, webhook, Meta verification |
| Product catalog ingestion | Import client's product list, map SKUs, units, aliases |
| AI prompt tuning | Customise parsing logic for client's ordering patterns |
| Integration testing | End-to-end testing with real messages |
| Go-live support | First-week monitoring and fine-tuning |

**Pricing lever:** Complexity of catalog (100 SKUs vs 5,000) and number of ordering patterns.

---

### 2. Monthly Retainer (Tiered)

Covers hosting, maintenance, support, and a bundled order quota.

| Tier | Included Orders/Month | Target Client Profile |
|------|----------------------|----------------------|
| Starter | Up to 500 | Small distributor, 1–2 reps |
| Growth | Up to 2,000 | Mid-size, multiple reps |
| Enterprise | Up to 10,000+ | Large distributor, high volume |

**What's included in the retainer:**
- Hosting & infrastructure (VPS, SSL, uptime monitoring)
- LLM API costs within the tier quota
- WhatsApp conversation costs within the tier quota
- Bug fixes & maintenance updates
- Email/chat support (SLA based on tier)

---

### 3. Usage Overage (Above Quota)

Orders above the monthly tier quota are charged per order at a flat rate.

This keeps the base price predictable while ensuring high-volume clients cover their actual costs.

---

## Cost Structure to Inform Pricing

Before setting prices, measure these per-client costs:

| Cost Item | Type | How to Measure |
|-----------|------|----------------|
| LLM API (Anthropic / OpenAI) | Variable, per token | v0.6.0 benchmarking |
| WhatsApp Cloud API | Variable, per conversation | Meta pricing tiers |
| VPS / Hosting | Fixed (shared across clients) | Monthly server cost ÷ client count |
| Development & maintenance | Fixed (team time) | Hours per month |
| Support | Semi-variable | Tickets per client per month |

**Key insight:** LLM cost optimisation (v0.6.0 in backlog) directly impacts margin. Solving this before v1.0 launch is essential.

---

## Pricing Principles

1. **Setup fee must cover onboarding cost + margin** — never onboard for free
2. **Monthly retainer must cover fixed costs per client** even at zero usage
3. **Usage tier must cover variable costs (LLM + WhatsApp)** with healthy margin
4. **Price anchoring:** Position against the cost of manual order processing (staff time, errors, missed orders) — the bot should pay for itself
5. **Start higher, discount selectively** — easier to offer a discount than to raise prices later

---

## Value Proposition for Pricing Conversations

> "Your team currently spends X hours/day manually reading WhatsApp messages, interpreting orders, and keying them into your ERP. Our bot does this in seconds, 24/7, with fewer errors. The monthly fee is a fraction of one employee's salary."

---

## Revenue Scenarios (Illustrative)

| Clients | Avg. Tier | Monthly Recurring | Annual Recurring | Setup Revenue |
|---------|-----------|-------------------|------------------|---------------|
| 5 | Starter | 5 × retainer | 60 × retainer | 5 × setup fee |
| 15 | Mixed | ~15 × avg retainer | ~180 × avg retainer | 15 × setup fee |
| 50 | Mixed | ~50 × avg retainer | ~600 × avg retainer | 50 × setup fee |

*Fill in actual numbers once v0.6.0 cost benchmarking is complete.*

---

## Open Questions

- [ ] What is ACS's current cost of manual order processing? (Helps set price anchor)
- [ ] Will clients accept per-order overage, or do they want a flat monthly cap?
- [ ] Should there be a separate charge for ERP integration (v1.0)?
- [ ] Annual discount for upfront payment? (Improves cash flow)
- [ ] White-label option for resellers?

---

## Next Steps

1. Complete LLM cost benchmarking (v0.6.0) to know true per-order cost
2. Calculate break-even per client at each tier
3. Validate pricing with ACS as pilot client (they may get a discounted "early adopter" rate)
4. Formalise into a rate card before onboarding client #2
