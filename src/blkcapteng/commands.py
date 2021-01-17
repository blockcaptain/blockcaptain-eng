import logging
import time
from pathlib import Path
from typing import List, Optional

import coolname
import pylxd
import typer
from pylxd.exceptions import NotFound
from pylxd.models import Image, Instance

cli = typer.Typer()
logger = logging.getLogger()

VM_IMAGE_ALIAS = "ubuntu-blkcapt"
DATA_DISKS = 3


@cli.command()
def image() -> None:
    client = pylxd.Client()
    try:
        get_local_image(client)
        print("image exists, trying to update...")
    except NotFound:
        print("image does not exist, trying to import...")

    import_image(client)


@cli.command()
def dev(name: Optional[str] = None, storage_pool: str = "default") -> None:
    client = pylxd.Client()
    name = ensure_name(name)
    import_image_if_not_exists(client)
    instance = create_vm(client, storage_pool, name)
    instance.start()


@cli.command()
def test(storage_pool: str = "default", package: Optional[Path] = None, keep: bool = False) -> None:
    if package is None:
        package = Path("./target/debian/blockcaptain_0.1.0_amd64.deb")

    client = pylxd.Client()
    name = "test-" + ensure_name(None)
    import_image_if_not_exists(client)
    logging.info(f"creating vm {name}")
    instance = create_vm(client, storage_pool, name)
    logging.info("starting vm")
    instance.start(wait=True)
    logging.info("waiting for guest agent")
    wait_for_agent(instance)
    logging.info("installing package")
    install_package(instance, package)
    logging.info("configuring")
    blkcapt(instance, ["pool", "create", "/dev/sdb", "/dev/sdc"])
    blkcapt(instance, ["dataset", "create", "default", "mydata"])
    blkcapt(instance, ["pool", "create", "-n", "backup", "/dev/sdd"])
    blkcapt(instance, ["container", "create", "backup", "mybackup"])
    # set clock ??
    logging.info("starting service")
    instance_run(instance, ["systemctl", "start", "blockcaptain"])
    time.sleep(3)
    logging.info("stopping service")
    instance_run(instance, ["systemctl", "stop", "blockcaptain"])
    # analyze final state ??
    if not keep:
        logging.info("destroying vm")
        destroy_vm(client, storage_pool, instance.name)


@cli.command()
def clean(name: str, storage_pool: str = "default") -> None:
    client = pylxd.Client()

    destroy_vm(client, storage_pool, name)


def ensure_name(name: Optional[str]) -> str:
    return coolname.generate_slug(2) if name is None else name


def install_package(instance: Instance, package: Path) -> None:
    target_path = Path("/tmp") / package.name
    instance.files.put(str(target_path), package.read_bytes())
    instance_run(instance, ["apt-get", "install", "-yq", "--reinstall", str(target_path)])


def instance_run(instance: Instance, command: List[str]) -> str:
    result = instance.execute(command)
    if result.exit_code != 0:
        raise Exception(f"command failed ({result.exit_code}): {result.stderr}")
    return result.stdout


def blkcapt(instance: Instance, command: List[str]) -> str:
    return instance_run(instance, ["blkcapt"] + command)


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
        print("image does not exist, trying to import...")
        return import_image(client)


def get_local_image(client: pylxd.Client) -> Image:
    return client.images.get_by_alias(VM_IMAGE_ALIAS)


def create_vm(client: pylxd.Client, storage_pool: str, name: str) -> Instance:
    try:
        get_local_image(client)
    except NotFound:
        print("image does not exist, trying to import...")
        import_image(client)

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
    config = {
        "architecture": "x86_64",
        "devices": device_config,
        "ephemeral": False,
        "profiles": ["default"],
        "name": name,
        "type": "virtual-machine",
        "source": {"type": "image", "alias": VM_IMAGE_ALIAS},
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
