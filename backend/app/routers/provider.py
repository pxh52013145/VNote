from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.enmus.exception import ProviderErrorEnum
from app.exceptions.provider import ProviderError
from app.gpt.provider.OpenAI_compatible_provider import OpenAICompatibleProvider
from app.services.model import ModelService
from app.services.provider import ProviderService
from app.utils.response import ResponseWrapper as R

router = APIRouter()


class ProviderRequest(BaseModel):
    name: str
    api_key: str
    base_url: str
    logo: Optional[str] = None
    type: str


class TestRequest(BaseModel):
    id: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class ProviderUpdateRequest(BaseModel):
    id: str
    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    logo: Optional[str] = None
    type: Optional[str] = None
    enabled: Optional[int] = None


@router.post("/add_provider")
def add_provider(data: ProviderRequest):
    try:
        res = ProviderService.add_provider(
            name=data.name,
            api_key=data.api_key,
            base_url=data.base_url,
            logo=data.logo,
            type_=data.type,
        )
        return R.success(msg="添加模型供应商成功", data=res)
    except Exception as e:
        return R.error(msg=str(e))


@router.get("/get_all_providers")
def get_all_providers():
    try:
        res = ProviderService.get_all_providers_safe()
        return R.success(data=res)
    except Exception as e:
        return R.error(msg=str(e))


@router.get("/get_provider_by_id/{id}")
def get_provider_by_id(id: str):
    try:
        res = ProviderService.get_provider_by_id_safe(id)
        return R.success(data=res)
    except Exception as e:
        return R.error(msg=str(e))


@router.post("/update_provider")
def update_provider(data: ProviderUpdateRequest):
    try:
        if all(
            field is None
            for field in [
                data.name,
                data.api_key,
                data.base_url,
                data.logo,
                data.type,
                data.enabled,
            ]
        ):
            return R.error(msg="请至少填写一个参数", code=400)

        provider_id = ProviderService.update_provider(id=data.id, data=dict(data))
        return R.success(msg="更新模型供应商成功", data={"id": provider_id})
    except Exception as e:
        return R.error(msg=str(e))


@router.get("/delete_provider/{id}")
def delete_provider(id: str):
    try:
        success = ProviderService.delete_provider(id)
        if success:
            return R.success(msg="删除模型供应商成功", data={"id": id})
        return R.error(msg="供应商不存在", code=404)
    except ValueError as e:
        return R.error(msg=str(e), code=400)
    except Exception as e:
        return R.error(msg=str(e))


@router.post("/connect_test")
def gpt_connect_test(data: TestRequest):
    # Prefer testing the provided credentials (supports "test before save").
    if data.api_key is not None or data.base_url is not None:
        api_key = (data.api_key or "").strip()
        base_url = (data.base_url or "").strip()
        if not api_key or not base_url:
            raise ProviderError(code=ProviderErrorEnum.WRONG_PARAMETER.code, message="请填写 API Key 和 Base URL")

        ok, error_message = OpenAICompatibleProvider.test_connection(api_key=api_key, base_url=base_url)
        if ok:
            return R.success(msg="连接成功")

        raise ProviderError(
            code=ProviderErrorEnum.CONNECTION_TEST_FAILED.code,
            message=error_message or ProviderErrorEnum.CONNECTION_TEST_FAILED.message,
        )

    if not data.id:
        raise ProviderError(code=ProviderErrorEnum.WRONG_PARAMETER.code, message="缺少供应商 ID")

    ModelService().connect_test(data.id)
    return R.success(msg="连接成功")
