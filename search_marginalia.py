import fitz

def search_marginalia(pdf_path):
    doc = fitz.open(pdf_path)
    for page in doc:
        words = page.get_text("words")
        for w in words:
            # x0, y0, x1, y1, text, block_no, line_no, word_no
            x0, y0, x1, y1, text = w[:5]
            if len(text) == 1 and text.isalpha() and x0 < 75:
                 print(f"Page {page.number+1} | Text: '{text}' | x0: {x0:.1f}, x1: {x1:.1f}")

if __name__ == "__main__":
    pdf_path = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/2025_12_366_390.pdf"
    search_marginalia(pdf_path)
