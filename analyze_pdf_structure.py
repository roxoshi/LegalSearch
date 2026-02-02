import fitz
import json
from pathlib import Path

def analyze_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    analysis = []
    
    # Analyze first 3 pages to see structure
    for page_num in range(min(3, len(doc))):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        
        page_analysis = {
            "page": page_num + 1,
            "blocks": []
        }
        
        for b in blocks:
            if b.get("type") == 0:  # text block
                block_info = {
                    "bbox": b["bbox"],
                    "lines": []
                }
                for line in b["lines"]:
                    line_info = {
                        "spans": []
                    }
                    for span in line["spans"]:
                        line_info["spans"].append({
                            "text": span["text"],
                            "font": span["font"],
                            "size": span["size"],
                            "flags": span["flags"],
                            "color": span["color"],
                            "is_bold": bool(span["flags"] & 2**4) # bit 4 is bold
                        })
                    block_info["lines"].append(line_info)
                page_analysis["blocks"].append(block_info)
        analysis.append(page_analysis)
    
    return analysis

if __name__ == "__main__":
    pdf_path = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/2025_12_366_390.pdf"
    results = analyze_pdf(pdf_path)
    print(json.dumps(results, indent=2))
