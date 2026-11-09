"""Regression checks for profile battery reporting; no external services needed.
Run: PYTHONPATH=. python tests/check_profile_battery.py
Requires the backend dependencies and fakeredis.
"""
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "local-profile-battery-check-not-production")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fakeredis.aioredis
from app.api.v1.endpoints import auth


class ProfileBatteryChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = fakeredis.aioredis.FakeRedis()
        self.redis_patch = patch("app.core.redis.get_redis_client", return_value=self.redis)
        self.redis_patch.start()
        self.person = SimpleNamespace(id=uuid.uuid4(), is_active=True, privacy={}, sleep_enabled=False, sleep_start=1380, sleep_end=420, sleep_timezone="UTC")
        self.viewer = SimpleNamespace(id=uuid.uuid4(), is_active=True)
        self.db = SimpleNamespace(get=AsyncMock(return_value=None),
                                  execute=AsyncMock(), commit=AsyncMock())
        self.key = f"battery:{self.person.id}"

    async def asyncTearDown(self):
        self.redis_patch.stop()
        await self.redis.aclose()

    async def profile(self, target_privacy=None, viewer_privacy=None):
        from app.models.models import Comrade
        async def lookup(model,identifier):
            if model is Comrade:return self.person if identifier==self.person.id else self.viewer
            return target_privacy if identifier==self.person.id else viewer_privacy
        self.db.get.side_effect = lookup
        return await auth.get_presence(self.person.id, self.viewer, self.db)

    async def store_battery(self, level=54, age=0):
        await self.redis.set(self.key, json.dumps({
            "level": level, "charging": True,
            "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
        }), ex=86400)

    async def test_default_sharing_survives_going_offline(self):
        session = uuid.uuid4()
        await auth.heartbeat_presence(auth.PresenceHeartbeat(
            session_id=session, active=True, battery_level=54, battery_charging=True),
            self.person, self.db)
        self.assertGreater(await self.redis.ttl(self.key), 90)
        live = await self.profile()
        self.assertEqual(live["battery_level"], 54)
        self.assertTrue(live["online"])
        self.assertFalse(live["battery_stale"])
        self.db.get.side_effect = None
        self.db.get.return_value = None
        await auth.heartbeat_presence(auth.PresenceHeartbeat(session_id=session, active=False),
                                      self.person, self.db)
        offline = await self.profile()
        self.assertFalse(offline["online"])
        self.assertEqual(offline["battery_level"], 54)
        self.assertTrue(offline["battery_stale"])
        self.assertIsNotNone(offline["battery_updated_at"])

    async def test_hiding_battery_deletes_last_report(self):
        await self.store_battery()
        await auth.set_battery_privacy(auth.BatteryPrivacy(show_battery=False),
                                      self.person, self.db)
        self.assertIsNone(await self.redis.get(self.key))
        hidden = await self.profile(SimpleNamespace(show_battery=False, show_last_seen=True, last_seen=None))
        self.assertTrue(hidden["battery_hidden"])
        self.assertIsNone(hidden["battery_level"])

    async def test_hidden_presence_does_not_override_battery_setting(self):
        await self.store_battery()
        report = await self.profile(SimpleNamespace(show_battery=True, show_last_seen=False))
        self.assertTrue(report["hidden"])
        self.assertIsNone(report["online"])
        self.assertIsNone(report["last_seen"])
        self.assertEqual(report["battery_level"], 54)
        self.assertTrue(report["battery_stale"])

    async def test_no_report_returns_no_invented_percentage(self):
        report = await self.profile()
        self.assertIsNone(report["battery_level"])
        self.assertFalse(report["battery_hidden"])

    async def test_old_report_is_not_presented_as_live(self):
        await self.store_battery(age=180)
        await self.redis.zadd(f"presence:{self.person.id}",
                             {"session": datetime.now(timezone.utc).timestamp() + 45})
        report = await self.profile()
        self.assertTrue(report["online"])
        self.assertTrue(report["battery_stale"])

    async def test_invalid_reports_are_ignored(self):
        for raw in ["not-json", "[]", '{"level":101}', '{"level":true}', '{"level":"54"}']:
            with self.subTest(raw=raw):
                await self.redis.set(self.key, raw)
                report = await self.profile()
                self.assertIsNone(report["battery_level"])

    async def test_disabled_sharing_does_not_store_heartbeat_battery(self):
        self.db.get.return_value = SimpleNamespace(show_battery=False)
        await auth.heartbeat_presence(auth.PresenceHeartbeat(
            session_id=uuid.uuid4(), active=True, battery_level=54),
            self.person, self.db)
        self.assertIsNone(await self.redis.get(self.key))

    async def test_zero_percent_is_valid(self):
        await self.store_battery(level=0)
        report = await self.profile()
        self.assertEqual(report["battery_level"], 0)


if __name__ == "__main__":
    unittest.main()
