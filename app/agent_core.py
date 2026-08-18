from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    """A named role plus its system prompt. No SDK, no tools — just a prompt."""

    name: str
    instructions: str

    def messages(self, user_input: str) -> list[dict]:
        return [
            {"role": "system", "content": self.instructions},
            {"role": "user", "content": user_input},
        ]
