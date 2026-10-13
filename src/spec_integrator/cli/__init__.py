"""Public command-line entry point."""

from spec_integrator.cli.dispatch import main
from spec_integrator.cli.parser import build_parser

__all__ = ["build_parser", "main"]
