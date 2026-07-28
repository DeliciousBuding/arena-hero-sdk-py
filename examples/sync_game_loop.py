"""Small synchronous Arena Hero game loop."""

import logging
from getpass import getpass

from arena_hero import ArenaHeroClient, Direction, UnitType

logger = logging.getLogger(__name__)


def play(api_key: str) -> None:
    """Connect and submit one complete plan for every actionable Turn."""

    with ArenaHeroClient(api_key=api_key) as game:
        for turn in game.turns():
            for worker in turn.workers:
                if worker.position in turn.resource_cells:
                    worker.harvest()
                else:
                    worker.move(Direction.RIGHT)

            if turn.core is not None and turn.resources >= 5:
                turn.core.spawn(UnitType.WORKER)

            receipt = turn.submit()
            logger.info("Accepted plan for Tick %d", receipt.tick)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    play(getpass("Arena Hero API key: "))
