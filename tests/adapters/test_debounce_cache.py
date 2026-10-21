import pytest

from fivecent.adapters.cache.memory import MemoryCache


@pytest.mark.asyncio
async def test_memory_debounce_drain_dedupes_message_ids() -> None:
    cache = MemoryCache()
    await cache.append_debounce_fragment(
        "t", "whatsapp", "336", text="Hi", provider_message_id="m1"
    )
    await cache.append_debounce_fragment(
        "t", "whatsapp", "336", text="Hi", provider_message_id="m1"
    )
    await cache.append_debounce_fragment(
        "t", "whatsapp", "336", text="Boat Saturday", provider_message_id="m2"
    )
    drained = await cache.drain_debounce_fragments("t", "whatsapp", "336")
    assert [item["text"] for item in drained] == ["Hi", "Boat Saturday"]
    assert await cache.drain_debounce_fragments("t", "whatsapp", "336") == []


@pytest.mark.asyncio
async def test_memory_debounce_generation_bumps() -> None:
    cache = MemoryCache()
    first = await cache.bump_debounce_generation("t", "whatsapp", "336")
    second = await cache.bump_debounce_generation("t", "whatsapp", "336")
    assert first == 1
    assert second == 2
    assert await cache.get_debounce_generation("t", "whatsapp", "336") == 2
