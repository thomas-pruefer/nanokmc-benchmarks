"""Exact source verification from the compact committed lock, without Git/network."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_or_verify_sources as sources

DEPENDENCY_LOCK = sources.load_dependency_lock()


class SourceLock(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nanokmc_source_lock_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(sources, "ROOT", self.root))
        self.data = b"fixed upstream fixture\n"
        self.entries = {"src/source.cpp": {"size": len(self.data), "sha256": sources.sha256(self.data)}}
        self.lock = json.loads(json.dumps(DEPENDENCY_LOCK))
        self.lock["sources"]["nanokmc"]["tree_sha256"] = sources.canonical_tree_digest(self.entries)
        self.write_lock()
        self.destination = self.root / "sources/nanokmc"
        self.file = self.destination / "src/source.cpp"

    def write_lock(self):
        (self.root / "dependencies.lock.json").write_text(json.dumps(self.lock), encoding="utf-8")

    def freeze(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(self.data)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def archive(self, entries=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in (entries or {"src/source.cpp": self.data}).items():
                if isinstance(name, str):
                    # Preserve unsafe backslashes for verification on Windows;
                    # ZipInfo's constructor normalizes them on that platform.
                    entry = zipfile.ZipInfo("entry")
                    entry.filename = name
                else:
                    entry = name
                archive.writestr(entry, data)
        return stream.getvalue()

    def git_mock(self, archive):
        def git(repo, *arguments):
            if arguments[0] == "archive":
                return archive
            return self.lock["sources"]["nanokmc"]["commit"].encode() + b"\n"
        return patch.object(sources, "git", side_effect=git)

    def test_pinned_tree_passes_and_generated_detail_is_not_required(self):
        self.freeze()
        result = sources.verify_frozen_export("nanokmc")
        self.assertEqual(result["tree_sha256"], self.lock["sources"]["nanokmc"]["tree_sha256"])
        self.assertEqual(result["file_count"], 1)
        detail = json.loads((self.root / "build/source-manifests/nanokmc.json").read_text())
        self.assertEqual(detail["files"], self.entries)
        self.assertFalse((self.root / "config/source-manifests").exists())

    def test_modified_missing_and_extra_files_each_fail(self):
        self.freeze()
        for mutation in ("modified", "missing", "extra"):
            with self.subTest(mutation=mutation):
                self.file.write_bytes(self.data)
                extra = self.destination / "unexpected.txt"
                extra.unlink(missing_ok=True)
                if mutation == "modified":
                    self.file.write_bytes(b"X" * len(self.data))
                elif mutation == "missing":
                    self.file.unlink()
                else:
                    extra.write_bytes(b"unexpected")
                before = self.snapshot()
                with self.assertRaisesRegex(RuntimeError, "does not match lock"):
                    sources.verify_frozen_export("nanokmc")
                self.assertEqual(self.snapshot(), before)

    def test_generated_manifest_cannot_authorize_changed_source(self):
        self.freeze()
        sources.verify_frozen_export("nanokmc")
        self.file.write_bytes(b"changed")
        generated = self.root / "build/source-manifests/nanokmc.json"
        generated.write_text(json.dumps({"files": {"src/source.cpp": {
            "size": 7, "sha256": sources.sha256(b"changed")}}, "tree_sha256": "0" * 64}))
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "does not match lock"):
            sources.verify_frozen_export("nanokmc", write_report=False)
        self.assertEqual(self.snapshot(), before)

    def test_canonical_digest_uses_exact_order_independent_json_definition(self):
        first = {"b": {"sha256": "abc", "size": 2}, "a": {"size": 1, "sha256": "def"}}
        second = {"a": {"sha256": "def", "size": 1}, "b": {"size": 2, "sha256": "abc"}}
        expected = sources.sha256(b'{"a":{"sha256":"def","size":1},"b":{"sha256":"abc","size":2}}')
        self.assertEqual(sources.canonical_tree_digest(first), expected)
        self.assertEqual(sources.canonical_tree_digest(second), expected)

    def test_read_only_verification_does_not_write_or_replace_reports(self):
        self.freeze()
        before = self.snapshot()
        sources.verify_frozen_export("nanokmc", write_report=False)
        self.assertEqual(self.snapshot(), before)
        sources.verify_frozen_export("nanokmc")
        generated = self.root / "build/source-manifests/nanokmc.json"
        generated.write_text("An untrusted report is not read during verification.\n")
        before = self.snapshot()
        sources.verify_frozen_export("nanokmc", write_report=False)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_lock_values_are_rejected(self):
        original = json.loads(json.dumps(self.lock))
        mutations = [lambda lock: lock.update(schema_version=1),
                     lambda lock: lock.update(unused_field=True),
                     lambda lock: lock["sources"]["nanokmc"].update(tree_sha256="invalid"),
                     lambda lock: lock["sources"]["nanokmc"].update(commit="HEAD"),
                     lambda lock: lock["sources"]["nanokmc"].update(url="C:/private/source"),
                     lambda lock: lock["sources"]["nanokmc"].update(tag="--unsafe"),
                     lambda lock: lock["sources"]["nanokmc"].update(files={}),
                     lambda lock: lock["sources"].pop("kmcos"),
                     lambda lock: lock["environments"]["benchmark"].update(unused=True),
                     lambda lock: lock["toolchains"]["ucrt64_cpp"].update(unused=True),
                     lambda lock: lock["toolchains"]["ucrt64_cpp"].update(executable_relative_to_msys2_root="C:/private/compiler.exe"),
                     lambda lock: lock["build_contract"]["kmcos"].update(unused=True),
                     lambda lock: lock["build_contract"]["nanokmc"].update(source_changes_allowed=True),
                     lambda lock: lock["model_sha256"].update({sources.MODEL_PATHS[0]: "bad"})]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.lock = json.loads(json.dumps(original))
                mutate(self.lock)
                self.write_lock()
                with self.assertRaisesRegex(RuntimeError, "Invalid dependency lock"):
                    sources.load_dependency_lock()

    def test_duplicate_json_keys_rejected(self):
        path = self.root / "dependencies.lock.json"
        path.write_text('{"schema_version":2,"schema_version":2}')
        with self.assertRaisesRegex(RuntimeError, "Duplicate dependency-lock key"):
            sources.load_dependency_lock()

    def test_reparse_point_in_frozen_tree_rejected(self):
        self.freeze()
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == self.file:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", lstat):
            with self.assertRaisesRegex(RuntimeError, "Symlink/reparse"):
                sources.verify_frozen_export("nanokmc", write_report=False)

    def test_symlink_in_frozen_tree_rejected(self):
        self.freeze()
        link = self.destination / "linked.cpp"
        try:
            os.symlink(self.file, link)
        except OSError as error:
            self.skipTest(f"Creating a test symlink is unavailable: {error}")
        with self.assertRaisesRegex(RuntimeError, "Symlink/reparse"):
            sources.verify_frozen_export("nanokmc", write_report=False)

    def test_exact_git_archive_acquires_and_verifies_actual_source(self):
        with self.git_mock(self.archive()) as git:
            result = sources.verify_export("nanokmc", self.root / "local-clone")
        self.assertEqual(self.file.read_bytes(), self.data)
        self.assertEqual(result["tree_sha256"], self.lock["sources"]["nanokmc"]["tree_sha256"])
        self.assertIn((self.root / "local-clone", "rev-parse", self.lock["sources"]["nanokmc"]["commit"] + "^{commit}"),
                      [call.args for call in git.call_args_list])
        self.assertIn((self.root / "local-clone", "rev-parse", "v0.1.0^{commit}"),
                      [call.args for call in git.call_args_list])

    def test_wrong_archive_digest_fails_before_any_source_writes(self):
        before = self.snapshot()
        with self.git_mock(self.archive({"src/source.cpp": b"modified archive"})):
            with self.assertRaisesRegex(RuntimeError, "Git export differs"):
                sources.verify_export("nanokmc", self.root / "local-clone")
        self.assertEqual(self.snapshot(), before)

    def test_read_only_export_cannot_acquire_missing_files(self):
        before = self.snapshot()
        with self.git_mock(self.archive()):
            with self.assertRaisesRegex(RuntimeError, "Read-only verification"):
                sources.verify_export("nanokmc", self.root / "local-clone", write_report=False)
        self.assertEqual(self.snapshot(), before)

    def test_unsafe_archive_paths_and_symlinks_rejected_before_writes(self):
        link = zipfile.ZipInfo("src/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        entries = [{"../escape": b"unsafe"}, {"/absolute": b"unsafe"},
                   {"C:/absolute": b"unsafe"}, {"src\\escape": b"unsafe"},
                   {"src/./source.cpp": b"unsafe"}, {link: b"../outside"}]
        for index, entry in enumerate(entries):
            with self.subTest(index=index):
                before = self.snapshot()
                with self.git_mock(self.archive(entry)):
                    with self.assertRaisesRegex(RuntimeError, "Unsafe archive|Unsupported archive"):
                        sources.verify_export("nanokmc", self.root / "local-clone")
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
