"""Persistent folder configuration, validated off the GUI thread."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class Folders:
    incoming: str
    originals: str
    processed: str
    export: str

    def as_dict(self):
        return asdict(self)

    def validate(self):
        paths = []
        for role, value in self.as_dict().items():
            if not value.strip():
                raise ValueError(f"Choose a {role.title()} folder.")
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise ValueError(f"{role.title()} must be an absolute path.")
            paths.append(path.resolve())
        for i, path in enumerate(paths):
            for other in paths[:i]:
                if path == other or path in other.parents or other in path.parents:
                    raise ValueError("The four folders must be separate, with none inside another.")
        if not paths[0].is_dir():
            raise ValueError("Incoming must be an existing readable folder.")
        # Read-only inspection: never create a probe in Incoming.
        with os.scandir(paths[0]) as entries:
            next(entries, None)
        for path in paths[1:]:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
        return Folders(*(str(path) for path in paths))
