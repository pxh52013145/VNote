from typing import Union

from openai import OpenAI

from app.utils.logger import get_logger

logger = get_logger(__name__)


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str, model: Union[str, None] = None):
        self.client = OpenAI(api_key=(api_key or "").strip(), base_url=(base_url or "").strip())
        self.model = model

    @property
    def get_client(self):
        return self.client

    @staticmethod
    def test_connection(api_key: str, base_url: str) -> tuple[bool, str | None]:
        """
        Test whether the given API key + base_url can be used by OpenAI Python SDK.

        Returns (ok, error_message).
        """
        try:
            client = OpenAI(api_key=(api_key or "").strip(), base_url=(base_url or "").strip())
            client.models.list()
            logger.info("连通性测试成功")
            return True, None
        except Exception as e:
            logger.info(f"连通性测试失败: {e}")
            return False, str(e)
