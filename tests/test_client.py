"""
Testes do cliente HTTP do MCP Gestão Contábil.

Rodar:  pip install -e ".[dev]" && pytest

Nenhum teste toca a rede: o transporte do httpx é substituído por um mock que
registra as requisições. O foco é o que pode quebrar em produção — a
serialização que protege o nHttp, o retry de 401 e o cache.
"""

import asyncio

import httpx
import pytest

import client as client_mod
from client import GestaoContabilClient


def montar(transport: httpx.MockTransport) -> GestaoContabilClient:
    """Cria um client apontando para um transporte falso."""
    c = GestaoContabilClient("http://nhttp.teste", "administrador@questor", "senha")
    c._http = httpx.AsyncClient(transport=transport, timeout=5.0)
    return c


def resposta_login() -> httpx.Response:
    return httpx.Response(200, json={"Token": "tok-123"}, headers={"Set-Cookie": "QuestorCloud=abc"})


# ---------------------------------------------------------------------------
# Serialização — a proteção contra o deadlock do nHttp
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chamadas_pesadas_nao_correm_em_paralelo():
    """Duas chamadas pesadas simultâneas travam o worker do nHttp — nunca podem sobrepor."""
    simultaneas = 0
    pico = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal simultaneas, pico
        if "LoginModulo" in request.url.path:
            return resposta_login()
        simultaneas += 1
        pico = max(pico, simultaneas)
        await asyncio.sleep(0.05)
        simultaneas -= 1
        return httpx.Response(200, json={"ok": True})

    c = montar(httpx.MockTransport(handler))
    await asyncio.gather(*[c.get("/api/pesada", pesado=True) for _ in range(5)])

    assert pico == 1, f"{pico} chamadas pesadas sobrepostas — o nHttp travaria"


@pytest.mark.asyncio
async def test_chamadas_leves_correm_em_paralelo():
    """As leves não podem ser penalizadas pelo lock das pesadas."""
    simultaneas = 0
    pico = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal simultaneas, pico
        if "LoginModulo" in request.url.path:
            return resposta_login()
        simultaneas += 1
        pico = max(pico, simultaneas)
        await asyncio.sleep(0.05)
        simultaneas -= 1
        return httpx.Response(200, json={"ok": True})

    c = montar(httpx.MockTransport(handler))
    await c.get("/api/aquece")  # login fora da medição
    await asyncio.gather(*[c.get("/api/leve") for _ in range(4)])

    assert pico > 1, "as chamadas leves ficaram serializadas sem necessidade"


@pytest.mark.asyncio
async def test_retry_401_dentro_do_lock_nao_causa_deadlock():
    """
    Regressão: se o retry de 401 voltasse por _request, tentaria readquirir um
    lock já na mão e o MCP travaria sozinho.
    """
    chamadas = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    c = montar(httpx.MockTransport(handler))
    r = await asyncio.wait_for(c.get("/api/pesada", pesado=True), timeout=2.0)

    assert r == {"ok": True}
    assert chamadas["n"] == 2, "deveria ter repetido a chamada uma vez após o 401"


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_e_cookie_vao_em_toda_request():
    vistos = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        vistos["token"] = request.url.params.get("token")
        vistos["cookie"] = request.headers.get("Cookie")
        return httpx.Response(200, json={})

    c = montar(httpx.MockTransport(handler))
    await c.get("/api/qualquer")

    assert vistos["token"] == "tok-123"
    assert vistos["cookie"] == "QuestorCloud=abc", "a Cloud rejeita request sem o cookie"


