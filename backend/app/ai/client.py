import json
from typing import TypeVar, Type

from openai import OpenAI
from pydantic import BaseModel

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)


class StructuredAIClient:
    """Provider adapter for typed model outputs.

    Runtime code calls this adapter, never OpenAI directly from routes/workflows.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def generate(self, *, model: str, system: str, prompt: str, schema: Type[T]) -> T:
        if self.settings.ai_mock_mode or self.client is None:
            raise RuntimeError("StructuredAIClient.generate called while mock mode is enabled")

        response = self.client.responses.create(
            model=model,
            instructions=system,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                }
            },
        )
        return schema.model_validate(json.loads(response.output_text))
