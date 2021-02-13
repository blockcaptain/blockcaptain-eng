import logging
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import coolname
import pylxd
import typer
from pylxd.exceptions import NotFound
from pylxd.models import Image, Instance

from blkcapteng.validation import validate

cli = typer.Typer()
logger = logging.getLogger("blkcapt")
logger.setLevel(logging.DEBUG)

VM_IMAGE_ALIAS = "ubuntu-blkcapt"
DATA_DISKS = 3


@cli.command()
def image() -> None:
    client = pylxd.Client()
    try:
        get_local_image(client)
        logger.info("image exists, trying to update...")
    except NotFound:
        logger.info("image does not exist, trying to import...")

    import_image(client)


@cli.command()
def dev(name: Optional[str] = None, storage_pool: str = "default", container: bool = False) -> None:
    client = pylxd.Client()
    name = ensure_name(name)
    if not container:
        import_image_if_not_exists(client)
    instance = create_instance(client, storage_pool, name, container)
    instance.start()


@cli.command()
def test(
    storage_pool: str = "default", package: Optional[Path] = None, keep: bool = False, container: bool = False
) -> None:
    if package is None:
        package = Path("./target/debian/blockcaptain_0.1.0_amd64.deb")

    client = pylxd.Client()
    name = "test-" + ensure_name(None)
    if not container:
        import_image_if_not_exists(client)
    logger.info(f"creating vm {name}")
    instance = create_instance(client, storage_pool, name, container)
    logger.info("starting vm")
    instance.start(wait=True)
    logger.info("waiting for guest agent")
    wait_for_agent(instance)
    logger.info("installing packages")
    install_package(instance, package)
    instance_run_script(
        instance,
        """
        set -e
        apt-get install -y jq
        """,
    )
    logger.info("copying restic")
    copy_file(instance, package.parent / "restic", Path("/usr/local/bin/restic"))
    instance_run(instance, ["chmod", "755", "/usr/local/bin/restic"])
    logger.info("configuring")

    instance_run_script(
        instance,
        """
        set -e
        DATASET_PRUNE_CRON="6 * * * * * *"
        CONTAINER_PRUNE_CRON="6 * * * * * *"
        blkcapt pool create -n primary --force /dev/sdb /dev/sdc
        blkcapt dataset create primary mydata -m 3 -s 10sec --prune-schedule "${DATASET_PRUNE_CRON}"
        blkcapt pool create -n backup --force /dev/sdd
        blkcapt container create backup mybackupbtr -m 2 -i 20s:1 2x30s:2 \
            --prune-schedule "${CONTAINER_PRUNE_CRON}"
        mkdir /mnt/backup/restic-repo
        RESTIC_PASSWORD=1234 restic init --repo /mnt/backup/restic-repo
        blkcapt restic attach -n mybackuprst --custom /mnt/backup/restic-repo -e RESTIC_PASSWORD=1234 \
             -m 1 -i 30s:2 2x1m:1 --prune-schedule "${CONTAINER_PRUNE_CRON}"
        blkcapt sync create mydata mybackupbtr
        blkcapt sync create mydata mybackuprst
        blkcapt service config -l trace
        """,
    )
    logger.info("starting service")

    FUNC_CAPTURE_STATE = """
        capture_state() {
            local name=$1
            btrfs sub list -r /mnt/primary | awk '{print $NF}' | awk '{print "mydata:" $0}' \
                >> /tmp/bce/${name}.state
            btrfs sub list -r /mnt/backup | awk '{print $NF}' | awk '{print "mybackupbtr:" $0}' \
                >> /tmp/bce/${name}.state
            RESTIC_PASSWORD=1234 restic snapshots -r /mnt/backup/restic-repo --json | jq -r '.[].tags[] \
                | select(contains("ts="))' | awk '{print "mybackuprst:" $0}' >> /tmp/bce/${name}.state
        }
    """

    instance_run_script(
        instance,
        FUNC_CAPTURE_STATE
        + """
        export -f capture_state
        set -e
        mkdir /tmp/bce
        SEC=$(date "+%S")
        if [[ $SEC -ne 0 ]]; then
            WAIT_SEC=$((60 - SEC))
            echo "Waiting ${WAIT_SEC}..."
            sleep $WAIT_SEC
        fi
        date +%s > /tmp/bce/tstart
        nohup bash -c "sleep 188; systemctl stop blockcaptain" &>/dev/null &
        nohup bash -c "sleep 63; capture_state first" &>/dev/null &
        nohup bash -c "sleep 123; capture_state second" &>/dev/null &
        nohup bash -c "sleep 183; capture_state third" &>/dev/null &

        systemctl start blockcaptain
        sleep 1
        """,
    )
    logger.info("running test cycle")
    time.sleep(187)
    logger.info("wait for service stop")
    instance_run_script(
        instance,
        """
        set -e
        i=0
        while systemctl is-active blockcaptain && [[ $i -lt 30 ]]; do
            sleep 1
            i=$((i + 1))
        done
        if systemctl is-active blockcaptain; then
            echo Service stop timed out.
            exit 1
        fi
        journalctl -u blockcaptain -o json | gzip > /tmp/log.json.gz
        """,
    )
    logger.info("pulling log")
    log_data = get_file(instance, Path("/tmp/log.json.gz"))
    with open(f"{name}.log.json.gz", "wb") as file:
        file.write(log_data)

    logger.info("getting snapshot states")
    instance_run_script(
        instance,
        FUNC_CAPTURE_STATE
        + """
        set -e
        capture_state final
        """,
    )

    base = int(get_file(instance, Path("/tmp/bce/tstart")).decode())
    first = get_file(instance, Path("/tmp/bce/first.state")).decode()
    second = get_file(instance, Path("/tmp/bce/second.state")).decode()
    third = get_file(instance, Path("/tmp/bce/third.state")).decode()
    final = get_file(instance, Path("/tmp/bce/final.state")).decode()

    logger.info("validating snapshot states")
    if not validate(first, second, third, final, base):
        logger.error("snapshot validation failed")
        raise typer.Exit(code=1)

    logger.info("checking log")
    instance_run_script(
        instance,
        """
        set -e
        if [[ $(journalctl -q -p warning -n 1 -u blockcaptain) ]]; then
            journalctl -p info -n 500 -u blockcaptain
            exit 1
        fi
        """,
    )
    if not keep:
        logger.info("destroying vm")
        destroy_vm(client, storage_pool, instance.name)


