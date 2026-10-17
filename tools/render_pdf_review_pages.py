from pathlib import Path

import pymupdf


root = Path.cwd()
out = root / "tmp" / "pdfs" / "rendered"
out.mkdir(parents=True, exist_ok=True)
jobs = {
    root / "sen65 information" / "PS_DS_SEN6x.pdf": (4, 5, 6, 7, 26, 30, 36),
    root / "sen65 information" / "PS_AN_SEN6x_Testing_And_Evaluation_Guide_D1.pdf": (3, 4, 5),
    root / "sen65 information" / "PS_AN_SEN6x_Handling_Instructions_D1.pdf": (2,),
}
for path, pages in jobs.items():
    doc = pymupdf.open(path)
    for page_number in pages:
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
        target = out / (path.stem + "_p%02d.png" % page_number)
        pix.save(target)
        print(target)
