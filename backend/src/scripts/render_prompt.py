"""Render the canonical agent system prompt from the prompt manifest."""

from __future__ import annotations

from agent.runtime.prompt_assembly import write_rendered_prompt


def main() -> None:
    rendered_path = write_rendered_prompt()
    print(rendered_path)


if __name__ == "__main__":
    main()
