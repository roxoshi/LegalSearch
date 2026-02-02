import fitz
import json

def analyze_margins_and_headers(pdf_path, max_pages=10):
    doc = fitz.open(pdf_path)
    analysis = []
    
    for i in range(min(max_pages, len(doc))):
        page = doc[i]
        blocks = page.get_text("dict")["blocks"]
        page_width = page.rect.width
        page_height = page.rect.height
        
        page_items = []
        for b in blocks:
            if b["type"] == 0:
                text = ""
                for line in b["lines"]:
                    for span in line["spans"]:
                        text += span["text"]
                
                # Report metadata for each block
                page_items.append({
                    "text": text.strip(),
                    "bbox": b["bbox"],
                    "page_width": page_width,
                    "page_height": page_height
                })
        analysis.append({"page": i + 1, "items": page_items})
    return analysis

if __name__ == "__main__":
    pdf_path = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/2025_12_366_390.pdf"
    results = analyze_margins_and_headers(pdf_path)
    
    # Print blocks that are likely marginal junk or headers
    for p in results:
        print(f"--- Page {p['page']} ---")
        for item in p['items']:
            x0, y0, x1, y1 = item['bbox']
            text = item['text']
            # Heuristic for margin: x1 < 100 (very far left)
            # Heuristic for header/footer: y1 < 100 or y0 > page_height - 100
            if x1 < 80 or y1 < 80 or y0 > item['page_height'] - 80:
                print(f"[{x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f}] : {text}")
