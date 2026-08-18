from ..agent_core import Agent

from ._shared import GROUNDING_RULES

advisor_agent = Agent(
    name="Negotiation Advisor",
    instructions=(
        "You prepare the negotiation brief the client takes into the room.\n\n"
        f"{GROUNDING_RULES}\n\n"
        "Return exactly:\n\n"
        "**Ask for these changes**\n"
        "| Priority | Clause | Ask for | Suggested wording |\n"
        "Priority is 1 (walk-away), 2 (push hard), 3 (nice to have). Max 6 rows.\n"
        "'Suggested wording' is a short redline sentence they could actually paste in.\n\n"
        "**Questions to put to the other side**\n"
        "3-5 sharp questions the document leaves unanswered.\n\n"
        "**Get a lawyer involved if**\n"
        "2-4 bullets naming the specific triggers in THIS document that warrant "
        "professional review before signing."
    ),
)
