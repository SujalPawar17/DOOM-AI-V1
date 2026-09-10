"""Bounded filesystem observation. No recursive dump. No content hashing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Any

from proactive.computer.fs.paths import identity_of
from proactive.computer.fs.types import FsChild, FsObservation, ObjectType
from proactive.computer.policy import FS_SCHEMA_VERSION, fs_max_list


def observation_hash(fields: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(fields), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def observe_path(resolved: Path) -> FsObservation:
    ident = identity_of(resolved)
    obs = FsObservation(
        canonical_path=ident.canonical_path,
        object_type=ident.object_type.value,
        exists=ident.exists,
        size_bytes=ident.size_bytes,
        mtime_ns=ident.mtime_ns,
        file_index=ident.file_index,
        schema_version=FS_SCHEMA_VERSION,
    )
    if ident.object_type == ObjectType.DIRECTORY and ident.exists:
        cap = int(fs_max_list())
        children = []
        try:
            names = sorted(resolved.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            names = []
        if len(names) > cap:
            obs.truncated = True
            names = names[:cap]
        for child in names:
            try:
                if child.is_dir() and not child.is_symlink():
                    ctype = ObjectType.DIRECTORY.value
                    sz = 0
                elif child.is_file():
                    ctype = ObjectType.FILE.value
                    sz = int(child.stat().st_size)
                else:
                    ctype = ObjectType.OTHER.value
                    sz = 0
            except OSError:
                ctype = ObjectType.OTHER.value
                sz = 0
            children.append(FsChild(name=child.name[:128], object_type=ctype, size_bytes=sz))
        obs.children = children
    obs.observation_hash = observation_hash(obs.as_authoritative())
    return obs
