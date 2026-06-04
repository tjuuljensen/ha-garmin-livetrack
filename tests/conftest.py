import asyncio
import tempfile

import pytest


class _FakeBus:
    def async_fire(self, event_type, event_data=None):
        return None

    def async_listen(self, event_type, listener):
        def _unsub():
            return None

        return _unsub


class _FakeServices:
    def __init__(self):
        self._registry = {}
        self.calls = []

    def has_service(self, domain, service):
        return (domain, service) in self._registry

    def async_register(self, domain, service, handler, **kwargs):
        self._registry[(domain, service)] = {
            "handler": handler,
            "kwargs": kwargs,
        }

    async def async_call(self, domain, service, payload, blocking=False):
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "payload": payload,
                "blocking": blocking,
            }
        )
        return None


class _FakeConfigEntries:
    async def async_unload_platforms(self, entry, platforms):
        return True

    async def async_forward_entry_setups(self, entry, platforms):
        return True


class _FakeHass:
    def __init__(self, loop):
        self.loop = loop
        self.bus = _FakeBus()
        self.services = _FakeServices()
        self.config_entries = _FakeConfigEntries()
        self.states = type("States", (), {"async_entity_ids": lambda self: []})()
        self.data = {}
        self.is_running = True
        self.config = type("Config", (), {"config_dir": tempfile.gettempdir()})()

    def async_create_task(self, coro):
        return self.loop.create_task(coro)

    def verify_event_loop_thread(self, _what):
        return None


@pytest.fixture
async def hass():
    return _FakeHass(asyncio.get_running_loop())
