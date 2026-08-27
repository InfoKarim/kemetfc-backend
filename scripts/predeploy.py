import subprocess
import sys


def main() -> int:
    migrate = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"])
    if migrate.returncode != 0:
        return migrate.returncode

    check = subprocess.run([sys.executable, "-m", "scripts.production_check"])
    return check.returncode


if __name__ == "__main__":
    raise SystemExit(main())
