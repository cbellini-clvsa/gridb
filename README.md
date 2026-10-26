# gridbot

Bot de grid trading (spot, multi-par) con control por Telegram y panel web.

```
bot/   → corre 24/7 en el VPS (Python + ccxt). Publica su estado en Upstash Redis.
web/   → panel de solo lectura desplegado en Vercel. Lee el estado desde Upstash.
```

```
OKX ⇄ bot (VPS) ⇄ Telegram
          │
          ▼  SET cada 30 s
     Upstash Redis
          ▲  GET al abrir la página
     Vercel (web/)
```

## 1. Bot en el VPS
Ver `bot/README.md` para la configuración (OKX demo, grilla, stops, Telegram) y el despliegue con systemd.
Para la web, además completá en `bot/.env`:
```
UPSTASH_REDIS_REST_URL=...
UPSTASH_REDIS_REST_TOKEN=...
```

## 2. Panel en Vercel
1. Importar este repo en Vercel → **Root Directory: `web`** (Framework preset: Other).
2. En el proyecto: Storage → Marketplace → **Upstash Redis** → crear base gratis. Vercel inyecta
   `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` (o `KV_REST_API_*`) en el proyecto; copiá esos mismos valores al `.env` del bot.
3. Settings → Environment Variables → agregar `PANEL_PASSWORD` (la clave con la que entrás al panel).
4. Deploy. Cada `git push` a `main` redespliega.

Sin `PANEL_PASSWORD` el panel no responde. La página guarda la clave en tu navegador para no pedirla cada vez.

## Seguridad
- `.env` está en `.gitignore`: las claves nunca van al repo. Si alguna vez se te escapa, rotala en OKX/Telegram/Upstash.
- Claves de OKX: solo permiso de trading, sin retiros.
- El panel es de solo lectura; los comandos (`/pausar`, `/reset`, `/stop`...) van por Telegram y solo responde a tu chat.
