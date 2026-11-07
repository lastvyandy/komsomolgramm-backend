"""Isolated HTTP checks for username normalization and duplicate registration.
Run with backend dependencies plus aiosqlite and httpx:
    PYTHONPATH=. python tests/check_username_auth.py
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "local-username-test-not-production")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api.v1.endpoints import auth
from app.db.session import get_async_db
from app.models.models import Base, Comrade


class UsernameAuthChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async def database():
            async with self.sessions() as session:
                yield session
        self.app = FastAPI()
        self.app.include_router(auth.router, prefix="/auth")
        self.app.dependency_overrides[get_async_db] = database
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.hash_patch = patch.object(auth, "get_password_hash", side_effect=lambda value: "hash:" + value)
        self.verify_patch = patch.object(auth, "verify_password", side_effect=lambda value, hashed: hashed == "hash:" + value)
        self.hash_patch.start()
        self.verify_patch.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        self.hash_patch.stop()
        self.verify_patch.stop()

    async def register(self, username):
        return await self.client.post("/auth/register", json={
            "username": username, "callsign": "Comrade", "password": "PassWORD123",
        })

    async def test_register_stores_lowercase_without_at_prefix(self):
        response = await self.register(" @Mi_Usuario ")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["username"], "mi_usuario")

    async def test_duplicate_different_case_returns_conflict(self):
        self.assertEqual((await self.register("mi_usuario")).status_code, 201)
        duplicate = await self.register("MI_USUARIO")
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    async def test_login_accepts_uppercase_and_at_prefix(self):
        await self.register("mi_usuario")
        response = await self.client.post("/auth/login", data={
            "username": " @MI_USUARIO ", "password": "PassWORD123",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["comrade"]["username"], "mi_usuario")

    async def test_password_is_not_lowercased(self):
        await self.register("mi_usuario")
        response = await self.client.post("/auth/login", data={
            "username": "MI_USUARIO", "password": "password123",
        })
        self.assertEqual(response.status_code, 401)

    async def test_invalid_embedded_at_and_short_name_are_rejected(self):
        for name in ["bad@name", "@ab"]:
            with self.subTest(name=name):
                self.assertEqual((await self.register(name)).status_code, 422)

    async def test_legacy_uppercase_accounts_can_still_login(self):
        async with self.sessions() as session:
            session.add(Comrade(username="LegacyUser", callsign="Legacy",
                                hashed_password="hash:PassWORD123", is_active=True))
            await session.commit()
        response = await self.client.post("/auth/login", data={
            "username": "legacyuser", "password": "PassWORD123",
        })
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
