"""
Walmart Order Splitting Dashboard — Flask back-end.

Endpoints
---------
GET  /api/orders?card=&date_from=&date_to=   list orders (with filters)
GET  /api/orders/<id>/items                   items + split checkboxes for one order
PUT  /api/splits/<item_id>                    toggle a roommate checkbox  { roommate, checked }
GET  /api/summary?card=&date_from=&date_to=   per-roommate totals
GET  /api/cards                               distinct card last-4 digits
GET  /api/export/csv?card=&date_from=&date_to= download splits as CSV
GET  /                                        serve the dashboard SPA
"""

import csv
import io
import os
import sqlite3
from flask import Flask, Response, g, jsonify, request, send_from_directory

DB_PATH = os.path.join(os.path.dirname(__file__), "walmart.db")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=STATIC_DIR)

# ── Roommate helpers (DB-backed, persist across restarts) ──────────
def migrate_db():
    """Create roommates table if missing; seed from existing splits or defaults."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS roommates (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT UNIQUE NOT NULL,
            sort_order INTEGER DEFAULT 0
        )
    """)
    count = c.execute("SELECT COUNT(*) FROM roommates").fetchone()[0]
    if count == 0:
        distinct = c.execute(
            "SELECT DISTINCT roommate FROM splits ORDER BY roommate"
        ).fetchall()
        seeds = [r[0] for r in distinct] if distinct else ["Roommate 1", "Roommate 2", "Roommate 3"]
        for i, name in enumerate(seeds):
            c.execute(
                "INSERT OR IGNORE INTO roommates (name, sort_order) VALUES (?, ?)",
                (name, i),
            )
    conn.commit()
    conn.close()


def get_roommates(db: sqlite3.Connection) -> list[str]:
    rows = db.execute("SELECT name FROM roommates ORDER BY sort_order, id").fetchall()
    return [r["name"] for r in rows]


# Run once at module load (dev server + gunicorn)
if os.path.exists(DB_PATH):
    migrate_db()


# ── DB helper ───────────────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


# ── API: list distinct card numbers ─────────────────────────────────
@app.get("/api/cards")
def api_cards():
    rows = get_db().execute(
        "SELECT DISTINCT payment_last4 FROM orders ORDER BY payment_last4"
    ).fetchall()
    return jsonify([r["payment_last4"] for r in rows])


# ── API: list orders (filterable) ───────────────────────────────────
@app.get("/api/orders")
def api_orders():
    db = get_db()
    clauses, params = [], []

    card = request.args.get("card")
    if card:
        clauses.append("o.payment_last4 = ?")
        params.append(card)

    date_from = request.args.get("date_from")
    if date_from:
        clauses.append("o.order_date >= ?")
        params.append(date_from)

    date_to = request.args.get("date_to")
    if date_to:
        clauses.append("o.order_date <= ?")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = f"""
        SELECT o.*,
               COUNT(i.id) AS item_count
        FROM orders o
        LEFT JOIN items i ON i.order_id = o.id
        {where}
        GROUP BY o.id
        ORDER BY o.order_date DESC
    """
    rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


