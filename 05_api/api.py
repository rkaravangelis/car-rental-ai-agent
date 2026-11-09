"""API: the agent as a web service.
One request:  1. JSON in  ->  2. agent  ->  3. JSON out
"""

# --- Setup --------------------------------------------------------------------------------
import sys  # gives access to sys.path: the folders Python searches on "import"
from pathlib import Path  # builds file paths that work on Windows and Linux

from fastapi import FastAPI  # the web framework: turns Python functions into URLs
from pydantic import BaseModel  # describes and validates the incoming JSON

# Numbered folders can't be imported as packages, so put 04_agent on the search path
sys.path.append(str(Path(__file__).parent.parent / "04_agent"))

app = FastAPI(title="Car Rental AI Agent")  # the web app; Uvicorn serves it ("uvicorn api:app")


# --- 1. Input: what the frontend sends ----------------------------------------------------
# {"question": "..."}  A missing or wrong field returns HTTP 422 before the agent runs.
class ChatRequest(BaseModel):
    question: str  # the customer's question as text


# --- 2. Agent: does the work --------------------------------------------------------------
from agent import CustomerServiceAgent

agent = CustomerServiceAgent()  # created once at startup, reused for every question


# --- 3. Output: what the frontend gets back -----------------------------------------------
from guardrails import AgentOutput


@app.post("/chat") 
def chat(request: ChatRequest) -> AgentOutput:  # Output Model
    return agent.run(request.question)
