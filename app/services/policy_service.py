import math

from app.pipeline.conditions import RegionIndex
from app.repositories.policy_repository import PolicyFilter, PolicyRepository
from app.schemas.policy import RegionOption, SidoRegions
from app.schemas.policy_info import CATEGORY_NAMES, today_kst
from app.schemas.public import Page
from app.schemas.session import POLICY_CATEGORIES, PolicyCategory, PolicySummary
from app.services.profile_codes import region_code

_CATEGORY_BY_POLICY_CATEGORY = {policy_category: category for category, policy_category in POLICY_CATEGORIES.items()}

# 시도 코드 순서는 새 코드(전남광주 12, 강원 51, 전북 52) 때문에 화면 순서로 쓸 수 없어 따로 정함
# 수도권 먼저, 그 뒤는 행정 표준 순서. 전남광주는 통합특별시라 옛 광주광역시 자리. 목록에 없는 시도는 맨 뒤에 코드 순
SIDO_ORDER = ("11", "41", "28", "26", "27", "12", "30", "31", "36", "51", "43", "44", "52", "47", "48", "50")


class PolicyService:
    def __init__(self, policy_repository: PolicyRepository) -> None:
        self._policy_repository = policy_repository

    # 프로필 검증·1차 필터와 같은 RegionIndex로 만들어 목록의 코드가 모두 세션 생성에서 받아짐
    async def list_regions(self) -> list[SidoRegions]:
        regions = RegionIndex(await self._policy_repository.list_regions())
        groups = []
        for sido_code, sido_name in sorted(regions.sido_names.items(), key=_sido_sort_key):
            units = sorted((unit for unit in regions.units if unit.sido_code == sido_code), key=lambda u: u.codes[0])
            options = [RegionOption(code=f"{sido_code}000", name=f"{sido_name} 전체")]
            # 세종처럼 시군구가 시도 자체인 곳은 "○○ 전체"와 같아 따로 두지 않음
            options += [
                RegionOption(code=RegionIndex.unit_code(unit), name=unit.label.removeprefix(sido_name).strip())
                for unit in units
                if unit.label != sido_name
            ]
            groups.append(SidoRegions(code=f"{sido_code}000", name=sido_name, sigungu=options))
        return groups

    async def search(
        self,
        keyword: str | None,
        category: PolicyCategory | None,
        region: str | None,
        page: int,
        size: int,
    ) -> Page[PolicySummary]:
        scope = None
        if region is not None:
            regions = RegionIndex(await self._policy_repository.list_regions())
            # 프로필과 같은 범위(고른 지역 + 그 시도 전체 + 전국)로 거름
            scope = regions.profile_region(region_code(region, regions, field="region"))
        policy_filter = PolicyFilter(
            sido_code=scope.sido_code if scope else None,
            region_codes=scope.codes if scope else (),
            keywords=tuple(keyword.split()) if keyword else (),
            large_categories=_large_category_names(category),
        )

        today = today_kst()
        records, total = await self._policy_repository.search(policy_filter, today, page * size, size)
        labels = await self._policy_repository.get_region_labels([record.policy_no for record in records])
        items = [
            PolicySummary.from_item(record.to_item(0.0, today, labels.get(record.policy_no)))
            for record in records
        ]
        return Page(items=items, page=page, size=size, total_elements=total, total_pages=math.ceil(total / size))


def _sido_sort_key(item: tuple[str, str]) -> tuple[int, str]:
    sido_code = item[0]
    return (SIDO_ORDER.index(sido_code) if sido_code in SIDO_ORDER else len(SIDO_ORDER), sido_code)


def _large_category_names(category: PolicyCategory | None) -> tuple[str, ...]:
    if category is None:
        return ()
    return tuple(sorted(CATEGORY_NAMES[_CATEGORY_BY_POLICY_CATEGORY[category]]))
