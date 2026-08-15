# Session prices flatten when a mentor's rate changes

**Status:** open, accepted for launch, to be discussed
**Found:** 15 Aug 2026, during the production audit
**Severity:** high for the mentors affected (silent price change), no data loss
**Affects:** migrated mentors with differentiated pricing. 13 session groups across the 48 mentors
who have services, plus 4 mentors who would lose a currency.

---

## What happens

Our pricing model stores **one hourly rate per mentor** and derives every session price from it:

```
set_price = hourly_rate x duration / 60
```

With only the rate and the duration as inputs, two sessions of the same length can never have
different prices. When `reprice_mentor_services()` runs, any prices that differed are overwritten
with the single derived figure.

The mentors imported from the legacy site did not price this way. They priced each session
individually, by topic. So repricing silently rewrites deliberate pricing decisions.

## Example, from live data

`vasanth-leyo`, stored `hourly_rate = 2998 INR`. Six active sessions, all 30 minutes:

| Session (all 30 min) | Price now | After a reprice |
|---|---|---|
| Job Market & Application Strategy | ₹999 | **₹1499** |
| Housing, Banking, Healthcare & Essentials | ₹999 | **₹1499** |
| Language, Kids & Social life in Singapore | ₹999 | **₹1499** |
| Studying Abroad: Courses, Costs & Admissions | ₹1499 | ₹1499 |
| Stand Out in the Job Market in Singapore | ₹1499 | ₹1499 |
| Visa, Work Permit, PR pathways to Singapore | ₹1499 | ₹1499 |

He built two tiers: ₹999 for lighter topics, ₹1499 for the ones people pay more for. After a reprice
he has one tier. Three sessions rise 50% without him asking.

The mentor is told only *after* saving, by the line "Your session prices update automatically from
this rate". Nothing states which sessions change, or by how much.

## Second effect: currency collapse

`reprice_mentor_services()` also writes the mentor's single `set_currency` onto every service. Four
mentors (`ajayen-yogakumar`, `arun-thanigaivel`, `fazil-m`, `zeel-patel`) price some sessions in
AUD/EUR and others in INR. Repricing forces all of them into one currency, so the prices their
customers in the other market saw are replaced by converted figures.

`arun-thanigaivel` is the clearest: three sessions in AUD (31, 35.99, 39.99) and two in INR (499,
1499). All five would become one currency at one price per length.

## When it fires

`reprice_mentor_services()` is called from:

- `set_mentor_initial_rate(reprice=True)` via `POST /mentor/setup-rate`, whenever the server sees the
  rate, currency or per-currency overrides actually change
- the profile save path (`db/mentors.py:679`, `:726`)

It does **not** fire for most mentors at first login: only 3 of 47 have a stored rate that differs
enough from their sessions to trigger it, because `reimport_migrated_prices` and
`fix_mentor_base_currency` already aligned the rest.

It **does** fire the first time a mentor adjusts their rate, even by a small amount. That is a normal
thing for a mentor to do, so this is latent rather than imminent.

## Scale

- **13 groups** of same-length sessions priced differently, across the 48 mentors with services
- **4 mentors** with mixed-currency sessions
- Largest spread: `zeel-patel`, four 45-minute sessions from AUD 45 to 2699 (mixed currency)

Query to re-derive the list:

```sql
select m.slug, s.duration, count(*), min(s.set_price), max(s.set_price), min(s.set_currency)
from mentors m join services s on s.mentor_id = m.id and s.is_active
where m.legacy_id is not null
group by m.slug, s.duration
having count(*) > 1 and min(s.set_price) <> max(s.set_price)
order by (max(s.set_price) - min(s.set_price)) desc;
```

## Why it is not simply a bug

The derived model is deliberate and has real benefits: a mentor changes one number and every session
follows, prices cannot drift out of step with the rate, and PPP and FX conversion have a single basis.
`reprice_mentor_services()` is doing exactly what it was written to do.

The mismatch is that the imported data was built on a different model, and nothing reconciles the two
or tells the mentor which one they are now on.

## Options for discussion

**1. Accept it.** Simplest model, one rate per mentor. Cost: mentors are repriced without warning,
and topic-based pricing is not expressible.

**2. Warn before saving.** Keep the behaviour, but show the specific consequence first: "This will
change 3 of your sessions from ₹999 to ₹1499." Contained frontend change, removes the surprise,
changes no money logic. Suggested for launch.

**3. Per-session prices.** Keep hand-set prices, derive only for new sessions, and give the mentor an
explicit "apply my rate to all sessions" action. Truest to how mentors actually price, but it changes
the pricing model and touches `compute_booking_price`, `display_service_prices` and the payout basis.

**4. Rate per duration.** A middle path: keep deriving, but let a mentor set a multiplier or an
override per session. More expressive than 1, less invasive than 3.

## Recommendation

Option 2 for launch, then revisit 3 or 4 once there is evidence about whether mentors want topic-based
pricing. Nobody should be repriced without being shown the numbers first.
