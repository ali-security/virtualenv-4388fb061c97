from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from virtualenv.run import cli_run
from virtualenv.seed.embed.pip_invoke import PipInvoke
from virtualenv.seed.wheels.embed import get_embed_wheel

if TYPE_CHECKING:
    from subprocess import Popen

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    "excluded",
    [pytest.param("pip", id="no-pip"), pytest.param("setuptools", id="no-setuptools"), pytest.param("", id="all")],
)
def test_base_bootstrap_via_pip_invoke(tmp_path: Path, current_fastest: str, excluded: str) -> None:
    command: Final = [
        "--seeder",
        "pip",
        str(tmp_path / "env"),
        "--download",
        "--creator",
        current_fastest,
        "--pip",
        "embed",
        "--setuptools",
        "embed",
        "--no-periodic-update",
    ]
    if excluded:
        command.append(f"--no-{excluded}")
    session: Final = cli_run(command)
    assert {path.name for path in session.creator.purelib.iterdir()} & {"pip", "setuptools"} == {
        "pip",
        "setuptools",
    } - {excluded}


def test_pip_invoke_offers_only_selected_wheels(tmp_path: Path, mocker: MockerFixture, for_py_version: str) -> None:
    seen: Final[list[tuple[list[str], set[str]]]] = []
    original: Final = PipInvoke._execute  # ruff:ignore[private-member-access]

    def _execute(cmd: list[str], env: dict[str, str]) -> Popen[bytes]:
        links: Final = [cmd[index + 1] for index, arg in enumerate(cmd) if arg == "--find-links"]
        seen.append((list(cmd), {entry.name for link in links for entry in Path(link).iterdir()}))
        return original(cmd, env)

    mocker.patch.object(PipInvoke, "_execute", side_effect=_execute)
    cli_run([
        str(tmp_path / "env"),
        "--seeder",
        "pip",
        "--download",
        "--pip",
        "embed",
        "--setuptools",
        "embed",
        "--no-periodic-update",
    ])
    ((cmd, offered),) = seen
    # pip must not reach an index on its own, nor see other archives that share a folder with the selected wheels
    assert ("--no-index" in cmd, cmd.count("--find-links"), offered) == (
        True,
        1,
        {get_embed_wheel(distribution, for_py_version).name for distribution in ("pip", "setuptools")},
    )


def test_pip_invoke_download_goes_through_verified_path(tmp_path: Path, mocker: MockerFixture) -> None:
    download: Final = mocker.patch(
        "virtualenv.seed.wheels.acquire.download_wheel",
        side_effect=RuntimeError("digest mismatch"),
    )
    with pytest.raises(RuntimeError, match="digest mismatch"):
        cli_run([
            str(tmp_path / "env"),
            "--seeder",
            "pip",
            "--download",
            "--pip",
            "0.0.0",
            "--no-setuptools",
            "--no-periodic-update",
        ])
    assert download.call_count == 1


def test_pip_invoke_reports_unavailable_wheel(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="could not get wheel for distribution pip"):
        cli_run([
            str(tmp_path / "env"),
            "--seeder",
            "pip",
            "--pip",
            "0.0.0",
            "--no-download",
            "--no-periodic-update",
        ])


@pytest.mark.usefixtures("_no_coverage")
def test_pip_invoke_freeze_uses_version_pin(tmp_path: Path) -> None:
    session: Final = cli_run([
        str(tmp_path / "env"),
        "--seeder",
        "pip",
        "--pip",
        "embed",
        "--no-setuptools",
        "--no-periodic-update",
    ])
    result: Final = subprocess.run(
        [str(session.creator.exe), "-I", "-m", "pip", "freeze", "--all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    version: Final = get_embed_wheel("pip", f"{sys.version_info.major}.{sys.version_info.minor}").version
    assert f"pip=={version}" in result.stdout.splitlines()
