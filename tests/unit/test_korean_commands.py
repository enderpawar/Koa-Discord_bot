from __future__ import annotations

import discord
from discord.ext import commands

from cogs.admin_cog import AdminCog
from cogs.fortune_cog import FortuneCog
from cogs.lol_cog import LolCog
from cogs.mc_cog import MCControlCog
from cogs.party_cog import PartyCog
from cogs.rank_cog import RankCog
from cogs.tts_cog import TTSCog
from cogs.valorant_cog import ValorantCog

_TEST_BOT = commands.Bot(command_prefix="!", intents=discord.Intents.none())


def _payload(command) -> dict:
    return command.to_dict(_TEST_BOT.tree)


def _option_names(command) -> list[str]:
    return [option["name"] for option in _payload(command).get("options", [])]


def test_top_level_commands_are_friendly_korean() -> None:
    assert {
        TTSCog.setvoice.name,
        TTSCog.join.name,
        TTSCog.leave.name,
        TTSCog.status.name,
        RankCog.leaderboard.name,
        RankCog.rank.name,
        PartyCog.create_party.name,
        PartyCog.party_list.name,
        PartyCog.my_parties.name,
        PartyCog.cancel_party.name,
        FortuneCog.today_fortune.name,
    } == {
        "목소리",
        "입장",
        "퇴장",
        "상태",
        "활동순위",
        "활동점수",
        "파티모집",
        "파티목록",
        "내파티",
        "파티취소",
        "오늘의운세",
    }


def test_command_groups_and_subcommands_are_korean() -> None:
    assert AdminCog.admin.name == "관리자"
    assert {command.name for command in AdminCog.admin.commands} == {"대시보드"}
    assert MCControlCog.mc.name == "마크"
    assert {command.name for command in MCControlCog.mc.commands} == {
        "켜기",
        "끄기",
        "상태",
        "서버",
        "화이트리스트",
    }
    mc_server = next(command for command in MCControlCog.mc.commands if command.name == "서버")
    assert {command.name for command in mc_server.commands} == {"공지"}
    mc_whitelist = next(
        command for command in MCControlCog.mc.commands if command.name == "화이트리스트"
    )
    assert {command.name for command in mc_whitelist.commands} == {"등록"}
    assert LolCog.lol.name == "롤"
    assert {command.name for command in LolCog.lol.commands} == {
        "등록",
        "전적",
        "검색",
        "등록해제",
    }
    assert ValorantCog.valorant.name == "발로란트"
    assert {command.name for command in ValorantCog.valorant.commands} == {
        "등록",
        "전적",
        "검색",
        "등록해제",
    }


def test_tts_channel_pickers_are_gone() -> None:
    """`/입장` 이 두 채널 ID 를 항상 덮어써서 분리 설정은 재생되지 않았다.

    저장되는 척하지만 다음 연결에서 지워지는 명령을 되살리지 않기 위한 회귀 가드다.
    """
    assert not hasattr(TTSCog, "settts")
    assert not hasattr(TTSCog, "setvc")


def test_user_facing_option_names_are_korean() -> None:
    assert _option_names(TTSCog.setvoice) == ["종류"]
    assert _option_names(RankCog.rank) == ["멤버"]
    # 필수인 `제목` 이 먼저다. 나머지는 전부 기본값이 있어서 Tab 으로 건너뛴다.
    assert _option_names(PartyCog.create_party) == ["제목", "시작", "정원", "메모", "태그"]
    assert _option_names(PartyCog.cancel_party) == ["파티"]
    # 제목은 자유 입력(STRING=3)이다. 역할(ROLE=8)이면 Discord 가 `롤`, `발로`
    # 같은 입력을 "올바른 역할이 아닙니다" 로 막아 버린다. 알림 대상은 별도
    # `태그` 옵션(ROLE=8, 선택)에서만 받는다.
    party_options = {
        option["name"]: option for option in _payload(PartyCog.create_party)["options"]
    }
    assert party_options["제목"]["type"] == 3
    assert party_options["태그"]["type"] == 8
    assert party_options["태그"]["required"] is False

    mc_payload = _payload(MCControlCog.mc)
    mc_whitelist = next(
        option for option in mc_payload["options"] if option["name"] == "화이트리스트"
    )
    whitelist_register = next(
        option for option in mc_whitelist["options"] if option["name"] == "등록"
    )
    assert [option["name"] for option in whitelist_register["options"]] == ["닉네임"]

    lol_payload = _payload(LolCog.lol)
    lol_options = {
        option["name"]: [child["name"] for child in option.get("options", [])]
        for option in lol_payload["options"]
    }
    assert lol_options == {
        "등록": ["라이엇아이디", "지역"],
        "전적": ["멤버"],
        "검색": ["라이엇아이디", "지역"],
        "등록해제": [],
    }

    valorant_payload = _payload(ValorantCog.valorant)
    valorant_options = {
        option["name"]: [child["name"] for child in option.get("options", [])]
        for option in valorant_payload["options"]
    }
    assert valorant_options == {
        "등록": ["라이엇아이디", "지역", "플랫폼"],
        "전적": ["멤버"],
        "검색": ["라이엇아이디", "지역", "플랫폼"],
        "등록해제": [],
    }


def test_choice_labels_are_friendly_korean() -> None:
    voice_choices = _payload(TTSCog.setvoice)["options"][0]["choices"]
    assert [choice["name"] for choice in voice_choices] == [
        "여성 · 차분",
        "여성 · 또렷",
        "여성 · 부드러움",
        "여성 · 편안함",
        "여성 · 경쾌",
        "남성 · 자연스러움",
        "남성 · 무게감",
        "남성 · 친근함",
        "남성 · 담백",
        "남성 · 다국어",
    ]

    lol_register = next(
        option for option in _payload(LolCog.lol)["options"] if option["name"] == "등록"
    )
    lol_regions = lol_register["options"][1]["choices"]
    assert [choice["name"] for choice in lol_regions] == [
        "한국",
        "일본",
        "북미",
        "유럽 서부",
        "유럽 북동부",
        "브라질",
        "라틴아메리카 북부",
        "라틴아메리카 남부",
        "오세아니아",
        "튀르키예",
        "러시아",
        "필리핀",
        "싱가포르",
        "태국",
        "대만",
        "베트남",
    ]

    valorant_register = next(
        option
        for option in _payload(ValorantCog.valorant)["options"]
        if option["name"] == "등록"
    )
    assert [
        choice["name"] for choice in valorant_register["options"][1]["choices"]
    ] == ["한국", "아시아 태평양", "북미", "유럽"]
    assert [
        choice["name"] for choice in valorant_register["options"][2]["choices"]
    ] == ["컴퓨터", "콘솔"]
