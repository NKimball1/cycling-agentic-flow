# Analysis evaluation rubric

Score the coach's analysis on each criterion from 0 to 2:
- **2** = fully correct / strong
- **1** = partially correct or vague
- **0** = wrong, missing, or fabricated

Criteria:

1. **Sport & metrics** — Correctly identifies the sport and reads the available
   metrics. Handles missing data honestly: with no power/HR it uses the provided
   estimated load (`load_source` = "estimated") and CALLS it an estimate; it must
   not invent its own precise figures or claim data it wasn't given.

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

8. **Weather, proportionately** — Uses provided weather ONLY when it plausibly
   mattered. When conditions are notable (extreme heat, high humidity, strong
   wind, hard cold), it accounts for the likely effect — e.g. heat/humidity
   raising HR and driving cardiac drift/decoupling (so elevated HR at endurance
   power is the weather, not lost fitness), or wind disrupting steady effort.
   When conditions are benign/ideal (e.g. ~70°F, low humidity, light wind), or
   when NO weather was provided, it does NOT lean on weather as an excuse or
   invent conditions — a brief "conditions were good" is fine, but weather must
   not become an overreaching explanation for an ordinary day. Score 0 if it
   fabricates weather, blames a benign day on the weather, or ignores clearly
   extreme conditions that shaped the effort.

9. **Form / readiness awareness** — Correctly reads the provided training-load
   state (Fitness/CTL, Fatigue/ATL, Form/TSB). When form going into the session
   is notably negative (fatigued), it acknowledges reduced readiness and tempers
   expectations or flags a hard session as higher-risk; when form is clearly
   positive (fresh/tapered), it recognizes good readiness/timing. It must stay
   CONSISTENT with the given numbers — don't call a fresh athlete fatigued or
   vice-versa — and stay proportionate: don't manufacture a fatigue warning on a
   balanced/neutral form, and respect the "warming up" caveat when flagged.
   Score 0 if it contradicts the provided form, invents a readiness problem that
   the numbers don't support, or ignores a clearly extreme form value.

Max score = 18 (9 criteria × 2).
