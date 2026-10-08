"""ECC integration: skills, commands, agent prompts and dev tools.

The whole surface is read-only. Entries load from local markdown into an
:class:`~alan.ecc.catalog.EccCatalog`, reach a run through the gated
``skill.*`` tools, and the eight advisory ``ecc.*`` tools analyse projects
without executing anything. Nothing here bypasses, weakens or replaces the
permission gate -- every tool registers through the ordinary
:class:`~alan.tools.registry.ToolRegistry` and is decided there exactly like a
built-in.
"""

from __future__ import annotations

from .catalog import EccCatalog, Skill, parse_frontmatter
from .devtools import EccDevTools
from .paths import find_ecc_root
from .tools import SkillTools

__all__ = [
    "EccCatalog",
    "EccDevTools",
    "Skill",
    "SkillTools",
    "find_ecc_root",
    "parse_frontmatter",
]
