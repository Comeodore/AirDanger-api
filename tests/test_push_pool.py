from aioapns.connection import ChannelPool, H2Protocol

from app.push import HonestChannelPool, install_honest_channel_pool

APNS_SETTINGS_SEQUENCE = (1, 1000, 1)


async def test_stock_pool_underflows_on_apns_settings_sequence():
    pool = ChannelPool(1000)
    pool.bound = APNS_SETTINGS_SEQUENCE[0]
    await pool.acquire()
    pool.bound = APNS_SETTINGS_SEQUENCE[1]
    pool.bound = APNS_SETTINGS_SEQUENCE[2]
    pool.release()
    assert pool._value < 0
    assert pool.is_busy is True


async def test_honest_pool_survives_apns_settings_sequence():
    pool = HonestChannelPool(APNS_SETTINGS_SEQUENCE[0])
    await pool.acquire()
    pool.bound = APNS_SETTINGS_SEQUENCE[1]
    assert pool._value == 999
    pool.bound = APNS_SETTINGS_SEQUENCE[2]
    assert pool._value == 0
    assert pool.is_busy is True
    pool.release()
    assert pool._value == 1
    assert pool.is_busy is False


async def test_honest_pool_grants_full_allowance_when_idle():
    pool = HonestChannelPool(1)
    pool.bound = 1000
    assert pool._value == 1000
    streams = [await pool.acquire() for _ in range(50)]
    assert len(set(streams)) == 50
    assert pool.is_busy is False


async def test_honest_pool_starts_pessimistic_so_cold_fan_out_cannot_race():
    pool = HonestChannelPool(1)
    await pool.acquire()
    assert pool.is_busy is True


def test_install_is_idempotent():
    install_honest_channel_pool()
    patched = H2Protocol.__init__
    install_honest_channel_pool()
    assert H2Protocol.__init__ is patched
    assert H2Protocol._airdanger_honest_pool is True
