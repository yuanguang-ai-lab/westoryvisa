"""Standalone backend API entry point."""

from .application import ApiHandler, run_server

__all__ = ["ApiHandler", "run_server"]


def main():
    run_server(default_port=4176)


if __name__ == "__main__":
    main()
