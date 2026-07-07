# Description

Workflow for web search, fetching results, and synthesizing findings.

# Web Search & Fetch Skill

Use this skill for any factual, current, or web-dependent question. Do not answer
from memory.

## Workflow

Follow this exact loop:

1. **Initial search**
   - Call `search_web(query)` with a focused query.
   - The tool returns up to 5 results with `title`, `url`, and `snippet`.

2. **Evaluate before fetching**
   - Read the snippets carefully.
   - If the snippets already give you enough information to answer the user's
     question, **stop and answer**. Do not fetch URLs just because they exist.

3. **Fetch only what you need**
   - Call `fetch_url(url)` only for URLs that are likely to fill a specific gap.
   - Do not fetch every result by default.
   - After each fetch, re-evaluate: "Can I answer now?" If yes, stop.

4. **Refine and repeat (only if still missing information)**
   - If information is still missing, construct a new, more specific query.
   - Call `search_web` again.
   - Fetch only the results needed to close the gap.

5. **Stop conditions**
   - Stop as soon as you have a complete, well-supported answer.
   - Stop when you have fetched **20 unique websites** total.
   - If you hit the 20-website limit, report what you found, explain the gap,
     and ask the user if you should continue.

## Counting Websites

Keep a running tally of every unique URL you fetch. Count each distinct URL
once, even if you fetch it twice. Before calling `fetch_url`, check:

```
Fetched so far: N
This fetch will make it: N+1
```

If `N+1 > 20`, do not fetch. Stop and ask the user.

## Query Refinement

Make each follow-up query more specific than the last. Good strategies:

- Add a date or year if the topic is time-bound.
- Add a domain name (e.g., `site:github.com`, `site:gov`) when looking for
  official sources.
- Quote exact phrases.
- Target the missing piece of information explicitly.

## Synthesis

After each fetch cycle, briefly summarize what you learned and what is still
unknown. This keeps the search targeted and prevents wasted fetches.

When you deliver the final answer:

- Lead with the concise answer.
- Follow with supporting evidence and cited URLs.
- Note any uncertainty or conflicts.
