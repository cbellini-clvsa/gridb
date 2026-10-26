"""Dos brokers con la misma interfaz: PaperBroker (simulado) y LiveBroker (ccxt)."""
import logging
import uuid

log = logging.getLogger("broker")


class BaseBroker:
    def __init__(self, exchange, db):
        self.ex = exchange
        self.db = db

    def last_price(self, symbol):
        return float(self.ex.fetch_ticker(symbol)["last"])

    def round_price(self, symbol, p):
        return float(self.ex.price_to_precision(symbol, p))

    def round_amount(self, symbol, a):
        return float(self.ex.amount_to_precision(symbol, a))

    def min_cost(self, symbol):
        lim = self.ex.market(symbol).get("limits", {}).get("cost", {})
        return lim.get("min") or 0.0


class PaperBroker(BaseBroker):
    """Simula órdenes límite usando el último precio. Nada toca el exchange."""

    def __init__(self, exchange, db, fee_pct, start_quote):
        super().__init__(exchange, db)
        self.fee = fee_pct / 100.0
        if self.db.get("paper_quote") is None:
            self.db.set("paper_quote", start_quote)

    @property
    def quote(self):
        return self.db.get("paper_quote", 0.0)

    def _adj(self, delta):
        self.db.set("paper_quote", self.quote + delta)

    def create_limit_order(self, symbol, side, price, amount):
        oid = "paper-" + uuid.uuid4().hex[:10]
        if side == "buy":
            cost = price * amount
            if cost > self.quote:
                raise RuntimeError(f"Saldo paper insuficiente ({self.quote:.2f}) para {cost:.2f}")
            self._adj(-cost)  # reservar
        return {"id": oid, "price": price, "amount": amount}

    def cancel(self, symbol, order):
        if order["side"] == "buy":
            self._adj(order["price"] * order["amount"])  # liberar reserva

    def check_fills(self, symbol, open_orders, price):
        """Devuelve las órdenes de `open_orders` que se habrían ejecutado."""
        filled = []
        for o in open_orders:
            if (o["side"] == "buy" and price <= o["price"]) or \
               (o["side"] == "sell" and price >= o["price"]):
                fee = o["price"] * o["amount"] * self.fee
                if o["side"] == "buy":
                    self._adj(-fee)
                else:
                    self._adj(o["price"] * o["amount"] - fee)
                filled.append({**o, "fill_price": o["price"], "fee": fee})
        return filled

    def market_sell(self, symbol, amount):
        price = self.last_price(symbol)
        fee = price * amount * self.fee
        self._adj(price * amount - fee)
        return price, fee

    def balance_text(self):
        return f"USDT (paper): {self.quote:.2f}"


class LiveBroker(BaseBroker):
    """Órdenes reales vía ccxt. Requiere claves API con permiso de trading."""

    def create_limit_order(self, symbol, side, price, amount):
        r = self.ex.create_order(symbol, "limit", side, amount, price)
        log.info("Orden real %s %s %s @ %s id=%s", side, amount, symbol, price, r["id"])
        return {"id": r["id"], "price": float(r.get("price") or price),
                "amount": float(r.get("amount") or amount)}

    def cancel(self, symbol, order):
        try:
            self.ex.cancel_order(order["id"], symbol)
        except Exception as e:  # ya ejecutada o inexistente
            log.warning("No se pudo cancelar %s: %s", order["id"], e)

    def check_fills(self, symbol, open_orders, price):
        if not open_orders:
            return []
        live_open = {o["id"] for o in self.ex.fetch_open_orders(symbol)}
        filled = []
        for o in open_orders:
            if o["id"] in live_open:
                continue
            try:
                r = self.ex.fetch_order(o["id"], symbol)
            except Exception as e:
                log.warning("fetch_order %s: %s", o["id"], e)
                continue
            if r["status"] == "closed":
                fee = 0.0
                if r.get("fee") and r["fee"].get("cost"):
                    fee = float(r["fee"]["cost"])
                filled.append({**o, "fill_price": float(r.get("average") or o["price"]),
                               "fee": fee})
            elif r["status"] in ("canceled", "expired", "rejected"):
                self.db.set_order_status(o["id"], "canceled")
                log.warning("Orden %s %s en el exchange", o["id"], r["status"])
        return filled

    def market_sell(self, symbol, amount):
        amount = self.round_amount(symbol, amount)
        r = self.ex.create_order(symbol, "market", "sell", amount)
        try:
            r = self.ex.fetch_order(r["id"], symbol)
        except Exception:
            pass
        price = float(r.get("average") or self.last_price(symbol))
        fee = float(r["fee"]["cost"]) if r.get("fee") and r["fee"].get("cost") else 0.0
        return price, fee

    def balance_text(self):
        b = self.ex.fetch_balance()
        parts = [f"{k}: {v:.4f}" for k, v in b.get("total", {}).items() if v]
        return ", ".join(parts[:12]) or "sin saldo"