@cli.command()
def clean(name: str, storage_pool: str = "default") -> None:
    client = pylxd.Client()
    destroy_vm(client, storage_pool, name)


def ensure_name(name: Optional[str]) -> str:
    return coolname.generate_slug(2) if name is None else name


def install_package(instance: Instance, package: Path) -> None:
    target_path = Path("/tmp") / package.name
    copy_file(instance, package, target_path)
    instance_run(instance, ["apt-get", "install", "-yq", "--reinstall", str(target_path)])


def copy_file(instance: Instance, source: Path, destination: Path) -> None:
    instance.files.put(str(destination), source.read_bytes())


def get_file(instance: Instance, source: Path) -> bytes:
    return instance.files.get(str(source))


def instance_run(instance: Instance, command: List[str]) -> str:
    result = instance.execute(command)
    if result.exit_code != 0:
        raise Exception(f"command failed ({result.exit_code}): {result.stdout} {result.stderr}")
    return result.stdout


def instance_run_script(instance: Instance, script: str) -> str:
    return instance_run(instance, ["bash", "-c", script])


def import_image(client: pylxd.Client) -> Image:
    image = client.images.create_from_url("https://lxd.blockcaptain.dev/ubuntu-vm")
    if not any(a["name"] == VM_IMAGE_ALIAS for a in image.aliases):
        image.add_alias(VM_IMAGE_ALIAS, "latest blkcapt dev vm image")
        image.save()
    return image