# ── API: items for one order ────────────────────────────────────────
@app.get("/api/orders/<int:order_id>/items")
def api_order_items(order_id: int):
    db = get_db()
    order = db.execute(
        "SELECT tax, delivery_charges, tip FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    items = db.execute(
        "SELECT * FROM items WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()

    result = []
    for item in items:
        splits = db.execute(
            "SELECT roommate, checked FROM splits WHERE item_id = ?", (item["id"],)
        ).fetchall()
        d = dict(item)
        d["splits"] = {s["roommate"]: bool(s["checked"]) for s in splits}
        result.append(d)

    return jsonify({
        "items": result,
        "tax": order["tax"] or 0,
        "delivery_charges": order["delivery_charges"] or 0,
        "tip": order["tip"] or 0,
    })


# ── API: toggle split checkbox ──────────────────────────────────────
@app.put("/api/splits/<int:item_id>")
def api_toggle_split(item_id: int):
    data = request.get_json(force=True)
    roommate = data.get("roommate")
    checked = int(bool(data.get("checked")))

    db = get_db()
    if roommate not in get_roommates(db):
        return jsonify({"error": "invalid roommate"}), 400

    db.execute(
        "UPDATE splits SET checked = ? WHERE item_id = ? AND roommate = ?",
        (checked, item_id, roommate),
    )
    db.commit()
    return jsonify({"ok": True})


# ── API: per-roommate spending summary ──────────────────────────────
@app.get("/api/summary")
def api_summary():
    db = get_db()
    clauses, params = [], []

    card = request.args.get("card")
    if card:
        clauses.append("o.payment_last4 = ?")
        params.append(card)

    date_from = request.args.get("date_from")
    if date_from:
        clauses.append("o.order_date >= ?")
        params.append(date_from)

    date_to = request.args.get("date_to")
    if date_to:
        clauses.append("o.order_date <= ?")
        params.append(date_to)

    where = ("AND " + " AND ".join(clauses)) if clauses else ""

    item_sql = f"""
        SELECT s.roommate,
               ROUND(SUM(
                   CASE WHEN s.checked = 1
                        THEN i.price * 1.0 / (
                            SELECT COUNT(*) FROM splits s2
                            WHERE s2.item_id = i.id AND s2.checked = 1
                        )
                        ELSE 0
                   END
               ), 2) AS item_total
        FROM splits s
        JOIN items i ON i.id = s.item_id
        JOIN orders o ON o.id = i.order_id
        WHERE 1=1 {where}
        GROUP BY s.roommate
        ORDER BY s.roommate
    """
    charges_sql = f"""
        SELECT ROUND(SUM(o.tax + o.delivery_charges), 2) AS total_charges
        FROM orders o
        WHERE 1=1 {where}
    """
    item_rows = db.execute(item_sql, params).fetchall()
    charges_row = db.execute(charges_sql, params).fetchone()
    total_charges = charges_row["total_charges"] or 0
    all_roommates = get_roommates(db)
    charge_per_roommate = round(total_charges / len(all_roommates), 2) if all_roommates else 0

    roommate_totals = {r["roommate"]: round((r["item_total"] or 0) + charge_per_roommate, 2)
                       for r in item_rows}
    grand = round(sum(roommate_totals.values()), 2)
    return jsonify(
        {
            "roommates": roommate_totals,
            "grand_total": grand,
            "tax_per_roommate": charge_per_roommate,
        }
    )


# ── API: roommate names ────────────────────────────────────────────
@app.get("/api/roommates")
def api_roommates():
    return jsonify(get_roommates(get_db()))


# ── API: rename roommate ───────────────────────────────────────────
@app.put("/api/roommates")
def api_rename_roommate():
    data = request.get_json(force=True)
    old_name = data.get("old_name")
    new_name = data.get("new_name", "").strip()
    if not old_name or not new_name:
        return jsonify({"error": "old_name and new_name required"}), 400

    db = get_db()
    db.execute("UPDATE roommates SET name = ? WHERE name = ?", (new_name, old_name))
    db.execute("UPDATE splits SET roommate = ? WHERE roommate = ?", (new_name, old_name))
    db.commit()
    return jsonify({"ok": True, "roommates": get_roommates(db)})


# ── API: add roommate ──────────────────────────────────────────────
@app.post("/api/roommates")
def api_add_roommate():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    db = get_db()
    existing = get_roommates(db)
    if name in existing:
        return jsonify({"error": "name already exists"}), 409

    db.execute(
        "INSERT INTO roommates (name, sort_order) VALUES (?, ?)", (name, len(existing))
    )
    # Create split rows (checked=1) for every existing item
    for row in db.execute("SELECT id FROM items").fetchall():
        db.execute(
            "INSERT OR IGNORE INTO splits (item_id, roommate, checked) VALUES (?, ?, 1)",
            (row["id"], name),
        )
    db.commit()
    return jsonify({"ok": True, "roommates": get_roommates(db)})


# ── API: delete roommate ───────────────────────────────────────────
@app.delete("/api/roommates/<path:name>")
def api_delete_roommate(name: str):
    db = get_db()
    roommates = get_roommates(db)
    if len(roommates) <= 1:
        return jsonify({"error": "Cannot remove the last person"}), 400
    if name not in roommates:
        return jsonify({"error": "Roommate not found"}), 404

    db.execute("DELETE FROM roommates WHERE name = ?", (name,))
    db.execute("DELETE FROM splits WHERE roommate = ?", (name,))
    db.commit()
    return jsonify({"ok": True, "roommates": get_roommates(db)})


# ── API: export splits as CSV ──────────────────────────────────────
@app.get("/api/export/csv")
def api_export_csv():
    db = get_db()
    clauses, params = [], []

    card = request.args.get("card")
    if card:
        clauses.append("o.payment_last4 = ?")
        params.append(card)
    date_from = request.args.get("date_from")
    if date_from:
        clauses.append("o.order_date >= ?")
        params.append(date_from)
    date_to = request.args.get("date_to")
    if date_to:
        clauses.append("o.order_date <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = f"""
        SELECT o.order_number, o.order_date, o.payment_last4, o.shipping_address,
               o.subtotal, o.tax, o.delivery_charges, o.tip, o.order_total,
               i.id AS item_id, i.product_name, i.quantity, i.price, i.delivery_status,
               s.roommate, s.checked
        FROM orders o
        JOIN items i ON i.order_id = o.id
        JOIN splits s ON s.item_id = i.id
        {where}
        ORDER BY o.order_date, o.order_number, i.id, s.roommate
    """
    rows = db.execute(sql, params).fetchall()

    # Pre-compute: number of checked roommates per item
    from collections import defaultdict
    item_checked_counts: dict = defaultdict(int)
    order_charges: dict = {}
    for row in rows:
        if row["checked"]:
            item_checked_counts[row["item_id"]] += 1
        order_charges[row["order_number"]] = (
            row["tax"] or 0,
            row["delivery_charges"] or 0,
        )

    num_roommates = len(get_roommates(db))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "order_number", "order_date", "payment_last4", "shipping_address",
        "product_name", "quantity", "item_price", "delivery_status",
        "roommate", "included",
        "item_share", "tax_share", "delivery_share", "total_share",
    ])

    for row in rows:
        n_checked = item_checked_counts[row["item_id"]]
        item_share = round(row["price"] / n_checked, 4) if row["checked"] and n_checked > 0 else 0.0
        tax, delivery = order_charges[row["order_number"]]
        tax_share = round(tax / num_roommates, 4)
        delivery_share = round(delivery / num_roommates, 4)
        total_share = round(item_share + tax_share + delivery_share, 4)

        writer.writerow([
            row["order_number"], row["order_date"], row["payment_last4"],
            row["shipping_address"],
            row["product_name"], row["quantity"], row["price"],
            row["delivery_status"],
            row["roommate"], "yes" if row["checked"] else "no",
            item_share, tax_share, delivery_share, total_share,
        ])

    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=walmart_splits.csv"},
    )


# ── Serve SPA ───────────────────────────────────────────────────────
@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print("Database not found! Run  python parse_orders.py  first.")
        raise SystemExit(1)
    app.run(debug=True, port=5001)
