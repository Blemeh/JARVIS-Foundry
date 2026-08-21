"""Render V3 God Mode execution plan markdown to PDF via Playwright."""
from __future__ import annotations

import re
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "docs" / "v3" / "08_V3_GOD_MODE_EXECUTION_PLAN.md"
OUT_PDF = ROOT / "evidence" / "v3" / "design" / "V3_GOD_MODE_EXECUTION_PLAN.pdf"
OUT_HTML = ROOT / "evidence" / "v3" / "design" / "V3_GOD_MODE_EXECUTION_PLAN.html"

# PDF: one compact mermaid only; ASCII diagrams always included
MAX_MERMAID_BLOCKS = 1


def md_to_html_body(text: str) -> str:
    mermaid_count = 0
    parts: list[str] = []
    pattern = re.compile(r"```(mermaid|text)?\n(.*?)```", re.DOTALL)
    last = 0
    for match in pattern.finditer(text):
        before = text[last : match.start()]
        if before.strip():
            parts.append(markdown.markdown(before, extensions=["tables", "fenced_code"]))
        lang = match.group(1) or ""
        code = match.group(2).strip()
        if lang == "mermaid":
            mermaid_count += 1
            if mermaid_count <= MAX_MERMAID_BLOCKS:
                parts.append(
                    f'<div class="diagram-box"><pre class="mermaid diagram-compact">\n{code}\n</pre></div>'
                )
            # skip extra mermaid blocks — ASCII covers the rest
        else:
            parts.append(f'<pre class="ascii-diagram"><code>{code}</code></pre>')
        last = match.end()
    tail = text[last:]
    if tail.strip():
        parts.append(markdown.markdown(tail, extensions=["tables", "fenced_code"]))
    return "\n".join(parts)


def build_html() -> str:
    md = MD_PATH.read_text(encoding="utf-8")
    # drop markdown image embeds — they cause blank pages in print
    md = re.sub(r"!\[[^\]]*\]\([^)]+\)\s*\n?", "", md)
    body = md_to_html_body(md)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>V3 God Mode Execution Plan</title>
  <style>
    @page {{
      size: A4;
      margin: 14mm 12mm;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Segoe UI", Calibri, Arial, sans-serif;
      font-size: 9.5pt;
      line-height: 1.4;
      color: #111;
      margin: 0;
      padding: 0;
    }}
    h1 {{
      font-size: 17pt;
      border-bottom: 2px solid #333;
      padding-bottom: 4px;
      margin-top: 0;
      page-break-after: avoid;
    }}
    h2 {{
      font-size: 12.5pt;
      margin-top: 1.1em;
      page-break-after: avoid;
      page-break-before: auto;
    }}
    h3, h4 {{
      font-size: 10.5pt;
      page-break-after: avoid;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      margin: 8px 0;
      font-size: 8.5pt;
      page-break-inside: avoid;
    }}
    th, td {{
      border: 1px solid #ccc;
      padding: 4px 6px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: #f0f0f0; }}
    pre.ascii-diagram {{
      background: #f6f8fa;
      border: 1px solid #ccc;
      padding: 8px 10px;
      font-size: 7.5pt;
      line-height: 1.25;
      white-space: pre;
      overflow-x: auto;
      page-break-inside: avoid;
      margin: 8px 0;
    }}
    pre:not(.mermaid) code {{
      font-family: Consolas, "Courier New", monospace;
    }}
    .diagram-box {{
      page-break-inside: avoid;
      margin: 10px 0;
      text-align: center;
    }}
    .diagram-compact {{
      display: inline-block;
      max-width: 100%;
      font-size: 8pt;
      background: #fafafa;
      border: 1px solid #ddd;
      padding: 6px;
      margin: 0 auto;
    }}
    .mermaid svg {{
      max-width: 100% !important;
      height: auto !important;
      max-height: 220px !important;
    }}
    ul, ol {{ margin: 0.3em 0 0.6em 1.1em; }}
    li {{ margin: 0.15em 0; }}
    hr {{
      border: none;
      border-top: 1px solid #ddd;
      margin: 0.8em 0;
      page-break-after: avoid;
    }}
    p {{ orphans: 3; widows: 3; }}
    h2 + table, h3 + table {{ page-break-before: avoid; }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def main() -> None:
    html = build_html()
    OUT_HTML.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 794, "height": 1123})
        page.goto(OUT_HTML.as_uri(), wait_until="networkidle")
        page.add_script_tag(url="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js")
        page.evaluate(
            """async () => {
              mermaid.initialize({
                startOnLoad: false,
                theme: 'default',
                securityLevel: 'loose',
                flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' }
              });
              await mermaid.run({ querySelector: '.mermaid' });
            }"""
        )
        page.wait_for_timeout(2500)
        # scale down any oversized SVG
        page.evaluate(
            """() => {
              document.querySelectorAll('.mermaid svg').forEach(svg => {
                svg.style.maxHeight = '200px';
                svg.style.width = '100%';
                svg.removeAttribute('height');
              });
            }"""
        )
        page.wait_for_timeout(500)
        page.pdf(
            path=str(OUT_PDF),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
        )
        browser.close()
    print(f"Wrote {OUT_PDF}")
    print(f"Wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
