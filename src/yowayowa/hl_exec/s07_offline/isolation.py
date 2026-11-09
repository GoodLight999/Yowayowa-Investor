# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""dry-run 出力先の隔離（設計書 §3・S07-A02）。

許可先は呼出側が明示した一時 dir 直下の ``dry-run/<run_id>/`` **だけ**である。
実台帳（``data/`` 配下）・既存 state・既存レポートは開かない。文字列 prefix だけに
依存せず、realpath・link・inode・親ディレクトリ実体を検査し、書込は
``O_CREAT|O_EXCL|O_NOFOLLOW`` の OS 強制で行う。

負の対照（すべて拒否する）:

* 実台帳と同じ path、実台帳の親/子 path
* symlink 経由の別名（base の途中・dry-run・run dir・出力ファイル）
* hardlink（``st_nlink > 1``）された出力ファイル
* 相対 path・``..`` による脱出・出力名のパス混入
* 既存 run root の再利用（run ごとに新規ルート）
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

DRY_RUN_DIRNAME = "dry-run"
OUTPUT_FILES = ("state.json", "events.jsonl", "report.json", "report.md")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class IsolationError(RuntimeError):
    """dry-run 出力先が隔離契約を満たさない（fail closed）。"""


def default_forbidden_roots(repo_root: str | Path) -> tuple[Path, ...]:
    """既定の禁止領域。``data/`` 全体（実台帳・既存 state・既存レポート）を含む。"""
    return (Path(repo_root).resolve(strict=False) / "data",)


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _assert_not_forbidden(
    target: Path, forbidden_roots: Iterable[str | Path], *, what: str
) -> None:
    resolved = target.resolve(strict=False)
    for root in forbidden_roots:
        forbidden = Path(root).resolve(strict=False)
        if _overlaps(resolved, forbidden):
            raise IsolationError(
                f"{what} {resolved} overlaps the forbidden real-ledger root {forbidden}; "
                "dry-run output must be physically separate"
            )


def _assert_no_symlink_components(path: Path, *, what: str) -> None:
    """path とその既存の親要素に symlink が無いことを lstat で確認する。"""
    parts: list[Path] = []
    current = path
    while True:
        parts.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for component in reversed(parts):
        if not component.exists() and not component.is_symlink():
            continue
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise IsolationError(f"{what} contains a symlink component: {component}")


@dataclass(frozen=True)
class IsolatedRunRoot:
    """検証済みの dry-run ルート。出力はこの下の 4 ファイルだけに限定する。"""

    base: Path
    run_id: str
    root: Path

    @classmethod
    def create(
        cls,
        base_dir: str | Path,
        run_id: str,
        *,
        forbidden_roots: Iterable[str | Path] = (),
    ) -> IsolatedRunRoot:
        base = Path(base_dir)
        if not str(base_dir).strip():
            raise IsolationError("dry-run base dir must be explicitly provided (fail closed)")
        if not base.is_absolute():
            raise IsolationError(f"dry-run base dir must be absolute: {base_dir!r}")
        if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
            raise IsolationError(
                "run_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ "
                f"(no slashes, no '..'): {run_id!r}"
            )
        if not base.exists():
            raise IsolationError(f"dry-run base dir does not exist: {base}")
        if not base.is_dir():
            raise IsolationError(f"dry-run base dir is not a directory: {base}")
        _assert_no_symlink_components(base, what="dry-run base dir")

        forbidden = tuple(Path(root) for root in forbidden_roots)
        root = base / DRY_RUN_DIRNAME / run_id
        _assert_not_forbidden(root, forbidden, what="dry-run run root")
        if root.exists() or root.is_symlink():
            raise IsolationError(f"run root already exists; every run needs a fresh root: {root}")

        container = base / DRY_RUN_DIRNAME
        if container.exists():
            if container.is_symlink() or not container.is_dir():
                raise IsolationError(f"dry-run container is not a real directory: {container}")
        else:
            container.mkdir(mode=0o700)
            os.chmod(container, 0o700)
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)

        resolved = root.resolve(strict=True)
        if resolved != root.resolve(strict=False):
            raise IsolationError(
                f"run root resolves elsewhere through a link: {root} -> {resolved}"
            )
        _assert_not_forbidden(resolved, forbidden, what="resolved dry-run run root")
        return cls(base=base.resolve(strict=True), run_id=run_id, root=resolved)

    def path_for(self, name: str) -> Path:
        """許可された固定ファイル名だけを解決する。"""
        if name not in OUTPUT_FILES:
            raise IsolationError(f"output name must be one of {list(OUTPUT_FILES)}, got {name!r}")
        candidate = self.root / name
        if candidate.is_symlink():
            raise IsolationError(f"refusing symlinked output file: {candidate}")
        if candidate.exists():
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise IsolationError(f"output path is not a regular file: {candidate}")
            if info.st_nlink > 1:
                raise IsolationError(
                    f"refusing hardlinked output file (st_nlink={info.st_nlink}): {candidate}"
                )
        parent = candidate.parent
        if parent.resolve(strict=True) != self.root:
            raise IsolationError(f"output path escapes the run root: {candidate}")
        if parent.stat().st_ino != self.root.stat().st_ino:
            raise IsolationError(f"output parent inode differs from the run root: {parent}")
        return candidate

    def open_exclusive(self, name: str, *, mode: str = "w") -> IO[str]:
        """新規作成のみ・symlink 追従なし・0600 の OS 強制で開く。"""
        path = self.path_for(name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise IsolationError(f"failed to create isolated output {path}: {exc}") from exc
        return os.fdopen(fd, mode, encoding="utf-8", newline="\n")


def guard_input_file(path: str | Path, *, forbidden_roots: Iterable[str | Path]) -> Path:
    """入力ファイルが実台帳領域を指していないことを確認する（読取前の gate）。"""
    candidate = Path(path)
    if not candidate.is_absolute():
        raise IsolationError(f"input path must be absolute: {path!r}")
    _assert_not_forbidden(candidate, forbidden_roots, what="input file")
    if candidate.is_symlink():
        raise IsolationError(f"input file must not be a symlink: {candidate}")
    if not candidate.is_file():
        raise IsolationError(f"input file does not exist: {candidate}")
    return candidate


@dataclass(frozen=True)
class TreeEntry:
    path: str
    inode: int
    size: int
    mtime_ns: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "inode": self.inode,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
        }


def tree_snapshot(root: str | Path) -> dict[str, TreeEntry]:
    """ディレクトリ配下の一覧・inode・size・mtime・sha256 を読み取り専用で取る。

    実台帳の不変性を主張するためではなく、**模擬台帳**が動かなかったことを
    テストで示すための補助である。
    """
    base = Path(root)
    result: dict[str, TreeEntry] = {}
    for path in sorted(base.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        info = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[str(path.relative_to(base))] = TreeEntry(
            path=str(path.relative_to(base)),
            inode=int(info.st_ino),
            size=int(info.st_size),
            mtime_ns=int(info.st_mtime_ns),
            sha256=digest,
        )
    return result
