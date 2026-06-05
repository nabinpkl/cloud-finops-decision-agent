"""Public index builder facade and CLI entrypoint."""

from __future__ import annotations

from normalize.indexing.build import BuildResult, SUPPORTED_PROVIDERS, build_provider

__all__ = ["BuildResult", "SUPPORTED_PROVIDERS", "build_provider"]


def main() -> None:
    from normalize.indexing.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
