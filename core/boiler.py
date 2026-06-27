"""
core/boiler.py — Controllo LOCALE del relè caldaia (Sonoff MINI-D, firmware eWeLink
in modalità LAN). Niente cloud per operare.

  - COMANDO: POST cifrato AES-128-CBC a http://<ip>:8081/zeroconf/switches
    (endpoint plurale; il singolare /zeroconf/switch e /zeroconf/info danno 404 su
    questo firmware). payload = {"switches":[{"switch":"on"|"off","outlet":0}]}.
  - STATO: letto PASSIVAMENTE dall'advertisement mDNS (_ewelink._tcp): il record
    TXT contiene lo stato cifrato (data1+data2+..) che decifriamo con la devicekey.
    Questo riflette anche i comandi del CRONOTERMOSTATO (che agisce in parallelo
    via S1/S2 sullo stesso relè).

Chiave AES = MD5(devicekey). La devicekey arriva dal config (gitignored), non dal
cloud: una volta estratta, il controllo è interamente in LAN.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from typing import Optional

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger("climate.boiler")


class BoilerController:
    """Controlla e monitora il relè caldaia Sonoff in LAN (cifrato)."""

    def __init__(self, deviceid: str, devicekey: str, ip: Optional[str] = None,
                 port: int = 8081, enabled: bool = True) -> None:
        self._devid = deviceid
        self._key = hashlib.md5(devicekey.encode()).digest()
        self._ip = ip                      # se None: risolto da mDNS
        self._port = port
        self._enabled = enabled
        self._session: Optional[aiohttp.ClientSession] = None
        self._azc = None                   # AsyncZeroconf
        self._browser = None
        self._state: Optional[bool] = None   # True=ON, False=OFF, None=ignoto
        self._state_ts: float = 0.0

    # -- ciclo di vita ------------------------------------------------------
    async def start(self) -> None:
        if not self._enabled:
            logger.info("Boiler controller disabilitato (config).")
            return
        self._session = aiohttp.ClientSession()
        try:
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
            self._azc = AsyncZeroconf()
            self._browser = AsyncServiceBrowser(
                self._azc.zeroconf, "_ewelink._tcp.local.",
                handlers=[self._on_service])
            logger.info("Boiler controller avviato (mDNS + LAN, device %s, ip %s).",
                        self._devid, self._ip or "auto")
        except Exception as exc:  # noqa: BLE001 - lo stato degrada, il comando resta
            logger.warning("Boiler: browser mDNS non avviato (%s). Stato non letto, "
                           "comando comunque attivo.", exc)

    async def stop(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.async_cancel()
            if self._azc is not None:
                await self._azc.async_close()
        except Exception:  # noqa: BLE001
            pass
        if self._session is not None:
            await self._session.close()
            self._session = None

    # -- mDNS: lettura passiva dello stato ----------------------------------
    def _on_service(self, zeroconf, service_type, name, state_change) -> None:
        if self._devid not in name:
            return
        import asyncio
        asyncio.ensure_future(self._refresh_from_mdns(zeroconf, service_type, name))

    async def _refresh_from_mdns(self, zeroconf, service_type, name) -> None:
        try:
            from zeroconf.asyncio import AsyncServiceInfo
            info = AsyncServiceInfo(service_type, name)
            if not await info.async_request(zeroconf, 3000):
                return
            # IP utile come fallback per i comandi se non in config.
            if self._ip is None:
                addrs = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
                if addrs:
                    self._ip = addrs[0]
            state = self._decrypt_props(info.properties)
            sw = (state.get("switches") or [{}])[0].get("switch")
            if sw in ("on", "off"):
                self._state = (sw == "on")
                self._state_ts = time.time()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Boiler: lettura mDNS fallita: %s", exc)

    def _decrypt_props(self, props: dict) -> dict:
        """Decifra lo stato dal TXT mDNS (data1+data2+.. cifrati con la devicekey)."""
        def s(v):
            return v.decode() if isinstance(v, (bytes, bytearray)) else v
        iv_b64 = None
        datas: dict[int, str] = {}
        for k, v in props.items():
            ks = s(k)
            if ks == "iv":
                iv_b64 = s(v)
            elif ks and ks.startswith("data") and ks[4:].isdigit():
                datas[int(ks[4:])] = s(v)
        ct = base64.b64decode("".join(datas[i] for i in sorted(datas)))
        d = Cipher(algorithms.AES(self._key), modes.CBC(base64.b64decode(iv_b64))).decryptor()
        pt = d.update(ct) + d.finalize()
        return json.loads(pt[:-pt[-1]])  # PKCS7 unpad

    # -- comando (cifrato) --------------------------------------------------
    def _encrypt(self, payload: dict) -> tuple[str, str]:
        iv = os.urandom(16)
        data = json.dumps(payload).encode()
        pad = 16 - (len(data) % 16)
        data += bytes([pad]) * pad
        e = Cipher(algorithms.AES(self._key), modes.CBC(iv)).encryptor()
        ct = e.update(data) + e.finalize()
        return base64.b64encode(iv).decode(), base64.b64encode(ct).decode()

    async def set(self, on: bool) -> bool:
        """Accende/spegne il relè caldaia. Ritorna True se il device ha accettato."""
        if not self._enabled or self._session is None:
            raise RuntimeError("Boiler controller non attivo")
        if self._ip is None:
            raise RuntimeError("Boiler: IP del device non ancora noto (mDNS)")
        iv, data = self._encrypt(
            {"switches": [{"switch": "on" if on else "off", "outlet": 0}]})
        body = {"sequence": str(int(time.time() * 1000)), "deviceid": self._devid,
                "selfApikey": "123", "iv": iv, "encrypt": True, "data": data}
        url = f"http://{self._ip}:{self._port}/zeroconf/switches"
        async with self._session.post(
                url, json=body, timeout=aiohttp.ClientTimeout(total=8)) as r:
            j = await r.json()
        ok = (j.get("error") == 0)
        if ok:
            self._state, self._state_ts = on, time.time()
            logger.info("Caldaia -> %s", "ON" if on else "OFF")
        else:
            logger.error("Comando caldaia fallito: %s", j)
        return ok

    def get_state(self) -> dict:
        """Stato corrente noto (da mDNS o ultimo comando)."""
        return {
            "enabled": self._enabled,
            "on": self._state,
            "age_seconds": int(time.time() - self._state_ts) if self._state_ts else None,
            "ip": self._ip,
        }
