from pathlib import Path

import pymupdf


source = Path("Schematic PDF_[No Variations] (1).pdf")
target = Path("tmp/pdfs/rendered/custom_board_schematic.png")
target.parent.mkdir(parents=True, exist_ok=True)
document = pymupdf.open(source)
pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
pixmap.save(target)
print(target, pixmap.width, pixmap.height)
