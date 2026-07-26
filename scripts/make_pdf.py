"""Export DOCUMENTATION.md to a print-ready PDF with rendered mermaid diagrams.

Pipeline: pandoc (GFM -> HTML fragment) -> inline mermaid.js + print CSS ->
headless Chrome --print-to-pdf.

Mermaid blocks are lifted out before pandoc runs and re-inserted afterwards as
raw <pre class="mermaid">. Letting pandoc touch them applies syntax
highlighting, which wraps the diagram source in <span> tags and mermaid then
fails to parse its own input.

Usage:
    python scripts/make_pdf.py                       # -> DOCUMENTATION.pdf
    python scripts/make_pdf.py --out other.pdf
    python scripts/make_pdf.py --md README.md
"""

import argparse
import base64
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Downloaded once; keep a local copy so the export works offline and the PDF
# can be regenerated identically later.
MERMAID_CANDIDATES = [
    REPO / "scripts" / "mermaid.min.js",
    Path(
        r"C:\Users\parot\AppData\Local\Temp\claude"
        r"\c--Users-parot-Desktop-Hackathon"
        r"\2e78d0d8-dfa7-4dae-993b-e124ebfd3be6\scratchpad\mermaid.min.js"
    ),
]

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", -apple-system, Helvetica, Arial, sans-serif;
  font-size: 10.2pt; line-height: 1.5; color: #14161a; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1 { font-size: 21pt; margin: 0 0 .3em; border-bottom: 2.5px solid #14161a; padding-bottom: .25em; }
