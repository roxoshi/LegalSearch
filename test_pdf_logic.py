
import logging
from pathlib import Path
import sys
import os

# Add root directory to path to allow imports if needed, though here we just need pipelines
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipelines.convert_pdf import convert_pdf_to_html

# Set up logging to see output
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_conversion")

def test():
    # Path to a sample PDF
    # We'll look in backend/.data/gst_pdfs
    pdf_dir = Path("backend/.data/gst_pdfs")
    
    if not pdf_dir.exists():
        logger.error(f"Directory {pdf_dir} does not exist.")
        # Create a dummy PDF if possible or ask user
        return

    # Find first PDF
    pdfs = list(pdf_dir.glob("*.pdf"))
    if not pdfs:
        logger.error("No PDFs found in backend/.data/gst_pdfs")
        return

    sample_pdf = pdfs[0]
    logger.info(f"Testing conversion on: {sample_pdf}")
    
    html_output = convert_pdf_to_html(sample_pdf)
    
    if html_output:
        logger.info("Conversion successful!")
        logger.info(f"HTML Preview (first 500 chars):\n{html_output[:500]}...")
        
        # Save to file for manual inspection
        output_path = "test_output.html"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_output)
        logger.info(f"Full HTML saved to {output_path}")
    else:
        logger.error("Conversion returned empty string.")

if __name__ == "__main__":
    test()
