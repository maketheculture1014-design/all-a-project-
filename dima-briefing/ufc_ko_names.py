"""UFC 한국 선수 한글 이름 매핑."""

KO_NAMES = {
    "Chan Sung Jung": "정찬성",
    "Doo Ho Choi": "최두호",
    "Sung Kyung Oh": "오성균",
    "Min Woo Kim": "김민우",
    "Kyung Ho Kang": "강경호",
    "Dong Hyun Kim": "김동현",
    "Takanori Gomi": "고미 타카노리",  # 일본 (참고용)
}


def ko_name(english_name: str) -> str:
    """한글 이름이 있으면 '한글(영문)', 없으면 영문 그대로."""
    ko = KO_NAMES.get(english_name)
    if ko:
        return f"{ko}({english_name})"
    return english_name
