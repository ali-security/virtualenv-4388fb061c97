from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from subprocess import CalledProcessError, Popen
from typing import TYPE_CHECKING, Final

import pytest

from virtualenv.app_data import AppDataDiskFolder
from virtualenv.seed.wheels.acquire import download_wheel, get_wheel, pip_wheel_env_run
from virtualenv.seed.wheels.embed import get_embed_wheel
from virtualenv.seed.wheels.periodic_update import UnverifiedWheelError, dump_datetime
from virtualenv.seed.wheels.util import Wheel

if TYPE_CHECKING:
    from collections.abc import Callable
    from ssl import SSLContext
    from unittest.mock import MagicMock

    from pytest_mock import MockerFixture


@pytest.fixture(autouse=True)
def _fake_release_date(mocker) -> None:
    mocker.patch("virtualenv.seed.wheels.periodic_update.release_date_for_wheel_path", return_value=None)


def test_pip_wheel_env_run_could_not_find(session_app_data, mocker) -> None:
    mocker.patch("virtualenv.seed.wheels.acquire.from_bundle", return_value=None)
    with pytest.raises(RuntimeError, match="could not find the embedded pip"):
        pip_wheel_env_run([], session_app_data, os.environ)


@pytest.mark.parametrize(
    "version_spec",
    [
        pytest.param(lambda _wheel: None, id="latest"),
        pytest.param(lambda _wheel: "", id="unpinned"),
        pytest.param(lambda wheel: f"=={wheel.version}", id="pinned"),
    ],
)
def test_download_wheel_uses_staged_archive(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
    version_spec: Callable[[Wheel], str | None],
) -> None:
    _, expected, _ = download_process
    result: Final = download_wheel("pip", version_spec(expected), for_py_version, [], session_app_data, tmp_path, {})
    assert (result.path, result.path.read_bytes(), list(tmp_path.iterdir())) == (
        tmp_path / expected.name,
        expected.path.read_bytes(),
        [tmp_path / expected.name],
    )


def test_download_wheel_ignores_path_reported_by_pip(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    process, expected, _ = download_process
    elsewhere: Final = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / expected.name).write_bytes(b"not the archive pip staged")
    house: Final = tmp_path / "house"
    house.mkdir()
    process.return_value.configure_mock(**{"communicate.return_value": (f"Saved {elsewhere / expected.name}", "")})
    result: Final = download_wheel("pip", None, for_py_version, [], session_app_data, house, {})
    assert (result.path, result.path.read_bytes(), list(house.iterdir())) == (
        house / expected.name,
        expected.path.read_bytes(),
        [house / expected.name],
    )


