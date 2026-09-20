"""Run the persisted visual flow plus dry-run delivery; no external publishing."""

from app.rendering.acceptance import main

if __name__ == "__main__":
    main(delivery=True)
