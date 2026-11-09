"""API client: the only place where the frontend talks to the agent's API."""

import os

import httpx

# Inside Docker, containers reach each other by service name: "api", not "localhost"
API_URL = os.getenv("API_URL", "http://api:8000/chat")


class AgentUnavailable(Exception):
    # Raised when the agent can't answer: API not reachable, timeout or server error
    pass


def ask(question: str) -> dict:
    # Send the question to POST /chat and return the agent's AgentOutput as a dict
    try:
        response = httpx.post(API_URL, json={"question": question}, timeout=120)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        raise AgentUnavailable(str(error)) from error  # the frontend doesn't need to know httpx
