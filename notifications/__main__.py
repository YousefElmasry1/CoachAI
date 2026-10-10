"""Execute the notification worker as a dedicated process."""

from .worker_runtime import main


if __name__ == "__main__":
    raise SystemExit(main())
