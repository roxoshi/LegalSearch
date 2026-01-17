import argparse
import fitz
import os
import logging
from pathlib import Path
from bs4 import BeautifulSoup

# logging
logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("pdf_conversion")


def convert_pdf_smart(pdf_path, html_path):
    doc = fitz.open(pdf_path)

    soup = BeautifulSoup("<html><body></body></html>", "html.parser")
    body = soup.body

    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] == 0: # this is a text block
                paragraph_text = []

                # check for indentation by looking at the left coordinate
                is_indented = b["bbox"][0] > 75

                for line in b["lines"]:
                    line_text = "".join([span["text"] for span in line["spans"]])
                    paragraph_text.append(line_text)

                # join lines with a space to allow web reflow
                combined_text = " ".join(paragraph_text)

                # create the <p> tag
                p_tag = soup.new_tag("p")
                if is_indented:
                    p_tag['style'] = "text-indent: 30px; margin-left: 20px;"

                p_tag.string = combined_text
                body.append(p_tag)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(soup.prettify())


def pdf_to_html_direct(pdf_path, html_path):
    doc = fitz.open(pdf_path)
    raw_html = ""

    for page in doc:
        raw_html += page.get_text("html")

    soup = BeautifulSoup(raw_html, 'html.parser')

    for tag in soup.find_all(True):
        if tag.has_attr('style'):
            del tag['style']

    for span in soup.find_all('span'):
        if 'bold' in str(span.get('class', [])).lower():
            span.name = 'strong'

    for tag in soup.find_all():
        if len(tag.get_text(strip=True)) == 0 and tag.name not in ['img','br']:
            tag.extract()

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(soup.prettify())

def convert_bulk_htmls(source_dir, output_dir):
    # create path if does not exists
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    for filename in os.listdir(source_dir):
        if filename.endswith(".pdf"):
            pdf_path = os.path.join(source_dir, filename)
            html_path = os.path.join(output_dir, filename.replace('.pdf', '.html'))

            try:
                convert_pdf_smart(pdf_path, html_path)
                logger.info("Successfully converted: %s", filename)
            except Exception:
                logger.exception(f"Failed to convert {filename}")

def main():
    parser = argparse.ArgumentParser(
        description="Convert a directory of PDFs to HTMLs"
    )
    parser.add_argument(
        "source_dir",
        help="Path to pdf files"
    )
    parser.add_argument(
        "output_dir",
        help="Path to store html files"
    )

    args = parser.parse_args()

    convert_bulk_htmls(args.source_dir, args.output_dir)
    logger.info(f"Conversion complete. HTMLs written at : {args.output_dir}")

if __name__ == '__main__':
    main()