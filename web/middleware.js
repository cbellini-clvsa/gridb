// Protege TODO el sitio (página y /api) con usuario y contraseña (HTTP Basic).
// Credenciales: variables de entorno PANEL_USER y PANEL_PASSWORD en Vercel.
import { next } from "@vercel/edge";

export const config = { matcher: "/(.*)" };

export default function middleware(request) {
  const user = process.env.PANEL_USER;
  const pass = process.env.PANEL_PASSWORD;
  if (!user || !pass) {
    return new Response("Faltan PANEL_USER / PANEL_PASSWORD en las variables de Vercel", { status: 500 });
  }
  const header = request.headers.get("authorization") || "";
  const expected = "Basic " + btoa(`${user}:${pass}`);
  if (header.length === expected.length && safeEqual(header, expected)) {
    return next();
  }
  return new Response("Autenticación requerida", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="gridbot", charset="UTF-8"', "Cache-Control": "no-store" },
  });
}

// comparación en tiempo constante para no filtrar información por tiempos de respuesta
function safeEqual(a, b) {
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
