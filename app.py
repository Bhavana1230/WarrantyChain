"""
WarrantyChain -- Flask backend.

Run:
    pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:5000
"""
import os
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, g, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

from blockchain import get_chain

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "data", "warrantychain.db")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = "dev-secret-change-me"  # replace in production


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('manufacturer','owner','service_center'))
        );

        CREATE TABLE IF NOT EXISTS products (
            serial_number TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            manufacturer TEXT NOT NULL,
            manufacture_date TEXT NOT NULL,
            warranty_months INTEGER NOT NULL,
            current_owner TEXT,
            purchase_date TEXT,
            status TEXT NOT NULL DEFAULT 'unsold'
        );

        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_number TEXT NOT NULL,
            filed_by TEXT NOT NULL,
            issue TEXT NOT NULL,
            filed_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_by TEXT,
            resolved_at TEXT,
            resolution_note TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- helpers
def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def current_user():
    if "user_id" not in session:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return row


def require_role(*roles):
    user = current_user()
    if user is None:
        return None, (jsonify(error="Not logged in"), 401)
    if user["role"] not in roles:
        return None, (jsonify(error=f"Requires role: {', '.join(roles)}"), 403)
    return user, None


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


# ---------------------------------------------------------------- auth
@app.post("/api/register")
def register():
    body = request.get_json(force=True)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = body.get("role")
    if role not in ("manufacturer", "owner", "service_center"):
        return jsonify(error="role must be manufacturer, owner, or service_center"), 400
    if not username or len(password) < 4:
        return jsonify(error="username required, password must be 4+ chars"), 400
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), role),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="username already taken"), 409
    return jsonify(message="registered"), 201


@app.post("/api/login")
def login():
    body = request.get_json(force=True)
    row = get_db().execute(
        "SELECT * FROM users WHERE username=?", (body.get("username"),)
    ).fetchone()
    if row is None or not check_password_hash(row["password_hash"], body.get("password", "")):
        return jsonify(error="invalid credentials"), 401
    session["user_id"] = row["id"]
    return jsonify(username=row["username"], role=row["role"])


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(message="logged out")


@app.get("/api/me")
def me():
    user = current_user()
    if user is None:
        return jsonify(user=None)
    return jsonify(user={"username": user["username"], "role": user["role"]})


# ---------------------------------------------------------------- products
@app.post("/api/products/mint")
def mint_product():
    user, err = require_role("manufacturer")
    if err:
        return err
    body = request.get_json(force=True)
    serial = (body.get("serial_number") or "").strip()
    name = (body.get("name") or "").strip()
    warranty_months = int(body.get("warranty_months") or 12)
    if not serial or not name:
        return jsonify(error="serial_number and name are required"), 400

    db = get_db()
    exists = db.execute("SELECT 1 FROM products WHERE serial_number=?", (serial,)).fetchone()
    if exists:
        return jsonify(error="a product with this serial number is already registered"), 409

    manufacture_date = now_iso()
    db.execute(
        """INSERT INTO products
           (serial_number, name, manufacturer, manufacture_date, warranty_months, current_owner, purchase_date, status)
           VALUES (?, ?, ?, ?, ?, NULL, NULL, 'unsold')""",
        (serial, name, user["username"], manufacture_date, warranty_months),
    )
    db.commit()

    block = get_chain().add_block("MINT", {
        "serial_number": serial,
        "name": name,
        "manufacturer": user["username"],
        "manufacture_date": manufacture_date,
        "warranty_months": warranty_months,
    })
    return jsonify(message="product minted", block_index=block.index, hash=block.hash), 201


@app.post("/api/products/<serial>/sell")
def sell_product(serial):
    """First sale: manufacturer/retailer marks the product sold to an owner username."""
    user, err = require_role("manufacturer")
    if err:
        return err
    body = request.get_json(force=True)
    buyer = (body.get("buyer_username") or "").strip()
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE serial_number=?", (serial,)).fetchone()
    if product is None:
        return jsonify(error="unknown serial number"), 404
    buyer_row = db.execute("SELECT * FROM users WHERE username=? AND role='owner'", (buyer,)).fetchone()
    if buyer_row is None:
        return jsonify(error="buyer must be a registered 'owner' username"), 400

    purchase_date = now_iso()
    db.execute(
        "UPDATE products SET current_owner=?, purchase_date=?, status='active' WHERE serial_number=?",
        (buyer, purchase_date, serial),
    )
    db.commit()

    block = get_chain().add_block("TRANSFER", {
        "serial_number": serial,
        "from": user["username"],
        "to": buyer,
        "purchase_date": purchase_date,
        "reason": "initial_sale",
    })
    return jsonify(message="sale recorded", block_index=block.index, hash=block.hash)


@app.post("/api/products/<serial>/transfer")
def transfer_product(serial):
    """Resale: current owner transfers to another registered owner."""
    user, err = require_role("owner")
    if err:
        return err
    body = request.get_json(force=True)
    new_owner = (body.get("new_owner_username") or "").strip()
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE serial_number=?", (serial,)).fetchone()
    if product is None:
        return jsonify(error="unknown serial number"), 404
    if product["current_owner"] != user["username"]:
        return jsonify(error="only the current owner can transfer this product"), 403
    new_owner_row = db.execute("SELECT * FROM users WHERE username=? AND role='owner'", (new_owner,)).fetchone()
    if new_owner_row is None:
        return jsonify(error="new_owner_username must be a registered 'owner'"), 400

    db.execute("UPDATE products SET current_owner=? WHERE serial_number=?", (new_owner, serial))
    db.commit()

    block = get_chain().add_block("TRANSFER", {
        "serial_number": serial,
        "from": user["username"],
        "to": new_owner,
        "purchase_date": now_iso(),
        "reason": "resale",
    })
    return jsonify(message="ownership transferred", block_index=block.index, hash=block.hash)


