"""Small asynchronous Arena Hero game loop."""

import asyncio
import logging
from getpass import getpass

from arena_hero import AsyncArenaHeroClient, Direction

logger = logging.getLogger(__name__)


async def play(api_key: str) -> None:
    """Connect and submit one complete plan for every actionable Turn."""

    async with AsyncArenaHeroClient(api_key=api_key) as game:
        async for turn in game.turns():
            for vanguard in turn.vanguards:
                vanguard.sweep(Direction.LEFT)

            for ranger in turn.rangers:
                if turn.visible_enemies:
                    ranger.shoot(turn.visible_enemies[0])

            receipt = await turn.submit()
            logger.info("Accepted plan for Tick %d", receipt.tick)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(play(getpass("Arena Hero API key: ")))
