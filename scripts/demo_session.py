#!/usr/bin/env python3
"""
Demo session for README GIF capture.

Run this in a clean terminal (recommended: iTerm2 1100x700, dark theme,
Menlo 14pt) while recording with Kap, Gifski, or LICEcap.

The pacing is tuned for screen recording. Each step pauses long enough
to be readable when looped at GIF speed.
"""

import asyncio
import sys
import time

# Banner
BANNER = r"""
   _____ _____   ____  _   _  _____ ______ ____   ____ _______
  / ____|  __ \ / __ \| \ | |/ ____|  ____|  _ \ / __ \__   __|
 | (___ | |__) | |  | |  \| | |  __| |__  | |_) | |  | | | |
  \___ \|  ___/| |  | | . ` | | |_ |  __| |  _ <| |  | | | |
  ____) | |    | |__| | |\  | |__| | |____| |_) | |__| | | |
 |_____/|_|     \____/|_| \_|\_____|______|____/ \____/  |_|

         Absorption engine for Claude  -  MCP server
"""


def pause(seconds: float = 1.5) -> None:
    """Hold the frame so the viewer can read it."""
    sys.stdout.flush()
    time.sleep(seconds)


def typed(text: str, delay: float = 0.02) -> None:
    """Type text character by character for a live-typing effect."""
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()


def section(title: str) -> None:
    print()
    print(f"\033[1;36m>>> {title}\033[0m")
    pause(0.6)


async def main() -> None:
    print("\033[2J\033[H", end="")  # clear screen
    print(BANNER)
    pause(2.0)

    section("Absorbing a StoreKit success trajectory")
    typed('  sponge_absorb(mode="experience", source="storekit_purchase.json")', delay=0.015)
    pause(0.8)
    print("  -> 4 skills extracted  -> tier 1 storage  -> audit-chain entry #142")
    pause(2.0)

    section("Absorbing a failed migration so Claude never repeats it")
    typed('  sponge_absorb(mode="failure", source="migration_error.log")', delay=0.015)
    pause(0.8)
    print("  -> 2 anti-skills created  -> linked to 'database_migration' node")
    pause(2.0)

    section("Asking Claude what it learned")
    typed('  sponge_recall(query="how to handle StoreKit .pending state")', delay=0.015)
    pause(1.0)
    print("  -> 3 skills returned, confidence 0.87")
    print("  -> source: storekit_purchase.json (absorbed 12s ago)")
    pause(2.5)

    section("Health check")
    typed("  sponge_health()", delay=0.015)
    pause(0.8)
    print("  vault:        ok")
    print("  audit_chain:  ok  (142 entries, integrity verified)")
    print("  memory:       ok  (47 skills, 3 anti-skills)")
    print("  llm:          ok  (claude-sonnet-4)")
    pause(3.0)

    print()
    print("\033[1;32mSpongeBot remembers. Claude gets sharper every session.\033[0m")
    pause(2.5)


if __name__ == "__main__":
    asyncio.run(main())
