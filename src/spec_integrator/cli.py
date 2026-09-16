"""Command-line entry point for spec-integrator."""

from spec_integrator.application.commands import (
    _SUBPARSER_BUILDERS,
    cmd_build,
    cmd_check_doc,
    cmd_check_src,
    cmd_format_doc,
    cmd_format_src,
    cmd_graph,
    cmd_init,
    cmd_llm_judge,
    cmd_llm_keyword_review,
    cmd_llm_single_review,
    cmd_llm_word,
    cmd_risk,
    main,
)

__all__ = [
    "_SUBPARSER_BUILDERS",
    "cmd_build",
    "cmd_check_doc",
    "cmd_check_src",
    "cmd_format_doc",
    "cmd_format_src",
    "cmd_graph",
    "cmd_init",
    "cmd_llm_judge",
    "cmd_llm_keyword_review",
    "cmd_llm_single_review",
    "cmd_llm_word",
    "cmd_risk",
    "main",
]


if __name__ == "__main__":
    main()
