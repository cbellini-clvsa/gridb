"""Grid bot multi-par sobre ccxt. Uso: python main.py [config.json]"""
import json
import logging
import os
import sys
import time

import ccxt
from dotenv import load_dotenv

from broker import LiveBroker, PaperBroker
from db import DB
from grid import GridTrader
from tg import Telegram
from web import start_web
from publish import Publisher

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def build_exchange(cfg, live):
    klass = getattr(ccxt, cfg["exchange"])
    params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
    if live:
        params["apiKey"] = os.getenv("EXCHANGE_API_KEY", "")
        params["secret"] = os.getenv("EXCHANGE_SECRET", "")
        if os.getenv("EXCHANGE_PASSWORD"):
            params["password"] = os.getenv("EXCHANGE_PASSWORD")
    ex = klass(params)
    if cfg.get("sandbox"):
        ex.set_sandbox_mode(True)
    ex.load_markets()
    return ex


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.live = cfg["mode"] == "live"
        self.db = DB(cfg.get("db_path", "gridbot.db"))
        self.ex = build_exchange(cfg, self.live)
        if self.live:
            self.broker = LiveBroker(self.ex, self.db)
        else:
            self.broker = PaperBroker(self.ex, self.db, cfg["paper"]["fee_pct"],
                                      cfg["paper"]["start_quote"])
        self.tg = Telegram(os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"),
                           self.command)
        self.traders = {s: GridTrader(s, cfg, self.broker, self.db, self.tg.send)
                        for s in cfg["pairs"]}
        self.prices = {}
        self.publisher = Publisher(min_interval=cfg.get("poll_seconds", 30))

    # ---- comandos de Telegram ----
    def command(self, text):
        parts = text.split()
        cmd, arg = parts[0].lower(), (parts[1].upper() if len(parts) > 1 else None)
        if cmd in ("/status", "/estado"):
            return "\n".join(t.status_line(self.prices.get(s, 0))
                             for s, t in self.traders.items() if t.grid) or "Sin grillas aún"
        if cmd == "/balance":
            try:
                return self.broker.balance_text()
            except Exception as e:
                return f"Error: {e}"
        if cmd == "/pnl":
            rows = self.db.pnl_summary()
            if not rows:
                return "Sin operaciones todavía"
            tot = sum(r["pnl"] or 0 for r in rows)
            lines = [f"{r['symbol']}: {r['n']} ops, {r['pnl'] or 0:+.4f} (fees {r['fees'] or 0:.4f})"
                     for r in rows]
            return "\n".join(lines) + f"\nTotal: {tot:+.4f}"
        if cmd in ("/pausar", "/pause"):
            for t in self._targets(arg):
                t.paused = True
            return f"⏸ Pausado {arg or 'todo'} (no se colocan compras nuevas)"
        if cmd in ("/reanudar", "/resume"):
            for t in self._targets(arg):
                t.paused = False
                if t.finished:   # vuelve a operar con una grilla nueva
                    t.finished = False
                    t.reset("reanudar")
            return f"▶️ Reanudado {arg or 'todo'}"
        if cmd == "/reset":
            if not arg or arg not in self.traders:
                return "Uso: /reset BTC/USDT"
            self.traders[arg].reset("manual")
            return "ok"
        if cmd in ("/grilla", "/grid", "/ladder"):
            if arg and arg in self.traders:
                return (self.traders[arg].ladder_text(self.prices.get(arg, 0)), True)
            if len(self.traders) == 1:
                s_ = next(iter(self.traders))
                return (self.traders[s_].ladder_text(self.prices.get(s_, 0)), True)
            return "Uso: /grilla BTC/USDT"
        if cmd == "/stop":
            if not arg or arg not in self.traders:
                return "Uso: /stop BTC/USDT  (liquida el inventario a mercado y detiene el par)"
            self.traders[arg].liquidate(self.prices.get(arg, 0), "manual")
            return "ok"
        if cmd == "/ops":
            tr = self.db.recent_trades(10)
            return "\n".join(f"{t['side']} {t['symbol']} {t['amount']:.6g} @ {t['price']:.6g} "
                             f"pnl {t['pnl']:+.4f}" for t in tr) or "Sin operaciones"
        return ("/status /grilla PAR /balance /pnl /ops\n/pausar [PAR] /reanudar [PAR]\n/reset PAR /stop PAR")

    def _targets(self, arg):
        return [self.traders[arg]] if arg in self.traders else self.traders.values()

    def snapshot(self):
        """Estado completo para el panel web."""
        pairs = []
        for s, t in self.traders.items():
            p = self.prices.get(s, 0)
            if not t.grid:
                continue
            pairs.append({**t.summary(p), "ladder": t.ladder(p)})
        return {"mode": "live" if self.live else "paper", "exchange": self.cfg["exchange"],
                "pairs": pairs, "pnl": self.db.pnl_summary(), "trades": self.db.recent_trades(200),
                "balance": self.broker.balance_text() if not self.live else "ver /balance"}

    # ---- bucle principal ----
    def run(self):
        self.tg.start()
        if self.cfg.get("web", {}).get("enabled"):
            start_web(self.cfg["web"], self.snapshot)
        self.tg.send(f"🤖 Grid bot iniciado en modo {'REAL' if self.live else 'PAPER'} "
                     f"({self.cfg['exchange']}): {', '.join(self.cfg['pairs'])}")
        interval = self.cfg.get("poll_seconds", 30)
        while True:
            for s, t in self.traders.items():
                try:
                    self.prices[s] = t.tick()
                except ccxt.NetworkError as e:
                    log.warning("%s red: %s", s, e)
                except Exception as e:
                    log.exception("%s: %s", s, e)
                    self.tg.send(f"❌ {s}: {e}")
            try:
                self.publisher.push(self.snapshot())
            except Exception as e:
                log.warning("snapshot: %s", e)
            time.sleep(interval)


if __name__ == "__main__":
    load_dotenv()
    path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    with open(path) as f:
        config = json.load(f)
    if config["mode"] == "live" and not os.getenv("EXCHANGE_API_KEY"):
        sys.exit("Modo live sin EXCHANGE_API_KEY en .env")
    Bot(config).run()
