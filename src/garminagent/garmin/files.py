"""Activity file downloads: Garmin serves the original file as a zip (M0); inside is
expected to be one FIT file per activity (several for some multisport activities)."""

import io
import zipfile
import zlib
from dataclasses import dataclass, field

from garminagent.garmin.errors import UnexpectedFileError

FIT_SIGNATURE = b".FIT"
# Guard against decompression bombs; real activity FIT files are a few MB at most.
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class ActivityFile:
    activity_id: int
    original: bytes = field(repr=False)  # exactly what Garmin sent, for the raw store
    fit_files: dict[str, bytes] = field(repr=False)  # member name -> FIT bytes


def is_fit(content: bytes) -> bool:
    return len(content) >= 12 and content[8:12] == FIT_SIGNATURE


def parse_activity_file(activity_id: int, content: bytes) -> ActivityFile:
    if is_fit(content):
        return ActivityFile(activity_id, content, {f"{activity_id}.fit": content})
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise UnexpectedFileError(f"activity {activity_id}: download is neither a zip nor a FIT file", "not_zip_or_fit")

    # A zip can pass is_zipfile and still fail to read: a bad CRC, an encrypted member, an
    # unsupported compression method, or a bomb that understates its size (zipfile stops at the
    # declared size). Each must mark the file invalid; an exception escaping here would fail
    # every run on the same file.
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
                raise UnexpectedFileError(
                    f"activity {activity_id}: zip expands beyond {MAX_UNCOMPRESSED_BYTES} bytes", "too_large"
                )
            fit_files = {m.filename: archive.read(m) for m in members if m.filename.lower().endswith(".fit")}
    except UnexpectedFileError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        OSError,
        ValueError,  # e.g. UnicodeDecodeError: the UTF-8 name flag set on a name that isn't UTF-8
        zlib.error,
    ) as exc:
        raise UnexpectedFileError(f"activity {activity_id}: zip can't be read", "corrupt_zip") from exc

    if not fit_files:
        names = sorted(m.filename.rsplit(".", 1)[-1] for m in members)
        raise UnexpectedFileError(
            f"activity {activity_id}: zip has no .fit member (extensions: {names})", "no_fit_member"
        )
    for name, data in fit_files.items():
        if not is_fit(data):
            raise UnexpectedFileError(f"activity {activity_id}: {name!r} lacks the FIT signature", "bad_fit_signature")
    return ActivityFile(activity_id, content, fit_files)
