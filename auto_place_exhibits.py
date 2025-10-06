#!/usr/bin/env python3
"""
Auto-place exhibits by EXIF/metadata date.
- Scans ./incoming_media/* for photos/videos
- Extracts date via exiftool (DateTimeOriginal/CreateDate/MediaCreateDate)
- If timeline has that date (H2 id like 'MM-DD-YYYY'), append exhibit links under that date
  - Branded page: bullet list items
  - Blue page: colored numbered badges
- Copies files into ./site/exhibits/YYYY-MM-DD/
- For no-match dates, files go to ./site/exhibits/unsorted/
- Rebuilds fresh ZIPs: static_site_bundle.zip, static_site_blue_bundle.zip
"""

import subprocess, json, shutil, re, html, zipfile
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
SITE = ROOT / "site"
BRANDED = SITE / "index.html"
BLUE = SITE / "blue" / "index.html"
INCOMING = ROOT / "incoming_media"
EXHIBITS = SITE / "exhibits"

assert BRANDED.exists(), "site/index.html not found"
assert BLUE.exists(), "site/blue/index.html not found"
assert INCOMING.exists(), "incoming_media folder not found (create it and drop files in)"

AUTO_FIX = """
<script>
(function(){
  document.querySelectorAll('a[href*="dropbox.com"], a[href*="/exhibits/"]').forEach(a=>{
    try{
      const u = new URL(a.href, window.location.href);
      if(u.hostname.includes('dropbox.com')){
        u.searchParams.delete('dl');
        u.searchParams.set('raw','1');
        a.href = u.toString();
      }
      a.target = '_blank';
      a.rel = 'noopener';
    }catch(e){}
  });
})();
</script>
""".strip()

# Colors for blue-numbered badges
PALETTE = ["#0b5bd3","#0a7f4a","#af5f00","#7a2ad3","#b1182b","#c0392b","#16a085"]

def run_exiftool(path: Path):
    """Return a dict with best-effort date strings, or {} if not available."""
    try:
        out = subprocess.check_output(
            ["exiftool", "-j",
             "-DateTimeOriginal", "-CreateDate", "-MediaCreateDate",
             "-QuickTime:CreateDate", "-QuickTime:CreateTime",
             "-FileModifyDate", str(path)],
            stderr=subprocess.DEVNULL, text=True
        )
        data = json.loads(out)[0] if out else {}
        return data
    except Exception:
        return {}

def best_date(meta: dict, fallback_time: float):
    cand = (
        meta.get("DateTimeOriginal")
        or meta.get("CreateDate")
        or meta.get("MediaCreateDate")
        or meta.get("QuickTime:CreateDate")
        or meta.get("QuickTime:CreateTime")
        or None
    )
    if cand:
        # Normalize 'YYYY:MM:DD HH:MM:SS' or variants
        cand = cand.replace("-", ":")
        try:
            dt = datetime.strptime(cand.split(".")[0], "%Y:%m:%d %H:%M:%S")
            return dt.date()
        except Exception:
            pass
    # Fallback to file modified time
    return datetime.fromtimestamp(fallback_time).date()

def add_autofix_once(html_src: str):
    if "document.querySelectorAll('a[href*=\"dropbox.com\"], a[href*=\"/exhibits/\"]')" in html_src:
        return html_src
    m = re.search(r"</body\s*>", html_src, flags=re.I)
    return (html_src[:m.start()] + "\n" + AUTO_FIX + "\n" + html_src[m.start():]) if m else (html_src + "\n" + AUTO_FIX + "\n")

def ensure_date_section(html_src: str, date_str_mmddyyyy: str):
    # look for <h2 id="MM-DD-YYYY">
    m = re.search(rf"<h2[^>]*id=['\"]{re.escape(date_str_mmddyyyy)}['\"][^>]*>.*?</h2>", html_src, flags=re.I|re.S)
    if m: return html_src, m.end(), True
    # If not present, create a new section before Exhibit Index
    ex = re.search(r"<h2>Exhibit Index", html_src, flags=re.I)
    at = ex.start() if ex else len(html_src)
    hdr = f"\n<h2 id='{date_str_mmddyyyy}' style='color:#0b5bd3;font-weight:800'>{date_str_mmddyyyy.replace('-', '/')}</h2>\n"
    html_src = html_src[:at] + hdr + html_src[at:]
    m = re.search(rf"<h2[^>]*id=['\"]{re.escape(date_str_mmddyyyy)}['\"][^>]*>.*?</h2>", html_src, flags=re.I|re.S)
    return html_src, m.end(), False

