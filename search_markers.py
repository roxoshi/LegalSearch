import fitz
import json
from pathlib import Path

def search_markers(pdf_path):
    doc = fitz.open(pdf_path)
    markers = ["HEADNOTE", "JUDGMENT", "ORDER", "Held", "HELD"]
    results = {}
    
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b.get("type") == 0:
                for line in b["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        for marker in markers:
                            if marker in text:
                                if marker not in results:
                                    results[marker] = []
                                results[marker].append({
                                    "page": page.number + 1,
                                    "text": text,
                                    "font": span["font"],
                                    "size": span["size"],
                                    "flags": span["flags"],
                                    "is_bold": bool(span["flags"] & 2**4)
                                })
    return results

if __name__ == "__main__":
    pdf_path = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/2025_12_366_390.pdf"
    results = search_markers(pdf_path)
    print(json.dumps(results, indent=2))
