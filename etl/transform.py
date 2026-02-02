
import sys
import os
import logging
from typing import List, Tuple
from pathlib import Path

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipelines.convert_pdf import convert_pdf_to_html
try:
    from backend.app.models import Document, DocumentChunk
    from backend.app.chunk_generator import RecursiveCharacterTextSplitter
except ImportError:
    from app.models import Document, DocumentChunk
    from app.chunk_generator import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from etl.schemas import DocumentJSON

logger = logging.getLogger(__name__)

class Transformer:
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        logger.info(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=4000,
            chunk_overlap=600,
            separators=["\n\n", "\n", ".", " ", ""]
        )

    def process_document(self, doc_json: DocumentJSON, pdf_path: Path) -> Tuple[Document, List[DocumentChunk]]:
        """
        Transforms raw JSON + PDF into Database Models (Document + Chunks).
        1. Convert PDF to HTML (Display Content).
        2. Create Document model.
        3. Chunk text_content.
        4. Generate embeddings.
        5. Create DocumentChunk models.
        """
        
        # 1. PDF Conversion
        display_html = None
        if pdf_path.exists():
            try:
                display_html = convert_pdf_to_html(pdf_path)
            except Exception as e:
                logger.error(f"PDF conversion failed for {pdf_path}: {e}")
                # Fallback? Or just leave None?
                # Use raw text wrapped in p tags as basic fallback
                display_html = f"<p>{doc_json.text_content}</p>"
        else:
            logger.warning(f"PDF not found at {pdf_path}, utilizing simple fallback.")
            display_html = f"<p>{doc_json.text_content}</p>"

        # 2. Create Document Model
        # Using exact fields from JSON matched to Model
        db_doc = Document(
            title=doc_json.title,
            petitioner=doc_json.petitioner,
            respondent=doc_json.respondent,
            judge=doc_json.judge,
            citation=doc_json.citation,
            decision_date=doc_json.decision_date,
            court=doc_json.court,
            case_id=doc_json.case_id,
            content=doc_json.text_content, # Raw text for backup/search
            display_content=display_html
        )

        # 3. Chunking
        if not doc_json.text_content:
            logger.warning(f"No text content for {doc_json.case_id}, skipping chunks.")
            return db_doc, []

        chunks_text = self.text_splitter.split_text(doc_json.text_content)
        
        # 4. Embedding
        if chunks_text:
            embeddings = self.model.encode(chunks_text)
        else:
            embeddings = []

        # 5. Create Chunk Models
        db_chunks = []
        for i, text in enumerate(chunks_text):
            chunk_embedding = embeddings[i].tolist()
            db_chunk = DocumentChunk(
                chunk_content=text,
                embedding=chunk_embedding
            )
            db_chunks.append(db_chunk)

        return db_doc, db_chunks
