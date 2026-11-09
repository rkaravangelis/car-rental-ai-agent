"""Retrieval with HyDE: find the policy chunks of one region that best match a question.

HyDE (Hypothetical Document Embedding): customers ask in everyday language, but the
policy is written in contract language. So the LLM first writes a short fake policy
passage that would answer the question, and we search with *that* text's vector.
"""

import os

import psycopg
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# --- Configuration ------------------------------------------------------------------------
load_dotenv()

MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

# Connection to our Postgres container.
DB_URL = (
    f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}"
    f"/{os.environ['POSTGRES_DB']}"
)

# Instruction for the hypothetical document generation.
HYDE_PROMPT = (
    "You are simulating a passage from a car rental company's terms and conditions. "
    "Your output will be embedded and used to retrieve the real passage. "
    "Write 2-4 sentences in formal contract language that would answer the customer's question. "
    "Do not invent specific amounts, dates or percentages. "
    "Do not introduce the passage, explain your reasoning or add commentary."
)

# --- Building blocks ----------------------------------------------------------------------
def embed(text: str) -> str:
    # Embed a text as a pgvector literal to return the vectors as text
    return str(MODEL.encode(text, normalize_embeddings=True).tolist())


def hypothetical_document(question: str, llm) -> tuple[str, dict]:
    # Let the LLM write a fake policy passage that would answer the question.
    response = llm.invoke([("system", HYDE_PROMPT), ("user", question)])

    # HyDE is an extra LLM call per question. Its tokens must be added to the agent's totals.
    usage = response.usage_metadata or {} 
    tokens = {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0)}
    return response.content.strip(), tokens


# --- Public API (used by the agent) -------------------------------------------------------
def retrieve(question: str, region: str, llm, k: int = 4) -> dict:
    # Find the k policy chunks of one region that best match the question.

    # Step 1: search with the fake passage, not with the raw question
    hyde_text, tokens = hypothetical_document(question, llm)

    # Step 2: vector search in pgvector, filtered by region
    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute(
            "SELECT source, page, content, 1 - (embedding <=> %(v)s::vector) AS similarity "
            "FROM chunks WHERE region = %(region)s "
            "ORDER BY embedding <=> %(v)s::vector LIMIT %(k)s",
            {"v": embed(hyde_text), "region": region, "k": k},
        ).fetchall()

    # Step 3: rows (tuples) -> dictionaries, easier to read for the agent and the frontend
    chunks = [
        {"source": s, "page": p, "content": c, "similarity": round(sim, 3)} for s, p, c, sim in rows
    ]
    return {"chunks": chunks, "hyde": hyde_text, "tokens": tokens}


def list_regions() -> list[str]:
    # All regions that have chunks in the database, e.g. ["Arizona", "General"].
    # A new PDF (e.g. "Texas_Terms.pdf") becomes a new region automatically after ingestion.
    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute("SELECT DISTINCT region FROM chunks ORDER BY region").fetchall()
    return [region for (region,) in rows]


def format_context(chunks: list[dict]) -> str:
    """Turn retrieved chunks into the context block for the LLM prompt.

    If nothing was found, it says so explicitly. The LLM then tells the customer it
    cannot help, instead of answering from its own knowledge (policy grounding).
    """
    if not chunks:
        return "No relevant policy passages were found."
    return "\n\n".join(
        f"[Chunk {i} | {c['source']} p.{c['page']} | similarity {c['similarity']}]\n{c['content']}"
        for i, c in enumerate(chunks, start=1)
    )


# --- Manual test -----------------------------------------------

if __name__ == "__main__":
    # Only needed for this test; in production the agent passes in its own LLM
    from langchain_ollama import ChatOllama

    llm = ChatOllama(model=os.getenv("MODEL_NAME", "gpt-oss:120b-cloud"), temperature=0)
    result = retrieve("How much deposit is held on my card?", "Arizona", llm)
    print("HyDE passage:", result["hyde"], "\nTokens:", result["tokens"], "\n")
    print(format_context(result["chunks"]))