h2 {
  font-size: 13.5pt; margin: 1.5em 0 .5em; padding-bottom: .18em;
  border-bottom: 1px solid #c8ccd2; break-after: avoid; page-break-after: avoid;
}
h3 { font-size: 11pt; margin: 1.1em 0 .4em; break-after: avoid; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code {
  font-family: Consolas, "SF Mono", monospace; font-size: 8.9pt;
  background: #f1f3f5; padding: .1em .3em; border-radius: 3px;
}
pre {
  background: #f7f8fa; border: 1px solid #dfe3e8; border-left: 3px solid #6b7480;
  border-radius: 4px; padding: .6em .8em; overflow-x: auto;
  break-inside: avoid; page-break-inside: avoid;
}
pre code { background: none; padding: 0; font-size: 8.4pt; line-height: 1.42; }
table {
  border-collapse: collapse; width: 100%; margin: .8em 0; font-size: 8.9pt;
  break-inside: avoid; page-break-inside: avoid;
}
th, td { border: 1px solid #ccd1d7; padding: .34em .5em; text-align: left; vertical-align: top; }
th { background: #eef0f3; font-weight: 600; }
tbody tr:nth-child(even) { background: #fafbfc; }
blockquote {
  margin: .8em 0; padding: .5em .9em; background: #fff8e6;
  border-left: 3px solid #d9a300; color: #4a3c10;
}
/* Both diagrams are wide left-to-right graphs. Constrained to portrait text
   width they render legible-to-a-machine but unreadable to a human, which
   defeats the point of shipping them. Chrome named pages (111+) let just
   these two blocks print landscape, roughly doubling usable width. */
@page diagram { size: A4 landscape; margin: 10mm; }
.diagram-page {
  page: diagram;
  break-before: page; page-break-before: always;
  break-after: page;  page-break-after: always;
  display: flex; flex-direction: column; justify-content: center;
  min-height: 175mm;
}
.diagram-page .diagram-caption {
  font-size: 8.5pt; color: #6b7480; text-align: center; margin-bottom: .4em;
  text-transform: uppercase; letter-spacing: .06em;
}
.mermaid {
  text-align: center; margin: 0 auto; background: #fff; width: 100%;
  break-inside: avoid; page-break-inside: avoid;
}
.mermaid svg { max-width: 100% !important; height: auto !important; }
a { color: #14161a; text-decoration: none; }
hr { border: 0; border-top: 1px solid #d6dae0; margin: 1.6em 0; }
"""

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>{css}</style>
<script>{mermaid_js}</script>
</head><body>
{body}
<script>
  mermaid.initialize({{
    startOnLoad: true,
    theme: "neutral",
    flowchart: {{ useMaxWidth: true, htmlLabels: true, curve: "basis" }},
    er: {{ useMaxWidth: true }},
    securityLevel: "loose"
  }});
</script>
</body></html>
"""


def find(paths, what):
    for p in paths:
        if Path(p).exists():
            return str(p)
    sys.exit(f"ERROR: could not find {what}. Looked in:\n  " + "\n  ".join(str(p) for p in paths))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default="DOCUMENTATION.md")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    md_path = REPO / args.md
    if not md_path.exists():
        sys.exit(f"ERROR: {md_path} not found")
    out_pdf = REPO / (args.out or (md_path.stem + ".pdf"))

    if not shutil.which("pandoc"):
        sys.exit("ERROR: pandoc not on PATH")
    mermaid_js = Path(find(MERMAID_CANDIDATES, "mermaid.min.js")).read_text(encoding="utf-8")
    chrome = find(CHROME_CANDIDATES, "Chrome or Edge")

    src = md_path.read_text(encoding="utf-8")

    # Lift mermaid fences out before pandoc sees them.
    blocks: list[str] = []

    def stash(m: re.Match) -> str:
        blocks.append(m.group(1))
        return f"\n\nMERMAIDPLACEHOLDER{len(blocks) - 1}ENDPLACEHOLDER\n\n"

    src = re.sub(r"```mermaid\n(.*?)```", stash, src, flags=re.S)
    print(f"  lifted {len(blocks)} mermaid diagram(s)")

    with tempfile.TemporaryDirectory() as td:
        tmp_md = Path(td) / "in.md"
        tmp_md.write_text(src, encoding="utf-8")
        body = subprocess.run(
            ["pandoc", "-f", "gfm", "-t", "html5", "--no-highlight", str(tmp_md)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout

        # Re-insert as raw text so mermaid parses its own source.
        for i, block in enumerate(blocks):
            needle = re.compile(
                rf"(?:<p>)?\s*MERMAIDPLACEHOLDER{i}ENDPLACEHOLDER\s*(?:</p>)?", re.S
            )
            if not needle.search(body):
                print(f"  WARNING: placeholder {i} not found in HTML")
            caption = "Figure {} — {}".format(
                i + 1,
                "System architecture" if "flowchart" in block else "Entity relationships",
            )
            replacement = (
                f'<div class="diagram-page">'
                f'<div class="diagram-caption">{caption}</div>'
                f'<pre class="mermaid">{html.escape(block)}</pre>'
                f"</div>"
            )
            body = needle.sub(replacement, body, count=1)

        # Inline every local image as a data URI. The HTML is written to a temp
        # directory, so relative <img src> paths would resolve to nothing and
        # Chrome would silently print empty boxes.
        def inline_img(m: re.Match) -> str:
            src = m.group(1)
            if src.startswith(("http://", "https://", "data:")):
                return m.group(0)
            p = (REPO / src).resolve()
            if not p.exists():
                print(f"  WARNING: image not found, skipping: {src}")
                return m.group(0)
            mime = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
            }.get(p.suffix.lower(), "application/octet-stream")
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            print(f"  inlined image: {src} ({p.stat().st_size / 1024:.0f} KB)")
            return m.group(0).replace(src, f"data:{mime};base64,{b64}")

        body = re.sub(r'<img[^>]*\bsrc="([^"]+)"', inline_img, body)

        page = PAGE.format(title=md_path.stem, css=CSS, mermaid_js=mermaid_js, body=body)
        tmp_html = Path(td) / "doc.html"
        tmp_html.write_text(page, encoding="utf-8")

        if out_pdf.exists():
            out_pdf.unlink()
        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--no-pdf-header-footer",
            # Mermaid renders asynchronously; without a virtual clock budget
            # Chrome prints the page before any diagram exists.
            "--virtual-time-budget=45000",
            "--run-all-compositor-stages-before-draw",
            f"--print-to-pdf={out_pdf}",
            tmp_html.as_uri(),
        ]
        print(f"  rendering via {Path(chrome).name} ...")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if not out_pdf.exists():
            print(r.stdout[-2000:])
            print(r.stderr[-2000:])
            sys.exit("ERROR: Chrome produced no PDF")

    size = out_pdf.stat().st_size
    print(f"\nOK  {out_pdf}  ({size / 1024:.0f} KB)")
    if size < 40_000:
        print("  WARNING: suspiciously small - diagrams may not have rendered")


if __name__ == "__main__":
    main()
