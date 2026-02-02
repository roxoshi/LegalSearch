import argparse
import fitz
import os
import logging
from pathlib import Path
from bs4 import BeautifulSoup
from collections import defaultdict

# logging
logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("pdf_conversion")


def analyze_marker_columns(doc):
    """
    Pass 1: Scan the entire document to identify vertical bands (columns)
    where single-character junk markers (A-H) frequently appear.
    """
    bins = defaultdict(int)
    page_count = len(doc)
    
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] == 0:
                text = "".join("".join(s["text"] for s in l["spans"]) for l in b["lines"]).strip()
                # Line markers A-H (sometimes lowercase) appearing in margins
                if len(text) == 1 and text.upper() in 'ABCDEFGH':
                    x_center = (b["bbox"][0] + b["bbox"][2]) / 2
                    # 5-point bins for precision
                    bin_idx = int(x_center // 5) * 5
                    bins[bin_idx] += 1
                    
    # A bin is a "Marker Column" if it contains markers on > 25% of pages
    threshold = max(2, page_count * 0.25)
    marker_columns = [b for b, count in bins.items() if count >= threshold]
    return marker_columns


def convert_pdf_to_html(pdf_path: Path) -> str:
    """
    Converts a PDF to HTML using a 2-pass approach:
    1. Identify marker columns globally.
    2. Filter content and preserve styles.
    """
    try:
        doc = fitz.open(pdf_path)
        
        # Pass 1: Global Analysis
        marker_columns = analyze_marker_columns(doc)
        logger.debug(f"Identified Marker Columns for {pdf_path.name}: {marker_columns}")
        
        soup = BeautifulSoup("<html><head><meta charset='utf-8'></head><body></body></html>", "html.parser")
        body = soup.body
        
        # CSS Style
        style = soup.new_tag("style")
        style.string = """
            body { 
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                line-height: 1.7; 
                color: #2d3748; 
                max-width: 850px; 
                margin: 0 auto; 
                padding: 40px 20px;
                background-color: #fff;
            }
            p { margin-bottom: 1.25em; text-align: justify; }
            strong { font-weight: 700; color: #1a202c; }
            h2 { 
                font-size: 1.4em; 
                font-weight: 700; 
                margin-top: 2.5em; 
                margin-bottom: 0.8em; 
                color: #2c5282; 
                border-bottom: 2px solid #ebf8ff;
                padding-bottom: 0.2em;
                text-transform: uppercase;
                letter-spacing: 0.03em;
            }
        """
        soup.head.append(style)

        def clean_text(text):
            replacements = {
                "â€“": "-", "â€”": "--", "–": "-", "—": "--",
                "â€œ": '"', "â€": '"', "â€˜": "'", "â€™": "'",
                "“": '"', "”": '"', "‘": "'", "’": "'",
                "â€¦": "...", "…": "...", "Â": "", "†": "", "â€ ": '"',
            }
            for bad, good in replacements.items():
                text = text.replace(bad, good)
            return " ".join(text.split())

        def is_header_footer(text, bbox, page_height):
            stripped_text = text.strip()
            if stripped_text.isdigit() and len(stripped_text) < 5:
                return True
            
            lower_text = stripped_text.lower()
            if "supreme court reports" in lower_text:
                return True
            if stripped_text.startswith("[") and "S.C.R." in stripped_text:
                return True
            
            y0, y1 = bbox[1], bbox[3]
            if y1 < page_height * 0.12 or y0 > page_height * 0.92:
                if len(stripped_text) < 140:
                    return True
            return False

        def is_adaptive_junk(text, bbox):
            """
            Uses Pass 1 results to filter junk markers.
            """
            stripped = text.strip()
            if len(stripped) == 1:
                x_center = (bbox[0] + bbox[2]) / 2
                bin_idx = int(x_center // 5) * 5
                # Check if it falls in a known marker column or extreme margin
                if bin_idx in marker_columns:
                    return True
                if bbox[2] < 50 or bbox[0] > 400: # Fallback margin safeguard
                    return True
            return False

        def is_heading(text, is_bold, bbox, page_height):
            stripped = text.strip()
            # A heading MUST have more than 1 character
            if len(stripped) <= 1:
                return False
                
            upper_text = stripped.upper()
            keywords = [
                "JUDGMENT", "ORDER", "HEADNOTE", "FACTUAL MATRIX", 
                "ISSUE FOR CONSIDERATION", "CASE LAW CITED", 
                "APPEARANCES FOR PARTIES", "LIST OF ACTS", 
                "LIST OF KEYWORDS", "CASE ARISING FROM", 
                "BOOKS AND PERIODICALS CITED"
            ]
            
            if is_bold:
                # Sections shouldn't be in the very top/bottom
                y0 = bbox[1]
                if y0 < page_height * 0.12 or y0 > page_height * 0.90:
                    return False
                    
                if len(stripped) < 160:
                    if any(k in upper_text for k in keywords):
                        return True
                    if stripped.isupper() and len(stripped) > 3:
                        return True
            return False

        structured_blocks = []
        
        for page in doc:
            page_height = page.rect.height
            blocks = page.get_text("dict")["blocks"]
            for b in blocks:
                if b["type"] == 0:
                    block_content = []
                    block_text = ""
                    block_is_bold = False
                    
                    for line in b["lines"]:
                        line_spans = []
                        for span in line["spans"]:
                            span_text = span["text"]
                            if not span_text.strip():
                                continue
                            
                            is_bold = bool(span["flags"] & 2**4) or "Bold" in span["font"]
                            line_spans.append({"text": span_text, "is_bold": is_bold})
                            block_text += span_text + " "
                            if is_bold:
                                block_is_bold = True
                        
                        if line_spans:
                            block_content.append(line_spans)
                    
                    cleaned_block_text = clean_text(block_text)
                    if not cleaned_block_text:
                        continue
                        
                    # 1. Adaptive Junk check
                    if is_adaptive_junk(cleaned_block_text, b["bbox"]):
                        continue
                        
                    # 2. Header/Footer check
                    if is_header_footer(cleaned_block_text, b["bbox"], page_height):
                        continue
                    
                    structured_blocks.append({
                        "text": cleaned_block_text,
                        "content": block_content,
                        "is_heading": is_heading(cleaned_block_text, block_is_bold, b["bbox"], page_height),
                        "bbox": b["bbox"]
                    })

        # Pass 3: Elements Reconstruction
        merged_elements = []
        current_element = None

        for block in structured_blocks:
            if block["is_heading"]:
                if current_element:
                    merged_elements.append(current_element)
                merged_elements.append({"type": "h2", "text": block["text"]})
                current_element = None
                continue

            if not current_element:
                current_element = {"type": "p", "content": block["content"]}
            else:
                prev_text = " ".join(" ".join(s["text"] for s in span_list) for span_list in current_element["content"])
                prev_ends_sentence = prev_text.strip() and prev_text.strip()[-1] in ['.', '?', '!', '"', '”']
                curr_starts_lower = block["text"] and block["text"][0].islower()
                
                if not prev_ends_sentence or curr_starts_lower:
                    current_element["content"].extend(block["content"])
                else:
                    merged_elements.append(current_element)
                    current_element = {"type": "p", "content": block["content"]}

        if current_element:
            merged_elements.append(current_element)

        # HTML construction
        for elem in merged_elements:
            if elem["type"] == "h2":
                tag = soup.new_tag("h2")
                tag.string = elem["text"]
                body.append(tag)
            else:
                p_tag = soup.new_tag("p")
                for line in elem["content"]:
                    for span in line:
                        if span["is_bold"]:
                            bold_tag = soup.new_tag("strong")
                            bold_tag.string = span["text"]
                            p_tag.append(bold_tag)
                        else:
                            p_tag.append(span["text"])
                    p_tag.append(" ")
                body.append(p_tag)

        return soup.prettify()
        
    except Exception as e:
        logger.error(f"Error converting PDF {pdf_path}: {e}")
        return ""


def pdf_to_html_direct(pdf_path, html_path):
    html_content = convert_pdf_to_html(Path(pdf_path))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def convert_bulk_htmls(source_dir, output_dir):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for filename in os.listdir(source_dir):
        if filename.endswith(".pdf"):
            pdf_path = os.path.join(source_dir, filename)
            html_path = os.path.join(output_dir, filename.replace(".pdf", ".html"))
            try:
                pdf_to_html_direct(pdf_path, html_path)
                logger.info("Successfully converted: %s", filename)
            except Exception:
                logger.exception(f"Failed to convert {filename}")


def main():
    parser = argparse.ArgumentParser(description="Convert a directory of PDFs to HTMLs")
    parser.add_argument("source_dir", help="Path to pdf files")
    parser.add_argument("output_dir", help="Path to store html files")
    args = parser.parse_args()
    convert_bulk_htmls(args.source_dir, args.output_dir)
    logger.info(f"Conversion complete. HTMLs written at: {args.output_dir}")


if __name__ == '__main__':
    main()