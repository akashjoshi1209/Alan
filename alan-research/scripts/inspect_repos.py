"""Harvest objective facts about each acquired repository.

This script deliberately produces **facts only** -- commit, licence, language
mix, declared dependencies, and a set of privacy/security signals found by
reading the repository. It does not decide whether Alan should use any of it.

That split matters. Licence identity, dependency list and telemetry calls are
mechanical and can be read off disk; "should Alan adopt this memory engine" is
a judgement that depends on Alan's own architecture and belongs to a human
reading the output. A script that emitted an integration verdict would be
making architecture decisions nobody reviewed, which is precisely the failure
mode this whole exercise exists to avoid.

Signals are conservative on purpose. A "telemetry" hit means the *string*
appears in the tree -- in source, a config, or documentation -- and is reported
as evidence to weigh, not as a proven behaviour.

Usage:  python inspect_repos.py [--out facts.json]
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "clone-results.json"
FACTS = ROOT / "repo-facts.json"

#: Extension -> language. Only extensions common enough to characterise a
#: project are listed; everything else is counted as "other".
LANGS = {
    ".py": "Python", ".pyi": "Python", ".rs": "Rust", ".go": "Go",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".swift": "Swift", ".c": "C", ".h": "C/C++ header", ".cpp": "C++",
    ".cc": "C++", ".hpp": "C/C++ header", ".cs": "C#", ".rb": "Ruby",
    ".php": "PHP", ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell",
    ".bat": "Batch", ".lua": "Lua", ".dart": "Dart", ".scala": "Scala",
    ".cu": "CUDA", ".m": "Objective-C", ".mm": "Objective-C++", ".zig": "Zig",
    ".md": "Markdown", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML",
    ".toml": "TOML", ".gradle": "Gradle", ".xml": "XML", ".html": "HTML",
    ".css": "CSS", ".ipynb": "Jupyter", ".proto": "Protobuf", ".txt": "Text",
}

LICENCE_FILES = [
    "LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md",
    "COPYING", "LICENSE-CODE", "LICENSE-MIT", "LICENSE-APACHE", "LICENSE-CC",
    "LICENSE.rst",
]

#: (text to match, SPDX id), in precedence order.
#:
#: Precedence matters and is not alphabetical. Most SPDX licences *mention*
#: other licences -- Apache-2.0's appendix ships an MIT template, BSD notices
#: reference the "3-clause BSD license" by name -- so a naive substring search
#: reports MIT for an Apache project. Longest and most specific first.
#:
#: `MIT License` is a title many projects omit, because the canonical MIT
#: LICENSE file opens with the copyright line and goes straight to "Permission
#: is hereby granted". Matching only the title therefore reports "unknown" for
#: ordinary MIT files -- a false negative that hides a perfectly usable licence,
#: which is worse than a noisy one. The grant sentence is the reliable signal.
LICENCE_MARKERS = [
    ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0"),
    ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL-3.0"),
    ("GNU GENERAL PUBLIC LICENSE", "GPL-3.0"),
    ("Mozilla Public License", "MPL-2.0"),
    ("Apache License", "Apache-2.0"),
    ("Attribution 4.0 International", "CC-BY-4.0"),
    ("Attribution-ShareAlike 4.0 International", "CC-BY-SA-4.0"),
    ("Attribution-NonCommercial", "CC-BY-NC-*"),
    ("Creative Commons Zero", "CC0-1.0"),
    ("The Unlicense", "Unlicense"),
    ("Business Source License", "BUSL"),
    ("Polyform Shield", "Polyform-Shield"),
    ("Redistribution and use in source and binary forms", "BSD-3-Clause"),
    ("Permission is hereby granted, free of charge", "MIT"),
    ("Permission to use, copy, modify, and/or distribute this software for any purpose", "ISC"),
]

#: Licences that are not open-source software licences. Adopting code under one
#: of these is a legal question, not a preference, so they are surfaced rather
#: than quietly normalised to a permissive-looking default.
#:
#: ``Polyform-Shield`` is source-available and non-commercial: it permits internal
#: use and forbids selling the software. ``CC-BY-*`` is a content licence being
#: applied to code, which is unusual and should be reviewed before reuse rather
#: than assumed permissive.
NON_OSD_LICENCES = {
    "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-*", "BUSL", "Polyform-Shield",
    "NONE-FOUND",
}

#: (label, patterns). Reported as evidence with a count, never as a conclusion.
CLOUD_SIGNALS = {
    "cloud_api_calls": [r"\bopenai\.", r"\banthropic\.", r"\bcohere\b", r"\bmistralai\b"],
    "telemetry_sdk": [r"\bsentry\b", r"\bposthog\b", r"\bsegment\.io\b", r"\bamplitude\b",
                      r"\bmixpanel\b", r"\btelemetry\b", r"\bdatadog\b"],
    "api_key_env": [r"OPENAI_API_KEY", r"ANTHROPIC_API_KEY", r"COHERE_API_KEY",
                    r"GITHUB_TOKEN", r"SLACK_BOT_TOKEN", r"DISCORD_TOKEN", r"HUGGINGFACE"],
    "auth_required": [r"\blogin\b.*\brequired\b", r"\bsign in\b.*\bto use\b"],
}

#: Supply-chain footguns worth surfacing before anyone runs anything.
RISK_SIGNALS = {
    "curl_pipe_shell": [r"curl[^\n|]*\|\s*(ba)?sh", r"wget[^\n|]*\|\s*(ba)?sh", r"iwr[^\n|]*\|\s*iex"],
    "postinstall_hook": [r"\"postinstall\"", r"\"preinstall\"", r"\"prepare\"\s*:\s*\"[^\"]*(make|cmake|python)"],
    "sudo_in_docs": [r"\bsudo\s+(apt|brew|dnf|pacman)\b"],
    "remote_code_load": [r"urllib\.request\.urlopen\(", r"requests\.get\(.*exec\("],
}

DEP_FILES = {
    "python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"],
    "node": ["package.json"],
    "rust": ["Cargo.toml"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "go": ["go.mod"],
}


def read_text(path: Path, limit: int = 400_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def scan_text_signals(root: Path, patterns: dict[str, list[str]]) -> dict[str, list[str]]:
    """Search the tree for each pattern group, reporting only real hits.

    Bounded on purpose: skip vendored trees and the largest binaries, or a
    single checkout of llama.cpp would spend minutes in a regex that nobody
    will read the output of.
    """
    skip = {".git", "node_modules", "vendor", "__pycache__", "dist", "build",
            "target", ".venv", "venv", "site-packages", "test-fixtures",
            "third_party", "3rdparty"}
    found: dict[str, list[str]] = {k: [] for k in patterns}
    scanned = 0
    for path in root.rglob("*"):
        if scanned > 6000:
            break
        if not path.is_file() or path.suffix.lower() not in {
            ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".rs", ".toml", ".json",
            ".yml", ".yaml", ".sh", ".kt", ".java", ".go", ".txt", ".cfg", ".ini",
        }:
            continue
        if skip & set(path.parts):
            continue
        try:
            if path.stat().st_size > 400_000:
                continue
        except OSError:
            continue
        scanned += 1
        body = read_text(path, 200_000)
        low = body.lower()
        for label, pats in patterns.items():
            for pat in pats:
                m = re.search(pat, body, re.IGNORECASE)
                if m:
                    rel = str(path.relative_to(root)).replace("\\", "/")
                    found[label].append(f"{rel}: {m.group(0)[:70]}")
                    break
    return {k: v[:6] for k, v in found.items()}


def detect_licence(root: Path) -> dict:
    """Identify every licence file, not just the first one.

    Dual-licensed repositories (``autogen`` ships CC-BY-4.0 for content and MIT
    for code, in two files) must not be reported as a single licence: reading the
    content licence alone would suggest the code is unusable, and reading the
    code licence alone would hide an attribution obligation. Both are reported.

    A repository with no licence file is *not* the same as one with a permissive
    licence -- absent a grant, default copyright applies and there is no
    permission to use. Where the README names a licence anyway, that is recorded
    as ``declared_in_readme`` so the gap stays visible instead of being quietly
    filled in.
    """
    found: list[dict] = []
    for name in LICENCE_FILES:
        p = root / name
        if not p.is_file():
            continue
        head = read_text(p, 6000).lower()
        spdx = "UNKNOWN"
        for needle, ident in LICENCE_MARKERS:
            if needle.lower() in head:
                spdx = ident
                break
        if spdx == "UNKNOWN":
            # The file may *name* licences for different subtrees rather than
            # containing one. ``AutoGPT`` does exactly this: Polyform Shield
            # inside ``autogpt_platform/``, MIT everywhere else. Naming the
            # split is far more useful than reporting "unknown" and leaving a
            # reader to assume the whole tree is unusable -- or usable.
            names = sorted(
                {
                    ident
                    for needle, ident in LICENCE_MARKERS
                    if needle.lower() in head
                }
            )
            if len(names) > 1:
                spdx = "COMPOSITE:" + "+".join(names)
            else:
                for named, ident in (
                    ("polyform shield", "Polyform-Shield"),
                    ("mit license", "MIT"),
                    ("apache license", "Apache-2.0"),
                    ("gplv3", "GPL-3.0"),
                    ("gpl-3.0", "GPL-3.0"),
                    ("agpl", "AGPL-3.0"),
                    ("lgpl", "LGPL-3.0"),
                ):
                    if named in head:
                        spdx = ident
                        break
        found.append({
            "file": name,
            "spdx": spdx,
            "osd": spdx not in NON_OSD_LICENCES and not spdx.startswith("COMPOSITE:"),
        })

    if not found:
        readme = next(
            (root / n for n in ("README.md", "README.rst", "README") if (root / n).is_file()),
            None,
        )
        declared = ""
        if readme is not None:
            m = re.search(
                r"(?i)licen[sc]e[^\n]{0,80}?(GPLv?3|AGPLv?3|LGPLv?3|MPL|Apache|MIT|BSD)\b",
                read_text(readme, 200_000),
            )
            if m:
                declared = m.group(1)
        return {
            "files": [],
            "spdx": "NONE-FOUND",
            "osd": False,
            "declared_in_readme": declared,
            "note": "no LICENSE file; default copyright applies, so there is no permission to use"
                    + (f"; README declares {declared}" if declared else ""),
        }

    # The most permissive file is the one that governs code reuse.
    def rank(f: dict) -> int:
        permissive = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC"}
        return 0 if (f["osd"] and f["spdx"] in permissive) else 1

    best = sorted(found, key=rank)[0]
    out = {"files": found, "spdx": best["spdx"], "osd": best["osd"], "dual": len(found) > 1}
    if len(found) > 1:
        out["note"] = "dual/multi-file: " + ", ".join(f"{f['file']}={f['spdx']}" for f in found)
    return out


def language_mix(root: Path) -> tuple[str, dict[str, int]]:
    skip = {".git", "node_modules", "vendor", "__pycache__", "dist", "build",
            "target", ".venv", "venv", "site-packages", "test-fixtures",
            "third_party", "3rdparty", "docs", "examples", "tests", "test"}
    counts: Counter[str] = Counter()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(root).parts)
        if len(parts) > 1 and (skip & parts):
            continue
        lang = LANGS.get(path.suffix.lower())
        if lang and lang not in {"Markdown", "JSON", "YAML", "TOML", "Text", "XML"}:
            counts[lang] += 1
    if not counts:
        return "unknown", {}
    return counts.most_common(1)[0][0], dict(counts.most_common(6))


def _py_deps(body: str) -> list[str]:
    """Read PEP 621 dependencies with ``tomllib``.

    Regex over a ``pyproject.toml`` is not viable: the identifier
    ``dependencies`` also appears in tool configuration (ruff, mypy) and in
    classifier lists, and a non-greedy ``[.*?]`` happily captures a version-pin
    array instead of the real dependency list. Earlier output proved the point --
    it returned ``.``, ``3.10``, ``E4``, ``--ignore`` as dependencies. Parse it.
    """
    try:
        data = tomllib.loads(body)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    out: list[str] = []
    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        out.append(re.split(r"[\s<>=!~;\[\(]", str(spec), 1)[0])
    for group in (project.get("optional-dependencies") or {}).values():
        for spec in group or []:
            out.append(re.split(r"[\s<>=!~;\[\(]", str(spec), 1)[0])
    poetry = ((data.get("tool") or {}).get("poetry") or {})
    for spec in (poetry.get("dependencies") or {}):
        if str(spec).lower() != "python":
            out.append(str(spec))
    return [d for d in out if d and not d.startswith(".")]


def _find_manifests(root: Path, names: tuple[str, ...], limit: int = 40) -> list[Path]:
    """Find manifests, including inside a monorepo's sub-packages.

    Most of these repositories are monorepos: ``langgraph`` keeps one
    ``pyproject.toml`` per ``libs/*`` package and has none at the root, and
    ``agent-framework`` is .NET with ``*.csproj`` throughout. A root-only lookup
    reports "no manifest" for the largest and most interesting projects, which
    is exactly backwards.
    """
    skip = {".git", "node_modules", "vendor", "target", "dist", "build",
            "__pycache__", ".venv", "venv", "site-packages", "docs", "examples"}
    found: list[Path] = []
    for path in root.rglob("*"):
        if len(found) >= limit:
            break
        if path.is_file() and path.name in names and not (skip & set(path.relative_to(root).parts)):
            found.append(path)
    # Shallowest first: the root or top-level package is the primary manifest.
    found.sort(key=lambda p: (len(p.relative_to(root).parts), p.stat().st_size))
    return found


def dependencies(root: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for eco, names in DEP_FILES.items():
        manifests = _find_manifests(root, tuple(names))
        if not manifests:
            continue
        p = manifests[0]
        body = read_text(p, 400_000)
        deps: list[str] = []
        if p.name == "requirements.txt":
            deps = [
                re.split(r"[<>=!~;\[\s]", ln.strip(), 1)[0]
                for ln in body.splitlines()
                if ln.strip() and not ln.startswith(("#", "-"))
            ]
        elif p.name == "pyproject.toml":
            deps = _py_deps(body)
        elif p.name == "package.json":
            try:
                pkg = json.loads(body)
                deps = list(pkg.get("dependencies") or {})
            except ValueError:
                pass
        elif p.name == "Cargo.toml":
            try:
                data = tomllib.loads(body)
                deps = list((data.get("dependencies") or {}))
            except (tomllib.TOMLDecodeError, ValueError):
                m3 = re.search(r"(?s)\[dependencies\](.*?)(?:\n\[|\Z)", body)
                if m3:
                    deps = re.findall(r"^([a-zA-Z0-9_\-]+)\s*=", m3.group(1), re.M)
        elif p.name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            deps = re.findall(r'(?i)artifactId["\'=:\s]+([A-Za-z0-9_.\-]+)', body)
        elif p.name == "go.mod":
            m4 = re.search(r"(?s)require\s*\((.*?)\)", body)
            if m4:
                deps = re.findall(r"^\s*([a-z0-9./\-]+)\s+v", m4.group(1), re.M)
        if deps:
            key = f"{eco}:{p.relative_to(root)}".replace("\\", "/")
            out[key] = sorted({d for d in deps if d and not d.startswith(("-", "."))})[:40]
        else:
            # A manifest was found but declares no required dependencies. That is
            # meaningful -- ``pydantic-ai`` keeps everything optional behind extras
            # -- and must not be reported the same way as having no manifest at
            # all, which means the repository is a monorepo whose primary
            # ecosystem was not detected.
            out[f"manifest:{eco}:{p.relative_to(root)}".replace("\\", "/")] = ["(no required deps)"]
    return out


def count_files(root: Path) -> dict:
    files = 0
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            files += 1
    return {"files": files}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=FACTS)
    args = ap.parse_args()

    if not RESULTS.exists():
        print(f"no {RESULTS}; run acquire.py first")
        return 2
    results = json.loads(RESULTS.read_text(encoding="utf-8"))["repositories"]

    facts: list[dict] = []
    for r in results:
        root = ROOT / r["path"]
        entry = {
            "name": r["name"],
            "category": r["category"],
            "url": r["url"],
            "priority": r.get("priority", ""),
            "kind": r.get("kind", ""),
            "status": r["status"],
            "commit": r.get("commit", ""),
            "commit_date": r.get("commit_date", ""),
            "branch": r.get("branch", ""),
            "size_mb": r.get("size_mb"),
            "error": r.get("error", ""),
        }
        if r["status"] in {"cloned", "already-present", "no-commit"} and root.is_dir():
            primary, mix = language_mix(root)
            entry.update(
                licence=detect_licence(root),
                language=primary,
                language_mix=mix,
                dependencies=dependencies(root),
                privacy_signals=scan_text_signals(root, CLOUD_SIGNALS),
                risk_signals=scan_text_signals(root, RISK_SIGNALS),
                **count_files(root),
            )
        facts.append(entry)
        lic = entry.get("licence", {})
        flag = " " if lic.get("osd", True) else "*"
        if lic.get("dual"):
            flag = "D"
        print(
            f"{entry['name']:<24} {entry['status']:<14} "
            f"{entry.get('language', '-'):<12} {flag} {str(lic.get('spdx', '-')):<24} "
            f"{entry.get('files', '-')!s:>7} files"
        )
        if lic.get("note"):
            print(f"{'':<25}note: {lic['note']}")

    args.out.write_text(json.dumps({"repositories": facts}, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for f in facts if f["status"] in {"cloned", "already-present", "no-commit"})
    blocked = [
        f["name"] for f in facts
        if f.get("licence") and not f["licence"].get("osd", True)
    ]
    print(f"\ninspected {ok}/{len(facts)} -> {args.out}")
    print("legend: * = not an OSI software licence, D = multiple licence files")
    if blocked:
        print("licence review required: " + ", ".join(blocked))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
