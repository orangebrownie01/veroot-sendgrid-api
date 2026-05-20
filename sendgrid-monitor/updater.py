import os
import sys
import time
import shutil
import hashlib
import subprocess


def wait_for_file_unlock(path, timeout=30):
    start = time.time()

    while time.time() - start < timeout:
        try:
            with open(path, "a"):
                return True
        except PermissionError:
            time.sleep(1)

    return False


def try_move(src, dst, retries=10, delay=1):
    """Retry shutil.move — Windows may still block briefly after unlock check."""
    for attempt in range(retries):
        try:
            shutil.move(src, dst)
            return True
        except (PermissionError, OSError):
            if attempt < retries - 1:
                time.sleep(delay)
    return False


def verify_checksum(path, expected_sha256):
    """Return True if file matches expected SHA-256 hex digest (or if no hash provided)."""
    if not expected_sha256:
        return True

    h = hashlib.sha256()

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)

    return h.hexdigest().lower() == expected_sha256.lower()


def write_error(app_dir, message):
    error_log = os.path.join(app_dir, "update_error.txt")

    with open(error_log, "w") as f:
        f.write(message)


def main():
    # Args: new_exe  target_exe  [expected_sha256]
    if len(sys.argv) < 3:
        write_error(
            os.path.dirname(sys.argv[0]) if sys.argv else ".",
            "Updater called with insufficient arguments."
        )
        return

    new_exe = sys.argv[1]
    target_exe = sys.argv[2]
    expected_sha256 = sys.argv[3] if len(sys.argv) >= 4 else None

    app_dir = os.path.dirname(target_exe)

    time.sleep(2)

    if not wait_for_file_unlock(target_exe, timeout=30):
        write_error(app_dir, f"Timed out waiting for {target_exe} to be released.")
        return

    # Verify the downloaded file before touching anything
    if not verify_checksum(new_exe, expected_sha256):
        write_error(app_dir, "Update aborted: SHA-256 checksum mismatch. The download may be corrupted.")
        return

    backup_exe = target_exe + ".bak"

    try:
        if os.path.exists(backup_exe):
            os.remove(backup_exe)

        if os.path.exists(target_exe):
            if not try_move(target_exe, backup_exe):
                raise Exception(f"Could not move {target_exe} to backup.")

        if not try_move(new_exe, target_exe):
            raise Exception(f"Could not move update into place at {target_exe}.")

        subprocess.Popen(
            [target_exe],
            cwd=app_dir
        )

    except Exception as e:
        write_error(app_dir, str(e))

        # Attempt rollback
        if os.path.exists(backup_exe) and not os.path.exists(target_exe):
            try:
                shutil.move(backup_exe, target_exe)
                write_error(app_dir, str(e) + "\n\nRollback to backup succeeded.")
            except Exception as rollback_err:
                write_error(app_dir, str(e) + f"\n\nRollback also failed: {rollback_err}")


if __name__ == "__main__":
    main()
