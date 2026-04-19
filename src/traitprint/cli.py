"""Click CLI entrypoint for traitprint."""

from __future__ import annotations

import click

from traitprint import __version__
from traitprint.git_ops import commit, init_repo
from traitprint.vault import VaultStore


@click.group()
@click.version_option(version=__version__, prog_name="traitprint")
def cli() -> None:
    """Traitprint -- local-first career identity vault for the agent era."""


@cli.command()
@click.option(
    "--path",
    type=click.Path(),
    default=None,
    help="Vault directory (default: ~/.traitprint)",
)
def init(path: str | None) -> None:
    """Create a new Traitprint vault."""
    store = VaultStore(path)
    vault_dir = store.directory

    if store.exists():
        click.echo(f"Vault already exists at {vault_dir}")
        return

    # Create directory
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Write .gitignore
    gitignore = vault_dir / ".gitignore"
    gitignore.write_text(".credentials\n", encoding="utf-8")

    # Initialize git repo
    init_repo(vault_dir)

    # Write empty vault
    vault = store.create_empty()
    store.save(vault)

    # Initial commit
    commit(vault_dir, "traitprint init")

    click.echo(f"Vault initialized at {vault_dir}")


@cli.group()
def vault() -> None:
    """Manage your career identity vault (Slice B)."""


@vault.command(name="show")
def vault_show() -> None:
    """Show vault summary (coming in Slice B)."""
    click.echo("Vault commands coming in Slice B.")


@cli.command(name="mcp-serve")
def mcp_serve() -> None:
    """Start the MCP server (coming in v0.1.0)."""
    click.echo("MCP server coming in v0.1.0")
