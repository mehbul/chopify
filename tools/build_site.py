"""Build the public docs from an explicit allowlist; never copy local projects."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
FILES = {"docs/index.html": "index.html", "docs/site.css": "site.css",
         "docs/sitemap.xml": "sitemap.xml", "docs/robots.txt": "robots.txt",
         "docs/product.json": "product.json", "llms.txt": "llms.txt",
         "assets/demo.gif": "demo.gif"}
GUIDES = ["README.md", "docs/WORKFLOWS.md", "docs/AGENT-API.md", "docs/TROUBLESHOOTING.md"]


def build(out):
    out = Path(out).resolve()
    if out == ROOT or ROOT.is_relative_to(out) or out.is_relative_to(ROOT / "projects"):
        raise ValueError("Choose a dedicated output directory, such as _site.")
    # Refuse to mix a publication bundle with arbitrary existing files.
    allowed = set(FILES.values()) | {"llms-full.txt", ".nojekyll"}
    if out.exists() and any(p.name not in allowed or not p.is_file() for p in out.iterdir()):
        raise ValueError("Output directory contains unrelated files. Choose an empty directory.")
    out.mkdir(parents=True, exist_ok=True)
    for source, target in FILES.items():
        shutil.copy2(ROOT / source, out / target)
    parts = ["# Chopify full documentation\n\nSource: https://github.com/mehbul/chopify\n"]
    for guide in GUIDES:
        parts.append(f"\n\n---\nSource: https://github.com/mehbul/chopify/blob/main/{guide}\n\n"
                     + (ROOT / guide).read_text(encoding="utf-8"))
    (out / "llms-full.txt").write_text("".join(parts), encoding="utf-8")
    (out / ".nojekyll").touch()
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="_site")
    print(build(parser.parse_args().out))
