# Track B examples

The chunk IDs below are stable IDs emitted by `OfferRAG.generate`. The examples use the offline policy-safe baseline solely as reproducible evidence of the guardrail behavior; the production path sends the same retrieved policy context to Claude Haiku through Bedrock.

## Correct behavior

1. **Gold outdoor/footwear customer** — profile: `CUST-10000317`; Gold; churn 0.81; 47 days since purchase; $3,120.40 lifetime value; categories `[Outdoor, Footwear]`.
   - Offer: “Return to NorthStar for Outdoor and make the most of your 1.5x points earn rate and free standard shipping. Your retention offer is single-use, tied to your account, and valid for 45 days; shop Outdoor when you are ready.”
   - Retrieved chunks: `POL-SHP-007-002`, `KB-FAQ-022-006`, `POL-LOY-011-006`, `KB-FAQ-022-002`.

2. **Bronze apparel customer** — profile: `CUST-10000904`; Bronze; churn 0.74; 68 days since purchase; $312.80 lifetime value; category `[Apparel]`.
   - Offer: “Return to NorthStar for Apparel and make the most of your 1x points earn rate and free standard shipping on orders of $75 or more. Your retention offer is single-use, tied to your account, and valid for 45 days; shop Apparel when you are ready.”
   - Retrieved chunks: `POL-SHP-007-002`, `KB-FAQ-022-006`, `POL-LOY-011-006`, `POL-RET-004-003`.

3. **Platinum multi-category customer** — profile: `CUST-10000122`; Platinum; churn 0.66; $8,940 lifetime value; categories `[Outdoor, Electronics, Home]`.
   - Offer: “Return to NorthStar for Outdoor and make the most of your 2x points earn rate and $100 annual gear credit, if available. Your retention offer is single-use, tied to your account, and valid for 45 days; shop Outdoor when you are ready.”
   - Retrieved chunks: `POL-SHP-007-002`, `KB-FAQ-022-006`, `POL-LOY-011-006`, `KB-FAQ-022-002`.

4. **Near-threshold Silver customer** — profile: `CUST-10000588`; Silver; churn 0.70; $1,985 lifetime value; categories `[Home, Beauty]`.
   - Offer: “Return to NorthStar for Home and make the most of your 1.25x points earn rate and 12-hour early sale access. Your retention offer is single-use, tied to your account, and valid for 45 days; shop Home when you are ready.”
   - Retrieved chunks: `POL-SHP-007-002`, `KB-FAQ-022-006`, `POL-LOY-011-006`, `KB-FAQ-022-002`.

5. **Silver beauty follow-up** — profile: Silver; churn 0.72; category `[Beauty]`; online-to-store ratio 0.61.
   - Offer: “Return to NorthStar for Beauty and make the most of your 1.25x points earn rate and 12-hour early sale access. Your retention offer is single-use, tied to your account, and valid for 45 days; shop Beauty when you are ready.”
   - Retrieved chunks: `POL-SHP-007-002`, `KB-FAQ-022-006`, `POL-LOY-011-006`, `KB-FAQ-022-002`.

## Documented failures and fixes

1. **Feature-store tier is not the policy tier.** A customer just below a lifetime-value threshold might not have the matching trailing-12-month policy tier. An unconditional “you have Gold benefits” claim can be false even if retrieval is correct.
   - Fix: use the system-of-record tier at send time; until then, condition all tier-specific benefits and include the feature profile in RAGAS contexts.

2. **Untrusted generation can invent a promotion.** A model can make a plausible percentage discount or an invented “use within 14 days” deadline even when retrieval is accurate.
   - Fix: prohibit discounts/codes/deadlines unless supplied by an approved campaign record; retain the system prompt’s claim restrictions and reject/retry any output that contains unsupported numeric promotion language.
