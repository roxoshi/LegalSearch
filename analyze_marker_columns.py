import fitz
import json
from collections import defaultdict

def analyze_columns(pdf_path):
    doc = fitz.open(pdf_path)
    # Track frequency of single-character blocks in x-bands
    # We'll use small bins (e.g. 10 points)
    bins = defaultdict(int)
    total_blocks = 0
    
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] == 0:
                text = "".join("".join(s["text"] for s in l["spans"]) for l in b["lines"]).strip()
                if len(text) == 1:
                    # use center of x-range for binning
                    x_center = (b["bbox"][0] + b["bbox"][2]) / 2
                    bin_idx = int(x_center // 5) * 5 # 5-point bins
                    bins[bin_idx] += 1
                total_blocks += 1
    
    # Identify high-frequency bins
    # A bin is suspicious if it has many more single characters than average or above a threshold
    suspicious_bins = [b for b, count in bins.items() if count > len(doc) * 0.3] # appearing on >30% of pages
    
    return sorted(suspicious_bins), bins

if __name__ == "__main__":
    pdf_path = "/home/rj/Documents/projects/LegalSearch/backend/.data/gst_pdfs/2017_2_434_465.pdf"
    marker_bins, all_bins = analyze_columns(pdf_path)
    print(f"Suspicious Marker Bins (x-centers): {marker_bins}")
    for b in marker_bins:
        print(f"Bin {b}: {all_bins[b]} occurrences")
