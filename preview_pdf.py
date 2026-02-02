import argparse
import logging
from pathlib import Path
import sys
import os

# Add root directory to path to allow imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipelines.convert_pdf import convert_pdf_to_html

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("preview_pdf")

def main():
    parser = argparse.ArgumentParser(description="Convert PDF to HTML for preview verification.")
    parser.add_argument("pdf_path", help="Path to the PDF file to convert")
    parser.add_argument("--output", "-o", default="preview.html", help="Path to save the output HTML (default: preview.html)")
    
    args = parser.parse_args()
    
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        logger.error(f"Error: File not found at {pdf_path}")
        return

    logger.info(f"Converting {pdf_path}...")
    
    try:
        html_content = convert_pdf_to_html(pdf_path)
        
        if not html_content:
            logger.error("Conversion failed or returned empty content.")
            return

        output_path = Path(args.output).resolve()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
            
        logger.info(f"✔ Success! HTML preview saved to:\n  {output_path}")
        logger.info("\nYou can open this file in your browser to verify formatting.")
        
    except Exception as e:
        logger.exception(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
