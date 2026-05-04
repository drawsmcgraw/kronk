#!/usr/bin/env python3
"""
Kronk hot tub monitor — single-attempt connection with long retry delay.

Each connection attempt opens a new geckolib session, waits for the facade,
then holds the connection until it drops. On any failure or disconnect the
session is closed completely (killing geckolib's internal retry loop), and
we wait a long delay before trying again. This prevents hammering the
Gecko in.touch 2 module, which has a small connection table and will crash
(red/blue LED) if bombarded with rapid reconnect attempts.
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

STATUS_FILE          = Path("/home/drew/git-repos/drawsmcgraw/kronk/data/hottub/status.json")
SPA_IDENTIFIER       = "SPA68:27:19:be:cd:08"
SPA_HOST             = "192.168.1.87"
CONNECT_TIMEOUT      = 60      # seconds to wait for initial facade
POLL_INTERVAL        = 300     # seconds between status file updates while connected
RECONNECT_DELAY_SHORT = 60     # seconds to wait after a clean disconnect
RECONNECT_DELAY_LONG  = 300    # seconds to wait after a connect failure (module settles)

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


def _on_online(curr_f: float, set_f: float) -> None:
    logger.info("HOT TUB BACK ONLINE — %.1f°F (set: %.1f°F)", curr_f, set_f)
    # TODO: add ntfy.sh alert here once subscribed
    # import subprocess
    # subprocess.run(["curl", "-s", "-d",
    #     f"Hot tub back online — {curr_f:.1f}°F",
    #     "https://ntfy.sh/YOUR_TOPIC"], check=False)


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


def _read_temps(spaman: "GeckoAsyncSpaMan") -> tuple[float, float]:
    heater = spaman.facade.water_heater
    unit = heater.temperature_unit
    return _to_f(heater.current_temperature, unit), _to_f(heater.target_temperature, unit)


_TERMINAL_EVENTS = frozenset({
    GeckoSpaEvent.SPA_NOT_FOUND,
    GeckoSpaEvent.CONNECTION_PROTOCOL_RETRY_TIME_EXCEEDED,
    GeckoSpaEvent.ERROR_PROTOCOL_RETRY_TIME_EXCEEDED,
    GeckoSpaEvent.ERROR_TOO_MANY_RF_ERRORS,
    GeckoSpaEvent.CONNECTION_CANNOT_FIND_SPA_PACK,
})


class _SpaMan(GeckoAsyncSpaMan):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._facade_ready = asyncio.Event()
        self._facade_gone = asyncio.Event()

    async def handle_event(self, event: GeckoSpaEvent, **_kwargs: Any) -> None:
        if event in _TERMINAL_EVENTS:
            self._facade_state_known.set()

        if event == GeckoSpaEvent.CLIENT_FACADE_IS_READY:
            self._facade_gone.clear()
            self._facade_ready.set()

        elif event == GeckoSpaEvent.CLIENT_FACADE_TEARDOWN:
            self._facade_ready.clear()
            self._facade_gone.set()


async def _run_once(stop: asyncio.Event) -> bool:
    """
    One connection attempt. Opens geckolib, waits for facade, holds the
    connection until it drops or stop fires. Returns True if we connected
    (retry sooner), False if we never connected (retry later).

    Exiting this function closes the async with block, which cancels all
    geckolib tasks including the sequence pump retry loop — leaving the
    module alone during the inter-attempt delay.
    """
    async with _SpaMan(
        str(uuid.uuid4()),
        spa_identifier=SPA_IDENTIFIER,
        spa_address=SPA_HOST,
        spa_name="Hot Tub",
    ) as spaman:
        _state["last_check"] = _now()
        logger.info("Attempting connection to spa pack at %s...", SPA_HOST)

        try:
            await asyncio.wait_for(spaman._facade_ready.wait(), timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "No response from spa pack after %ds — backing off %ds before retry",
                CONNECT_TIMEOUT, RECONNECT_DELAY_LONG,
            )
            _on_offline()
            return False  # never connected → use long delay

        if stop.is_set():
            return False

        # ── Connected ─────────────────────────────────────────────────────────
        curr_f, set_f = _read_temps(spaman)
        try:
            spa_name = spaman.spa_name
        except AssertionError:
            spa_name = SPA_IDENTIFIER

        prev_online = _state["online"]
        _state.update({
            "online": True,
            "spa_name": spa_name,
            "temperature_f": curr_f,
            "set_temperature_f": set_f,
            "last_seen": _now(),
            "last_check": _now(),
            "offline_since": None,
            "consecutive_failures": 0,
        })
        _write_status()

        if prev_online is not True:
            _on_online(curr_f, set_f)
        else:
            logger.info("Reconnected — %.1f°F (set: %.1f°F)", curr_f, set_f)

        # ── Stay connected: poll every POLL_INTERVAL ───────────────────────────
        while not stop.is_set() and not spaman._facade_gone.is_set():
            disconnect_task = asyncio.ensure_future(spaman._facade_gone.wait())
            stop_task = asyncio.ensure_future(stop.wait())
            sleep_task = asyncio.ensure_future(asyncio.sleep(POLL_INTERVAL))

            done, pending = await asyncio.wait(
                {disconnect_task, stop_task, sleep_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if stop.is_set() or spaman._facade_gone.is_set():
                break

            # Periodic temp update
            if spaman.facade is not None:
                curr_f, set_f = _read_temps(spaman)
                _state.update({
                    "temperature_f": curr_f,
                    "set_temperature_f": set_f,
                    "last_seen": _now(),
                    "last_check": _now(),
                })
                _write_status()
                logger.debug("Hot tub %.1f°F (set: %.1f°F)", curr_f, set_f)

        # ── Disconnected ───────────────────────────────────────────────────────
        if spaman._facade_gone.is_set() and not stop.is_set():
            _on_offline()

    # Exiting async with cancels all geckolib tasks + sequence pump retry loop
    return True  # was connected → use short delay


async def main() -> None:
    logger.info(
        "Kronk hot tub monitor starting — %s, status updates every %ds, "
        "retry after connect failure: %ds, retry after disconnect: %ds",
        SPA_HOST, POLL_INTERVAL, RECONNECT_DELAY_LONG, RECONNECT_DELAY_SHORT,
    )

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    while not stop.is_set():
        was_connected = await _run_once(stop)
        if stop.is_set():
            break
        delay = RECONNECT_DELAY_SHORT if was_connected else RECONNECT_DELAY_LONG
        logger.info("Next connection attempt in %ds", delay)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    logger.info("Monitor stopped.")


if __name__ == "__main__":
    asyncio.run(main())
