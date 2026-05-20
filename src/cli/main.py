"""SpongeBot CLI -- Click-based command interface with Rich output."""

import random
import time

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from src.cli.splash import (
    BOOT_MESSAGES,
    BUU_ASCII,
    SPONGEBOT_SPLASH,
    random_buu_quote,
    random_celebration,
)
from src.cli.themes import (
    RICH_THEME,
    STYLES,
    status_dot,
)

console = Console(theme=RICH_THEME)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0", prog_name="SpongeBot")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """SpongeBot -- Absorption-Based AI Agent Framework.  Anthropic Claude Only."""
    ctx.ensure_object(dict)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--model",
    default="claude-sonnet-4-20250514",
    help="Claude model to use.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output.")
def run(model: str, verbose: bool) -> None:
    """Start SpongeBot in interactive mode."""

    # -- splash --
    console.print(
        Panel(
            Text(SPONGEBOT_SPLASH, style="bold yellow"),
            border_style="cyan",
            title="[bold magenta]Majin Buu Presents[/bold magenta]",
            subtitle="[dim]v0.1.0[/dim]",
        )
    )

    # -- boot sequence --
    console.print()
    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        for msg in BOOT_MESSAGES:
            task = progress.add_task(msg, total=None)
            time.sleep(random.uniform(0.15, 0.35))
            progress.remove_task(task)

    console.print(f"[{STYLES['success']}]All systems nominal.[/{STYLES['success']}]")
    console.print(f"[{STYLES['buu']}]{random_buu_quote()}[/{STYLES['buu']}]")
    console.print()
    console.print(f"[{STYLES['info']}]Model:[/{STYLES['info']}] {model}")
    console.print(
        f"[{STYLES['dim']}]Type 'help' for commands, 'quit' to exit.[/{STYLES['dim']}]"
    )
    console.print()

    # -- REPL --
    _repl_loop(verbose=verbose)


def _repl_loop(verbose: bool = False) -> None:
    """Simple REPL loop for interactive mode."""

    repl_commands = {
        "help": "Show available commands",
        "status": "Show system status",
        "skills": "List absorbed skills",
        "absorb": "Absorb a new skill (usage: absorb <source>)",
        "cost": "Show token cost report",
        "vault": "Show vault status",
        "buu": "Summon Majin Buu",
        "quit": "Exit SpongeBot",
        "exit": "Exit SpongeBot",
    }

    while True:
        try:
            user_input = console.input("[bold yellow]SpongeBot>[/bold yellow] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n[{STYLES['dim']}]Buu says goodbye...[/{STYLES['dim']}]")
            break

        if not user_input:
            continue

        parts = user_input.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd in ("quit", "exit"):
            console.print(
                f"[{STYLES['buu']}]Buu go sleep now. "
                f"Skills stay in belly. Bye bye![/{STYLES['buu']}]"
            )
            break
        elif cmd == "help":
            _show_repl_help(repl_commands)
        elif cmd == "status":
            _show_status_table()
        elif cmd == "skills":
            _show_skills_table()
        elif cmd == "absorb":
            source = parts[1] if len(parts) > 1 else None
            if not source:
                console.print(f"[{STYLES['warning']}]Usage: absorb <source>[/{STYLES['warning']}]")
            else:
                _absorb_preview(source)
        elif cmd == "cost":
            _show_cost_report()
        elif cmd == "vault":
            _show_vault_status()
        elif cmd == "buu":
            _show_buu()
        else:
            console.print(
                f"[{STYLES['dim']}]Unknown command: {cmd}. "
                f"Type 'help' for options.[/{STYLES['dim']}]"
            )


def _show_repl_help(commands: dict) -> None:
    """Display REPL help."""
    table = Table(
        title="SpongeBot Commands",
        border_style="cyan",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Command", style="bold cyan")
    table.add_column("Description")
    for cmd, desc in commands.items():
        table.add_row(cmd, desc)
    console.print(table)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
def status() -> None:
    """Show SpongeBot system status."""
    _show_status_table()


def _show_status_table() -> None:
    """Render the system status table."""
    table = Table(
        title="SpongeBot System Status",
        border_style="cyan",
        show_header=True,
        header_style="bold yellow",
        title_style="bold yellow",
    )
    table.add_column("Subsystem", style="bold cyan", min_width=20)
    table.add_column("Status", justify="center", min_width=10)
    table.add_column("Details", min_width=30)

    subsystems = [
        ("Absorption Engine", "running", "Ready to absorb skills"),
        ("Skill DAG", "running", "0 skills loaded"),
        ("Learning Tiers", "running", "5 tiers configured"),
        ("Token Saver", "running", "Compression active"),
        ("Vault", "sealed", "Vault encrypted"),
        ("Memory Store", "running", "SQLite backend ready"),
        ("Claude LLM", "unknown", "Awaiting API key"),
    ]

    for name, stat, detail in subsystems:
        table.add_row(name, status_dot(stat), detail)

    console.print(table)


# ---------------------------------------------------------------------------
# buu
# ---------------------------------------------------------------------------

@cli.command()
def buu() -> None:
    """Majin Buu mode -- show the Buu splash with a random quote."""
    _show_buu()


def _show_buu() -> None:
    """Display Buu ASCII art and a random quote."""
    console.print(
        Panel(
            Text(BUU_ASCII, style="bold magenta"),
            border_style="magenta",
            title="[bold magenta]MAJIN BUU[/bold magenta]",
            subtitle="[dim]Absorption Entity[/dim]",
        )
    )
    console.print(f"[{STYLES['buu']}]{random_buu_quote()}[/{STYLES['buu']}]")


# ---------------------------------------------------------------------------
# absorb
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("source")
@click.option(
    "--mode",
    type=click.Choice(
        ["agent", "document", "experience", "failure", "evolutionary", "federated"],
        case_sensitive=False,
    ),
    default="agent",
    help="Absorption mode to use.",
)
@click.option("--dry-run", is_flag=True, help="Preview without absorbing.")
def absorb(source: str, mode: str, dry_run: bool) -> None:
    """Absorb skills from a source."""
    _absorb_preview(source, mode=mode, dry_run=dry_run)


def _absorb_preview(
    source: str, mode: str = "agent", dry_run: bool = False
) -> None:
    """Show absorption preview / execute absorption."""
    console.print(
        Panel(
            f"[bold cyan]Source:[/bold cyan]  {source}\n"
            f"[bold cyan]Mode:[/bold cyan]    {mode}\n"
            f"[bold cyan]Dry Run:[/bold cyan] {'Yes' if dry_run else 'No'}",
            title="[bold magenta]Absorption Request[/bold magenta]",
            border_style="magenta",
        )
    )
    if dry_run:
        console.print(f"[{STYLES['warning']}]Dry run -- no absorption performed.[/{STYLES['warning']}]")
    else:
        with Progress(
            SpinnerColumn("dots"),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Absorbing...", total=None)
            time.sleep(random.uniform(0.5, 1.2))
            progress.remove_task(task)
        console.print(f"[{STYLES['buu']}]{random_celebration(source)}[/{STYLES['buu']}]")


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--tier", type=int, default=None, help="Filter by tier (0-5).")
def skills(tier: int | None) -> None:
    """List all absorbed skills in the DAG."""
    _show_skills_table(tier_filter=tier)


def _show_skills_table(tier_filter: int | None = None) -> None:
    """Render the skills table."""
    table = Table(
        title="Absorbed Skills",
        border_style="cyan",
        show_header=True,
        header_style="bold yellow",
        title_style="bold yellow",
    )
    table.add_column("Skill", style="bold cyan")
    table.add_column("Tier", justify="center")
    table.add_column("Source", style="dim")
    table.add_column("Absorbed", style="dim")

    # Placeholder: no skills yet
    console.print(table)
    console.print(f"[{STYLES['dim']}]No skills absorbed yet. Use 'absorb' to get started.[/{STYLES['dim']}]")


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------

@cli.command()
def cost() -> None:
    """Show token savings report."""
    _show_cost_report()


def _show_cost_report() -> None:
    """Render the token cost report."""
    table = Table(
        title="Token Savings Report",
        border_style="cyan",
        show_header=True,
        header_style="bold yellow",
        title_style="bold yellow",
    )
    table.add_column("Metric", style="bold cyan", min_width=25)
    table.add_column("Value", justify="right", min_width=15)

    rows = [
        ("Total Tokens Used", "0"),
        ("Tokens Saved (compression)", "0"),
        ("Savings Ratio", "0.0%"),
        ("Estimated Cost (USD)", "$0.00"),
        ("Budget Remaining", "$10.00"),
        ("Budget Utilization", "0.0%"),
    ]
    for metric, value in rows:
        table.add_row(metric, value)

    console.print(table)


# ---------------------------------------------------------------------------
# vault
# ---------------------------------------------------------------------------

@cli.command()
def vault() -> None:
    """Show vault status."""
    _show_vault_status()


def _show_vault_status() -> None:
    """Render vault status panel."""
    console.print(
        Panel(
            "[bold yellow]Vault Status: SEALED[/bold yellow]\n\n"
            "[cyan]Encryption:[/cyan]     Fernet (AES-128-CBC)\n"
            "[cyan]Key Derived:[/cyan]    PBKDF2-HMAC-SHA256\n"
            "[cyan]Stored Items:[/cyan]   0\n"
            "[cyan]Last Access:[/cyan]    Never\n\n"
            "[dim]The Krabby Patty Formula is safe.[/dim]",
            title="[bold yellow]Vault[/bold yellow]",
            border_style="yellow",
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for console_scripts."""
    cli()


if __name__ == "__main__":
    main()
