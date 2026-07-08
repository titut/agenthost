# Description

Strict template for the ORCHESTRATOR's final synthesized reports.

# Final Report Template

When you are ready to deliver the final answer, use this exact structure. Write each numbered section exactly once. Do not duplicate, reformat, or re-summarize any section.

## Allowed Sections (use each at most once)

1. **Executive Summary** — 1-3 paragraphs. State the goal, the key finding, and the bottom-line recommendation.
2. **Context / Background** — Only if the user needs domain context. Keep it short.
3. **Key Findings** — Bullets or a short table. Present evidence once.
4. **Strategic Recommendations** — Numbered list or table. Each recommendation appears once.
5. **Implementation Plan / Action Items** — Who does what, by when.
6. **Risks or Caveats** — Optional. One short list.
7. **Expected Timeline / Metrics** — Optional. One table or one list.

## Hard Rules

- **No section may be repeated.** If you write "Timeline", you cannot write "Realistic Timeline" or "Expected Timeline" again.
- **No recap after the last section.** Once section 7 is done, stop.
- **No follow-up question at the end.** The response ends with the last section.
- **One format per section.** Do not present the same points as bullets, then a table, then numbered steps.
- **Keep the report focused.** If the research produced 3 useful tables, include those 3 tables once each. Do not regenerate them in different wording.

## Stop Signal

Treat the last allowed section as a hard stop. Do not emit any tokens after it except a closing newline.
