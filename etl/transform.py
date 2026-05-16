import html as _html
import logging
import os
import sys

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from backend.app.embeddings import EmbeddingModel
    from backend.app.models import Document, DocumentChunk
except ImportError:
    from app.embeddings import EmbeddingModel  # type: ignore[no-redef]
    from app.models import Document, DocumentChunk  # type: ignore[no-redef]

from etl.schemas import ANALYSIS_FIELD_LABELS, AnalysisJSON, MetadataJSON

logger = logging.getLogger(__name__)

# Values that indicate a field was not populated by the LLM
_SKIP_VALUES = frozenset({"", "not mentioned", "n/a", "na", "none", "not applicable"})


def generate_html(analysis: AnalysisJSON) -> str:
    """Generate a self-contained HTML string from an AnalysisJSON.

    Each section becomes:
        <section class="case-section">
          <h2>Heading</h2>
          <p>paragraph 1</p>
          <p>paragraph 2</p>
        </section>

    All wrapped in <div class="case-analysis">.
    Text is split on double-newlines to form individual <p> elements.
    Content is HTML-escaped.
    """
    sections = []
    for field_name, heading in ANALYSIS_FIELD_LABELS.items():
        text = getattr(analysis, field_name, "")
        if not text:
            continue
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        para_html = "".join(f"<p>{_html.escape(p)}</p>" for p in paragraphs)
        sections.append(
            f'<section class="case-section">'
            f"<h2>{_html.escape(heading)}</h2>"
            f"{para_html}"
            f"</section>"
        )
    return f'<div class="case-analysis">{"".join(sections)}</div>'


class Transformer:
    def __init__(self, model_name: str = "sentence-transformers/all-mpnet-base-v2"):
        self.model = EmbeddingModel(model_name)

    def process_document(
        self, case_id: str, analysis: AnalysisJSON, metadata: MetadataJSON
    ) -> tuple[Document, list[DocumentChunk]]:
        """Transform an AnalysisJSON + MetadataJSON into DB models.

        1. Generate HTML display_content from the 10 analysis fields.
        2. Concatenate all fields into a single `content` string (summary first).
        3. Create one DocumentChunk per non-empty analysis field (max 10).
        4. Embed all chunks in a single batch call.
        """
        display_content = generate_html(analysis)

        # Full text: summary first so content[:1000] gives a meaningful excerpt.
        field_texts_ordered = [getattr(analysis, field) for field in ANALYSIS_FIELD_LABELS]
        content = "\n\n".join(t for t in field_texts_ordered if t)

        effective_title = metadata.title if metadata.title else case_id

        db_doc = Document(
            title=effective_title,
            petitioner=metadata.petitioner,
            respondent=metadata.respondent,
            judge=metadata.judge,
            citation=metadata.citation,
            decision_date=metadata.decision_date,
            disposal_nature=metadata.disposal_nature or None,
            court=metadata.court,
            case_id=case_id,
            content=content,
            display_content=display_content,
        )

        # Build one chunk per analysis field, skipping empty / placeholder values.
        chunk_texts: list[str] = []
        for field_name in ANALYSIS_FIELD_LABELS:
            text = getattr(analysis, field_name, "").strip()
            if text.lower() in _SKIP_VALUES:
                continue
            chunk_texts.append(text)

        db_chunks: list[DocumentChunk] = []
        if chunk_texts:
            # Batch encode all fields at once for efficiency.
            embeddings = self.model.encode(chunk_texts)
            for i, text in enumerate(chunk_texts):
                emb = embeddings[i]
                embedding_list = emb.tolist() if hasattr(emb, "tolist") else list(emb)
                db_chunks.append(DocumentChunk(chunk_content=text, embedding=embedding_list))

        return db_doc, db_chunks
