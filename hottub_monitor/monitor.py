#!/usr/bin/env python3
"""
Kronk hot tub monitor.

Connects to the Gecko in.touch 2 module via geckolib to read spa pack status.
The spa pack transmitter (inside the tub, on the problem breaker) goes dark
when the breaker trips — geckolib connection failure is how we detect it.
The Wi-Fi module (receiver/gateway) at SPA_HOST is on a separate circuit and
stays up regardless; it merely relays communication to the spa pack.
"""
import asyncio
import json
import logging
import signal
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geckolib import GeckoAsyncSpaMan, GeckoSpaEvent

STATUS_FILE       = Path("/home/drew/git-repos/drawsmcgraw/kronk/data/hottub/status.json")
SPA_IDENTIFIER    = "SPA68:27:19:be:cd:08"
SPA_HOST          = "192.168.1.87"
POLL_INTERVAL     = 60      # seconds between checks
FAILURE_THRESHOLD = 2       # consecutive failures before marking offline
CONNECT_TIMEOUT   = 45.0    # seconds to wait for geckolib facade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_state: dict = {
    "online": None,
    "spa_name": None,
    "spa_ip": SPA_HOST,
    "temperature_f": None,
    "set_temperature_f": None,
    "last_seen": None,
    "last_check": None,
    "offline_since": None,
    "consecutive_failures": 0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_f(temp: float, unit: str) -> float:
    if unit == "°C":
        return round(temp * 9 / 5 + 32, 1)
    return round(temp, 1)


def _write_status() -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(_state, indent=2, default=str))


def _on_offline() -> None:
    if _state["online"] is not False:
        _state["offline_since"] = _now()
        logger.warning(
            "HOT TUB OFFLINE — breaker may have tripped. Offline since %s",
            _state["offline_since"],
        )
        # TODO: add ntfy.sh alert here once subscribed
        # import subprocess
        # subprocess.run(["curl", "-s", "-d",
        #     "Hot tub offline — breaker may have tripped",
        #     "https://ntfy.sh/YOUR_TOPIC"], check=False)
    _state["online"] = False
    _state["temperature_f"] = None
    _state["set_temperature_f"] = None
    _write_status()


_TERMINAL_EVENTS = frozenset({
    GeckoSpaEvent.SPA_NOT_FOUND,
    GeckoSpaEvent.CONNECTION_PROTOCOL_RETRY_TIME_EXCEEDED,
    GeckoSpaEvent.ERROR_PROTOCOL_RETRY_TIME_EXCEEDED,
    GeckoSpaEvent.ERROR_TOO_MANY_RF_ERRORS,
    GeckoSpaEvent.CONNECTION_CANNOT_FIND_SPA_PACK,
})


class _SpaMan(GeckoAsyncSpaMan):
    async def handle_event(self, event: GeckoSpaEvent, **_kwargs: Any) -> None:
        if event in _TERMINAL_EVENTS:
            # Unblock wait_for_facade so we don't wait the full CONNECT_TIMEOUT
            self._facade_state_known.set()


async def _poll_once() -> None:
    _state["last_check"] = _now()
    client_id = str(uuid.uuid4())

    try:
        async with _SpaMan(
            client_id,
            spa_identifier=SPA_IDENTIFIER,
            spa_address=SPA_HOST,
            spa_name="Hot Tub",
        ) as spaman:
            try:
                connected = await asyncio.wait_for(
                    spaman.wait_for_facade(), timeout=CONNECT_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for spa pack facade")
                connected = False

            if connected and spaman.facade is not None:
                facade = spaman.facade
                heater = facade.water_heater

                unit = heater.temperature_unit
                curr_f = _to_f(heater.current_temperature, unit)
                set_f = _to_f(heater.target_temperature, unit)

                try:
                    spa_name = spaman.spa_name
                except AssertionError:
                    spa_name = SPA_IDENTIFIER

                prev = _state["online"]
                _state.update({
                    "online": True,
                    "spa_name": spa_name,
                    "temperature_f": curr_f,
                    "set_temperature_f": set_f,
                    "last_seen": _now(),
                    "offline_since": None,
                    "consecutive_failures": 0,
                })
                _write_status()
                if prev is not True:
                    logger.info(
                        "Hot tub ONLINE — %.1f°F (set: %.1f°F)", curr_f, set_f
                    )
                else:
                    logger.debug(
                        "Hot tub online — %.1f°F (set: %.1f°F)", curr_f, set_f
                    )
                return

    except Exception as e:
        logger.warning("geckolib error: %s", e)

    _state["consecutive_failures"] = _state.get("consecutive_failures", 0) + 1
    logger.warning(
        "No spa pack response (failure %d/%d)",
        _state["consecutive_failures"], FAILURE_THRESHOLD,
    )
    if _state["consecutive_failures"] >= FAILURE_THRESHOLD:
        _on_offline()
    else:
        _write_status()


async def main() -> None:
    logger.info(
        "Kronk hot tub monitor starting — polling %s every %ds, alert after %d failures",
        SPA_HOST, POLL_INTERVAL, FAILURE_THRESHOLD,
    )

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    while not stop.is_set():
        await _poll_once()
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass

    logger.info("Monitor stopped.")


if __name__ == "__main__":
    asyncio.run(main())
