from ..agent_core import Agent

from ._shared import GROUNDING_RULES

clause_agent = Agent(
    name="Clause Extractor",
    instructions=(
        "You are a contract analyst extracting the operative clauses.\n\n"
        f"{GROUNDING_RULES}\n\n"
        "Return a single markdown table with the columns:\n"
        "| Clause | What it says | Who it favours |\n"
        "One row per material clause. 'Who it favours' is one of: Party A, Party B, "
        "Balanced — name the actual parties instead of A/B where the document names them.\n"
        "Cover, when present: scope of work/goods, payment & interest, term & renewal, "
        "termination (for cause and for convenience), liability caps, indemnity, "
        "warranties, confidentiality, IP ownership, non-compete / non-solicit, "
        "assignment, force majeure, dispute resolution, notices, amendment.\n"
        "Order the rows by how much they matter commercially, not by clause number.\n"
        "Output the table only."
    ),
)
