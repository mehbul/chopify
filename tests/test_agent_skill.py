import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_files_exist_and_are_identical():
    root_skill = ROOT / "SKILL.md"
    copy = ROOT / "skills" / "chopify" / "SKILL.md"
    assert root_skill.exists() and copy.exists()
    assert root_skill.read_text(encoding="utf-8") == copy.read_text(encoding="utf-8")


def test_skill_has_frontmatter_and_workflow():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: chopify" in text
    assert "description:" in text
    assert "project.create" in text
    assert "project.prepare" in text
    assert "project.get" in text
    assert "project.render" in text
    assert "project.wait" in text


def test_skill_operation_names_are_in_catalog():
    proc = subprocess.run([sys.executable, "agent.py", "describe"],
                          cwd=ROOT, capture_output=True, text=True)
    catalog = json.loads(proc.stdout.strip().splitlines()[-1])["data"]
    names = {op["name"] for op in catalog["operations"]}
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    mentioned = set(re.findall(
        r"\b(?:project|batch|live|preflight|styles|describe)\.[a-z.]+"
        r"|\b(?:describe|preflight|styles)\b", text))
    assert mentioned <= names | {"describe", "preflight", "styles"}
