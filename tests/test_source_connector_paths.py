from __future__ import annotations

from pathlib import Path

import pytest

from sources.base import (
    SourceError,
    content_sha256,
    prepare_source_cache_directory,
    prepare_verified_staging,
)
from sources.github_repo import GitHubRepoSource
from sources.local_dir import LocalDirectorySource
from sources.runner import SourceSpec, SourceSyncer


def test_local_directory_rejects_symlink_candidate_escaping_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "secret.md"
    target.write_text("secret", encoding="utf-8")
    link = root / "linked.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    source = LocalDirectorySource(str(root), extensions=[".md"])
    with pytest.raises(SourceError, match="符号链接|重解析点|越界"):
        list(source.fetch(tmp_path / "work"))


def test_local_directory_rejects_mocked_reparse_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    document = root / "guide.md"
    document.write_text("guide", encoding="utf-8")

    monkeypatch.setattr(
        "sources.local_dir._is_reparse_point",
        lambda path: path.name == "guide.md",
    )
    source = LocalDirectorySource(str(root), extensions=[".md"])
    with pytest.raises(SourceError, match="重解析点"):
        list(source.fetch(tmp_path / "work"))


def test_github_cache_boundary_uses_path_ancestry_not_string_prefix(tmp_path: Path) -> None:
    workdir = tmp_path / "cache"
    workdir.mkdir()
    source = GitHubRepoSource("owner/docs")

    with pytest.raises(SourceError, match="路径越界"):
        source._safe_dest(workdir, "../cache-evil/guide.md")


def test_local_directory_normal_file_still_reads_from_resolved_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    document = root / "guide.md"
    document.write_text("guide", encoding="utf-8")

    generator = LocalDirectorySource(str(root), extensions=[".md"]).fetch(tmp_path / "work")
    fetched = next(generator)
    staged = fetched.local_path
    assert staged != document.resolve()
    assert staged.read_bytes() == b"guide"
    assert fetched.rel_path == "guide.md"
    generator.close()
    assert not staged.exists()



def test_local_secure_open_rejects_replacement_between_check_and_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    candidate = root / "guide.md"
    candidate.write_text("safe", encoding="utf-8")
    secret = outside / "secret.md"
    secret.write_text("secret", encoding="utf-8")

    from sources import local_dir

    original = local_dir._secure_read_file
    replaced = False

    def replace_then_open(path: Path, resolved_root: Path):
        nonlocal replaced
        if not replaced:
            replaced = True
            path.unlink()
            try:
                path.symlink_to(secret)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlink unavailable: {exc}")
        return original(path, resolved_root)

    monkeypatch.setattr(local_dir, "_secure_read_file", replace_then_open)
    with pytest.raises(SourceError, match="符号链接|重解析点|越界|安全打开"):
        list(LocalDirectorySource(str(root), extensions=[".md"]).fetch(tmp_path / "work"))



def test_parser_consumes_verified_staging_after_original_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    workdir = tmp_path / "work"
    root.mkdir()
    outside.mkdir()
    original = root / "guide.md"
    original.write_text("verified-safe", encoding="utf-8")
    malicious = outside / "malicious.md"
    malicious.write_text("outside-malicious", encoding="utf-8")

    generator = LocalDirectorySource(str(root), extensions=[".md"]).fetch(workdir)
    fetched = next(generator)
    staged = fetched.local_path
    original.unlink()
    try:
        original.symlink_to(malicious)
    except (OSError, NotImplementedError):
        original.write_text("replacement-malicious", encoding="utf-8")

    class ParserPipeline:
        consumed: bytes = b""

        def add_file(self, path: str, **_kwargs):
            self.consumed = Path(path).read_bytes()
            return type("Result", (), {"chunk_count": 1})()

    parser = ParserPipeline()
    parser.add_file(str(fetched.local_path))
    assert parser.consumed == b"verified-safe"
    assert fetched.content_hash == content_sha256(b"verified-safe")
    assert staged.is_relative_to((workdir / "_verified").resolve())
    generator.close()
    assert not staged.exists()



def test_source_cache_never_uses_traversal_or_absolute_source_name(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    syncer = SourceSyncer(object(), cache_root, state_mode="json")
    for name in ("../../outside", str((tmp_path / "absolute").resolve())):
        spec = SourceSpec(name=name, type="local_dir", dataset_id="dataset")
        source_dir = syncer._source_cache_dir(spec)
        assert source_dir.is_relative_to(cache_root.resolve())
        assert ".." not in source_dir.name
        assert name not in str(source_dir)


def test_source_cache_rejects_unsafe_source_id(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="safe immutable"):
        prepare_source_cache_directory(tmp_path / "cache", "../../source")


def test_verified_staging_rejects_existing_symlink(tmp_path: Path) -> None:
    workdir = tmp_path / "cache" / "source-safe" / "files"
    outside = tmp_path / "outside"
    workdir.mkdir(parents=True)
    outside.mkdir()
    link = workdir / "_verified"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    with pytest.raises(SourceError, match="symlink|reparse"):
        prepare_verified_staging(workdir, ttl_seconds=60)


def test_verified_staging_ttl_sweep_removes_old_and_retains_new(tmp_path: Path) -> None:
    workdir = tmp_path / "cache" / "source-safe" / "files"
    verified = prepare_verified_staging(workdir, ttl_seconds=60, now=1000)
    old = verified / "old.md"
    new = verified / "new.md"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    import os

    os.utime(old, (900, 900))
    os.utime(new, (980, 980))
    verified.chmod(0o755)
    prepare_verified_staging(workdir, ttl_seconds=60, now=1000)
    assert not old.exists()
    assert new.exists()
    if os.name != "nt":
        assert verified.stat().st_mode & 0o777 == 0o700



def test_secure_open_rejects_root_replaced_by_symlink_before_handle_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    original_root = tmp_path / "root-original"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "guide.md").write_text("safe", encoding="utf-8")
    (outside / "guide.md").write_text("malicious", encoding="utf-8")

    from sources import local_dir

    secure_open = local_dir._secure_read_file
    replaced = False

    def replace_root_then_open(candidate: Path, resolved_root: Path):
        nonlocal replaced
        if not replaced:
            replaced = True
            root.rename(original_root)
            try:
                root.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                original_root.rename(root)
                pytest.skip(f"directory symlink unavailable: {exc}")
        return secure_open(candidate, resolved_root)

    monkeypatch.setattr(local_dir, "_secure_read_file", replace_root_then_open)
    with pytest.raises(SourceError, match="openat|reparse|越界|安全打开"):
        list(LocalDirectorySource(str(root), extensions=[".md"]).fetch(tmp_path / "work"))