def test_download_wheel_not_found_raises(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    download_process[0].side_effect = None
    with pytest.raises(RuntimeError, match="could not find downloaded wheel"):
        download_wheel("pip", None, for_py_version, [], session_app_data, tmp_path, {})
    assert list(tmp_path.iterdir()) == []


def test_download_wheel_rejects_extra_staged_archive(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    process, _, _ = download_process
    stage_expected: Final = process.side_effect

    def downloaded(cmd: list[str], **kwargs: str | int | bool | dict[str, str]) -> Popen[str]:
        (Path(cmd[cmd.index("-d") + 1]) / "pip-0.0.0-1-py3-none-any.whl").write_bytes(b"decoy")
        return stage_expected(cmd, **kwargs)

    process.side_effect = downloaded
    with pytest.raises(RuntimeError, match="expected one archive"):
        download_wheel("pip", None, for_py_version, [], session_app_data, tmp_path, {})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("download_process")
def test_download_wheel_rejects_wrong_version(
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="unexpected downloaded wheel"):
        download_wheel("pip", "==0.0.0", for_py_version, [], session_app_data, tmp_path, {})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("download_process")
def test_download_wheel_rejects_wrong_distribution(
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="unexpected downloaded wheel"):
        download_wheel("setuptools", None, for_py_version, [], session_app_data, tmp_path, {})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("download_process")
def test_download_wheel_rejects_unsupported_python(
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="does not support Python"):
        download_wheel("pip", None, "2.7", [], session_app_data, tmp_path, {})
    assert list(tmp_path.iterdir()) == []


def test_download_fails(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    download_process[0].return_value.configure_mock(returncode=7)
    with pytest.raises(CalledProcessError) as context:
        download_wheel("pip", None, for_py_version, [], session_app_data, tmp_path, {})
    error: Final = context.value
    assert (
        error.output,
        error.stderr,
        error.returncode,
        error.cmd[:3],
        error.cmd[-1],
        Path(error.cmd[error.cmd.index("-d") + 1]).parent,
        list(tmp_path.iterdir()),
    ) == (
        "progress complete",
        "",
        7,
        [sys.executable, "-m", "pip"],
        "pip",
        tmp_path,
        [],
    )


def test_download_wheel_python_io_encoding(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    download_wheel("pip", None, for_py_version, [], session_app_data, tmp_path, {})
    assert download_process[0].call_args.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"


def test_download_wheel_keeps_failed_verification_out_of_cache(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
) -> None:
    download_process[2].extend(b"\n")
    with pytest.raises(RuntimeError, match="but PyPI reports"):
        download_wheel("pip", None, for_py_version, [], session_app_data, tmp_path, {})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "env_var",
    [
        pytest.param(name, id=name.lower())
        for name in (
            "PIP_INDEX_URL",
            "PIP_EXTRA_INDEX_URL",
            "PIP_INDEX",
        )
    ],
)
def test_download_wheel_preserves_custom_index_policy(
    download_process: tuple[MagicMock, Wheel, bytearray],
    for_py_version: str,
    session_app_data: AppDataDiskFolder,
    tmp_path: Path,
    env_var: str,
) -> None:
    download_process[2].extend(b"\n")
    result: Final = download_wheel(
        "pip",
        None,
        for_py_version,
        [],
        session_app_data,
        tmp_path,
        {env_var: "https://index.example/simple"},
    )
    assert result.path.read_bytes() == bytes(download_process[2])


@pytest.fixture
def download_process(mocker: MockerFixture, for_py_version: str) -> tuple[MagicMock, Wheel, bytearray]:
    wheel: Final = get_embed_wheel("pip", for_py_version)
    payload: Final = bytearray(wheel.path.read_bytes())
    # PyPy can retain the hash input buffer until garbage collection.
    metadata: Final = json.dumps({
        "releases": {
            wheel.version: [
                {
                    "filename": wheel.name,
                    "digests": {"sha256": hashlib.sha256(bytes(payload)).hexdigest()},
                }
            ]
        }
    }).encode()

    def metadata_response(*_args: str, **_kwargs: SSLContext | None) -> BytesIO:
        return BytesIO(metadata)

    # isolate the PyPI metadata cache so the mocked response neither reads nor leaks a real lookup in this process
    mocker.patch.dict("virtualenv.seed.wheels.periodic_update._PYPI_CACHE", clear=True)
    mocker.patch("virtualenv.seed.wheels.periodic_update.urlopen", autospec=True, side_effect=metadata_response)

    def downloaded(cmd: list[str], **_kwargs: str | int | bool | dict[str, str]) -> Popen[str]:
        (Path(cmd[cmd.index("-d") + 1]) / wheel.name).write_bytes(payload)
        return process.return_value

    process: Final = mocker.patch("virtualenv.seed.wheels.acquire.Popen", autospec=True, side_effect=downloaded)
    process.return_value.configure_mock(**{"communicate.return_value": ("progress complete", ""), "returncode": 0})
    return process, wheel, payload


@pytest.fixture
def downloaded_wheel(mocker):
    wheel = Wheel.from_path(Path("setuptools-0.0.0-py2.py3-none-any.whl"))
    return wheel, mocker.patch("virtualenv.seed.wheels.acquire.download_wheel", return_value=wheel)


@pytest.mark.parametrize("version", ["bundle", "0.0.0"])
def test_get_wheel_download_called(mocker, for_py_version, session_app_data, downloaded_wheel, version) -> None:
    distribution = "setuptools"
    write = mocker.patch("virtualenv.app_data.via_disk_folder.JSONStoreDisk.write")
    wheel = get_wheel(distribution, version, for_py_version, [], True, session_app_data, False, os.environ)
    assert wheel is not None
    assert wheel.name == downloaded_wheel[0].name
    assert downloaded_wheel[1].call_count == 1
    assert write.call_count == 1


@pytest.mark.parametrize("version", ["embed", "pinned"])
def test_get_wheel_download_not_called(mocker, for_py_version, session_app_data, downloaded_wheel, version) -> None:
    distribution = "setuptools"
    expected = get_embed_wheel(distribution, for_py_version)
    if version == "pinned":
        version = expected.version
    write = mocker.patch("virtualenv.app_data.via_disk_folder.JSONStoreDisk.write")
    wheel = get_wheel(distribution, version, for_py_version, [], True, session_app_data, False, os.environ)
    assert wheel is not None
    assert wheel.name == expected.name
    assert downloaded_wheel[1].call_count == 0
    assert write.call_count == 0


def test_get_wheel_falls_back_to_bundle_when_unverified(
    mocker: MockerFixture, for_py_version: str, session_app_data: AppDataDiskFolder
) -> None:
    mocker.patch("virtualenv.seed.wheels.acquire.download_wheel", side_effect=UnverifiedWheelError("refusing"))

    wheel = get_wheel("setuptools", "bundle", for_py_version, [], True, session_app_data, False, os.environ)

    assert wheel is not None
    assert wheel.name == get_embed_wheel("setuptools", for_py_version).name


def test_get_wheel_unverified_pinned_version_fails(
    mocker: MockerFixture, for_py_version: str, session_app_data: AppDataDiskFolder
) -> None:
    mocker.patch("virtualenv.seed.wheels.acquire.download_wheel", side_effect=UnverifiedWheelError("refusing"))

    with pytest.raises(UnverifiedWheelError, match="refusing"):
        get_wheel("setuptools", "0.0.0", for_py_version, [], True, session_app_data, False, os.environ)


def test_get_wheel_download_cached(
    tmp_path: Path,
    mocker: MockerFixture,
    for_py_version: str,
    downloaded_wheel: tuple[Wheel, MagicMock],
    time_freeze: Callable[[datetime], None],
) -> None:
    time_freeze(datetime.now(tz=timezone.utc))
    from virtualenv.app_data.via_disk_folder import JSONStoreDisk  # ruff:ignore[import-outside-top-level]

    app_data = AppDataDiskFolder(folder=str(tmp_path))
    expected = downloaded_wheel[0]
    write = mocker.spy(JSONStoreDisk, "write")
    # 1st call, not cached, download is called
    wheel = get_wheel(expected.distribution, expected.version, for_py_version, [], True, app_data, False, os.environ)
    assert wheel is not None
    assert wheel.name == expected.name
    assert downloaded_wheel[1].call_count == 1
    assert write.call_count == 1
    # 2nd call, cached, download is not called
    wheel = get_wheel(expected.distribution, expected.version, for_py_version, [], True, app_data, False, os.environ)
    assert wheel is not None
    assert wheel.name == expected.name
    assert downloaded_wheel[1].call_count == 1
    assert write.call_count == 1
    wrote_json = write.call_args[0][1]
    assert wrote_json == {
        "completed": None,
        "periodic": None,
        "started": None,
        "versions": [
            {
                "filename": expected.name,
                "release_date": None,
                "found_date": dump_datetime(datetime.now(tz=timezone.utc)),
                "source": "download",
            },
        ],
    }


@pytest.mark.parametrize(
    "distribution",
    [
        "pip space",
        "pip[extra]",
        "pip;os.system('x')",
        "--index-url=http://evil",
        "-r requirements.txt",
        "",
        ".pip",
        "pip-",
    ],
)
def test_download_wheel_rejects_bad_distribution(distribution: str, session_app_data) -> None:
    with pytest.raises(ValueError, match="suspicious distribution name"):
        download_wheel(distribution, None, "3.14", [], session_app_data, "folder", os.environ)


@pytest.mark.parametrize(
    "version_spec",
    [
        "==1.0 --index-url=http://evil",
        ">=1.0",
        "== 1.0",
        "==1.0;echo",
    ],
)
def test_download_wheel_rejects_bad_version_spec(version_spec: str, session_app_data, mocker: MockerFixture) -> None:
    mocker.patch("virtualenv.seed.wheels.acquire.Popen")
    with pytest.raises(ValueError, match="suspicious version spec"):
        download_wheel("pip", version_spec, "3.14", [], session_app_data, "folder", os.environ)
