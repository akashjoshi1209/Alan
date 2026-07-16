"""Path utilities."""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Generator, List, Optional, Union
from contextlib import contextmanager
import fnmatch


def expand_path(path: Union[str, Path]) -> Path:
    """Expand user and environment variables in path."""
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()


def ensure_dir(path: Union[str, Path], mode: int = 0o755) -> Path:
    """Ensure directory exists."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    return path


def ensure_file(path: Union[str, Path], content: str = "", mode: int = 0o644) -> Path:
    """Ensure file exists with content."""
    path = Path(path)
    ensure_dir(path.parent)
    if not path.exists():
        path.write_text(content)
        path.chmod(mode)
    return path


def safe_join(base: Union[str, Path], *paths: str) -> Path:
    """Safely join paths, preventing directory traversal."""
    base = Path(base).resolve()
    target = (base / Path(*paths)).resolve()

    if not target.is_relative_to(base):
        raise ValueError(f"Path {target} is outside base {base}")

    return target


def find_upwards(
    filename: str,
    start: Optional[Union[str, Path]] = None,
    limit: Optional[int] = None,
) -> Optional[Path]:
    """Find file by searching upwards from start directory."""
    current = Path(start).resolve() if start else Path.cwd()
    count = 0

    while current != current.parent:
        target = current / filename
        if target.exists():
            return target

        if limit is not None and count >= limit:
            break

        current = current.parent
        count += 1

    return None


def find_files(
    root: Union[str, Path],
    pattern: str = "**/*",
    recursive: bool = True,
) -> List[Path]:
    """Find files matching pattern."""
    root = Path(root)
    if recursive:
        return list(root.glob(pattern))
    return list(root.glob(pattern))


def copy_tree(
    src: Union[str, Path],
    dst: Union[str, Path],
    ignore: Optional[List[str]] = None,
    overwrite: bool = False,
) -> None:
    """Copy directory tree."""
    src = Path(src)
    dst = Path(dst)

    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    if dst.exists() and not overwrite:
        raise FileExistsError(f"Destination exists: {dst}")

    if ignore:
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns(*ignore),
            dirs_exist_ok=overwrite,
        )
    else:
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)


def move_tree(
    src: Union[str, Path],
    dst: Union[str, Path],
    overwrite: bool = False,
) -> None:
    """Move directory tree."""
    src = Path(src)
    dst = Path(dst)

    if dst.exists() and not overwrite:
        raise FileExistsError(f"Destination exists: {dst}")

    if dst.exists():
        shutil.rmtree(dst)

    shutil.move(str(src), str(dst))


def remove_tree(path: Union[str, Path]) -> None:
    """Remove directory tree."""
    shutil.rmtree(path, ignore_errors=True)


def get_size(path: Union[str, Path]) -> int:
    """Get size of file or directory in bytes."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size

    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def format_size(size: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} EB"


def get_free_space(path: Union[str, Path]) -> int:
    """Get free disk space in bytes."""
    stat = shutil.disk_usage(path)
    return stat.free


def get_relative_path(path: Union[str, Path], base: Union[str, Path]) -> Path:
    """Get relative path from base."""
    path = Path(path).resolve()
    base = Path(base).resolve()
    return path.relative_to(base)


def is_subpath(path: Union[str, Path], parent: Union[str, Path]) -> bool:
    """Check if path is subpath of parent."""
    path = Path(path).resolve()
    parent = Path(parent).resolve()
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def common_prefix(*paths: Union[str, Path]) -> Path:
    """Find common prefix of paths."""
    resolved = [Path(p).resolve() for p in paths]
    if not resolved:
        return Path(".")

    parts = [p.parts for p in resolved]
    common = []
    for parts_group in zip(*parts):
        if len(set(parts_group)) == 1:
            common.append(parts_group[0])
        else:
            break
    return Path(*common)


