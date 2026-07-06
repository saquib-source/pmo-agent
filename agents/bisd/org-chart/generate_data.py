#!/usr/bin/env python3
"""Regenerate the org-chart tree (window.DATA) inside index.html.

Source of truth: naming_tree.txt — the BISD Business System Naming Tree,
one node per line, 4-space indentation per level. Edit that file (or drop in
a new export), run this script, and the chart follows. Nothing else changes:
simulated metrics stay deterministic by node path, and any real numbers live
in the EASY VALUE DEFINITIONS block (window.VALUES) in index.html.

Levels are assigned by depth:
  0 Subsidiary · 1 Division · 2 Department · 3 Sub-Department · 4 Function

Run:  python3 generate_data.py
"""

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
TREE_TXT = HERE / "naming_tree.txt"
INDEX = HERE / "index.html"
LEVELS = ["Subsidiary", "Division", "Department", "Sub-Department", "Function"]
INDENT = 4


def parse_tree(path: pathlib.Path) -> dict:
    root = None
    stack = []  # (depth, node)
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        stripped = raw.lstrip(" ")
        spaces = len(raw) - len(stripped)
        if spaces % INDENT:
            sys.exit(f"{path.name}:{lineno}: indentation must be a multiple of {INDENT} spaces")
        depth = spaces // INDENT
        if depth >= len(LEVELS):
            sys.exit(f"{path.name}:{lineno}: depth {depth} exceeds the {len(LEVELS)}-level model")
        node = {"name": stripped.strip(), "children": [], "level": LEVELS[depth]}
        if depth == 0:
            if root is not None:
                sys.exit(f"{path.name}:{lineno}: more than one root")
            root = node
            stack = [(0, node)]
            continue
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if not stack or stack[-1][0] != depth - 1:
            sys.exit(f"{path.name}:{lineno}: '{node['name']}' skips a level")
        stack[-1][1]["children"].append(node)
        stack.append((depth, node))
    if root is None:
        sys.exit(f"{path.name}: empty tree")
    return root


def main() -> None:
    tree = parse_tree(TREE_TXT)
    payload = json.dumps(tree, ensure_ascii=False, separators=(",", ":"))

    html = INDEX.read_text(encoding="utf-8")
    new_html, n = re.subn(
        r"window\.DATA=\{.*?\};</script>",
        lambda _: "window.DATA=" + payload + ";</script>",
        html,
        count=1,
        flags=re.S,
    )
    if n != 1:
        sys.exit("index.html: could not find the window.DATA=...; block")
    INDEX.write_text(new_html, encoding="utf-8")

    counts = {}
    def tally(node):
        counts[node["level"]] = counts.get(node["level"], 0) + 1
        for child in node["children"]:
            tally(child)
    tally(tree)
    total = sum(counts.values())
    print(f"window.DATA regenerated from {TREE_TXT.name}: {total} nodes "
          f"({', '.join(f'{counts.get(l, 0)} {l}' for l in LEVELS)})")


if __name__ == "__main__":
    main()
