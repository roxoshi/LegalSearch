import fitz
import json

def find_marginal_letters(pdf_path, max_pages=10):
    doc = fitz.open(pdf_path)
    found = []
    
    for i in range(min(max_pages, len(doc))):
        page = doc[i]
        blocks = page.get_text("dict")["blocks"]
        
        for b in blocks:
            if b["type"] == 0:
                text = "".join("".join(s["text"] for s in l["spans"]) for l in b["lines"]).strip()
                # Check for single character letters or very short strings in the left margin
                # x0 < 60 is usually a good indicator for marginalia
                if len(text) == 1 and text.isalpha() and b["bbox"][0] < 60:
                    found.append({
                        "page": i + 1,
                        "text": text,
                        "bbox": b["bbox"]
                    })
                # Also just print short things on the left
                elif len(text) < 5 and b["bbox"][0] < 60:
                    found.append({
                        "page": i + 1,
                        "text": text,
                        "bbox": b["bbox"]
                    })
                    
    return found

if __name__ == "__main__":
    pdf_path = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/2025_12_366_390.pdf"
    results = find_marginal_letters(pdf_path)
    for r in results:
        print(f"Page {r['page']} | Text: '{r['text']}' | BBox: {[round(x,1) for x in r['bbox']]}")
