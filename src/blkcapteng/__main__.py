import logging
import warnings

from rich.logging import RichHandler

from blkcapteng.commands import cli


def main() -> None:
    warnings.filterwarnings("ignore", module="pylxd")
    logging.basicConfig(level=logging.WARNING, format="%(message)s", datefmt="[%X]", handlers=[RichHandler()])
    cli(prog_name="blkcapteng")


if __name__ == "__main__":
    main()
