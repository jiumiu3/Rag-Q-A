import re
from typing import Annotated

from fastapi import Header

from app.core.exceptions import IdentityError

USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


async def current_user_id(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> str:
    """解析上游注入的用户标识，业务接口不得从请求体接受用户归属。"""

    user_id = (x_user_id or "").strip()
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise IdentityError("缺少或非法的 X-User-Id 请求头")
    return user_id
