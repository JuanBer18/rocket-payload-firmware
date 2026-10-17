from pathlib import Path

import fitz


root = Path.cwd()
out_dir = root / "tmp" / "pdfs" / "text"
out_dir.mkdir(parents=True, exist_ok=True)

for path in sorted(root.rglob("*.pdf")):
    if "tmp" in path.parts:
        continue
    doc = fitz.open(path)
    lines = []
    for number, page in enumerate(doc, 1):
        lines.append("\n===== PAGE %d =====\n" % number)
        lines.append(page.get_text("text"))
    relative = path.relative_to(root).as_posix().replace("/", "__")
    target = out_dir / (relative + ".txt")
    target.write_text("".join(lines), encoding="utf-8")
    print("%s | %d pages | %s" % (path.relative_to(root), len(doc), target))
