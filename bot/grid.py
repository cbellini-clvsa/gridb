"""Lógica de grid trading (grid "largo": compra abajo, vende un nivel arriba)."""
import logging
import time

log = logging.getLogger("grid")


def auto_range(exchange, symbol, timeframe="1h", lookback=168, margin_pct=2.0):
    """Rango = mínimo/máximo de las últimas `lookback` velas, ampliado un margen."""
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=lookback)
    lows = [c[3] for c in ohlcv]
    highs = [c[2] for c in ohlcv]
    low, high = min(lows), max(highs)
    m = margin_pct / 100.0
    return low * (1 - m), high * (1 + m)


def build_levels(low, high, n):
    """n+1 precios espaciados geométricamente (mismo % entre niveles)."""
    ratio = (high / low) ** (1.0 / n)
    return [low * ratio ** i for i in range(n + 1)]


class GridTrader:
    def __init__(self, symbol, cfg, broker, db, notify):
        self.symbol = symbol
        self.cfg = cfg
        self.broker = broker
        self.db = db
        self.notify = notify
        self.paused = False
        self.finished = False
        self.out_of_range_notified = False
        self.grid = db.get_grid(symbol)

    # ---------- construcción ----------
    def ensure_grid(self):
        if self.grid:
            return
        g = self.cfg["grid"]
        manual = self.cfg.get("ranges", {}).get(self.symbol)
        if manual:
            low, high = float(manual[0]), float(manual[1])
        else:
            low, high = auto_range(self.broker.ex, self.symbol, g["timeframe"],
                                   g["lookback_candles"], g["margin_pct"])
        levels = [self.broker.round_price(self.symbol, p)
                  for p in build_levels(low, high, g["levels"])]
        self.db.save_grid(self.symbol, low, high, levels)
        self.grid = self.db.get_grid(self.symbol)
        price = self.broker.last_price(self.symbol)
        placed = self._place_initial_buys(price)
        step = (levels[1] / levels[0] - 1) * 100
        self.notify(f"🧱 Grilla {self.symbol}\n"
                    f"Rango {low:.6g} – {high:.6g} ({g['levels']} niveles, paso {step:.2f}%)\n"
                    f"Precio {price:.6g} · {placed} compras colocadas")

    def _place_initial_buys(self, price):
        quote = self.cfg["grid"]["quote_per_level"]
        min_cost = self.broker.min_cost(self.symbol)
        if min_cost and quote < min_cost:
            self.notify(f"⚠️ {self.symbol}: quote_per_level={quote} < mínimo del exchange {min_cost}")
        placed = 0
        existing = {o["level"] for o in self.db.open_orders(self.symbol, "buy")}
        for i, p in enumerate(self.grid["levels"][:-1]):  # el nivel más alto es sólo de venta
            if p < price and i not in existing:
                if self._place("buy", i):
                    placed += 1
        return placed

    def _place(self, side, level, amount=None, buy_cost=0.0):
        price = self.grid["levels"][level]
        if amount is None:
            amount = self.cfg["grid"]["quote_per_level"] / price
        amount = self.broker.round_amount(self.symbol, amount)
        try:
            r = self.broker.create_limit_order(self.symbol, side, price, amount)
        except Exception as e:
            log.error("%s: no se pudo colocar %s nivel %d: %s", self.symbol, side, level, e)
            self.notify(f"❌ {self.symbol}: fallo al colocar {side} @ {price:.6g}: {e}")
            return False
        self.db.add_order({"id": r["id"], "symbol": self.symbol, "side": side,
                           "price": r["price"], "amount": r["amount"],
                           "level": level, "buy_cost": buy_cost})
        return True

    # ---------- ciclo ----------
    def tick(self):
        if self.finished:
            return self.broker.last_price(self.symbol)
        self.ensure_grid()
        price = self.broker.last_price(self.symbol)
        open_orders = self.db.open_orders(self.symbol)
        for o in self.broker.check_fills(self.symbol, open_orders, price):
            self._on_fill(o)
        if self._check_exit(price) or self._check_stop(price):
            return price
        self._check_range(price)
        return price

    def _check_stop(self, price):
        """Stop: por patrimonio (realizado + latente) o por precio bajo el rango."""
        st = self.cfg.get("stop", {})
        if not st.get("enabled"):
            return False
        capital = self.cfg["grid"]["levels"] * self.cfg["grid"]["quote_per_level"]
        _, realized = self.db.cycles_since(self.symbol, self.grid["created"])
        net = realized + self.unrealized(price)
        reason = None
        if st.get("max_loss_pct") and net <= -capital * st["max_loss_pct"] / 100:
            reason = f"patrimonio {net:+.4f} ({net / capital * 100:+.2f}% del capital)"
        floor = self.grid["low"] * (1 - st.get("price_below_low_pct", 0) / 100)
        if st.get("price_below_low_pct") and price < floor:
            reason = f"precio {price:.6g} bajo el piso {floor:.6g}"
        if not reason:
            return False
        self.liquidate(price, reason, st.get("action", "liquidate"))
        return True

    def liquidate(self, price, reason, action="liquidate"):
        """Cancela todo; con action=liquidate vende el inventario a mercado y se detiene."""
        for o in self.db.open_orders(self.symbol, "buy"):
            self.broker.cancel(self.symbol, o)
            self.db.set_order_status(o["id"], "canceled")
        if action != "liquidate":
            self.paused = True
            self.notify(f"🛑 STOP {self.symbol} ({reason}) → compras canceladas, ventas se mantienen, pausado.")
            return
        sells = self.db.open_orders(self.symbol, "sell")
        total_amt = sum(o["amount"] for o in sells)
        total_cost = sum(o["buy_cost"] for o in sells)
        for o in sells:
            self.broker.cancel(self.symbol, o)
            self.db.set_order_status(o["id"], "canceled")
        pnl = 0.0
        if total_amt > 0:
            fp, fee = self.broker.market_sell(self.symbol, total_amt)
            pnl = fp * total_amt - fee - total_cost
            self.db.add_trade(self.symbol, "sell", fp, total_amt, fee, -1, pnl)
        self.finished = True
        self.notify(f"🛑 STOP {self.symbol} ({reason}) → liquidado {total_amt:.6g} a mercado, "
                    f"resultado {pnl:+.4f}. Detenido; /reanudar {self.symbol} para rearmar.")

    def _check_exit(self, price):
        """Salida limpia: inventario cero (sin ventas pendientes) + objetivo cumplido."""
        ex = self.cfg.get("exit", {})
        if not ex.get("enabled"):
            return False
        if self.db.open_orders(self.symbol, "sell"):
            return False  # todavía hay inventario sin vender
        cycles, pnl = self.db.cycles_since(self.symbol, self.grid["created"])
        if cycles == 0:
            return False
        capital = self.cfg["grid"]["levels"] * self.cfg["grid"]["quote_per_level"]
        pnl_pct = pnl / capital * 100
        ok_cycles = cycles >= ex.get("min_cycles", 0)
        ok_pnl = pnl_pct >= ex.get("min_profit_pct", 0)
        if not (ok_cycles and ok_pnl):
            return False
        for o in self.db.open_orders(self.symbol, "buy"):
            self.broker.cancel(self.symbol, o)
            self.db.set_order_status(o["id"], "canceled")
        msg = (f"✅ {self.symbol} plano con objetivo cumplido: {cycles} ciclos, "
               f"ganancia {pnl:+.4f} ({pnl_pct:+.2f}% del capital de la grilla)")
        if ex.get("restart"):
            self.db.delete_grid(self.symbol)
            self.grid = None
            self.notify(msg + " → nueva grilla")
            self.ensure_grid()
        else:
            self.finished = True
            self.notify(msg + " → detenido. /reanudar " + self.symbol + " para seguir.")
        return True

    def _on_fill(self, o):
        self.db.set_order_status(o["id"], "filled")
        lv, amt, fp = o["level"], o["amount"], o["fill_price"]
        if o["side"] == "buy":
            cost = fp * amt + o["fee"]
            self.db.add_trade(self.symbol, "buy", fp, amt, o["fee"], lv)
            # la venta se coloca siempre, incluso en pausa, para no dejar inventario colgado
            sell_amt = amt * (1 - self.cfg["grid"].get("sell_amount_haircut_pct", 0.1) / 100)
            self._place("sell", lv + 1, amount=sell_amt, buy_cost=cost)
            self.notify(f"🟢 Compra {self.symbol} {amt:.6g} @ {fp:.6g} (nivel {lv}) → "
                        f"venta en {self.grid['levels'][lv + 1]:.6g}")
        else:
            pnl = fp * amt - o["fee"] - o["buy_cost"]
            self.db.add_trade(self.symbol, "sell", fp, amt, o["fee"], lv, pnl)
            msg = f"🔴 Venta {self.symbol} {amt:.6g} @ {fp:.6g} · ganancia {pnl:+.4f}"
            if not self.paused:
                self._place("buy", lv - 1)
                msg += f" → recompra en {self.grid['levels'][lv - 1]:.6g}"
            self.notify(msg)

    def _check_range(self, price):
        low, high = self.grid["low"], self.grid["high"]
        inside = low <= price <= high
        if inside:
            self.out_of_range_notified = False
            return
        mode = self.cfg["grid"].get("auto_reset", "up")
        above = price > high
        if (above and mode in ("up", "both")) or (not above and mode == "both"):
            self.reset(f"precio {'sobre' if above else 'bajo'} el rango")
        elif not self.out_of_range_notified:
            self.out_of_range_notified = True
            self.notify(f"⚠️ {self.symbol} fuera de rango ({price:.6g}, rango {low:.6g}–{high:.6g}). "
                        f"Usá /reset {self.symbol} para recalcular.")

    # ---------- comandos ----------
    def reset(self, reason="manual"):
        """Cancela compras abiertas y recalcula la grilla. Las ventas pendientes se mantienen."""
        for o in self.db.open_orders(self.symbol, "buy"):
            self.broker.cancel(self.symbol, o)
            self.db.set_order_status(o["id"], "canceled")
        self.db.delete_grid(self.symbol)
        self.grid = None
        self.out_of_range_notified = False
        self.notify(f"♻️ Reset {self.symbol} ({reason})")
        self.ensure_grid()

    def unrealized(self, price):
        """PnL latente del inventario (comprado y aún no vendido), valuado a `price`."""
        sells = self.db.open_orders(self.symbol, "sell")
        fee = self.cfg.get("paper", {}).get("fee_pct", 0.1) / 100
        return sum(price * o["amount"] * (1 - fee) - o["buy_cost"] for o in sells)

    def ladder(self, price):
        """Filas de la grilla de arriba hacia abajo, con la orden pendiente en cada nivel."""
        if not self.grid:
            return []
        buys = {o["level"]: o for o in self.db.open_orders(self.symbol, "buy")}
        sells = {o["level"]: o for o in self.db.open_orders(self.symbol, "sell")}
        rows = []
        for i in reversed(range(len(self.grid["levels"]))):
            p = self.grid["levels"][i]
            if i in sells:
                o = sells[i]
                rows.append({"level": i, "price": p, "side": "sell", "amount": o["amount"],
                             "latent": price * o["amount"] - o["buy_cost"]})
            elif i in buys:
                rows.append({"level": i, "price": p, "side": "buy", "amount": buys[i]["amount"]})
            else:
                rows.append({"level": i, "price": p, "side": None})
        return rows

    def summary(self, price):
        cycles, realized = self.db.cycles_since(self.symbol, self.grid["created"]) if self.grid else (0, 0.0)
        sells = self.db.open_orders(self.symbol, "sell")
        buys = self.db.open_orders(self.symbol, "buy")
        return {"symbol": self.symbol, "price": price,
                "low": self.grid["low"] if self.grid else None,
                "high": self.grid["high"] if self.grid else None,
                "state": "finished" if self.finished else ("paused" if self.paused else "running"),
                "buys": len(buys), "sells": len(sells),
                "held": sum(o["amount"] for o in sells),
                "cycles": cycles, "realized": realized,
                "unrealized": self.unrealized(price) if self.grid else 0.0}

    def ladder_text(self, price):
        """Escalera en texto monoespaciado para Telegram."""
        rows = self.ladder(price)
        if not rows:
            return f"{self.symbol}: sin grilla"
        lines = [f"{self.symbol}  precio {price:.6g}", ""]
        shown = False
        for r in rows:
            if not shown and price > r["price"]:
                lines.append(f"  ==> {price:.6g} <== precio")
                shown = True
            if r["side"] == "sell":
                tag = f"VENTA {r['amount']:.5g}  lat {r['latent']:+.3f}"
            elif r["side"] == "buy":
                tag = f"compra {r['amount']:.5g}"
            else:
                tag = "·"
            lines.append(f"{r['level']:>2}  {r['price']:>10.6g}  {tag}")
        if not shown:
            lines.append(f"  ==> {price:.6g} <== precio")
        s = self.summary(price)
        lines += ["", f"{s['sells']} ventas arriba / {s['buys']} compras abajo",
                  f"Realizado {s['realized']:+.4f} · No realizado {s['unrealized']:+.4f}"]
        return "\n".join(lines)

    def status_line(self, price):
        buys = len(self.db.open_orders(self.symbol, "buy"))
        sells = self.db.open_orders(self.symbol, "sell")
        held = sum(o["amount"] for o in sells)
        flag = "✅" if self.finished else ("⏸" if self.paused else "▶️")
        return (f"{flag} {self.symbol}: {price:.6g} | rango {self.grid['low']:.6g}–"
                f"{self.grid['high']:.6g} | {buys} compras / {len(sells)} ventas abiertas "
                f"| en cartera {held:.6g}")
