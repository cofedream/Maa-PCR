from .modelbase import Request
from .responses import *
from .common import *
from .enums import *
from pydantic import Field

class DailyTaskTopRequest(Request[DailyTaskTopResponse]):
    setting_alchemy_count: int = None
    is_check_by_term_normal_gacha: int = None
    @property
    def url(self) -> str:
        return "daily_task/top"

class HomeIndexRequest(Request[HomeIndexResponse]):
    message_id: int = None
    tips_id_list: List[int] = None
    is_first: int = None
    gold_history: int = None
    @property
    def url(self) -> str:
        return "home/index"

class LoadIndexRequest(Request[LoadIndexResponse]):
    carrier: str = None
    @property
    def url(self) -> str:
        return "load/index"

class UnitRoleGachaIndexRequest(Request[UnitRoleGachaIndexResponse]):
    @property
    def url(self) -> str:
        return "unit_role/gacha_index"

class ShopItemListRequest(Request[ShopItemListResponse]):
    @property
    def url(self) -> str:
        return "shop/item_list"
