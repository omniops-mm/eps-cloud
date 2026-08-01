"""Worker entrypoint. Just exists for now."""

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")


def main() -> None:
    log.info("worker up")
    while True:
        time.sleep(30)
        log.info("pulse")


if __name__ == "__main__":
    main()
