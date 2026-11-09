"""MCP server: the agent's only way to move money.
This file is a small program that offers two tools: `create_refund` (writes) and
`get_account` (reads).

What happens on a refund?
    1. Check the amount        (0 < amount <= 100)
    2. Check the owner's money (the company must have enough left)
    3. Book one new row        (transactions: company -> customer)
    4. Report back             (booked yes/no + the customer's new balance from the ledger)
"""

import os

import psycopg  # Postgres driver
from mcp.server.fastmcp import FastMCP  # turns a Python function into an MCP tool

# --- Settings -----------------------------------------------------------------------------
OWNER = "Rental Company"  # pays every refund
CUSTOMER = "Alex Miller"  # receives every refund
MAX_REFUND = 100  # same limit as the guardrails, checked again here: the tool protects itself

# Database connection. The agent hands over only the POSTGRES_* variables to this process.
DB_URL = (
    f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}"
    f"/{os.environ['POSTGRES_DB']}"
)

mcp = FastMCP("ledger")  # the server; "ledger" is the name the agent sees


# --- Tool ---------------------------------------------------------------------------------
@mcp.tool()  # registers the function below as a tool.
def create_refund(amount: float, reason: str) -> dict:
    """Refund an amount from the owner to the customer and book it in the ledger."""

    # Step 1: amount check. Even if someone calls this tool directly, past all guardrails.
    if not 0 < amount <= MAX_REFUND:
        return {
            "booked": False,
            "message": f"Refused: the amount must be between 0 and {MAX_REFUND}.",
        }

    with psycopg.connect(DB_URL) as conn:  # one database transaction: all or nothing
        # Step 2: does the owner have enough money? Balances are calculated from the ledger.
        query = "SELECT balance FROM balances WHERE name = %s"
        balance = conn.execute(query, (OWNER,)).fetchone()[0]
        if amount > balance:
            return {"booked": False, "message": f"Refused: {OWNER} has only {balance} left."}

        # Step 3: book the refund as ONE new row. 
        transaction_id = conn.execute(
            "INSERT INTO transactions (from_account, to_account, amount, reason) "
            "SELECT owner.id, customer.id, %s, %s FROM accounts owner, accounts customer "
            "WHERE owner.name = %s AND customer.name = %s RETURNING id",
            (amount, reason, OWNER, CUSTOMER),
        ).fetchone()[0]

        # Step 4: read the customer's new balance.
        new_balance = conn.execute(query, (CUSTOMER,)).fetchone()[0]

    # Leaving the "with" block saves (commits) the row. The agent reads this dictionary.
    return {
        "booked": True,
        "message": f"Refund of {amount:g} booked (transaction {transaction_id}).",
        "new_balance": float(new_balance),
    }


@mcp.tool()
def get_account() -> dict:
    """Read the customer's current balance and the total refunded so far. Read-only."""
    with psycopg.connect(DB_URL) as conn:
        query = "SELECT balance FROM balances WHERE name = %s"
        balance = conn.execute(query, (CUSTOMER,)).fetchone()[0]
        refunded = conn.execute(  # sum of all refunds the customer received
            "SELECT COALESCE(SUM(t.amount), 0) FROM transactions t "
            "JOIN accounts a ON a.id = t.to_account WHERE a.name = %s",
            (CUSTOMER,),
        ).fetchone()[0]
    return {"balance": float(balance), "refunded": float(refunded)}


# --- Start --------------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run()  # waits for tool calls from the agent on stdin, answers on stdout
