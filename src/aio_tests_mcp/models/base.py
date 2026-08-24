"""Base model shared by every AIO Tests API model."""

from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound="ApiModel")


class ApiModel(BaseModel):
    """Base model providing the common API conversion methods.

    Subclasses parse raw AIO Tests API payloads via
    :meth:`from_api_response` and render themselves for MCP tool output via
    :meth:`to_simplified_dict`.
    """

    @classmethod
    def from_api_response(cls: type[T], data: dict[str, Any], **kwargs: Any) -> T:
        """Convert an API response to a model instance.

        Args:
            data: The API response data.
            **kwargs: Additional context parameters.

        Returns:
            An instance of the model.

        Raises:
            NotImplementedError: If the subclass does not implement this method.
        """
        raise NotImplementedError("Subclasses must implement from_api_response")

    def to_simplified_dict(self) -> dict[str, Any]:
        """Convert the model to a simplified dictionary for tool responses.

        Returns:
            A dictionary with only the essential fields.
        """
        return self.model_dump(exclude_none=True)
