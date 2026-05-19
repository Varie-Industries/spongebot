"""SpongeBot ASCII art, splash screens, and boot sequence messages."""

import random

SPONGEBOT_SPLASH = r"""
   _____ _____   ____  _   _  _____ ______ ____   ____ _______
  / ____|  __ \ / __ \| \ | |/ ____|  ____|  _ \ / __ \__   __|
 | (___ | |__) | |  | |  \| | |  __| |__  | |_) | |  | | | |
  \___ \|  ___/| |  | | . ` | | |_ |  __| |  _ <| |  | | | |
  ____) | |    | |__| | |\  | |__| | |____| |_) | |__| | | |
 |_____/|_|     \____/|_| \_|\_____|______|____/ \____/  |_|

              Absorption-Based AI Agent Framework
                    Anthropic Claude Only
"""

BUU_ASCII = r"""
          .-~~~~~~-.
        .'          '.
       /   O      O   \
      :                :
      |     ______     |
      :     .'  '.     ;
       \   /  BUU \   /
        './ \____/ \.'
     ____/          \____
    /    '-.__  __.-'    \
   /          ''          \
  |    ABSORPTION MODE    |
   \                     /
    '-._______________.-'
"""

BUU_QUOTES = [
    "Buu hungry! Buu eat your skills and make them BETTER!",
    "Buu absorb! Buu stronger now!",
    "You make Buu angry! Buu turn you into chocolate!",
    "Buu want candy! Give Buu your APIs!",
    "Buu no like OpenAI. Buu only like Claude!",
    "Buu make you cookie! ...then eat cookie.",
    "Buu absorb everything! Nothing escapes Buu!",
    "Super Buu mode activated! Maximum absorption!",
    "Buu tired of weak skills. Buu evolve them!",
    "Kid Buu chaos mode! All skills being recombined!",
    "Buu turn your framework into candy and EAT IT!",
    "Me Buu! Me absorb all the tokens! Nom nom nom!",
    "Buu doesn't need fine-tuning. Buu IS the model!",
    "Buu's belly hold infinite skills! Try Buu!",
    "You think you strong? Buu absorb you too!",
]

BOOT_MESSAGES = [
    "Initializing absorption matrix...",
    "Charging Buu's belly reactor...",
    "Scanning for skills to devour...",
    "Encrypting the Krabby Patty Formula...",
    "Loading Skill DAG topology...",
    "Priming the learning tiers...",
    "Calibrating token compression layers...",
    "Connecting to Claude API...",
    "Warming up the chocolate beam...",
    "Inflating Buu's absorption antenna...",
]

ABSORPTION_CELEBRATIONS = [
    "NOM NOM NOM! Skill [{skill}] has been DEVOURED!",
    "Buu's belly rumbles... [{skill}] absorbed successfully!",
    "CHOCOLATE BEAM! [{skill}] is now part of Buu!",
    "Buu did it! [{skill}] makes Buu even STRONGER!",
    "Another one bites the dust! [{skill}] consumed!",
    "Super absorption complete! [{skill}] integrated into the matrix!",
    "Buu's power level just increased! [{skill}] is OURS now!",
    "The porous one has absorbed [{skill}]! Excellent!",
]

SKILL_MOODS = {
    "excited": [
        "I'M READY! I'M READY! I'M READY!",
        "This is the BEST skill EVER!",
        "Buu so happy! More skills! MORE!",
    ],
    "curious": [
        "Ooooh, what does THIS skill do?",
        "Buu never seen this before... interesting...",
        "Let Buu taste this knowledge...",
    ],
    "angry": [
        "This skill BROKEN! Buu FIX IT!",
        "WHO GAVE BUU BAD DATA?!",
        "Buu turn this bug into CHOCOLATE!",
    ],
    "satisfied": [
        "Buu full now. Good skills today.",
        "All skills performing within parameters. Buu pleased.",
        "The absorption matrix is well-fed.",
    ],
}


def random_buu_quote() -> str:
    """Return a random Buu quote."""
    return random.choice(BUU_QUOTES)


def random_boot_message() -> str:
    """Return a random boot message."""
    return random.choice(BOOT_MESSAGES)


def random_celebration(skill_name: str) -> str:
    """Return a random absorption celebration for the given skill."""
    template = random.choice(ABSORPTION_CELEBRATIONS)
    return template.format(skill=skill_name)


def random_mood_line(mood: str) -> str:
    """Return a random mood-appropriate line."""
    lines = SKILL_MOODS.get(mood, SKILL_MOODS["satisfied"])
    return random.choice(lines)
