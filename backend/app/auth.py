from fastapi import Header

from app.security import validate_admin_token


async def verify_admin_token(x_admin_token: str = Header(default="")) -> None:
    validate_admin_token(x_admin_token)

