#!/usr/bin/env python3
"""Export documents changed since a given timestamp as JSONL.

Each line contains a document with its chunks (including embeddings).
Used for incremental database sync between staging and dev/prod.

Usage:
    python scripts/db-incremental-export.py --since 2024-01-01T00:00:00 --output export.jsonl
"""

import argparse
import json
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text


def main():
    parser = argparse.ArgumentParser(description="Export changed documents as JSONL")
    parser.add_argument(
        "--since", required=True, help="ISO timestamp: export documents updated after this time"
    )
    parser.add_argument("--output", required=True, help="Output JSONL file path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/search_db")
    engine = create_engine(database_url)

    since = datetime.fromisoformat(args.since)

    with engine.connect() as conn:
        # Get changed documents
        docs = conn.execute(
            text("""
                SELECT id, title, petitioner, respondent, judge, citation,
                       decision_date, court, case_id, content, display_content,
                       is_gst_core, extracted_provisions, extracted_statutes,
                       created_at, updated_at
                FROM documents
                WHERE updated_at > :since
                ORDER BY updated_at
            """),
            {"since": since},
        ).fetchall()

        if not docs:
            print("No documents changed since", args.since, file=sys.stderr)
            # Write empty file
            with open(args.output, "w") as f:
                pass
            return

        print(f"Exporting {len(docs)} documents...", file=sys.stderr)

        with open(args.output, "w") as f:
            for doc in docs:
                doc_dict = dict(doc._mapping)

                # Serialize timestamps as ISO strings
                for ts_field in ("created_at", "updated_at"):
                    if doc_dict[ts_field] is not None:
                        doc_dict[ts_field] = doc_dict[ts_field].isoformat()

                # Get chunks for this document
                chunks = conn.execute(
                    text("""
                        SELECT id, chunk_content, embedding
                        FROM document_chunks
                        WHERE document_id = :doc_id
                        ORDER BY id
                    """),
                    {"doc_id": doc_dict["id"]},
                ).fetchall()

                doc_dict["chunks"] = []
                for chunk in chunks:
                    chunk_dict = {
                        "chunk_content": chunk.chunk_content,
                        "embedding": list(chunk.embedding) if chunk.embedding else None,
                    }
                    doc_dict["chunks"].append(chunk_dict)

                # Remove the internal id (target will generate its own)
                del doc_dict["id"]

                f.write(json.dumps(doc_dict, default=str) + "\n")

        print(f"Exported {len(docs)} documents to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
