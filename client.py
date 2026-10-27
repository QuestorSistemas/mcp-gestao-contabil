"""
Cliente HTTP para a API nHttp do Questor Gestão Contábil.

Suporta múltiplas conexões nomeadas:
  - Conexão "default" carregada do .env (GC_BASE_URL, GC_LOGON_NAME, GC_PASSWORD)
  - Conexões adicionais pré-configuradas no .env com prefixo GC_CONN_{nome}_*
  - Conexões dinâmicas registradas em runtime via register_client()

O token obtido no login é renovado automaticamente em caso de 401.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Indica se conexões além da "default" são permitidas.
# Controlado por GC_ALLOW_MULTI_CONN=true no .env ou via allow_multi_connection().
_multi_conn_allowed: bool = os.getenv("GC_ALLOW_MULTI_CONN", "").lower() == "true"

# Validação de certificado TLS. O padrão é desligado porque a Cloud Questor fica
# atrás de um proxy nginx com certificado que o httpx não valida, e o nHttp local
# roda em http puro. Ligue com GC_VERIFY_SSL=true onde o certificado for válido.
_verify_ssl: bool = os.getenv("GC_VERIFY_SSL", "").lower() == "true"

# Circuit breaker: depois de N timeouts seguidos, o nHttp provavelmente está com
# worker travado e toda chamada seguinte vai apenas esperar o timeout inteiro
# antes de falhar. Falhar rápido é mais útil — e não empilha mais requisições
# em cima de um servidor já engasgado.
_BREAKER_LIMITE = int(os.getenv("GC_BREAKER_FALHAS", "3"))
_BREAKER_ESPERA_S = int(os.getenv("GC_BREAKER_ESPERA", "60"))

# Registro de clientes nomeados: {"default": GestaoContabilClient, "filial2": ...}
_clients: dict[str, "GestaoContabilClient"] = {}


class ServidorIndisponivel(RuntimeError):
    """
    O nHttp parou de responder. Levantada sem ir à rede enquanto o circuit
    breaker está aberto.

    tipo:
      worker_dead       — o worker nWebExe não respondeu; provável travamento
      connection_refused — TCP indisponível, serviço parado
    """

    def __init__(self, mensagem: str, tipo: str = "worker_dead"):
        super().__init__(mensagem)
        self.tipo = tipo


class GestaoContabilClient:
    def __init__(self, base_url: str, logon_name: str, password: str):
        if not base_url or not logon_name or not password:
            raise RuntimeError("base_url, logon_name e password são obrigatórios.")

        self.base_url = base_url.rstrip("/")
        self.logon_name = logon_name
        self.password = password
        self._token: str | None = None
        self._session_cookie: str | None = None  # cookie QuestorCloud da sessão
        self._versao_cache: str | None = None
        self._objeto_cache: dict[str, list] = {}

        self._http: httpx.AsyncClient | None = None
        # O worker nWebExe do nHttp trava quando recebe duas chamadas pesadas ao
        # mesmo tempo, e depois disso o servidor inteiro para de responder até
        # alguém reiniciar o serviço. Este lock serializa as chamadas pesadas;
        # as leves (versão, menus, consulta) seguem em paralelo.
        self._heavy_lock = asyncio.Lock()

        # Estado do circuit breaker
        self._falhas_seguidas = 0
        self._aberto_ate: float = 0.0
        self._ultimo_tipo: str = "worker_dead"

    # -- Circuit breaker ----------------------------------------------------

    def _checar_breaker(self) -> None:
        """Falha rápido enquanto o breaker estiver aberto, sem tocar na rede."""
        if self._aberto_ate and time.monotonic() < self._aberto_ate:
            restante = int(self._aberto_ate - time.monotonic()) + 1
            if self._ultimo_tipo == "connection_refused":
                motivo = (
                    f"O servidor {self.base_url} recusou a conexão "
                    f"{self._falhas_seguidas}x seguidas. O serviço Questor.nHttp "
                    f"provavelmente está parado."
                )
            else:
                motivo = (
                    f"O servidor {self.base_url} não respondeu "
                    f"{self._falhas_seguidas}x seguidas. O worker nWebExe_Cx* "
                    f"provavelmente travou — no servidor, encerre os processos "
                    f"nWebExe_Cx* ou reinicie o serviço Questor.nHttp."
                )
            raise ServidorIndisponivel(
                f"{motivo} Novas tentativas liberadas em ~{restante}s.",
                tipo=self._ultimo_tipo,
            )

    def _registrar_falha(self, tipo: str) -> None:
        self._falhas_seguidas += 1
        self._ultimo_tipo = tipo
        if self._falhas_seguidas >= _BREAKER_LIMITE:
            self._aberto_ate = time.monotonic() + _BREAKER_ESPERA_S
            # A sessão quase certamente morreu junto com o worker.
            self._token = None
            self._session_cookie = None

    def _registrar_sucesso(self) -> None:
        self._falhas_seguidas = 0
        self._aberto_ate = 0.0

    @property
    def saudavel(self) -> bool:
        return not (self._aberto_ate and time.monotonic() < self._aberto_ate)

    def _client(self) -> httpx.AsyncClient:
        """Cliente HTTP persistente: reaproveita a conexão em vez de reabrir a cada request."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                verify=_verify_ssl,
                timeout=httpx.Timeout(120.0, connect=30.0),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._http

    async def aclose(self) -> None:
        """Fecha a conexão persistente. Seguro chamar mais de uma vez."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    async def _login(self) -> str:
        self._checar_breaker()
        try:
            resp = await self._client().post(
                f"{self.base_url}/home/LoginModulo",
                params={"LogonName": self.logon_name, "PlainPassword": self.password},
                timeout=30,
            )
        except httpx.TimeoutException:
            self._registrar_falha("worker_dead")
            raise
        except httpx.ConnectError:
            self._registrar_falha("connection_refused")
            raise

        self._registrar_sucesso()
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"Login recusado pelo Questor ({resp.status_code}). Verifique "
                f"GC_LOGON_NAME (formato usuario@conexao) e GC_PASSWORD no .env."
            )
        resp.raise_for_status()

        # Armazena cookie de sessão (QuestorCloud) para enviar em todas as requests
        self._session_cookie = resp.cookies.get("QuestorCloud")
        try:
            data = resp.json()
            token = (
                data.get("Token") or data.get("token")
                or data.get("BearerToken") or data.get("bearerToken")
            )
            if not token:
                raise RuntimeError(f"Token não encontrado na resposta: {data}")
            return token
        except (json.JSONDecodeError, AttributeError):
            token = resp.text.strip().strip('"')
            if not token:
                raise RuntimeError("Token vazio retornado pelo login")
            return token

    async def _get_token(self) -> str:
        if not self._token:
            self._token = await self._login()
        return self._token

    def _auth_headers(self, token: str, has_body: bool = False) -> dict:
        headers: dict = {}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self._session_cookie:
            headers["Cookie"] = f"QuestorCloud={self._session_cookie}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: Any = None,
        retry: bool = True,
        pesado: bool = False,
    ) -> Any:
        """
        pesado=True serializa a chamada com as demais pesadas desta conexão.
        Duas chamadas pesadas simultâneas travam o worker do nHttp de forma
        permanente (reproduzido em 07/05/2026).
        """
        if pesado:
            async with self._heavy_lock:
                return await self._executar(method, path, params, body, retry, pesado)
        return await self._executar(method, path, params, body, retry, pesado)

    async def _executar(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: Any = None,
        retry: bool = True,
        pesado: bool = False,
    ) -> Any:
        self._checar_breaker()

        token = await self._get_token()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        clean_params["token"] = token

        try:
            resp = await self._client().request(
                method=method,
                url=f"{self.base_url}{path}",
                headers=self._auth_headers(token, has_body=body is not None),
                params=clean_params,
                json=body,
            )
        except httpx.TimeoutException:
            self._registrar_falha("worker_dead")
            raise
        except httpx.ConnectError:
            self._registrar_falha("connection_refused")
            raise

        # Resposta recebida: o servidor está vivo, mesmo que devolva erro de negócio.
        self._registrar_sucesso()

        if resp.status_code == 401 and retry:
            self._token = None
            self._session_cookie = None
            # Já estamos dentro do lock quando pesado=True; repetir por _executar
            # evita tentar readquiri-lo e travar a si mesmo.
            return await self._executar(method, path, params, body, retry=False, pesado=pesado)

        if resp.status_code >= 400:
            detail = resp.text[:500] if resp.content else ""
            raise httpx.HTTPStatusError(
                f"{resp.status_code} {resp.reason_phrase}: {detail}",
                request=resp.request,
                response=resp,
            )

        if resp.content:
            try:
                return resp.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                try:
                    return {"resultado": resp.content.decode("latin-1")}
                except UnicodeDecodeError:
                    import base64 as _b64
                    return {"dados_base64": _b64.b64encode(resp.content).decode("ascii")}
        return {"sucesso": True}

    async def get(
        self, path: str, params: dict | None = None, body: Any = None, pesado: bool = False
    ) -> Any:
        return await self._request("GET", path, params=params, body=body, pesado=pesado)

    async def post(
        self, path: str, params: dict | None = None, body: Any = None, pesado: bool = False
    ) -> Any:
        return await self._request("POST", path, params=params, body=body, pesado=pesado)

    async def versao_questor(self) -> str:
        """Consulta a versão do Questor no servidor. Chamada leve."""
        resp = await self.get("/api/TnWebDMDadosGerais/PegarVersaoQuestor")
        if isinstance(resp, dict):
            return str(resp.get("Versao") or resp.get("versao") or resp)
        return str(resp) if resp else "desconhecida"

    async def revalidar_versao(self) -> tuple[str | None, str]:
        """
        Relê a versão do Questor. Se mudou desde o último cache, descarta os
        parâmetros de rotina em memória — um upgrade do sistema pode ter alterado
        os campos obrigatórios.

        Retorna (versao_anterior, versao_atual).
        """
        anterior = self._versao_cache
        try:
            atual = await self.versao_questor()
        except Exception:
            atual = "desconhecida"

        if anterior is not None and atual != anterior:
            self._objeto_cache.clear()
        self._versao_cache = atual
        return anterior, atual

    async def get_objeto_params(self, action_name: str) -> list[dict]:
        """
        Retorna os parâmetros de uma rotina, com cache em memória.

        A versão do Questor é registrada na primeira chamada. A comparação que
        de fato invalida o cache acontece em revalidar_versao(), disparada pela
        tool gc_cache_limpar — não a cada chamada, para não gastar um request
        extra contra um servidor que trava sob carga.
        """
        if self._versao_cache is None:
            try:
                self._versao_cache = await self.versao_questor()
            except Exception:
                self._versao_cache = "desconhecida"

        if action_name not in self._objeto_cache:
            resp = await self.get(
                "/api/TnWebDMDadosObjetos/Pegar",
                params={"_AActionName": action_name},
                pesado=True,
            )
            if isinstance(resp, list):
                params_list = resp
            elif isinstance(resp, dict):
                params_list = (
                    resp.get("Fields") or resp.get("fields")
                    or resp.get("Parametros") or resp.get("parametros")
                    or []
                )
            else:
                params_list = []
            self._objeto_cache[action_name] = params_list

        return self._objeto_cache[action_name]

    def invalidar_cache(self) -> None:
        self._objeto_cache.clear()
        self._versao_cache = None

    async def logout(self) -> None:
        if self._token:
            try:
                await self.post("/home/logout")
            finally:
                self._token = None
                self._session_cookie = None
                await self.aclose()

    @property
    def token(self) -> str | None:
        return self._token


# ---------------------------------------------------------------------------
# Gerenciamento de conexões
# ---------------------------------------------------------------------------

def _load_default() -> None:
    """Carrega a conexão 'default' e conexões nomeadas do .env."""
    base_url = os.getenv("GC_BASE_URL", "")
    logon_name = os.getenv("GC_LOGON_NAME", "")
    password = os.getenv("GC_PASSWORD", "")

    if not base_url or not logon_name or not password:
        raise RuntimeError(
            "GC_BASE_URL, GC_LOGON_NAME e GC_PASSWORD não configurados. "
            "Execute 'python setup_config.py' primeiro."
        )
    _clients["default"] = GestaoContabilClient(base_url, logon_name, password)

    # Conexões adicionais: GC_CONN_{nome}_BASE_URL / _LOGON_NAME / _PASSWORD
    prefixes: set[str] = set()
    for key in os.environ:
        if key.startswith("GC_CONN_") and key.endswith("_BASE_URL"):
            prefixes.add(key[len("GC_CONN_"):-len("_BASE_URL")])

    for name in prefixes:
        u = os.getenv(f"GC_CONN_{name}_BASE_URL", "")
        l = os.getenv(f"GC_CONN_{name}_LOGON_NAME", "")
        p = os.getenv(f"GC_CONN_{name}_PASSWORD", "")
        if u and l and p:
            _clients[name.lower()] = GestaoContabilClient(u, l, p)


def get_client(conexao: str = "default") -> GestaoContabilClient:
    """
    Retorna o cliente para a conexão especificada.
    Múltiplas conexões só são permitidas se GC_ALLOW_MULTI_CONN=true no .env
    ou após chamar allow_multi_connection().
    """
    if not _clients:
        _load_default()

    name = conexao.lower()

    if name != "default" and not _multi_conn_allowed:
        raise PermissionError(
            "Múltiplas conexões não estão habilitadas. "
            "Defina GC_ALLOW_MULTI_CONN=true no .env para permitir."
        )

    if name not in _clients:
        raise KeyError(
            f"Conexão '{name}' não encontrada. "
            f"Conexões disponíveis: {list_connections()}"
        )

    return _clients[name]


def register_client(nome: str, base_url: str, logon_name: str, password: str) -> None:
    """Registra uma nova conexão nomeada em runtime."""
    if not _clients:
        _load_default()
    if not _multi_conn_allowed:
        raise PermissionError(
            "Múltiplas conexões não estão habilitadas. "
            "Defina GC_ALLOW_MULTI_CONN=true no .env para permitir."
        )
    _clients[nome.lower()] = GestaoContabilClient(base_url, logon_name, password)


def list_connections() -> list[str]:
    """Lista os nomes das conexões registradas."""
    if not _clients:
        _load_default()
    return sorted(_clients.keys())


def allow_multi_connection() -> None:
    """Habilita suporte a múltiplas conexões em runtime (sem reiniciar)."""
    global _multi_conn_allowed
    _multi_conn_allowed = True
