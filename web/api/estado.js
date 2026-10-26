// GET /api/estado  → snapshot del bot leído de Upstash. Requiere header x-panel-key.
export default async function handler(req, res) {
  const key = process.env.PANEL_PASSWORD;
  if (!key) return res.status(500).json({ error: "Falta PANEL_PASSWORD en Vercel" });
  if (req.headers["x-panel-key"] !== key) return res.status(401).json({ error: "no autorizado" });

  const url = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN;
  if (!url || !token) return res.status(500).json({ error: "Faltan credenciales de Upstash" });

  const r = await fetch(`${url}/get/gridbot:snapshot`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  const data = await r.json();
  if (!data.result) return res.status(404).json({ error: "El bot todavía no publicó nada" });

  res.setHeader("Cache-Control", "no-store");
  res.status(200).send(data.result);
}
