"""앱 테마 색. 기본은 붉은 '크림슨' 테마 (예전 Ecliptica Overlay풍 '일식' 테마도 남겨둠)."""

# 키 이름은 두 테마가 같다. corona=주 강조색, corona2=보조 강조색, blood=경고/오류
# Ecliptica DPS 미터기와 같은 색 (검붉은 배경 + 빨강 강조 + 금색 포인트)
CRIMSON = {
    "corona": "#FF3B3B",   # 주 강조 (DPS 숫자 빨강: 버튼, 선택, 내 카드 테두리)
    "corona2": "#CF2131",  # 진한 빨강 (진행 막대, 위쪽 띠, 주변 말 카드 띠)
    "mine": "#FFC83D",     # 금색 포인트 (내 말 번역 = 미터기의 'Jim 등장!!' 색)
    "ok": "#FFC83D",       # 정상 상태 점 (실행 중, VRChat 연결됨)
    "text": "#FFF0F0",     # 흰 글자
    "dim": "#F0B3B8",      # 분홍빛 라벨 글자
    "blood": "#FF9F43",    # 경고/오류 (주황)
    "blood2": "#FF8A3D",
    "outline": "#480A0D",  # 큰 숫자 테두리 느낌
    "night": "#1A0A0C",    # 배경
    "rim": "#5A1819",      # 구분선·테두리
    "band": "#2A0F13",     # 패널
}

ECLIPSE = {
    "corona": "#F4C45A",   # 금색 강조 (내 말 번역)
    "corona2": "#FF7A45",  # 주황 (그라데이션 끝, 주변 말 강조)
    "text": "#ECE8FF",     # 일반 글자
    "dim": "#CFC8F5",      # 설명 글자
    "blood": "#E0425E",    # 오류
    "blood2": "#FF8A3D",
    "outline": "#07050F",
    "night": "#17132E",    # 배경
    "rim": "#332C5E",      # 테두리
    "band": "#221C43",     # 패널
}
P = CRIMSON
FONT = "Malgun Gothic"


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """두 색을 t(0~1) 비율로 섞는다."""
    (ra, ga, ba), (rb, gb, bb) = _rgb(a), _rgb(b)
    c = lambda x, y: max(0, min(255, round(x + (y - x) * t)))
    return "#%02X%02X%02X" % (c(ra, rb), c(ga, gb), c(ba, bb))


def rgba(h, alpha):
    return _rgb(h) + (alpha,)