@contextmanager
def temp_dir(prefix: str = "tmp", suffix: str = "") -> Generator[Path, None, None]:
    """Create temporary directory context."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix, suffix=suffix))
    try:
        yield tmp
    finally:
        remove_tree(tmp)


@contextmanager
def temp_file(
    prefix: str = "tmp",
    suffix: str = "",
    mode: str = "w",
    encoding: str = "utf-8",
) -> Generator[Path, None, None]:
    """Create temporary file context."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    try:
        yield Path(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def atomic_write(
    path: Union[str, Path],
    content: Union[str, bytes],
    mode: str = "w",
    encoding: str = "utf-8",
) -> None:
    """Write file atomically using temp file + rename."""
    path = Path(path)
    ensure_dir(path.parent)

    with temp_file(suffix=path.suffix, mode=mode, encoding=encoding) as tmp:
        if isinstance(content, bytes):
            tmp.write_bytes(content)
        else:
            tmp.write_text(content, encoding=encoding)
        os.replace(tmp, path)


def read_lines(path: Union[str, Path], encoding: str = "utf-8") -> List[str]:
    """Read file lines."""
    return Path(path).read_text(encoding=encoding).splitlines()


def write_lines(
    path: Union[str, Path],
    lines: List[str],
    encoding: str = "utf-8",
    newline: str = "\n",
) -> None:
    """Write lines to file."""
    content = newline.join(lines)
    if not content.endswith(newline):
        content += newline
    Path(path).write_text(content, encoding=encoding)


def touch(path: Union[str, Path]) -> Path:
    """Create empty file or update timestamp."""
    path = Path(path)
    path.touch(exist_ok=True)
    return path


def find_executable(name: str) -> Optional[Path]:
    """Find executable in PATH."""
    path = shutil.which(name)
    return Path(path) if path else None


def normalize_path(path: Union[str, Path]) -> Path:
    """Normalize path (resolve, expand user, expand vars)."""
    return expand_path(path).resolve()


def relative_to_cwd(path: Union[str, Path]) -> Path:
    """Get path relative to current working directory."""
    return Path(path).resolve().relative_to(Path.cwd())


def watch_paths(
    paths: List[Union[str, Path]],
    patterns: Optional[List[str]] = None,
    recursive: bool = True,
):
    """Watch paths for changes (generator yielding events)."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent

    class EventHandler(FileSystemEventHandler):
        def __init__(self, patterns):
            self.patterns = patterns or ["*"]

        def should_handle(self, event: FileSystemEvent) -> bool:
            if event.is_directory:
                return True
            return any(fnmatch.fnmatch(event.src_path, p) for p in self.patterns)

        def on_any_event(self, event: FileSystemEvent):
            if self.should_handle(event):
                self.events.append(event)

    handler = EventHandler(patterns)
    handler.events = []

    observer = Observer()
    for path in paths:
        observer.schedule(handler, str(path), recursive=recursive)

    observer.start()
    try:
        while True:
            if handler.events:
                yield handler.events.pop(0)
            else:
                time.sleep(0.1)
    finally:
        observer.stop()
        observer.join()


def copy_file(
    src: Union[str, Path],
    dst: Union[str, Path],
    overwrite: bool = False,
) -> Path:
    """Copy file."""
    src = Path(src)
    dst = Path(dst)

    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")

    if dst.exists() and not overwrite:
        raise FileExistsError(f"Destination exists: {dst}")

    ensure_dir(dst.parent)
    shutil.copy2(src, dst)
    return dst


def move_file(
    src: Union[str, Path],
    dst: Union[str, Path],
    overwrite: bool = False,
) -> Path:
    """Move file."""
    src = Path(src)
    dst = Path(dst)

    if dst.exists() and not overwrite:
        raise FileExistsError(f"Destination exists: {dst}")

    ensure_dir(dst.parent)
    shutil.move(str(src), str(dst))
    return dst


def read_json(path: Union[str, Path]) -> dict:
    """Read JSON file."""
    import json
    return json.loads(Path(path).read_text())


def write_json(
    path: Union[str, Path],
    data: dict,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """Write JSON file."""
    import json
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=ensure_ascii))


def read_yaml(path: Union[str, Path]) -> dict:
    """Read YAML file."""
    import yaml
    return yaml.safe_load(Path(path).read_text())


def write_yaml(path: Union[str, Path], data: dict) -> None:
    """Write YAML file."""
    import yaml
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(yaml.dump(data, default_flow_style=False))


def find_config_file(
    names: List[str],
    start: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """Find config file by searching upwards."""
    for name in names:
        found = find_upwards(name, start)
        if found:
            return found
    return None