-- Enable vector support (the RAG tables are created later by langchain-postgres)
CREATE EXTENSION IF NOT EXISTS vector;

-- Who has money
CREATE TABLE accounts (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    is_owner        BOOLEAN NOT NULL DEFAULT FALSE,
    initial_balance NUMERIC(10, 2) NOT NULL
);

-- Every refund = one row. Never UPDATE, only INSERT.
CREATE TABLE transactions (
    id           SERIAL PRIMARY KEY,
    from_account INT NOT NULL REFERENCES accounts(id),
    to_account   INT NOT NULL REFERENCES accounts(id),
    amount       NUMERIC(10, 2) NOT NULL CHECK (amount > 0),
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Balance is calculated, never stored
CREATE VIEW balances AS
SELECT a.name,
       a.initial_balance
       + COALESCE((SELECT SUM(amount) FROM transactions WHERE to_account = a.id), 0)
       - COALESCE((SELECT SUM(amount) FROM transactions WHERE from_account = a.id), 0) AS balance
FROM accounts a;

INSERT INTO accounts (name, is_owner, initial_balance) VALUES
    ('Rental Company', TRUE, 100),
    ('Alex Miller', FALSE, 1000);