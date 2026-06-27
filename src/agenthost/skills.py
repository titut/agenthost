"""Skill loading: read markdown skill files from the agent's skills/ folder.

Skills are plain text knowledge written by users as .md files. agenthost loads them
and injects them into the agent's context so the LLM can use them.
"""
from __future__ import annotations

from pathlib import Path


def load_skills(skills_dir: Path) -> dict[str, str]:
    """Load all .md files from skills_dir and return {filename_stem: content}."""
    skills: dict[str, str] = {}
    if not skills_dir.exists():
        return skills

    for file in sorted(skills_dir.glob("*.md")):
        skills[file.stem] = file.read_text(encoding="utf-8")
    return skills
