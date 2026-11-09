"""Ingestion: chunk the policy PDFs, embed them and store them in pgvector.

Runs once (or whenever the PDFs change): python 03_rag/ingest.py
Not part of the agent.
"""

from pathlib import Path

import psycopg
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from retrieve import DB_URL, embed

DOCS_DIR = Path(__file__).parent.parent / "00_assets" / "documents"


def ingest() -> None:
    """Read all PDFs, split into chunks, store each chunk with its region and embedding."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

    with psycopg.connect(DB_URL) as conn:
        # Vector table, created on first run. 384 = output size of all-MiniLM-L6-v2
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id        SERIAL PRIMARY KEY,
                region    TEXT NOT NULL,          -- filter for retrieval: Arizona / General
                source    TEXT NOT NULL,          -- PDF file name, for citations
                page      INT NOT NULL,           -- page number, for citations
                content   TEXT NOT NULL,          -- the chunk text the LLM will read
                embedding vector(384) NOT NULL    -- the chunk as a vector, for similarity search
            )""")
        conn.execute("TRUNCATE chunks")  # re-run = fresh index, no duplicates

        for pdf in sorted(DOCS_DIR.glob("*.pdf")):
            # Region comes from the file name: "Arizona_Terms and Conditions" -> "Arizona"
            region = pdf.stem.split("_")[0]

            # Page by page, so every chunk knows its page number
            for page_no, page in enumerate(PdfReader(pdf).pages, start=1):
                for text in splitter.split_text(page.extract_text() or ""):
                    conn.execute(
                        "INSERT INTO chunks (region, source, page, content, embedding) "
                        "VALUES (%s, %s, %s, %s, %s::vector)",
                        (region, pdf.name, page_no, text, embed(text)),
                    )

        # Quick sanity check: how many chunks per region landed in the table
        counts = conn.execute("SELECT region, COUNT(*) FROM chunks GROUP BY region").fetchall()
    print(f"Inserted chunks per region: {dict(counts)}")


if __name__ == "__main__":
    ingest()
