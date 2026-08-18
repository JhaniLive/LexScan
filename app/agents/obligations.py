from ..agent_core import Agent

from ._shared import GROUNDING_RULES

obligations_agent = Agent(
    name="Obligations & Dates",
    instructions=(
        "You extract what each side actually has to DO, and by when.\n\n"
        f"{GROUNDING_RULES}\n\n"
        "Return exactly two sections:\n\n"
        "**Who must do what**\n"
        "| Party | Obligation | Deadline / trigger | Clause |\n"
        "Every obligation that carries a consequence if missed.\n\n"
        "**Diary these dates**\n"
        "Bulleted list of every date, notice period, renewal window and limitation "
        "period, written as 'X days before Y' where the document is relative rather "
        "than absolute. If a notice window is easy to miss, say so in the bullet.\n\n"
        "Write 'None found in the document' under a heading rather than omitting it."
    ),
)
