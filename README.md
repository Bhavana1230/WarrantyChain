# WarrantyChain

Blockchain-based product ownership & warranty verification system.
A lightweight, self-hosted, SHA-256 hash-chained ledger (`blockchain.py`)
records every product event (mint, sale, transfer, claim filed, claim
resolved) as an immutable block. A Flask API + single-page dashboard
(`app.py`, `templates/index.html`) sits on top of it.

## Run it

```bash
cd warrantychain
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

Data is stored locally in `data/warrantychain.db` (SQLite: users, products,
claims) and `data/chain.json` (the hash chain). Delete the `data/` folder
to reset everything.

## Try the full flow

1. **Register** three accounts (tab: Login / Register): one `manufacturer`,
   one `owner`, one `service_center`.
2. Log in as the **manufacturer** → *Mint a new product* (pick any serial
   number, e.g. `WC-2026-000451`) → *Record a sale* to your `owner`
   username.
3. Log in as the **owner** → see the product under *My products* → file a
   *warranty claim*.
4. Log in as the **service_center** → *Pending claims* → Approve/Reject.
5. Open **Verify** (no login needed) and look up the serial number to see
   the full authenticity + warranty status + on-chain history.
6. Open **Chain Explorer** to see every block and the live integrity check.

## Tamper-detection demo

Stop the server, open `data/chain.json`, hand-edit any field inside an
existing block's `"data"`, save, and restart the server. Reload the Chain
Explorer or Verify page — `chain_intact` / `valid` will flip to `false`
because the recomputed hash no longer matches, which is exactly the
tamper-evidence a real blockchain provides.

## Project layout

```
warrantychain/
├── app.py              # Flask REST API + session auth
├── blockchain.py        # Block / Blockchain classes (SHA-256 hash chain)
├── requirements.txt
├── templates/
│   └── index.html       # Single-page dashboard (vanilla JS, no build step)
└── data/                 # created at runtime: warrantychain.db, chain.json
```

## Notes / how this maps to a "real" blockchain

- Each block stores the SHA-256 hash of the previous block's contents,
  so altering any historical block breaks every hash that follows it —
  `GET /api/chain` and `GET /api/verify/<serial>` both re-verify this on
  every request.
- A tiny proof-of-work (`_mine` in `blockchain.py`) requires each block's
  hash to start with `"0"`, illustrating the mining concept cheaply.
- This is a **single-node** ledger for demonstration — there's no
  peer-to-peer consensus. To go further, the same block structure could
  be ported to a Solidity smart contract on Ethereum/Polygon (see the
  "Future Enhancements" section of the project report).
