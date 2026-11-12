# This file was generated with the assistance of an AI coding tool.
"""A growing wait after failed sign-ins, per user name, on top of the rate limit per address.

Someone with many addresses gets past the limit per address, so failures are also counted per user name, in
the store of the server's own data (``opencde.store.logbook``), where every server process sees them. The first
attempts cost nothing; after that each failure doubles the wait, up to a ceiling. Unknown names count the same
way, so the answer says nothing about which accounts exist, and names are stored only as hashes.

The price of counting per name is that anyone can make an account wait by failing on purpose. The ceiling is
what keeps that a nuisance rather than a lockout, and nothing here ever disables an account.
"""

from __future__ import annotations

from opencde.ids import hash_secret
from opencde.settings import Settings
from opencde.store.accounts.users import User
from opencde.store.protocols import SignInThrottleStore, UserStore
from opencde.timeutil import later_iso, now_iso, parse_iso

FORGET_AFTER_SECONDS = 24 * 3600


class SignInThrottled(Exception):
    """The user name has to wait before the next attempt."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"wait {seconds} seconds")
        self.seconds = seconds


def _key(username: str) -> str:
    return hash_secret(username.strip().casefold())


def delay_after(failures: int, settings: Settings) -> int:
    """Seconds to wait after this many failures in a row: none at first, then doubling up to the ceiling."""
    over = failures - settings.sign_in_free_attempts
    if over < 0:
        return 0
    # The exponent is capped so that a very long run of failures cannot grow the number without end.
    return min(settings.sign_in_delay_seconds * 2 ** min(over, 30), settings.sign_in_max_delay_seconds)


async def wait_seconds(throttles: SignInThrottleStore, username: str) -> int:
    """How long this user name still has to wait; 0 when it may try."""
    until = await throttles.blocked_until(_key(username))
    if not until:
        return 0
    remaining = (parse_iso(until) - parse_iso(now_iso())).total_seconds()
    return max(0, int(remaining) + 1) if remaining > 0 else 0


async def record_failure(throttles: SignInThrottleStore, username: str, settings: Settings) -> None:
    def until(failures: int) -> str | None:
        delay = delay_after(failures, settings)
        return later_iso(delay) if delay else None

    await throttles.fail(_key(username), forget_at=later_iso(FORGET_AFTER_SECONDS), until=until)


async def forget(throttles: SignInThrottleStore, username: str) -> None:
    await throttles.forget(_key(username))


async def authenticate(
    users: UserStore, throttles: SignInThrottleStore, settings: Settings, username: str, password: str
) -> User | None:
    """``users.authenticate`` behind the wait. Raises :class:`SignInThrottled` without looking at the password."""
    if not settings.sign_in_throttle_enabled:
        return await users.authenticate(username, password)
    seconds = await wait_seconds(throttles, username)
    if seconds:
        raise SignInThrottled(seconds)
    user = await users.authenticate(username, password)
    if user is None:
        await record_failure(throttles, username, settings)
    else:
        await forget(throttles, username)
    return user


__all__ = ["SignInThrottled", "authenticate", "delay_after", "forget", "record_failure", "wait_seconds"]
