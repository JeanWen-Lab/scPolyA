from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlencode

from scpolya_ui.i18n import DEFAULT_LOCALE, translate
from scpolya_ui.state.persistence import ProductRegistries


@dataclass
class ProductAppState:
    locale: str = DEFAULT_LOCALE
    active_page: str = "workspaces"
    registries: ProductRegistries | None = None
    selected_workspace_id: str | None = None
    product_base_path: str = "/product"
    payload: dict[str, Any] = field(default_factory=dict)

    def t(self, key: str, **params: Any) -> str:
        return translate(self.locale, key, **params)

    def page_href(
        self,
        page: str | None = None,
        *,
        locale: str | None = None,
        workspace_id: str | None = None,
        extra_query: Mapping[str, Any] | None = None,
    ) -> str:
        target = page or self.active_page
        if target == "workspaces":
            path = self.product_base_path
        else:
            path = f"{self.product_base_path}/{target}"
        query: dict[str, str] = {"locale": locale or self.locale}
        selected_workspace_id = workspace_id or self.selected_workspace_id
        if selected_workspace_id:
            query["workspace"] = selected_workspace_id
        for key, value in (extra_query or {}).items():
            if value is not None and value != "":
                query[str(key)] = str(value)
        return f"{path}?{urlencode(query)}"
