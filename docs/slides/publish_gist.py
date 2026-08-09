"""Publish the slide decks to the public gist they used to be linked from.

LEGACY. The canonical copy is now GitHub Pages -- .github/workflows/pages.yml serves
docs/slides/ at /slides/ on every push to main, where the relative asset paths resolve
on their own and none of the rewriting below is needed. The gist URL has been shared,
so this keeps it working; run it only if you care about that copy.

The decks are self-contained HTML, but their images and videos are relative
(src="assets/..."), which only resolves when the deck sits next to
docs/slides/assets/ -- on a gist there are no directories, so every asset 404s.
So the published copy gets those paths rewritten to raw.githubusercontent.com
URLs on main. The files in the repo keep the relative paths and are unchanged.

Because the rewritten URLs point at main, a slide that references a NEW asset
needs that asset pushed to main before publishing, or the gist renders a hole.

    uv run docs/slides/publish_gist.py            # publish
    uv run docs/slides/publish_gist.py --dry-run  # show what would change
"""

import io
import json
import pathlib
import subprocess
import sys

GIST = "36e7232e247738f36460c5d1a97191ab"
RAW = "https://raw.githubusercontent.com/kazunori279/xls32-fpga-synth/main/docs/slides/"
HERE = pathlib.Path(__file__).parent
NAMES = ("index.html", "index_ja.html")

files = {}
for name in NAMES:
    src = io.open(HERE / name, encoding="utf-8").read()
    n = src.count('src="assets/')
    for rel in sorted({s.split('"')[0] for s in src.split('src="assets/')[1:]}):
        if not (HERE / "assets" / rel).exists():
            sys.exit(f"{name}: assets/{rel} is missing -- it would 404 on the gist")
    files[name] = {"content": src.replace('src="assets/', f'src="{RAW}assets/')}
    print(f"{name}: {n} asset paths rewritten to main")

if "--dry-run" in sys.argv:
    sys.exit(0)

body = json.dumps({"files": files})
out = subprocess.run(["gh", "api", "-X", "PATCH", f"gists/{GIST}", "--input", "-"],
                     input=body, capture_output=True, text=True)
if out.returncode:
    sys.exit(out.stderr.strip())
for name, meta in json.loads(out.stdout)["files"].items():
    print(f"published {name}  {meta['size']} bytes")
print(f"https://gist.github.com/kazunori279/{GIST}")
