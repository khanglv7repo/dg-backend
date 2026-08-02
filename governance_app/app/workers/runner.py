"""Deprecated compatibility entrypoint. Use execution_worker or agent_worker explicitly."""

from app.workers.execution_worker import main


if __name__ == "__main__":
    main()
