from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from importlib.metadata import distributions
from pathlib import Path
from shutil import copyfile
from stat import S_IMODE
from subprocess import Popen
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final

from virtualenv.seed.embed.base_embed import BaseEmbed
from virtualenv.seed.embed.via_app_data.pip_install.base import shell_shebang
from virtualenv.seed.wheels import Version, get_wheel, pip_wheel_env_run
from virtualenv.util.subprocess import LogCmd

if TYPE_CHECKING:
    from collections.abc import Generator

    from virtualenv.config.cli.parser import VirtualEnvOptions
    from virtualenv.create.creator import Creator

LOGGER = logging.getLogger(__name__)


class PipInvoke(BaseEmbed):
    def __init__(self, options: VirtualEnvOptions) -> None:
        super().__init__(options)

    def run(self, creator: Creator) -> None:
        if not self.enabled:
            return
        for_py_version = creator.interpreter.version_release_str
        with self.get_pip_install_cmd(creator.exe, for_py_version) as cmd:
            env = pip_wheel_env_run(self.extra_search_dir, self.app_data, self.env)
            self._execute(cmd, env)
        if os.name == "posix":
            self._rewrite_launchers(creator)

    def _rewrite_launchers(self, creator: Creator) -> None:
        selected: Final = self.distribution_to_versions()
        exe: Final = str(creator.exe).encode("utf-8")
        for distribution in distributions(path=sorted({str(creator.purelib), str(creator.platlib)})):
            if distribution.metadata["Name"].lower().replace("-", "_") not in selected:
                continue
            rows: Final = list(csv.reader(io.StringIO(distribution.read_text("RECORD") or "")))
            changed = False
            for row in rows:
                path: Final = Path(os.path.abspath(str(distribution.locate_file(row[0]))))
                if path.parent != creator.script_dir or not path.is_file():
                    continue
                content = path.read_bytes()
                if content.startswith(b"#!/bin/sh\n'''exec' "):
                    body = content.split(b"\n", 3)[3]
                elif content.startswith(b"#!" + exe + b"\n") and len(exe.split()) > 1:
                    body = content.split(b"\n", 1)[1]
                else:
                    continue
                content = shell_shebang(str(creator.exe)) + body
                mode: Final = S_IMODE(path.stat().st_mode) & 0o755
                path.unlink()
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb") as stream:
                    stream.write(content)
                row[1:] = [
                    "sha256=" + urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii"),
                    str(len(content)),
                ]
                changed = True
            if changed:
                record: Final = next(row[0] for row in rows if row[0].endswith(".dist-info/RECORD"))
                with Path(str(distribution.locate_file(record))).open("w", encoding="utf-8", newline="") as stream:
                    csv.writer(stream).writerows(rows)

    @staticmethod
    def _execute(cmd: list[str], env: dict[str, str]) -> Popen[bytes]:
        LOGGER.debug("pip seed by running: %s", LogCmd(cmd, env))
        process = Popen(cmd, env=env)
        process.communicate()
        if process.returncode != 0:
            msg = f"failed seed with code {process.returncode}"
            raise RuntimeError(msg)
        return process

    @contextmanager
    def get_pip_install_cmd(self, exe: Path, for_py_version: str) -> Generator[list[str], None, None]:
        cmd: Final = [
            str(exe),
            "-m",
            "pip",
            "-q",
            "install",
            "--only-binary",
            ":all:",
            "--disable-pip-version-check",
            "--ignore-installed",
            "--no-index",
        ]
        # Only the selected archives may participate in pip's filename-based selection.
        with TemporaryDirectory(prefix="virtualenv-seed-") as folder:
            cmd.extend(["--find-links", folder])
            for dist, version in self.distribution_to_versions().items():
                wheel: Final = get_wheel(
                    distribution=dist,
                    version=version,
                    for_py_version=for_py_version,
                    search_dirs=self.extra_search_dir,
                    download=self.download,
                    app_data=self.app_data,
                    do_periodic_update=self.periodic_update,
                    env=self.env,
                )
                if wheel is None:
                    msg: Final = f"could not get wheel for distribution {dist}"
                    raise RuntimeError(msg)
                copyfile(wheel.path, Path(folder) / wheel.name)
                cmd.append(Version.as_pip_req(dist, wheel.version))
            yield cmd


__all__ = [
    "PipInvoke",
]
