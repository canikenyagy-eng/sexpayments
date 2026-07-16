from typing import AsyncGenerator, Callable, Type, TypeVar

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.session import get_db
from app.modules.base.service import BaseService

ServiceType = TypeVar("ServiceType", bound=BaseService)


def get_service(service_class: Type[ServiceType]) -> Callable:
    """
    Dependency injection factory for services.
    Usage:
        @router.get("/")
        async def endpoint(service: MyService = Depends(get_service(MyService))):
            ...
    """

    async def _get_service(
        session: AsyncSession = Depends(get_db),
    ) -> AsyncGenerator[ServiceType, None]:
        yield service_class(session)

    return _get_service