def import_image_if_not_exists(client: pylxd.Client) -> Image:
    try:
        return get_local_image(client)
    except NotFound:
        logger.info("image does not exist, trying to import...")
        return import_image(client)


def get_local_image(client: pylxd.Client) -> Image:
    return client.images.get_by_alias(VM_IMAGE_ALIAS)


def create_instance(client: pylxd.Client, storage_pool: str, name: str, container: bool = False) -> Instance:
    if container:
        CHAR_BEGIN = 98
        loop_devices = [
            create_loop_device(create_loop_file(Path(f"/tmp/loopback-{name}-{i}.img")))
            for i in range(1, DATA_DISKS + 1)
        ]

        def dev_name(x: int) -> str:
            return f"sd{chr(CHAR_BEGIN + x)}"

        device_config = {
            dev_name(i): {"source": f"{str(device)}", "path": f"/dev/{dev_name(i)}", "type": "unix-block"}
            for i, device in enumerate(loop_devices)
        }
        device_config["btrfs-control"] = {"source": "/dev/btrfs-control", "type": "unix-char"}
        source = {
            "type": "image",
            "alias": "20.04",
            "server": "https://cloud-images.ubuntu.com/releases",
            "protocol": "simplestreams",
            "mode": "pull",
        }
        type = "container"
        inner_config = {"security.privileged": "true", "raw.apparmor": "mount,"}
    else:
        pool = client.storage_pools.get(storage_pool)
        for i in range(1, DATA_DISKS + 1):
            config = {
                "config": {"size": "128MiB"},
                "name": f"{name}-disk{i}",
                "type": "custom",
                "content_type": "block",
            }
            pool.volumes.create(config, wait=True)

        device_config = {
            f"{name}-disk{i}": {"pool": "default", "source": f"{name}-disk{i}", "type": "disk"}
            for i in range(1, DATA_DISKS + 1)
        }
        source = {"type": "image", "alias": VM_IMAGE_ALIAS}
        type = "virtual-machine"
        inner_config = {}

    config = {
        "architecture": "x86_64",
        "devices": device_config,
        "ephemeral": False,
        "profiles": ["default"],
        "name": name,
        "type": type,
        "source": source,
        "config": inner_config,
    }

    return client.instances.create(config, wait=True)


def destroy_vm(client: pylxd.Client, storage_pool: str, name: str) -> None:
    try:
        instance = client.instances.get(name)
        state = instance.state()
        if state.status != "Stopped":
            instance.stop(force=True, wait=True)
        instance.delete(wait=True)
    except NotFound:
        pass

    pool = client.storage_pools.get(storage_pool)
    for i in range(1, DATA_DISKS + 1):
        try:
            pool.volumes.get("custom", f"{name}-disk{i}").delete()
        except NotFound:
            pass


def wait_for_agent(instance: Instance) -> None:
    for _ in range(6):
        response = instance.api["exec"].post(
            json={
                "command": ["true"],
                "wait-for-websocket": False,
                "interactive": False,
            }
        )

        json = response.json()
        operation_id = json["metadata"]["id"]
        for _ in range(5):
            try:
                operation = instance.client.operations.get(operation_id)
                if operation.metadata is not None and operation.metadata.get("return", 1) == 0:
                    return
            except NotFound:
                pass

            time.sleep(1)

        time.sleep(1)

    raise Exception("timed out waiting for agent")


def create_loop_device(file: Path) -> Path:
    device = subprocess.check_output(["losetup", "--show", "-f", str(file)], text=True).strip()
    return Path(device)


def create_loop_file(file: Path) -> Path:
    subprocess.check_call(
        ["dd", "if=/dev/zero", f"of={str(file)}", "bs=8M", "count=32"],
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    return file