@pytest.mark.asyncio
async def test_credencial_errada_da_mensagem_util():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    c = montar(httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="GC_LOGON_NAME"):
        await c.get("/api/qualquer")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parametros_sao_buscados_uma_vez_so():
    chamadas = {"objeto": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        if "PegarVersaoQuestor" in request.url.path:
            return httpx.Response(200, json={"Versao": "26.4.0.1"})
        chamadas["objeto"] += 1
        return httpx.Response(200, json=[{"Name": "Empresa", "Required": True}])

    c = montar(httpx.MockTransport(handler))
    a = await c.get_objeto_params("nCtbRRBalancete")
    b = await c.get_objeto_params("nCtbRRBalancete")

    assert a == b
    assert chamadas["objeto"] == 1, "a segunda chamada deveria sair do cache"


@pytest.mark.asyncio
async def test_versao_nova_descarta_o_cache():
    """O contrato que o CLAUDE.md promete: upgrade do Questor invalida o cache."""
    versao = {"v": "26.4.0.1"}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        if "PegarVersaoQuestor" in request.url.path:
            return httpx.Response(200, json={"Versao": versao["v"]})
        return httpx.Response(200, json=[{"Name": "Empresa", "Required": True}])

    c = montar(httpx.MockTransport(handler))
    await c.get_objeto_params("nCtbRRBalancete")
    assert c._objeto_cache

    versao["v"] = "26.5.0.0"
    anterior, atual = await c.revalidar_versao()

    assert (anterior, atual) == ("26.4.0.1", "26.5.0.0")
    assert not c._objeto_cache, "a mudança de versão deveria ter descartado o cache"


@pytest.mark.asyncio
async def test_mesma_versao_preserva_o_cache():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        if "PegarVersaoQuestor" in request.url.path:
            return httpx.Response(200, json={"Versao": "26.4.0.1"})
        return httpx.Response(200, json=[{"Name": "Empresa", "Required": True}])

    c = montar(httpx.MockTransport(handler))
    await c.get_objeto_params("nCtbRRBalancete")
    await c.revalidar_versao()

    assert c._objeto_cache, "sem upgrade, o cache deve continuar de pé"


# ---------------------------------------------------------------------------
# Respostas não-JSON
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resposta_texto_puro_vira_campo_resultado():
    """Exportação NLI devolve CSV cru, sem envelope JSON."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        return httpx.Response(200, content="COD;NOME\n1;Banco".encode("latin-1"))

    c = montar(httpx.MockTransport(handler))
    r = await c.get("/api/Integracao/Exportar")

    assert "resultado" in r and "Banco" in r["resultado"]


@pytest.mark.asyncio
async def test_erro_http_traz_o_corpo_da_resposta():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        return httpx.Response(500, text="Cliente nao respondeu em 30s: nWebExe_Cx...")

    c = montar(httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError, match="nWebExe_Cx"):
        await c.get("/api/pesada")


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_breaker_abre_apos_n_timeouts_e_falha_rapido():
    """
    Depois de N timeouts seguidos o servidor está travado: as chamadas
    seguintes têm de falhar na hora, sem ir à rede e sem esperar o timeout.
    """
    idas_a_rede = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        idas_a_rede["n"] += 1
        raise httpx.ReadTimeout("timeout", request=request)

    c = montar(httpx.MockTransport(handler))

    for _ in range(client_mod._BREAKER_LIMITE):
        with pytest.raises(httpx.TimeoutException):
            await c.get("/api/x")

    antes = idas_a_rede["n"]
    with pytest.raises(client_mod.ServidorIndisponivel, match="nWebExe_Cx"):
        await c.get("/api/x")

    assert idas_a_rede["n"] == antes, "com o breaker aberto não se deve tocar a rede"
    assert not c.saudavel


@pytest.mark.asyncio
async def test_breaker_distingue_servico_parado_de_worker_travado():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado", request=request)

    c = montar(httpx.MockTransport(handler))
    for _ in range(client_mod._BREAKER_LIMITE):
        with pytest.raises(httpx.ConnectError):
            await c.get("/api/x")

    with pytest.raises(client_mod.ServidorIndisponivel) as e:
        await c.get("/api/x")

    assert e.value.tipo == "connection_refused"
    assert "Questor.nHttp" in str(e.value)


@pytest.mark.asyncio
async def test_uma_resposta_boa_zera_o_contador():
    """Timeouts esparsos não podem abrir o breaker."""
    falhar = {"sim": True}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        if falhar["sim"]:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"ok": True})

    c = montar(httpx.MockTransport(handler))

    for _ in range(client_mod._BREAKER_LIMITE - 1):
        with pytest.raises(httpx.TimeoutException):
            await c.get("/api/x")

    falhar["sim"] = False
    await c.get("/api/x")
    assert c._falhas_seguidas == 0

    falhar["sim"] = True
    with pytest.raises(httpx.TimeoutException):
        await c.get("/api/x")
    assert c.saudavel, "uma falha isolada após sucesso não pode abrir o breaker"


@pytest.mark.asyncio
async def test_breaker_reabre_apos_a_janela_de_espera(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        return httpx.Response(200, json={"ok": True})

    c = montar(httpx.MockTransport(handler))
    c._falhas_seguidas = client_mod._BREAKER_LIMITE
    c._aberto_ate = 0.0  # janela já expirada

    assert c.saudavel
    assert await c.get("/api/x") == {"ok": True}


@pytest.mark.asyncio
async def test_erro_de_negocio_nao_conta_como_servidor_morto():
    """500 do Questor é resposta — o servidor está vivo."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if "LoginModulo" in request.url.path:
            return resposta_login()
        return httpx.Response(500, text="erro de negocio")

    c = montar(httpx.MockTransport(handler))
    for _ in range(client_mod._BREAKER_LIMITE + 2):
        with pytest.raises(httpx.HTTPStatusError):
            await c.get("/api/x")

    assert c.saudavel, "erro HTTP não é indisponibilidade"
    # Checar só `saudavel` não bastaria: _registrar_sucesso() roda antes do
    # tratamento de status, então um _registrar_falha no ramo >=400 seria
    # zerado a cada volta e nunca abriria o breaker — o teste passaria à toa.
    assert c._falhas_seguidas == 0, "resposta HTTP recebida não pode contar como falha"


# ---------------------------------------------------------------------------
# Múltiplas conexões
# ---------------------------------------------------------------------------

def test_conexao_extra_bloqueada_por_padrao(monkeypatch):
    monkeypatch.setattr(client_mod, "_multi_conn_allowed", False)
    monkeypatch.setitem(client_mod._clients, "default", object())
    with pytest.raises(PermissionError, match="GC_ALLOW_MULTI_CONN"):
        client_mod.get_client("filial2")


def test_conexao_inexistente_com_multi_ligado(monkeypatch):
    monkeypatch.setattr(client_mod, "_multi_conn_allowed", True)
    monkeypatch.setitem(client_mod._clients, "default", object())
    with pytest.raises(KeyError):
        client_mod.get_client("nao_existe")
