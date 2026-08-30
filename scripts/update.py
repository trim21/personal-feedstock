import hashlib
import os
import pathlib
import re
from typing import Any

import click
import httpx2
import jmespath
import yaml

project_root = pathlib.Path(__file__).parent.parent

client = httpx2.Client(follow_redirects=True, timeout=600.0)

GITHUB_API = "https://api.github.com"


def github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def repo_slug(repository: str) -> str:
    m = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/?", repository)
    if not m:
        raise ValueError(f"unsupported repository url: {repository!r}")
    return m.group(1)


def fetch_tarball_sha256(url: str) -> str:
    return hashlib.sha256(client.get(url, headers=github_headers()).content).hexdigest()


def latest_release(slug: str) -> tuple[str, str]:
    release = client.get(
        f"{GITHUB_API}/repos/{slug}/releases/latest", headers=github_headers()
    ).json()
    tag: str = release["tag_name"]
    return tag, tag.removeprefix("v")


def sync_sha256(package: str, recipe_content: str, recipe: dict[str, Any]) -> str:
    version: str = recipe["context"]["version"]
    url: str = recipe["source"]["url"].replace("${{ version }}", version)
    sha256 = fetch_tarball_sha256(url)

    if sha256 == jmespath.search("source.sha256", recipe):
        print(f"{package}: sha256 up to date for {version}")
        return recipe_content

    print(f"{package}: sync sha256 for {version}")
    return update_object_patch(recipe_content, sha256, "source.sha256")


def upgrade(recipe_content: str, recipe: dict[str, Any], slug: str) -> str:
    tag, version = latest_release(slug)
    url = f"https://github.com/{slug}/archive/refs/tags/{tag}.tar.gz"
    sha256 = fetch_tarball_sha256(url)

    if version == jmespath.search("context.version", recipe):
        if sha256 == jmespath.search("source.sha256", recipe):
            print(f"up to date at {version}")
            return recipe_content

    print(f"update to {version}")

    recipe_content = update_object_patch(recipe_content, version, "context.version")

    current_url = jmespath.search("source.url", recipe)
    if "${{ version }}" not in current_url:
        recipe_content = update_object_patch(recipe_content, url, "source.url")

    return update_object_patch(recipe_content, sha256, "source.sha256")


@click.command()
@click.option(
    "--sync-sha",
    is_flag=True,
    default=False,
    help="keep the current version, only update source.sha256 for it",
)
@click.argument("packages", nargs=-1)
def main(sync_sha: bool, packages: list[str]) -> None:
    if not packages:
        packages = [f.name for f in project_root.joinpath("packages").iterdir()]

    for package in packages:
        recipe_file = project_root.joinpath("packages", package, "recipe.yaml")
        recipe_content = recipe_file.read_text("utf8")
        recipe: dict[str, Any] = yaml.safe_load(recipe_content)

        if sync_sha:
            new_content = sync_sha256(package, recipe_content, recipe)
        else:
            slug = repo_slug(recipe["about"]["repository"])
            new_content = upgrade(recipe_content, recipe, slug)

        if new_content != recipe_content:
            recipe_file.write_text(new_content, newline="\n")


def update_object_patch(old_content: str, new_value: str, object_path: str) -> str:
    recipe = yaml.safe_load(old_content)
    current_value = jmespath.search(object_path, recipe)
    if not isinstance(current_value, str):
        raise ValueError(
            f"expecting to update str, got {current_value!r} instead: {object_path=!r}"
        )

    if old_content.count(current_value) == 1:
        new_content = old_content.replace(current_value, new_value)
        assert jmespath.search(object_path, yaml.safe_load(new_content)) == new_value
        return new_content

    s = old_content.split(current_value)

    for i in range(1, len(s)):
        new_content = current_value.join(s[:i]) + new_value + current_value.join(s[i:])
        if jmespath.search(object_path, yaml.safe_load(new_content)) == new_value:
            return new_content

    raise Exception("failed to update content")


if __name__ == "__main__":
    main()
