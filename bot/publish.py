"""Publica el snapshot del bot en Upstash Redis (REST) para que lo lea la web en Vercel.
Un solo comando SET por ciclo; con un ciclo de 30 s son ~86 000 comandos/mes (límite gratis: 500 000)."""
import json
import logging
import os
import time

import requests

log = logging.getLogger("publish")


class Publisher:
    def __init__(self, key="gridbot:snapshot", min_interval=30):
        self.url = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
        self.token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
        self.enabled = bool(self.url and self.token)
        self.key = key
        self.min_interval = min_interval
        self.last = 0.0
        if not self.enabled:
            log.info("Publicación a Upstash desactivada (faltan UPSTASH_REDIS_REST_URL/TOKEN)")

    def push(self, snapshot):
        if not self.enabled or time.time() - self.last < self.min_interval:
            return
        self.last = time.time()
        body = json.dumps({**snapshot, "updated": time.time()}, ensure_ascii=False)
        try:
            r = requests.post(f"{self.url}/set/{self.key}", data=body.encode(),
                              headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
            if r.status_code != 200:
                log.warning("Upstash %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("Upstash: %s", e)
