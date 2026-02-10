#!/usr/bin/env python3
"""Import documents from a JSONL export file.

For each document: deletes existing by case_id, then inserts with chunks.
Preserves created_at/updated_at from the source.

Usage:
    python scripts/db-incremental-import.py --input export.jsonl
"""

import argparse
import json
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text


def main():
    parser = argparse.ArgumentParser(description="Import documents from JSONL")
    parser.add_argument("--input", required=True, help="Input JSONL file path")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    database_url = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/search_db")
    engine = create_engine(database_url)

    imported = 0
    skipped = 0

    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            doc = json.loads(line)
            chunks = doc.pop("chunks", [])
            case_id = doc["case_id"]

            with engine.begin() as conn:
                # Delete existing document and its chunks (cascade)
                conn.execute(
                    text("""
                        DELETE FROM document_chunks
                        WHERE document_id IN (SELECT id FROM documents WHERE case_id = :case_id)
                    """),
                    {"case_id": case_id},
                )
                conn.execute(
                    text("DELETE FROM documents WHERE case_id = :case_id"),
                    {"case_id": case_id},
                )

                # Insert document, preserving source timestamps
                result = conn.execute(
                    text("""
                        INSERT INTO documents (
                            title, petitioner, respondent, judge, citation,
                            decision_date, court, case_id, content, display_content,
                            is_gst_core, extracted_provisions, extracted_statutes,
                            created_at, updated_at
                        ) VALUES (
                            :title, :petitioner, :respondent, :judge, :citation,
                            :decision_date, :court, :case_id, :content, :display_content,
                            :is_gst_core, :extracted_provisions, :extracted_statutes,
                            :created_at, :updated_at
                        ) RETURNING id
                    """),
                    doc,
                )
                doc_id = result.scalar()

                # Insert chunks
                for chunk in chunks:
                    embedding = chunk.get("embedding")
                    if embedding is not None:
                        # Convert list to pgvector string format
                        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
                    else:
                        embedding_str = None

                    conn.execute(
                        text("""
                            INSERT INTO document_chunks (document_id, chunk_content, embedding)
                            VALUES (:doc_id, :chunk_content, :embedding)
                        """),
                        {
                            "doc_id": doc_id,
                            "chunk_content": chunk["chunk_content"],
                            "embedding": embedding_str,
                        },
                    )

            imported += 1

    print(f"Imported {imported} documents ({skipped} skipped)", file=sys.stderr)


if __name__ == "__main__":
    main()
