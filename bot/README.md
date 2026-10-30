# Grid Bot (ccxt) — multi-par, rango automático, paper/live

Bot de grid trading para spot. Compra en cada nivel por debajo del precio y vende un nivel más arriba.
El rango se calcula solo con el mínimo/máximo de los últimos 7 días (velas 1h) + 2 % de margen.
Consume ~80 MB de RAM: anda sobrado en un VPS de 1 GB.

## Archivos
- `main.py` – arranque, bucle principal y comandos de Telegram
- `grid.py` – lógica de la grilla
- `broker.py` – `PaperBroker` (simulado) y `LiveBroker` (órdenes reales)
- `db.py` – SQLite (grillas, órdenes, operaciones, saldo paper)
- `tg.py` – Telegram sin librerías pesadas
- `web.py` – panel web de solo lectura (escalera de órdenes, PnL realizado y no realizado)
- `config.json` – parámetros · `.env` – claves · `gridbot.service` – systemd

## Configuración rápida
1. `cp .env.example .env` y completá Telegram (opcional) y claves del exchange (sólo para live).
2. Editá `config.json`:
   - `exchange`: id de ccxt (`binance`, `bybit`, `okx`, `kucoin`, `gateio`, `mexc`, `bitget`, `kraken`...).
     Lista completa: `python -c "import ccxt; print(ccxt.exchanges)"`
   - `pairs`: los pares a operar.
   - `grid.levels`: cantidad de niveles. `grid.quote_per_level`: USDT por nivel.
     **Capital necesario ≈ levels × quote_per_level por par** (en el peor caso, todas las compras ejecutadas).
   - `grid.auto_reset`: `"up"` recalcula sólo si el precio sale por arriba (sin riesgo),
     `"both"` también por abajo (sigue comprando en caída: más exposición), `"off"` nunca (avisa por Telegram).
   - `ranges`: rango manual por par, ej. `{"BTC/USDT": [80000, 120000]}`. Si un par no está, usa rango automático.
   - `exit`: salida limpia. Cuando el par queda **plano** (sin ventas pendientes = inventario cero) y cumplió
     `min_cycles` ciclos o `min_profit_pct` % de ganancia sobre el capital de la grilla, cancela las compras y se detiene
     (`restart: true` arranca una grilla nueva en vez de detenerse). `/reanudar PAR` lo vuelve a activar.
   - `mode`: `"paper"` primero. Pasá a `"live"` sólo cuando hayas visto varios días de operaciones simuladas.
   - `sandbox: true` usa el testnet del exchange (Binance/Bybit lo tienen) con claves de testnet.

## OKX demo (configuración actual de `config.json`)
1. En OKX, cambiá a **Demo trading** y, dentro del modo demo, entrá a API management → *Create Demo Trading API Key*
   (permiso: Trade). Guardá key, secret y passphrase.
2. En `.env`: `EXCHANGE_API_KEY`, `EXCHANGE_SECRET` y `EXCHANGE_PASSWORD` (la passphrase).
3. `config.json` ya trae `"exchange": "okx"`, `"mode": "live"`, `"sandbox": true`. El `sandbox` manda el header
   `x-simulated-trading: 1` en cada pedido, así que las órdenes van a la cuenta demo. Para pasar a real: `sandbox: false`
   y claves creadas desde la cuenta real.

## Stop
`stop` en `config.json` tiene dos reglas, cualquiera dispara:
- `max_loss_pct`: si realizado + no realizado de la grilla cae a −X% del capital de la grilla (levels × quote_per_level).
- `price_below_low_pct`: si el precio perfora el mínimo del rango en X%. Nunca pongas el stop dentro del rango.

`action: "liquidate"` cancela todo, vende el inventario a mercado y detiene el par; `"pause"` sólo cancela compras y deja
las ventas pendientes (sin realizar pérdida). `/stop PAR` lo dispara a mano. `/reanudar PAR` rearma una grilla nueva.

## Correr en local
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py config.json
```

## Desplegar en VPS (Ubuntu, 1 GB) — ej. Oracle Cloud Free Tier
```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/cbellini-clvsa/gridb.git ~/gridbot && cd ~/gridbot/bot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env            # completar claves
sudo cp gridbot.service /etc/systemd/system/ # revisá User= y rutas si tu usuario no es "ubuntu"
sudo systemctl daemon-reload
sudo systemctl enable --now gridbot
journalctl -u gridbot -f                     # ver logs en vivo
```
Si el VPS tiene poca RAM, agregá 1 GB de swap: `sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`.

## Comandos de Telegram
`/status` `/grilla PAR` `/balance` `/pnl` `/ops` · `/pausar [PAR]` `/reanudar [PAR]` · `/reset PAR` `/stop PAR`

Pausar deja de colocar **compras** nuevas, pero las ventas de lo ya comprado se siguen colocando para no dejar inventario colgado.
`/reset` cancela las compras abiertas y recalcula la grilla; las ventas pendientes se mantienen.

## Panel web (local en el VPS, opcional)
Si usás el panel en Vercel (ver README de la raíz), podés dejar `web.enabled: false` y no abrir ningún puerto.
Con `web.enabled: true` en `config.json`, el bot sirve una página en el puerto 8080 (configurable) con:
métricas totales, curva de ganancia acumulada y, por cada par, la escalera completa: cada nivel con su precio,
si tiene venta o compra pendiente, cantidad, PnL latente de cada venta, y una fila resaltada donde está el precio.
Se protege con usuario y contraseña (`WEB_USER` / `WEB_PASS` en `.env`); sin contraseña no sirve nada.

Para verla desde el celular hay que abrir el puerto en el VPS:
- Oracle Cloud: en la VCN → Security List → agregar regla de ingreso TCP 8080, y en la VM:
  `sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT && sudo netfilter-persistent save`
- Otros VPS: `sudo ufw allow 8080`

Luego entrás a `http://IP_DEL_VPS:8080`. Es HTTP sin cifrar: usá una contraseña que no uses en otro lado,
o mejor accedé por túnel SSH (`ssh -L 8080:localhost:8080 ubuntu@IP` y abrir `http://localhost:8080`) sin abrir el puerto.

## Seguridad y riesgos
- Claves API **sólo con permiso de trading spot, nunca retiros**, y restringidas a la IP del VPS si el exchange lo permite.
- El bot sólo responde al `TELEGRAM_CHAT_ID` configurado.
- Grid gana en mercados laterales. En una caída fuerte termina con todo el capital en el activo (en pérdida latente);
  en una subida fuerte se queda en USDT y deja de ganar hasta el reset. Dimensioná `quote_per_level` para que puedas tolerar el peor caso.
- Esto no es asesoramiento financiero. Probá en paper varios días antes de operar con dinero real.
