import asyncpg
from typing import List, Dict, Optional


class Database:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            self.db_url,
            ssl='require',
            min_size=1,
            max_size=10
        )

    async def init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    referrer_id BIGINT,
                    is_bot BOOLEAN DEFAULT FALSE,
                    subscribed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    referral_id SERIAL PRIMARY KEY,
                    referrer_id BIGINT REFERENCES users(user_id),
                    referred_id BIGINT UNIQUE REFERENCES users(user_id),
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    admin_id BIGINT PRIMARY KEY
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS captcha (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
                    passed BOOLEAN DEFAULT FALSE,
                    message_id BIGINT
                )
            ''')

    async def execute(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> List[Dict]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def close(self):
        if self.pool:
            await self.pool.close()