@app.get("/api/products/mine")
def my_products():
    user, err = require_role("owner")
    if err:
        return err
    rows = get_db().execute(
        "SELECT * FROM products WHERE current_owner=?", (user["username"],)
    ).fetchall()
    return jsonify(products=[dict(r) for r in rows])


@app.get("/api/products/manufactured")
def manufactured_products():
    user, err = require_role("manufacturer")
    if err:
        return err
    rows = get_db().execute(
        "SELECT * FROM products WHERE manufacturer=?", (user["username"],)
    ).fetchall()
    return jsonify(products=[dict(r) for r in rows])


# ---------------------------------------------------------------- warranty
def _warranty_status(product_row):
    if product_row["purchase_date"] is None:
        return "not_yet_sold", None
    purchase = datetime.strptime(product_row["purchase_date"], "%Y-%m-%dT%H:%M:%SZ")
    expiry = purchase + timedelta(days=30 * int(product_row["warranty_months"]))
    active = datetime.utcnow() <= expiry
    return ("active" if active else "expired"), expiry.strftime("%Y-%m-%dT%H:%M:%SZ")


@app.post("/api/products/<serial>/claim")
def file_claim(serial):
    user, err = require_role("owner")
    if err:
        return err
    body = request.get_json(force=True)
    issue = (body.get("issue") or "").strip()
    if not issue:
        return jsonify(error="issue description required"), 400

    db = get_db()
    product = db.execute("SELECT * FROM products WHERE serial_number=?", (serial,)).fetchone()
    if product is None:
        return jsonify(error="unknown serial number"), 404
    if product["current_owner"] != user["username"]:
        return jsonify(error="only the current owner can file a claim"), 403

    status, expiry = _warranty_status(product)
    if status != "active":
        return jsonify(error=f"warranty is not active (status: {status})"), 400

    pending = db.execute(
        "SELECT 1 FROM claims WHERE serial_number=? AND status='pending'", (serial,)
    ).fetchone()
    if pending:
        return jsonify(error="a claim for this product is already pending"), 409

    filed_at = now_iso()
    cur = db.execute(
        "INSERT INTO claims (serial_number, filed_by, issue, filed_at, status) VALUES (?, ?, ?, ?, 'pending')",
        (serial, user["username"], issue, filed_at),
    )
    db.commit()
    claim_id = cur.lastrowid

    block = get_chain().add_block("CLAIM_FILED", {
        "serial_number": serial,
        "claim_id": claim_id,
        "filed_by": user["username"],
        "issue": issue,
        "warranty_expiry": expiry,
    })
    return jsonify(message="claim filed", claim_id=claim_id, block_index=block.index, hash=block.hash), 201


@app.get("/api/claims/pending")
def pending_claims():
    user, err = require_role("service_center")
    if err:
        return err
    rows = get_db().execute("SELECT * FROM claims WHERE status='pending'").fetchall()
    return jsonify(claims=[dict(r) for r in rows])


@app.post("/api/claims/<int:claim_id>/resolve")
def resolve_claim(claim_id):
    user, err = require_role("service_center")
    if err:
        return err
    body = request.get_json(force=True)
    decision = body.get("decision")
    note = (body.get("note") or "").strip()
    if decision not in ("approved", "rejected"):
        return jsonify(error="decision must be 'approved' or 'rejected'"), 400

    db = get_db()
    claim = db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if claim is None:
        return jsonify(error="unknown claim id"), 404
    if claim["status"] != "pending":
        return jsonify(error="claim already resolved"), 409

    resolved_at = now_iso()
    db.execute(
        "UPDATE claims SET status=?, resolved_by=?, resolved_at=?, resolution_note=? WHERE id=?",
        (decision, user["username"], resolved_at, note, claim_id),
    )
    db.commit()

    block = get_chain().add_block("CLAIM_RESOLVED", {
        "serial_number": claim["serial_number"],
        "claim_id": claim_id,
        "resolved_by": user["username"],
        "decision": decision,
        "note": note,
    })
    return jsonify(message="claim resolved", block_index=block.index, hash=block.hash)


# ---------------------------------------------------------------- verification (public)
@app.get("/api/verify/<serial>")
def verify(serial):
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE serial_number=?", (serial,)).fetchone()
    if product is None:
        return jsonify(authentic=False, message="No product registered on-chain with this serial number."), 404

    status, expiry = _warranty_status(product)
    history = [
        {"event": b.event_type, "timestamp": b.timestamp, "data": b.data, "hash": b.hash}
        for b in get_chain().history_for_serial(serial)
    ]
    valid, reason = get_chain().is_valid()

    return jsonify(
        authentic=True,
        chain_intact=valid,
        chain_issue=reason,
        product={
            "serial_number": product["serial_number"],
            "name": product["name"],
            "manufacturer": product["manufacturer"],
            "manufacture_date": product["manufacture_date"],
            "current_owner": product["current_owner"],
            "purchase_date": product["purchase_date"],
            "warranty_status": status,
            "warranty_expiry": expiry,
        },
        history=history,
    )


# ---------------------------------------------------------------- chain explorer
@app.get("/api/chain")
def chain_view():
    valid, reason = get_chain().is_valid()
    return jsonify(valid=valid, issue=reason, blocks=get_chain().as_list())


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
