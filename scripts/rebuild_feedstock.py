import os
import pathlib
import re
import subprocess
import tempfile
import tomllib

import click
import httpx2

project_root = pathlib.Path(__file__).parent.parent

client = httpx2.Client(follow_redirects=True, timeout=600.0)

GITHUB_API = "https://api.github.com"

UPSTREAM_OWNER = "conda-forge"
FORK_OWNER = "trim21"
DEPENDENCY = "go-nocgo"


def github_headers() -> dict[str, str]:
    token = os.environ["GITHUB_TOKEN"]
    return {"Authorization": f"Bearer {token}"}


def pinned_version(name: str) -> str:
    pixi_file = project_root.joinpath("watch", name, "pixi.toml")
    spec = tomllib.loads(pixi_file.read_text("utf8"))["dependencies"][DEPENDENCY]
    version = spec.removeprefix("==")
    if version == spec:
        raise ValueError(f"expecting exact pin like ==1.2.3, got {spec!r}")
    return version


def local_update_pr_open(repo_slug: str, path: str) -> bool:
    prs = client.get(
        f"{GITHUB_API}/repos/{repo_slug}/pulls",
        params={"state": "open", "per_page": "100"},
        headers=github_headers(),
    ).json()
    for pr in prs:
        files = client.get(
            f"{GITHUB_API}/repos/{repo_slug}/pulls/{pr['number']}/files",
            headers=github_headers(),
        ).json()
        if any(file["filename"] == path for file in files):
            print(f"local update pr {pr['html_url']} is open, skip")
            return True
    return False


def upstream_pr_exists(upstream_slug: str, head: str) -> bool:
    prs = client.get(
        f"{GITHUB_API}/repos/{upstream_slug}/pulls",
        params={"state": "all", "head": head},
        headers=github_headers(),
    ).json()
    if prs:
        print(f"upstream pr {prs[0]['html_url']} already exists, skip")
        return True
    return False


def bump_build_number(recipe_file: pathlib.Path) -> None:
    content = recipe_file.read_text("utf8")
    new, n = re.subn(
        r"(?m)^(\s*number:\s*)(\d+)$",
        lambda m: m.group(1) + str(int(m.group(2)) + 1),
        content,
        count=1,
    )
    if n != 1:
        raise ValueError(
            f"expecting exactly one build.number in {recipe_file}, got {n}"
        )
    recipe_file.write_text(new, newline="\n")


def run(*args: str, cwd: pathlib.Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def open_rebuild_pr(name: str, version: str) -> None:
    upstream_slug = f"{UPSTREAM_OWNER}/{name}-feedstock"
    fork_slug = f"{FORK_OWNER}/{name}-feedstock"
    branch = f"rebuild/{DEPENDENCY}-{version}"
    token = os.environ["GITHUB_TOKEN"]

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = pathlib.Path(tmp) / "feedstock"
        run(
            "git",
            "clone",
            "--depth=1",
            f"https://github.com/{upstream_slug}",
            str(clone_dir),
        )

        bump_build_number(clone_dir / "recipe" / "recipe.yaml")

        run("git", "-C", str(clone_dir), "config", "user.name", FORK_OWNER)
        run(
            "git",
            "-C",
            str(clone_dir),
            "config",
            "user.email",
            f"{FORK_OWNER}@users.noreply.github.com",
        )
        run("git", "-C", str(clone_dir), "checkout", "-b", branch)
        run(
            "git",
            "-C",
            str(clone_dir),
            "commit",
            "-am",
            f"rebuild for {DEPENDENCY} {version}",
        )
        run(
            "git",
            "-C",
            str(clone_dir),
            "push",
            f"https://x-access-token:{token}@github.com/{fork_slug}",
            f"HEAD:refs/heads/{branch}",
        )

    repo = client.get(
        f"{GITHUB_API}/repos/{upstream_slug}", headers=github_headers()
    ).json()
    response = client.post(
        f"{GITHUB_API}/repos/{upstream_slug}/pulls",
        headers=github_headers(),
        json={
            "title": f"rebuild for {DEPENDENCY} {version}",
            "head": f"{FORK_OWNER}:{branch}",
            "base": repo["default_branch"],
            "body": (
                f"Bump build number to rebuild {name} against {DEPENDENCY} {version}.\n\n"
                "Triggered by trim21/personal-feedstock."
            ),
        },
    )
    if response.is_error:
        raise SystemExit(
            f"failed to create pull request: {response.status_code} {response.text}"
        )
    print(f"pull request created: {response.json()['html_url']}")


@click.command()
@click.argument("name")
def main(name: str) -> None:
    repo_slug = os.environ.get("GITHUB_REPOSITORY", "trim21/personal-feedstock")
    watch_path = f"watch/{name}/pixi.toml"

    version = pinned_version(name)
    print(f"{name}: pinned {DEPENDENCY} {version}")

    if local_update_pr_open(repo_slug, watch_path):
        return
    if upstream_pr_exists(
        f"{UPSTREAM_OWNER}/{name}-feedstock",
        f"{FORK_OWNER}:rebuild/{DEPENDENCY}-{version}",
    ):
        return

    open_rebuild_pr(name, version)


if __name__ == "__main__":
    main()
