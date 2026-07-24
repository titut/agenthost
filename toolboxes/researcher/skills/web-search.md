# Description

Workflow for bounded, plan-first web research.

# Web Search Skill

Use this skill for any factual, current, or web-dependent question. Do not answer
from memory.

## Workflow

Follow this exact loop once per user turn:

1. **Plan**
   - Decide whether the question needs web research.
   - If it does, write a plan of 1 to 5 search queries.
   - Each query should target a different angle, source, or specific fact.
   - Do not write more than 5 queries.

2. **Execute the plan**
   - Call `research_query(question, plan)` **once** with the user's original
     question and your list of queries.
   - The tool will run all queries, fetch the most relevant pages, and return the
     top-ranked chunks with their source URLs.

3. **Evaluate and stop**
   - Read the returned chunks.
   - If 2–3 reputable sources directly agree and answer the question, trust it
     and stop.
   - If the returned chunks give a complete answer, stop immediately.
   - If the answer is incomplete, give the best partial answer, cite the sources,
     and explain what is missing.

4. **Answer**
   - After `research_query` returns, you must answer the user.
   - You may not call `research_query` again in the same turn.
   - Do not keep searching for more certainty. A good, cited answer is better than
     a perfect one.

## What a Good Plan Looks Like

**Question:** "What is the latest stable version of Python and when was it released?"

**Good plan:**

1. "Python latest stable version release date"
2. "python.org downloads latest release"

**Bad plan:**

1. "Python" (too broad)
2. "Python version history" (unnecessary follow-up)
3. "Python 3.12 release notes" (too specific before knowing the version)
4. "Python 3.13 release notes" (too specific)
5. "Python 3.11 release date" (irrelevant)

Keep queries focused and avoid redundant exploration.

## Synthesis

When you deliver the final answer:

- Lead with the concise answer.
- Follow with supporting evidence and cited URLs.
- Note any uncertainty or conflicts.
- Do not add a recap, summary, or "in conclusion" section after the final section.
