import os
import subprocess
import urllib.parse

import click
import httpx2

GITHUB_API = "https://api.github.com"

# registry ghcr.io/trim21/personal-feedstock, channel trim21-pkgs
MIRROR_PREFIX = "personal-feedstock/trim21-pkgs/"


def github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    return subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()


@click.command()
@click.option(
    "--prefix",
    default=MIRROR_PREFIX,
    show_default=True,
    help="ghcr package name prefix to make public",
)
@click.option(
    "--dry-run", is_flag=True, help="list matching packages without changing them"
)
def main(prefix: str, dry_run: bool) -> None:
    """Set the ghcr.io OCI mirror packages to public.

    Packages pushed by the oci-mirror workflow start private, and every
    conda package name, repodata.json and shard repository under the
    channel is a separate ghcr package. This flips them all in one pass
    instead of one by one in the package settings UI.

    The token needs the read:packages and write:packages scopes:
    gh auth refresh -h github.com -s write:packages
    """
    client = httpx2.Client(
        base_url=GITHUB_API,
        headers={"Authorization": f"Bearer {github_token()}"},
        timeout=60.0,
    )

    targets: list[str] = []
    page = 1
    while True:
        response = client.get(
            "/user/packages",
            params={
                "package_type": "container",
                "visibility": "private",
                "page": page,
                "per_page": 100,
            },
        )
        response.raise_for_status()
        packages = response.json()
        if not packages:
            break
        targets += [
            package["name"]
            for package in packages
            if package["name"].startswith(prefix)
        ]
        page += 1

    if not targets:
        click.echo(f"no private packages under {prefix!r}")
        return
    for name in targets:
        click.echo(name)
        if dry_run:
            continue
        encoded = urllib.parse.quote(name, safe="")
        response = client.patch(
            f"/user/packages/container/{encoded}/visibility",
            json={"state": "public"},
        )
        response.raise_for_status()
    click.echo(f"{len(targets)} package(s) public")


if __name__ == "__main__":
    main()