def inject_bullets(html_src: str, pos: int, items_html: str):
    ul = re.search(r"\s*<ul>(.*?)</ul>", html_src[pos:], flags=re.S)
    if ul:
        start = pos + ul.start(); end = pos + ul.end()
        chunk = html_src[start:end].replace("</ul>", items_html + "\n</ul>")
        return html_src[:start] + chunk + html_src[end:]
    return html_src[:pos] + "\n<ul>\n" + items_html + "\n</ul>\n" + html_src[pos:]

def inject_badges(html_src: str, pos: int, blocks_html: str):
    return html_src[:pos] + blocks_html + html_src[pos:]

def zipdir(src_dir: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in src_dir.rglob("*"):
            z.write(p, p.relative_to(src_dir.parent))

# 1) Build a list of media files
media = [p for p in INCOMING.iterdir() if p.is_file()]

# 2) Read and patch both HTML pages in memory
b_html = BRANDED.read_text(encoding="utf-8", errors="ignore")
blue_html = BLUE.read_text(encoding="utf-8", errors="ignore")

# 3) Process each file
date_counters = {}  # for badge numbering per day in blue page

for f in media:
    meta = run_exiftool(f)
    dt = best_date(meta, f.stat().st_mtime)
    yyyy_mm_dd = dt.strftime("%Y-%m-%d")
    mm_dd_yyyy = dt.strftime("%m-%d-%Y")

    # Decide target folder
    target_dir = EXHIBITS / yyyy_mm_dd
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = f.name
    # Avoid name collisions
    i = 2
    while (target_dir/target_name).exists():
        stem, ext = f.stem, f.suffix
        target_name = f"{stem}_{i}{ext}"
        i += 1
    shutil.copy2(f, target_dir/target_name)

    rel_href = f"exhibits/{yyyy_mm_dd}/{target_name}"
    label = f"Exhibit — {f.name}"

    # BRANDED: bullets
    b_html, pos, existed = ensure_date_section(b_html, mm_dd_yyyy)
    bullet = f'<li><strong>(Exhibit)</strong> File — {html.escape(f.name)} — <a href="{rel_href}" target="_blank" rel="noopener">Open</a></li>'
    b_html = inject_bullets(b_html, pos, bullet)

    # BLUE: colored numbered badges (per date)
    count = date_counters.get(mm_dd_yyyy, 0) + 1
    date_counters[mm_dd_yyyy] = count
    color = PALETTE[(count-1) % len(PALETTE)]
    badge = f"<span style='display:inline-block;min-width:1.6rem;height:1.6rem;line-height:1.6rem;text-align:center;border-radius:.5rem;background:{color};color:#fff;font-weight:700;margin-right:.5rem'>{count}</span>"
    blue_html, pos2, existed2 = ensure_date_section(blue_html, mm_dd_yyyy)
    block = f"<div style='margin:.35rem 0 .5rem 0'>{badge}<strong>(Exhibit)</strong> File — {html.escape(f.name)} — <a href='{rel_href}' target='_blank' rel='noopener'>Open</a></div>"
    blue_html = inject_badges(blue_html, pos2, block)

# 4) Add auto-fix script (once)
b_html = add_autofix_once(b_html)
blue_html = add_autofix_once(blue_html)

# 5) Write pages back
BRANDED.write_text(b_html, encoding="utf-8")
BLUE.write_text(blue_html, encoding="utf-8")

# 6) Fresh ZIPs for upload
zipdir(SITE, ROOT/"static_site_bundle.zip")          # whole site (including /blue)
zipdir(SITE/"blue", ROOT/"static_site_blue_bundle.zip")

print("Done.")
print("Zips:")
print(" -", (ROOT/"static_site_bundle.zip").resolve())
print(" -", (ROOT/"static_site_blue_bundle.zip").resolve())
