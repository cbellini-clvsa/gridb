"""Panel web de solo lectura. Usa http.server (sin dependencias), auth básica y auto-refresh."""
import base64
import html
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("web")

CSS = """
:root{--bg:#f6f6f4;--card:#fff;--tx:#1b1b1b;--mut:#6b6b66;--bd:#dcdcd6;--up:#1d9e75;--dn:#d85a30;--mark:#e6f1fb;--markb:#185fa5}
@media(prefers-color-scheme:dark){:root{--bg:#141414;--card:#1f1f1f;--tx:#ececec;--mut:#9a9a94;--bd:#333;--mark:#0c447c;--markb:#85b7eb}}
*{box-sizing:border-box}body{margin:0;padding:12px;font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--tx)}
h1{font-size:18px;font-weight:500;margin:4px 0 12px}h2{font-size:16px;font-weight:500;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px;margin-bottom:12px}
.m{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}.m div{background:var(--bg);border-radius:8px;padding:8px}
.m small{display:block;color:var(--mut);font-size:12px}.m b{font-size:17px;font-weight:500}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
td,th{padding:4px 6px;border-bottom:1px solid var(--bd);text-align:right}th{color:var(--mut);font-weight:500}
td:first-child,th:first-child{text-align:left}
.sell{color:var(--dn)}.buy{color:var(--up)}.pos{color:var(--up)}.neg{color:var(--dn)}.dim{color:var(--mut)}
tr.mark td{background:var(--mark);color:var(--markb);font-weight:500;border-top:2px solid var(--markb);border-bottom:2px solid var(--markb)}
.tag{font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid var(--bd);color:var(--mut)}
.muted{color:var(--mut);font-size:12px}svg{width:100%;height:90px;display:block}
"""


def fmt(x, d=6):
    return f"{x:.{d}g}" if isinstance(x, (int, float)) else str(x)


def signed(x, d=4):
    cls = "pos" if x > 0 else ("neg" if x < 0 else "dim")
    return f'<span class="{cls}">{x:+.{d}f}</span>'


def pnl_chart(trades):
    """Curva de ganancia realizada acumulada (SVG inline)."""
    pts = [t for t in reversed(trades) if t["side"] == "sell"]
    if len(pts) < 2:
        return '<p class="muted">Sin ventas suficientes para graficar.</p>'
    acc, ys = 0.0, []
    for t in pts:
        acc += t["pnl"] or 0
        ys.append(acc)
    lo, hi = min(0, min(ys)), max(ys) or 1
    w, h = 400, 90
    coords = []
    for i, y in enumerate(ys):
        x = i / (len(ys) - 1) * w
        yy = h - (y - lo) / (hi - lo or 1) * (h - 6) - 3
        coords.append(f"{x:.1f},{yy:.1f}")
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polyline fill="none" stroke="var(--up)" stroke-width="2" points="{" ".join(coords)}"/></svg>'
            f'<p class="muted">Ganancia realizada acumulada: {acc:+.4f} ({len(ys)} ventas)</p>')


def render(snap, refresh):
    out = [f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<meta http-equiv=refresh content={refresh}><title>Grid bot</title><style>{CSS}</style></head><body>"
           f"<h1>Grid bot <span class=tag>{snap['mode']}</span> <span class=tag>{html.escape(snap['exchange'])}</span></h1>"]
    tot_r = sum(p["realized"] for p in snap["pairs"])
    tot_u = sum(p["unrealized"] for p in snap["pairs"])
    out.append(f"<div class=card><div class=m><div><small>Realizado</small><b>{signed(tot_r)}</b></div>"
               f"<div><small>No realizado</small><b>{signed(tot_u)}</b></div>"
               f"<div><small>Neto</small><b>{signed(tot_r + tot_u)}</b></div>"
               f"<div><small>Saldo</small><b style='font-size:13px'>{html.escape(snap['balance'])}</b></div></div>"
               f"{pnl_chart(snap['trades'])}</div>")
    state_es = {"running": "operando", "paused": "pausado", "finished": "detenido"}
    for p in snap["pairs"]:
        out.append(f"<div class=card><h2>{html.escape(p['symbol'])} <span class=tag>{state_es[p['state']]}</span></h2>"
                   f"<div class=m><div><small>Precio</small><b>{fmt(p['price'])}</b></div>"
                   f"<div><small>Rango</small><b style='font-size:13px'>{fmt(p['low'])} – {fmt(p['high'])}</b></div>"
                   f"<div><small>Ventas arriba / compras abajo</small><b>{p['sells']} / {p['buys']}</b></div>"
                   f"<div><small>En cartera</small><b>{fmt(p['held'], 5)}</b></div>"
                   f"<div><small>Realizado ({p['cycles']} ciclos)</small><b>{signed(p['realized'])}</b></div>"
                   f"<div><small>No realizado</small><b>{signed(p['unrealized'])}</b></div></div>"
                   "<table><tr><th>Nivel</th><th>Precio</th><th>Orden</th><th>Cantidad</th><th>Latente</th></tr>")
        shown = False
        for r in p["ladder"]:
            if not shown and p["price"] > r["price"]:
                out.append(f"<tr class=mark><td>precio</td><td>{fmt(p['price'])}</td><td colspan=3>◀ estás acá</td></tr>")
                shown = True
            if r["side"] == "sell":
                out.append(f"<tr><td>{r['level']}</td><td>{fmt(r['price'])}</td><td class=sell>venta</td>"
                           f"<td>{fmt(r['amount'], 5)}</td><td>{signed(r['latent'])}</td></tr>")
            elif r["side"] == "buy":
                out.append(f"<tr><td>{r['level']}</td><td>{fmt(r['price'])}</td><td class=buy>compra</td>"
                           f"<td>{fmt(r['amount'], 5)}</td><td class=dim>—</td></tr>")
            else:
                out.append(f"<tr class=dim><td>{r['level']}</td><td>{fmt(r['price'])}</td><td>·</td><td></td><td></td></tr>")
        if not shown:
            out.append(f"<tr class=mark><td>precio</td><td>{fmt(p['price'])}</td><td colspan=3>◀ estás acá</td></tr>")
        out.append("</table></div>")
    out.append(f"<p class=muted>Se actualiza cada {refresh} s.</p></body></html>")
    return "".join(out)


def start_web(cfg, snapshot):
    user, pw = os.getenv("WEB_USER", "admin"), os.getenv("WEB_PASS", "")
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    refresh = cfg.get("refresh_seconds", 30)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if not pw or self.headers.get("Authorization") != f"Basic {token}":
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="gridbot"')
                self.end_headers()
                self.wfile.write(b"Configura WEB_PASS en .env" if not pw else b"")
                return
            try:
                body = render(snapshot(), refresh).encode()
            except Exception as e:
                log.exception("web render")
                body = f"<pre>Error: {html.escape(str(e))}</pre>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer((cfg.get("host", "0.0.0.0"), cfg.get("port", 8080)), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Panel web en puerto %s", cfg.get("port", 8080))
