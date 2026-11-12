"""Read only producer-approved artifacts, without path traversal or symlink races."""

import os
import stat
from pathlib import PurePosixPath


def read_artifact(root, run_id, relative, kind):
    parts = PurePosixPath(relative).parts
    if (not parts or PurePosixPath(relative).is_absolute()
            or any(part in {".", ".."} for part in parts) or any(char in relative for char in "\\:%")
            or kind not in {"masked_screenshot", "sanitized_report"}):
        raise KeyError("artifact_not_found")
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for part in (run_id, *parts[:-1]):
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        limit = 10 * 1024 * 1024 if kind == "masked_screenshot" else 1024 * 1024
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise KeyError("artifact_not_found")
        chunks, size = [], 0
        while data := os.read(descriptor, min(65536, limit + 1 - size)):
            chunks.append(data)
            size += len(data)
            if size > limit:
                raise KeyError("artifact_not_found")
        return b"".join(chunks), "image/png" if kind == "masked_screenshot" else "application/json"
    except OSError:
        raise KeyError("artifact_not_found") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
