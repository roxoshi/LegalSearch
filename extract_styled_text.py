import fitz
import json

def get_styled_text(pdf_path, max_pages=5):
    doc = fitz.open(pdf_path)
    output = []
    
    for i in range(min(max_pages, len(doc))):
        page = doc[i]
        blocks = page.get_text("dict")["blocks"]
        page_content = []
        for b in blocks:
            if b["type"] == 0:
                block_text = ""
                has_bold = False
                for line in b["lines"]:
                    for span in line["spans"]:
                        text = span["text"]
                        block_text += text
                        if bool(span["flags"] & 2**4) or "Bold" in span["font"]:
                            has_bold = True
                
                page_content.append({
                    "text": block_text.strip(),
                    "has_bold": has_bold,
                    "bbox": b["bbox"]
                })
        output.append({
            "page": i + 1,
            "blocks": page_content
        })
    return output

if __name__ == "__main__":
    pdf_path = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/2025_12_366_390.pdf"
    results = get_styled_text(pdf_path)
    for p in results:
        print(f"--- Page {p['page']} ---")
        for b in p["blocks"]:
            bold_str = "[BOLD] " if b["has_bold"] else ""
            print(f"{bold_str}{b['text'][:100]}")
