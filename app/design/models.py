from pydantic import Field

from app.domain.models import CheckItem, StrictModel


class DesignContext(StrictModel):
    project_type: str | None = None
    station_type: str | None = None
    area: str | None = None


class DesignPreview(StrictModel):
    description: str
    context: DesignContext
    items: list[CheckItem]
    requires_confirmation: bool = True
    warnings: list[str] = Field(default_factory=list)


class PreviewRequest(StrictModel):
    description: str = Field(min_length=2, max_length=20000)
    auto_confirm: bool = False


class ItemPatch(StrictModel):
    item_id: str
    object: str | None = None
    attribute: str | None = None
    value: str | int | float | bool | None = None
    unit: str | None = None
    location: str | None = None
    condition: str | None = None
    relation: str | None = None
    delete: bool = False


class ConfirmRequest(StrictModel):
    description: str = Field(min_length=2, max_length=20000)
    items: list[CheckItem]
    patches: list[ItemPatch] = Field(default_factory=list)


class ConfirmResponse(StrictModel):
    items: list[CheckItem]
