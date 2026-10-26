"""Responsibilities: Discover repository-owned workflow skills independently of Python tools.
Implementation: Load bounded Markdown workflows/references and declarative JSON tool policies
on demand; initial discovery exposes only single-line name/description frontmatter.
Related Modules: backend/agent_skills; tools; runtime.
Declaration Index: catalog; tool_policy; read_skill; instructions; fingerprint.
Variable Index: ROOT — trusted skill directory, never selected by user/model paths;
MAX_SKILL_CHARS — combined workflow/reference character limit.
"""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "agent_skills"
MAX_SKILL_CHARS = 16000


def catalog():
    """Functionality: Read only skill discovery metadata.
    Inputs: Trusted ROOT/*/SKILL.md files; Outputs: name-to-description mapping.
    Logic: Accept an intentionally small frontmatter contract and reject malformed packages.
    Constraints: No shell, imports, arbitrary paths or executable skill content.
    """
    result = {}
    for path in sorted(ROOT.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\nname: ([a-z][a-z0-9-]*)\ndescription: ([^\n]+)\n---\n", text)
        if not match or match[1] != path.parent.name or len(text) > MAX_SKILL_CHARS:
            raise ValueError(f"Invalid skill package: {path.parent.name}")
        result[match[1]] = match[2]
    if not result:
        raise ValueError("No agent skills installed")
    return result


def tool_policy(name):
    """Functionality: Load declarative tool scope, prerequisites and per-turn invocation limits.
    Inputs: discovered skill and tools.json; Outputs: validated policy dictionary.
    Logic: Validate membership, acyclic dependencies, limits, result-count pause conditions
    optional boolean confirmation fields and deterministic candidate-presentation templates;
    runtime intersects
    the scope with real discovery. Constraints: No executable code, intent keywords or permissions.
    """
    if name not in catalog():
        raise ValueError("Unknown skill")
    source = (ROOT / name / "tools.json").read_text(encoding="utf-8")
    if len(source) > 4096:
        raise ValueError("Skill tool scope too large")
    policy = json.loads(source)
    if (not isinstance(policy, dict)
            or set(policy) != {"tools", "requires", "limits", "result_counts"}):
        raise ValueError("Invalid skill policy")
    names, requires, limits = policy["tools"], policy["requires"], policy["limits"]
    if (not isinstance(names, list) or not names or len(names) > 40
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", item)
                   for item in names) or len(set(names)) != len(names)):
        raise ValueError("Invalid skill tool scope")
    if not isinstance(requires, dict) or not isinstance(limits, dict):
        raise ValueError("Invalid tool constraints")
    for tool, dependencies in requires.items():
        if (tool not in names or not isinstance(dependencies, list)
                or any(dependency not in names or dependency == tool
                       for dependency in dependencies)):
            raise ValueError("Invalid tool dependency")
    resolved = set()
    while len(resolved) < len(names):
        ready = {tool for tool in names if set(requires.get(tool, [])).issubset(resolved)}
        if ready.issubset(resolved):
            raise ValueError("Cyclic tool dependencies")
        resolved.update(ready)
    for tool, limit in limits.items():
        if tool not in names or type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError("Invalid tool limit")
    if not isinstance(policy["result_counts"], dict):
        raise ValueError("Invalid result constraints")
    for tool, rule in policy["result_counts"].items():
        if (tool not in names or not isinstance(rule, dict)
                or not {"field", "min", "max"}.issubset(rule)
                or set(rule) - {"field", "min", "max", "confirm_if", "selection"}
                or not isinstance(rule["field"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_]*", rule["field"])
                or type(rule["min"]) is not int or type(rule["max"]) is not int
                or not 0 <= rule["min"] <= rule["max"] <= 1000):
            raise ValueError("Invalid result count constraint")
        if "confirm_if" in rule and (
            not isinstance(rule["confirm_if"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]*", rule["confirm_if"])
        ):
            raise ValueError("Invalid result confirmation field")
        if "selection" in rule:
            selection = rule["selection"]
            if (not isinstance(selection, dict)
                    or set(selection) != {"label_field", "max_display", "prompt", "guard_tools",
                                          "coordinate_tool", "coordinate_argument"}
                    or not isinstance(selection["label_field"], str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]*", selection["label_field"])
                    or type(selection["max_display"]) is not int
                    or not 1 <= selection["max_display"] <= 5
                    or not isinstance(selection["prompt"], dict)
                    or set(selection["prompt"]) != {"zh", "en"}
                    or any(not isinstance(value, str) or not 1 <= len(value) <= 300
                           for value in selection["prompt"].values())
                    or not isinstance(selection["guard_tools"], list)
                    or not selection["guard_tools"]
                    or any(not isinstance(tool, str) or tool not in names
                           for tool in selection["guard_tools"])
                    or selection["coordinate_tool"] not in selection["guard_tools"]
                    or not isinstance(selection["coordinate_argument"], str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]*", selection["coordinate_argument"])):
                raise ValueError("Invalid selection presentation policy")
    return policy


def read_skill(name):
    """Functionality: Load a discovered workflow on demand.
    Inputs: Exact catalog name; Outputs: combined workflow/reference Markdown or ValueError.
    Logic: Membership check precedes resolution; only this skill's references/*.md are loaded,
    with a combined size bound and resolved-path containment check.
    Constraints: No model-supplied paths, global knowledge preload or executable references.
    """
    if name not in catalog():
        raise ValueError("Unknown skill")
    directory = ROOT / name
    text = (directory / "SKILL.md").read_text(encoding="utf-8")
    for path in sorted((directory / "references").glob("*.md")):
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Skill reference escapes its package")
        if path.stat().st_size > MAX_SKILL_CHARS * 4:
            raise ValueError("Skill reference too large")
        text += f"\n\nReference: {path.name}\n" + path.read_text(encoding="utf-8")
        if len(text) > MAX_SKILL_CHARS:
            raise ValueError("Combined skill context too large")
    return text


def instructions():
    """Functionality: Construct the generic harness prompt and skill catalog.
    Inputs: Trusted system.md and catalog; Outputs: prompt string.
    Logic: Workflows remain in Markdown; only metadata is preloaded.
    Constraints: Only read_skill loads trusted workflows; other tool/page text is untrusted data.
    """
    return (
        (ROOT / "system.md").read_text(encoding="utf-8")
        + "\nAvailable skills:\n"
        + "\n".join(f"- {name}: {description}" for name, description in catalog().items())
    )


def fingerprint():
    """Functionality: Identify the exact prompt/skill bundle for evaluation reproducibility.
    Inputs: All trusted Markdown/JSON paths and bytes; Outputs: SHA256 hex string.
    Logic: Hash relative path and bytes in sorted order; Constraints: no user or secret content.
    """
    digest = hashlib.sha256()
    for path in sorted(path for path in ROOT.rglob("*") if path.suffix in {".md", ".json"}):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
