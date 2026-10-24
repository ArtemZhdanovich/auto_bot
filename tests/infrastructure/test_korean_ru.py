"""Тесты хангыль → русский: транслитерация, карты, интеграция с адаптером."""

import pytest

from app.infrastructure.sources.korean import dehangul, has_hangul, translit


class TestTranslit:
    def test_simple_words(self) -> None:
        # 강남 → каннам (финаль ㅇ → н, без пробела)
        assert translit("강남") == "каннам"
        assert translit("부산") == "пусан"
        assert translit("수원") == "сувон"

    def test_vowel_boundary_space(self) -> None:
        # ㅁ-финаль + шумная инициаль → граница слова
        assert translit("김대원") == "ким тэвон"
        # финаль ㅇ + гласная инициаль → граница слова
        assert translit("경임") == "кён им"
        assert translit("경임") == "кён им"

    def test_mixed_script(self) -> None:
        # латиница/цифры не трогаются, хангыль исчезает
        assert "BMW" in translit("BMW 5시리즈")
        assert not any(0xAC00 <= ord(c) <= 0xD7A3 for c in translit("K5 하이브리드"))

    def test_non_hangul_untouched(self) -> None:
        assert translit("Toyota (GM Daewoo) 2020") == "Toyota (GM Daewoo) 2020"


class TestDehangul:
    def test_makes(self) -> None:
        assert dehangul("도요타") == "Toyota"
        assert dehangul("Kg모빌리티(쌍용)") == "KGM (SsangYong)"
        assert dehangul("쉐보레(Gm대우)") == "Chevrolet (GM Daewoo)"

    def test_models(self) -> None:
        assert dehangul("더 뉴 그랜저 IG") == "New Grandeur IG"
        assert dehangul("올 뉴 렉스턴") == "All-new Rexton"
        assert dehangul("K5 하이브리드") == "K5 Hybrid"
        assert dehangul("레인지로버 4세대") == "Range Rover 4-е поколение"

    def test_colors_and_notes(self) -> None:
        assert dehangul("진주색") == "Перламутровый"
        assert dehangul("교환") == "Замена"

    def test_summary_template(self) -> None:
        s = "Диагностика Encar (엔카진단): пройдена — 2026-08-24 부산 사상 · запись техосмотра (성능기록부): доступна"
        out = dehangul(s)
        assert "엔카" not in out and "성능" not in out
        assert out == (
            "Диагностика Encar: пройдена — 2026-08-24 пусан сасан"
            " · запись техосмотра: доступна"
        )

    def test_inspector(self) -> None:
        out = dehangul("(주)디몰성능센터 김대원")
        assert "АО" in out and "ким тэвон" in out
        assert has_hangul(out) is False

    def test_verdict_comment(self) -> None:
        assert "без ДТП" in dehangul("본 차량은 '무사고' 차량입니다")

    def test_latin_untouched(self) -> None:
        assert dehangul("No,paint/TAX100%") == "No,paint/TAX100%"

    def test_empty(self) -> None:
        assert dehangul("") == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("도요타", "Toyota"),
        ("기아 더 뉴 K3", "Kia New K3"),
        ("진주색", "Перламутровый"),
        ("E-클래스 W213", "E-класс W213"),
    ],
)
def test_word_map(value: str, expected: str) -> None:
    assert dehangul(value) == expected


def test_has_hangul() -> None:
    assert has_hangul("쏘렌토") is True
    assert has_hangul("Sorento") is False
    assert has_hangul(None) is False
    assert has_hangul(42) is False
