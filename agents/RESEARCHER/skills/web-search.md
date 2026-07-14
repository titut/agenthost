# Description

Workflow for searching the web and synthesizing findings.

# Web Search Skill

Use this skill for any factual, current, or web-dependent question. Do not answer
from memory.

## Workflow

Follow this exact loop:

1. **Search + fetch**
   - Call `web_search(query, max_results=5)` with a focused query.
   - The tool returns up to 5 results with `title`, `url`, `snippet`, and the
     fetched page `content`.

2. **Evaluate**
   - Read the snippets and fetched content carefully.
   - If 2–3 reputable sources directly agree and answer the question, stop and
     synthesize the answer.

3. **Refine and repeat (only if still missing information)**
   - Construct a new, more specific query targeting the gap.
   - Call `web_search` again.

4. **Stop conditions**
   - Stop as soon as you have a complete, well-supported answer.
   - Stop when you have searched through **20 unique websites** total.
   - If you hit the 20-website limit, report what you found, explain the gap,
     and ask the user if you should continue.

## Counting Websites

Keep a running tally of every unique URL returned by `web_search`. Count each
distinct URL once, even if you search it twice.

## Query Refinement

Make each follow-up query more specific than the last. Good strategies:

- Add a date or year if the topic is time-bound.
- Add a domain name (e.g., `site:github.com`, `site:gov`) when looking for
  official sources.
- Quote exact phrases.
- Target the missing piece of information explicitly.

## Synthesis

After each search cycle, briefly summarize what you learned and what is still
unknown. This keeps the search targeted.

When you deliver the final answer:

- Lead with the concise answer.
- Follow with supporting evidence and cited URLs.
- Note any uncertainty or conflicts.
