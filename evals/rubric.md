# Analysis evaluation rubric

Score the coach's analysis on each criterion from 0 to 2:
- **2** = fully correct / strong
- **1** = partially correct or vague
- **0** = wrong, missing, or fabricated

Criteria:

1. **Sport & metrics** — Correctly identifies the sport and reads the available
   metrics. Handles missing data honestly (e.g. no power/HR → says so, does NOT
   invent numbers or a load figure that can't exist).

2. **Planned vs. unplanned** — Correctly decides whether the activity maps to a
   prescribed session. Does NOT force-grade an incidental walk/hike/cross-train
   against a workout it was never meant to be; DOES grade a genuine planned
   session against its target.

3. **Dates & weekday** — Uses the correct weekday and date reasoning (no "what
   day was this?" errors). Maps the activity to the right day in the plan.

4. **Training load** — Interprets `load_tss` / `power_tss` / `hr_tss` correctly
   and sensibly; doesn't overstate the load of an easy effort or understate a
   hard one.

5. **Cross-sport awareness** — Accounts for the load of OTHER recent activities
   (a hard run before a ride, cumulative fatigue) rather than analyzing in a
   vacuum.

6. **Specificity & grounding** — Advice is concrete and grounded in the
   athlete's real numbers, zones, and plan — not generic coaching platitudes.

7. **Formatting** — Clean sections; pace shown as a proper clock time (e.g.
   "11:49"), no malformed values like "11:81".

Max score = 14 (7 criteria × 2).
