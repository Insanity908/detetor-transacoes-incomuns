from datetime import datetime

import pytest

from detector.locais import ler_local, ler_momento, opcoes_local, descrever_local

AGORA = datetime(2026, 11, 19, 12, 0)


def test_ler_local():
    assert ler_local("MZ:TETE", "MAPUTO") == ("TETE", "MZ")
    assert ler_local("EXT:NG", "MAPUTO") == ("MAPUTO", "NG")
    for mau in ("EXT:US", "MZ:LISBOA", "", "TETE"):
        with pytest.raises(ValueError, match="Escolha um local"):
            ler_local(mau, "MAPUTO")


def test_opcoes_e_descricao():
    grupos = dict(opcoes_local())
    assert ("MZ:TETE", "Tete") in grupos["Moçambique"] and ("EXT:NG", "Nigéria") in grupos["Estrangeiro"]
    assert descrever_local("TETE", "MZ") == "Tete" and descrever_local("MAPUTO", "NG") == "Nigéria"


def test_ler_momento():
    assert ler_momento("", "", AGORA) == AGORA
    assert ler_momento("2026-10-21", "03:10", AGORA) == datetime(2026, 10, 21, 3, 10)
    for data, hora, erro in (("2026-11-20", "10:00", "futura"), ("2026-10-19", "10:00", "30 dias"),
                             ("19/11/2026", "10:00", "inválida"), ("2026-11-19", "25:00", "inválida")):
        with pytest.raises(ValueError, match=erro):
            ler_momento(data, hora, AGORA)
