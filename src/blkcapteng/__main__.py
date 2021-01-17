import logging

from rich.logging import RichHandler

from blkcapteng.commands import cli


def main() -> None:
    logging.basicConfig(level="NOTSET", format="%(message)s", datefmt="[%X]", handlers=[RichHandler()])
    cli(prog_name="blkcapteng")


if __name__ == "__main__":
    main()
