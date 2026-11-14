"""Verify and extract only Google's pinned native Linux CLI binary."""

import hashlib
from pathlib import Path
import tarfile


EXPECTED_SHA512 = (
    "f08faaee588327dba8722bdc60f26a14f6915a8f2d756ed7959cde0a706d4e62"
    "fd9f831ab1a077dace5f947caaa6e851457df5023180bb8d3f54386804a3787f"
)
EXPECTED_MEMBER = "antigravity"
EXPECTED_BINARY_BYTES = 281_432_776
MAX_ARCHIVE_BYTES = 200_000_000


def install_cli(archive: Path, output: Path) -> None:
    if not archive.is_file() or archive.is_symlink():
        raise SystemExit("native CLI archive is missing or not a regular file")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise SystemExit("native CLI archive exceeds the 200 MB build limit")

    digest = hashlib.sha512()
    with archive.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != EXPECTED_SHA512:
        raise SystemExit("native CLI archive SHA-512 mismatch")

    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) != 1 or members[0].name != EXPECTED_MEMBER:
                raise SystemExit("native CLI archive has an unexpected binary inventory")
            member = members[0]
            if not member.isfile() or member.issym() or member.islnk():
                raise SystemExit("native CLI archive member is not a regular file")
            if member.size != EXPECTED_BINARY_BYTES:
                raise SystemExit("native CLI binary size mismatch")
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise SystemExit("native CLI archive member could not be read")
            with extracted, output.open("xb") as target:
                remaining = member.size
                while remaining:
                    chunk = extracted.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise SystemExit("native CLI archive member ended unexpectedly")
                    target.write(chunk)
                    remaining -= len(chunk)
            output.chmod(0o555)
    finally:
        archive.unlink(missing_ok=True)


def main() -> None:
    install_cli(Path("/tmp/agy-cli.tar.gz"), Path("/opt/agy/agy"))


if __name__ == "__main__":
    main()
