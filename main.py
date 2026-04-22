import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from src.pipeline import run

if __name__ == "__main__":
    processed = run()
    print(f"\nTrascrizioni elaborate: {processed}")
