from __future__ import annotations

from pydantic import BaseModel

from cashflow_audit.errors import PortError


class OpenAIChat:
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return schema.model_validate_json(content)
        except Exception as exc:
            raise PortError("chat failed") from exc
