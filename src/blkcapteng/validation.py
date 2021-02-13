import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class SnapshotState:
    data: List[int] = field(default_factory=list)
    backupbtr: List[int] = field(default_factory=list)
    backuprst: List[int] = field(default_factory=list)


logger = logging.getLogger("blkcapt")


def validate(first: str, second: str, third: str, final: str, base_timestamp: int) -> bool:
    return (
        check_state(
            "first",
            parse_state_file(first, base_timestamp),
            SnapshotState(
                data=[10, 20, 30, 40, 50, 60],
                backupbtr=[10, 20, 30, 40, 50, 60],
                backuprst=[10, 20, 30, 40, 50, 60],
            ),
        )
        and check_state(
            "second",
            parse_state_file(second, base_timestamp),
            SnapshotState(
                data=[40, 50, 60, 70, 80, 90, 100, 110, 120],
                backupbtr=[20, 30, 50, 60, 70, 80, 90, 100, 110, 120],
                backuprst=[20, 50, 60, 70, 80, 90, 100, 110, 120],
            ),
        )
        and check_state(
            "third",
            parse_state_file(third, base_timestamp),
            SnapshotState(
                data=[100, 110, 120, 130, 140, 150, 160, 170, 180],
                backupbtr=[50, 60, 80, 90, 110, 120, 130, 140, 150, 160, 170, 180],
                backuprst=[20, 80, 110, 120, 130, 140, 150, 160, 170, 180],
            ),
        )
        and check_state(
            "final",
            parse_state_file(final, base_timestamp),
            SnapshotState(
                data=[160, 170, 180],
                backupbtr=[110, 120, 140, 150, 170, 180],
                backuprst=[80, 140, 170, 180],
            ),
        )
    )


def check_state(stage_name: str, actual: SnapshotState, reference: SnapshotState) -> bool:
    failed = None

    if not check_list(actual.data, reference.data):
        failed = ("data", actual.data, reference.data)

    if not check_list(actual.backupbtr, reference.backupbtr):
        failed = ("backupbtr", actual.backupbtr, reference.backupbtr)

    if not check_list(actual.backuprst, reference.backuprst):
        failed = ("backuprst", actual.backuprst, reference.backuprst)

    if failed is not None:
        name, failed_actual, failed_reference = failed
        logger.error(f"'{name}' in stage '{stage_name}' failed validation")
        logger.error(f"actual: {failed_actual}")
        logger.error(f"expect: {failed_reference}")
        return False

    return True


def check_list(actual: List[int], reference: List[int]) -> bool:
    if len(actual) != len(reference):
        return False

    for a, r in zip(actual, reference):
        if abs(r - a) > 2:
            return False

    return True


def parse_state_file(data: str, base_timestamp: int) -> SnapshotState:
    state = SnapshotState()
    for line in data.splitlines(keepends=False):
        header, value = line.split(":", 2)
        if header == "mydata":
            stamp = Path(value).name
            state.data.append(parse_bcts(stamp) - base_timestamp)
        elif header == "mybackupbtr":
            stamp = Path(value).stem
            state.backupbtr.append(parse_bcts(stamp) - base_timestamp)
        elif header == "mybackuprst":
            stamp = value[3:]
            state.backuprst.append(parse_bcts(stamp) - base_timestamp)

    return state


def parse_bcts(data: str) -> int:
    ts = datetime.datetime.strptime(data, "%Y-%m-%dT%H-%M-%S%z").timestamp()
    return int(ts)
