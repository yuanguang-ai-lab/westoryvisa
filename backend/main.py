"""Primary standalone backend API entry point."""

from .application import ApiHandler as Handler
from .application import main

__all__ = ["Handler", "main"]


if __name__ == "__main__":
    main()
