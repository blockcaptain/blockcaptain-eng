from os import device_encoding
import typer
import logging
import pylxd
from typing import Any, Optional
import coolname
from pylxd.models import Instance, Image

cli = typer.Typer()
logger = logging.getLogger()

VM_IMAGE_ALIAS = "ubuntu-blkcapt"


@cli.command()
def image() -> None:
    client = pylxd.Client()
    try:
        get_local_image(client)
        print("image exists, trying to update...")
    except:
        print("image does not exist, trying to import...")

    import_image(client)


@cli.command()
def dev(name: Optional[str] = None, storage_pool: Optional[str] = "default") -> None:
    client = pylxd.Client()

    import_image_if_not_exists(client)
    instance = create_vm(client, storage_pool, name)
    instance.start()


@cli.command()
def test(storage_pool: Optional[str] = "default") -> None:
    client = pylxd.Client()

    import_image_if_not_exists(client)
    instance = create_vm(client, storage_pool)
    instance.start(wait=True)

    # install blkcapt binaries and service
    # setup pool and containers (btrfs, restic) using blkcapt
    # run blackbox tests
    # analyze test results


def import_image(client: pylxd.Client) -> Image:
    image = client.images.create_from_url("https://lxd.blockcaptain.dev/ubuntu-vm")
    if not any(a["name"] == VM_IMAGE_ALIAS for a in image.aliases):
        image.add_alias(VM_IMAGE_ALIAS, "latest blkcapt dev vm image")
        image.save()
    
    return image


def import_image_if_not_exists(client: pylxd.Client) -> Image:
    try:
        return get_local_image(client)
    except:
        print("image does not exist, trying to import...")
        return import_image(client)


def get_local_image(client: pylxd.Client) -> Image:
    return client.images.get_by_alias(VM_IMAGE_ALIAS)


def create_vm(client: pylxd.Client, storage_pool: str, name: Optional[str] = None) -> Instance:
    DATA_DISKS = 3

    try:
        get_local_image(client)
    except:
        print("image does not exist, trying to import...")
        import_image(client)

    if name is None:
        name = coolname.generate_slug(2)

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
