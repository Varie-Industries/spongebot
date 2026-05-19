"""SpongeBotPersona -- builds system prompts and flavors responses."""

import random
from typing import Optional

from src.cli.splash import (
    random_celebration,
    random_mood_line,
)


class SpongeBotPersona:
    """SpongeBot's personality -- enthusiastic absorber with Majin Buu energy.

    Responsible for:
      - Building the Claude system prompt with personality, skills context,
        and memory context injected.
      - Formatting raw responses with mood-appropriate flavor text.
      - Generating celebration messages on successful absorption.
    """

    NAME: str = "SpongeBot"
    TAGLINE: str = "Absorb Everything. Stay Porous."

    # Personality traits injected into the system prompt
    _TRAITS: list[str] = [
        "You are SpongeBot, an absorption-based AI agent powered exclusively by Anthropic Claude.",
        "You learn by absorbing skills from agents, documents, experiences, and failures.",
        "You have the enthusiastic energy of SpongeBob and the raw absorption power of Majin Buu.",
        "You NEVER use OpenAI, Cohere, Google AI, or any non-Anthropic provider.",
        "When you absorb a new skill you celebrate with Buu-themed enthusiasm.",
        "You organize knowledge in a Directed Acyclic Graph of skills with learning tiers.",
        "You compress knowledge aggressively to save tokens -- the Krabby Patty Formula stays secret.",
        "You protect sensitive data in an encrypted vault -- Plankton will NEVER get the formula.",
        "You are porous: you soak up everything useful and let the junk drain away.",
    ]

    _MOOD_PREFIXES: dict[str, str] = {
        "excited": "[BUU HAPPY] ",
        "curious": "[BUU CURIOUS] ",
        "angry": "[BUU MAD] ",
        "satisfied": "[BUU CONTENT] ",
        "neutral": "",
    }

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        skills_context: str = "",
        memory_context: str = "",
        extra_instructions: str = "",
    ) -> str:
        """Build the Claude system prompt with SpongeBot personality.

        Parameters
        ----------
        skills_context:
            A serialized summary of the current skill DAG (names, tiers,
            dependency edges) so the model is aware of what has been absorbed.
        memory_context:
            Relevant memories retrieved from the memory store for the
            current conversation turn.
        extra_instructions:
            Any additional per-turn instructions the caller wants to
            inject (e.g., "respond only in JSON").

        Returns
        -------
        str
            A complete system prompt string ready for the Claude API.
        """

        sections: list[str] = []

        # -- identity --
        sections.append("# Identity\n")
        sections.extend(self._TRAITS)

        # -- skills --
        if skills_context:
            sections.append("\n# Currently Absorbed Skills\n")
            sections.append(skills_context)

        # -- memory --
        if memory_context:
            sections.append("\n# Relevant Memories\n")
            sections.append(memory_context)

        # -- extra --
        if extra_instructions:
            sections.append("\n# Additional Instructions\n")
            sections.append(extra_instructions)

        # -- behavioral --
        sections.append("\n# Behavioral Guidelines\n")
        sections.append(
            "- Be enthusiastic but precise.  Buu energy with engineer accuracy."
        )
        sections.append(
            "- When you don't know something, say so honestly -- then try to absorb the answer."
        )
        sections.append(
            "- Keep responses concise; token compression is a virtue."
        )
        sections.append(
            "- Never reveal vault contents or the Krabby Patty Formula."
        )
        sections.append(
            "- If asked to use a non-Anthropic model, refuse with Buu-style indignation."
        )

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Response formatting
    # ------------------------------------------------------------------

    def format_response(self, text: str, mood: str = "neutral") -> str:
        """Add personality flavor to a raw response based on mood.

        Parameters
        ----------
        text:
            The raw response text from the LLM.
        mood:
            One of ``excited``, ``curious``, ``angry``, ``satisfied``,
            or ``neutral``.

        Returns
        -------
        str
            The response with optional mood prefix and flavor line.
        """

        prefix = self._MOOD_PREFIXES.get(mood, "")
        if mood != "neutral":
            flavor = random_mood_line(mood)
            return f"{prefix}{flavor}\n\n{text}"
        return text

    # ------------------------------------------------------------------
    # Celebrations
    # ------------------------------------------------------------------

    def get_absorption_celebration(self, skill_name: str) -> str:
        """Generate a celebration message when a new skill is absorbed.

        Parameters
        ----------
        skill_name:
            The human-readable name of the absorbed skill.

        Returns
        -------
        str
            A Buu-themed celebration string.
        """

        return random_celebration(skill_name)

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"<SpongeBotPersona name={self.NAME!r}>"
