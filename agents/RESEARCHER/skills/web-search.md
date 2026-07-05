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

2. **Fetch all results**
   - Call `fetch_url(url, query=...)` once for **every** URL returned.
   - Pass the user's research question in the `query` parameter so the
     summarizer focuses on relevant facts.
   - Do not skip results because the snippet looks sufficient.

3. **Evaluate**
   - Read the fetched content.
   - Decide whether you have enough information to answer the user's question.

4. **Refine and repeat (if needed)**
   - If information is missing, construct a new, more specific query.
   - Call `search_web` again.
   - Fetch every result from the new search, passing the research question to
     each `fetch_url` call.

5. **Stop conditions**
   - Stop when you have a complete, well-supported answer.
   - Stop when you have fetched **50 unique websites** total.
   - If you hit the 50-website limit, report what you found, explain the gap,
     and ask the user if you should continue.

## Counting Websites

Keep a running tally of every unique URL you fetch. Count each distinct URL
once, even if you fetch it twice. Before calling `fetch_url`, check:

```
Fetched so far: N
This fetch will make it: N+1
```

If `N+1 > 50`, do not fetch. Stop and ask the user.

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
