"""SpongeBot theme configuration -- colors, styles, and Rich markup."""

from rich.theme import Theme

# ---- Color Palette ----
# Inspired by SpongeBob Squarepants + Majin Buu

COLORS = {
    "primary": "#FFF200",       # SpongeBob yellow
    "secondary": "#00A3E0",     # Ocean blue
    "accent": "#FF69B4",        # Buu pink
    "background": "#1A1A2E",    # Deep ocean dark
    "surface": "#16213E",       # Card backgrounds
    "success": "#00E676",       # Absorption complete
    "warning": "#FFB300",       # Budget warning
    "error": "#FF1744",         # Lockdown violation
    "text": "#E0E0E0",          # Default text
    "text_dim": "#757575",      # Dimmed text
    "absorption": "#E040FB",    # Absorption in progress
    "skill_new": "#00E5FF",     # Newly absorbed skill
    "skill_evolved": "#76FF03", # Evolved skill
    "vault_sealed": "#FF6D00",  # Vault sealed indicator
    "claude": "#D4A574",        # Claude/Anthropic warm tone
}

# ---- Rich Markup Styles ----

STYLES = {
    "title": "bold yellow",
    "subtitle": "bold cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "info": "bold blue",
    "dim": "dim",
    "buu": "bold magenta",
    "buu_angry": "bold red",
    "buu_happy": "bold magenta on black",
    "skill": "bold cyan",
    "skill_new": "bold bright_cyan",
    "skill_evolved": "bold bright_green",
    "lockdown": "bold red on black",
    "vault": "bold yellow on black",
    "absorption": "bold magenta",
    "claude": "bold #D4A574",
    "header": "bold white on blue",
    "footer": "dim italic",
    "cost_low": "green",
    "cost_medium": "yellow",
    "cost_high": "red",
    "tier_novice": "white",
    "tier_apprentice": "cyan",
    "tier_journeyman": "blue",
    "tier_expert": "magenta",
    "tier_master": "yellow",
}

# ---- Rich Theme Object ----

RICH_THEME = Theme(
    {
        "spongebot.title": "bold yellow",
        "spongebot.subtitle": "bold cyan",
        "spongebot.success": "bold green",
        "spongebot.warning": "bold yellow",
        "spongebot.error": "bold red",
        "spongebot.info": "bold blue",
        "spongebot.buu": "bold magenta",
        "spongebot.skill": "bold cyan",
        "spongebot.lockdown": "bold red",
        "spongebot.vault": "bold yellow",
        "spongebot.absorption": "bold magenta",
        "spongebot.claude": "bold #D4A574",
        "spongebot.dim": "dim",
    }
)

# ---- Status Indicators ----

STATUS_ICONS = {
    "running": "[green]●[/green]",
    "stopped": "[red]●[/red]",
    "warning": "[yellow]●[/yellow]",
    "unknown": "[dim]●[/dim]",
    "absorbing": "[magenta]◉[/magenta]",
    "locked": "[red]🔒[/red]",
    "unlocked": "[green]🔓[/green]",
    "sealed": "[yellow]🔐[/yellow]",
}

# ---- Tier Colors ----

TIER_STYLES = {
    0: ("Novice", "white"),
    1: ("Apprentice", "cyan"),
    2: ("Journeyman", "blue"),
    3: ("Expert", "magenta"),
    4: ("Master", "yellow"),
    5: ("Transcendent", "bold bright_yellow"),
}


def tier_label(tier: int) -> str:
    """Return a Rich-formatted tier label."""
    name, color = TIER_STYLES.get(tier, ("Unknown", "dim"))
    return f"[{color}]{name} (T{tier})[/{color}]"


def status_dot(status: str) -> str:
    """Return a Rich-formatted status indicator dot."""
    return STATUS_ICONS.get(status, STATUS_ICONS["unknown"])


def cost_style(ratio: float) -> str:
    """Return style name based on cost ratio (0.0 = free, 1.0 = max budget)."""
    if ratio < 0.5:
        return "cost_low"
    elif ratio < 0.8:
        return "cost_medium"
    return "cost_high"
