import fitz
import os
from pathlib import Path

def scan_all_pdfs(source_dir):
    source_path = Path(source_dir)
    results = []
    for pdf_file in source_path.glob("*.pdf"):
        try:
            doc = fitz.open(pdf_file)
            for page in doc:
                words = page.get_text("words")
                for w in words:
                    x0, y0, x1, y1, text = w[:5]
                    # Check for single letters A-G in the far left margin
                    # Coordinates in Fitz are usually 0-600 width
                    if len(text) == 1 and text.isupper() and text in 'ABCDEFG' and x0 < 60:
                        results.append(f"{pdf_file.name} | P{page.number+1} | {text} | x0:{x0:.1f}")
                        if len(results) > 100:
                            return results
        except Exception:
            continue
    return results

if __name__ == "__main__":
    pdf_dir = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/"
    results = scan_all_pdfs(pdf_dir)
    for res in results:
        print(res)
