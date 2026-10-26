"""Persistencia en SQLite: grillas, órdenes, operaciones y estado."""
import json
import sqlite3
import threading
import time


class DB:
    def __init__(self, path="gridbot.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init()

    def _init(self):
        with self.lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS grids(
                symbol TEXT PRIMARY KEY, low REAL, high REAL,
                levels TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS orders(
                id TEXT PRIMARY KEY, symbol TEXT, side TEXT, price REAL,
                amount REAL, level INTEGER, status TEXT, buy_cost REAL,
                created REAL, filled REAL);
            CREATE TABLE IF NOT EXISTS trades(
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT,
                price REAL, amount REAL, fee REAL, level INTEGER, pnl REAL, ts REAL);
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            """)
            self.conn.commit()

    # ---- grillas ----
    def save_grid(self, symbol, low, high, levels):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO grids VALUES (?,?,?,?,?)",
                (symbol, low, high, json.dumps(levels), time.time()))
            self.conn.commit()

    def get_grid(self, symbol):
        with self.lock:
            r = self.conn.execute(
                "SELECT * FROM grids WHERE symbol=?", (symbol,)).fetchone()
        if not r:
            return None
        return {"low": r["low"], "high": r["high"],
                "levels": json.loads(r["levels"]), "created": r["created"]}

    def delete_grid(self, symbol):
        with self.lock:
            self.conn.execute("DELETE FROM grids WHERE symbol=?", (symbol,))
            self.conn.commit()

    # ---- órdenes ----
    def add_order(self, o):
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                (o["id"], o["symbol"], o["side"], o["price"], o["amount"],
                 o["level"], "open", o.get("buy_cost", 0.0), time.time(), None))
            self.conn.commit()

    def set_order_status(self, oid, status):
        with self.lock:
            self.conn.execute(
                "UPDATE orders SET status=?, filled=? WHERE id=?",
                (status, time.time() if status == "filled" else None, oid))
            self.conn.commit()

    def open_orders(self, symbol, side=None):
        q = "SELECT * FROM orders WHERE symbol=? AND status='open'"
        args = [symbol]
        if side:
            q += " AND side=?"
            args.append(side)
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY price", args).fetchall()
        return [dict(r) for r in rows]

    # ---- operaciones ----
    def add_trade(self, symbol, side, price, amount, fee, level, pnl=0.0):
        with self.lock:
            self.conn.execute(
                "INSERT INTO trades(symbol,side,price,amount,fee,level,pnl,ts) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (symbol, side, price, amount, fee, level, pnl, time.time()))
            self.conn.commit()

    def pnl_summary(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT symbol, COUNT(*) n, SUM(pnl) pnl, SUM(fee) fees "
                "FROM trades GROUP BY symbol").fetchall()
        return [dict(r) for r in rows]

    def cycles_since(self, symbol, ts):
        """Ventas cerradas (ciclos completos) y ganancia desde que se creó la grilla."""
        with self.lock:
            r = self.conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl FROM trades "
                "WHERE symbol=? AND side='sell' AND ts>=?", (symbol, ts)).fetchone()
        return r["n"], r["pnl"]

    def recent_trades(self, n=10):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM trades ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    # ---- clave/valor ----
    def get(self, k, default=None):
        with self.lock:
            r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, k, v):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)",
                              (k, json.dumps(v)))
            self.conn.commit()
