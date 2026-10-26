"""Telegram mínimo con long-polling en un hilo. Sólo usa `requests`."""
import logging
import threading
import requests

log = logging.getLogger("tg")


class Telegram:
    def __init__(self, token, chat_id, handler):
        self.enabled = bool(token and chat_id)
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.handler = handler  # fn(texto) -> respuesta
        self.offset = 0

    def send(self, text, pre=False):
        if not self.enabled:
            log.info("[TG] %s", text)
            return
        payload = {"chat_id": self.chat_id, "text": text[:4000]}
        if pre:
            esc = text[:3900].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            payload = {"chat_id": self.chat_id, "text": f"<pre>{esc}</pre>", "parse_mode": "HTML"}
        try:
            requests.post(f"{self.base}/sendMessage", timeout=10, json=payload)
        except Exception as e:
            log.warning("Telegram send: %s", e)

    def start(self):
        if not self.enabled:
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                r = requests.get(f"{self.base}/getUpdates", timeout=40,
                                 params={"offset": self.offset, "timeout": 30}).json()
                for u in r.get("result", []):
                    self.offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue  # ignorar a cualquiera que no sea el dueño
                    text = msg.get("text", "")
                    if text.startswith("/"):
                        reply = self.handler(text)
                        if isinstance(reply, tuple):
                            self.send(reply[0], pre=reply[1])
                        else:
                            self.send(reply)
            except Exception as e:
                log.warning("Telegram poll: %s", e)
