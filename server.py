"""
Servidor MCP para a API nHttp do Questor Gestão Contábil.

O nHttp é um servidor HTTP local (Windows) que expõe dados e rotinas
do Questor Gestão Contábil via REST/JSON.

Pré-requisito: execute 'python setup_config.py' uma vez para configurar
               GC_BASE_URL, GC_LOGON_NAME e GC_PASSWORD no arquivo .env

Múltiplas conexões: defina GC_ALLOW_MULTI_CONN=true no .env e use o
parâmetro conexao="nome" nos tools. Conexões adicionais podem ser
pré-configuradas no .env (GC_CONN_{nome}_BASE_URL / _LOGON_NAME / _PASSWORD)
ou registradas em runtime via gc_conexao_adicionar.

Iniciar: python server.py
"""

import base64 as _base64
import configparser
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__version__ = "0.3.1-beta"

import asyncio

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

import atualizacao
from client import get_client, list_connections, register_client


class _GestaoContabilMCP(FastMCP):
    """
    FastMCP que avisa, uma vez por sessão, quando há versão nova publicada.

    A verificação roda em segundo plano na primeira chamada — a resposta não
    espera pelo GitHub — e o aviso é anexado à primeira resposta que encontrar
    ele pronto. Assim o cliente fica sabendo sem ninguém precisar avisá-lo, que
    era o pedido; e sem atualizar nada sozinho, que era a condição.
    """

    async def call_tool(self, name, arguments):
        if getattr(self, "_verificacao", None) is None:
            self._verificacao = asyncio.create_task(atualizacao.preparar_aviso(__version__))

        resultado = await super().call_tool(name, arguments)

        # Quem já está atualizando não precisa ser avisado de que pode atualizar.
        if name == "gc_atualizar":
            return resultado

        aviso = atualizacao.consumir_aviso()
        if not aviso:
            return resultado

        bloco = TextContent(type="text", text=aviso.strip())
        # O FastMCP devolve lista de conteúdo, ou (lista, estruturado) quando a
        # tool tem retorno tipado. Nos dois casos o aviso vai no fim do texto.
        if isinstance(resultado, tuple):
            conteudo, estruturado = resultado
            return [*conteudo, bloco], estruturado
        return [*resultado, bloco]


mcp = _GestaoContabilMCP(
    "gestao-contabil",
    instructions="""
Você é um assistente integrado ao Questor Gestão Contábil via API Cloud (nHttp).

## Autenticação
Login é AUTOMÁTICO — nunca chame gc_login manualmente. O cliente gerencia token e sessão.

## Fluxo obrigatório para executar relatórios ou processos

1. **Descobrir o action_name** (se não souber):
   - gc_menus(tipo=4) → relatórios
   - gc_menus(tipo=3) → processos
   - gc_menus(tipo=1) → cadastros
   - gc_menus(tipo=4, filtro="empresa") → busca relatórios com "empresa"
   - gc_menus(tipo=2, modulo="Contabilidade") → consultas do módulo Contabilidade

2. **Inspecionar parâmetros ANTES de executar**:
   - gc_inspecionar("action_name") → mostra obrigatórios, opcionais, valores aceitos e labels

3. **Executar com todos os obrigatórios preenchidos**:
   - gc_relatorio_executar("action_name", parametros_json={...})
   - gc_processo_executar("action_name", parametros_json={...})

## Regras importantes
- SEMPRE chame gc_inspecionar antes de executar — nunca tente adivinhar parâmetros
- Os campos obrigatórios estão em "obrigatorios"; se não passá-los todos, a execução falha
- Valores válidos de cada campo estão em "valores_aceitos" do gc_inspecionar
- Para relatórios PDF: base64=True (padrão), tipo_retorno="nrwexPDF"
- O resultado PDF/NHZ vem em base64 no campo "Data"
""",
)

_CONN_DOC = (
    'conexao: nome da conexão a usar (padrão "default"). '
    "Múltiplas conexões requerem GC_ALLOW_MULTI_CONN=true no .env."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_download_dir() -> Path:
    """Retorna (e garante existência de) a pasta de downloads configurada em GC_DOWNLOAD_DIR."""
    raw = os.getenv("GC_DOWNLOAD_DIR", "")
    folder = Path(raw) if raw else Path.home() / "Downloads" / "gestao-contabil"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _parse_json(value: str | dict | list | None, context: str) -> Any:
    """Faz parse de string JSON, levantando erro descritivo em caso de falha.
    Aceita também dict/list já parseados (enviados por clientes MCP que auto-parseiam JSON)."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON inválido em '{context}': {e}") from e


async def _validar_params(action_name: str, params: dict, conexao: str) -> list[str]:
    """Retorna lista de campos obrigatórios ausentes. Usa cache."""
    schema = await get_client(conexao).get_objeto_params(action_name)
    obrigatorios = [
        p["Name"] for p in schema
        if p.get("Required") is True or str(p.get("Required", "")).lower() == "true"
    ]
    return [campo for campo in obrigatorios if campo not in params]


def _extrair_grids_resultado(resultado: dict) -> list[dict]:
    """Percorre Widgets e retorna todos os grids que têm items."""
    grids: list[dict] = []

    def _visitar(obj: Any) -> None:
        if isinstance(obj, dict):
            if "items" in obj and "columns" in obj and isinstance(obj.get("items"), list) and obj["items"]:
                grids.append({
                    "nomegrid": obj.get("nomegrid", "grid"),
                    "caption": obj.get("Caption", ""),
                    "columns": obj.get("columns", []),
                    "items": obj["items"],
                    "totalcount": obj.get("totalcount", len(obj["items"])),
                })
            else:
                for v in obj.values():
                    _visitar(v)
        elif isinstance(obj, list):
            for item in obj:
                _visitar(item)

    _visitar(resultado.get("Widgets", {}))
    return grids


def _salvar_grids_csv(grids: list[dict], action_name: str) -> list[dict]:
    """Salva cada grid em um CSV no download dir. Retorna lista de infos."""
    import csv

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    download_dir = _get_download_dir()
    arquivos = []

    for grid in grids:
        cols_defs = [
            c for c in grid["columns"]
            if c.get("dataField")
            and c.get("dataField") != "INDICE"
            and c.get("visible", True) is not False
            and c.get("type") != "buttons"
        ]
        if not cols_defs:
            cols_defs = [c for c in grid["columns"] if c.get("dataField") and c.get("type") != "buttons"]

        fields = [c["dataField"] for c in cols_defs]
        headers = [c.get("caption", c["dataField"]) for c in cols_defs]

        suffix = f"_{grid['nomegrid']}" if len(grids) > 1 else ""
        path = download_dir / f"{action_name}{suffix}_{ts}.csv"

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(headers)
            for row in grid["items"]:
                writer.writerow([row.get(field, "") for field in fields])

        arquivos.append({
            "arquivo": str(path),
            "grid": grid["nomegrid"],
            "registros_salvos": len(grid["items"]),
            "total_servidor": grid["totalcount"],
        })

    return arquivos


# ---------------------------------------------------------------------------
# GERENCIAMENTO DE CONEXÕES
# ---------------------------------------------------------------------------

@mcp.tool()
async def gc_conexao_listar() -> dict:
    """
    Lista todas as conexões registradas (default + adicionais).
    A conexão "default" é sempre carregada do .env.
    Conexões extras são adicionadas via gc_conexao_adicionar ou
    pré-configuradas no .env com prefixo GC_CONN_{nome}_*.
    """
    return {"conexoes": list_connections()}


@mcp.tool()
async def gc_conexao_adicionar(
    nome: str,
    base_url: str,
    logon_name: str,
    password: str,
) -> dict:
    """
    Registra uma nova conexão nomeada em runtime.
    Requer GC_ALLOW_MULTI_CONN=true no .env.

    nome:       identificador da conexão (ex: "filial2", "empresa_sp")
    base_url:   URL base do servidor nHttp (ex: "http://seu-servidor:8080")
    logon_name: usuário no formato usuario@conexao (ex: "admin@empresa2")
    password:   senha do usuário

    Após registrar, use conexao="nome" em qualquer tool para usar esta conexão.
    """
    register_client(nome, base_url, logon_name, password)
    return {"sucesso": True, "conexao": nome.lower(), "base_url": base_url}


def _slugify(nome_secao: str) -> str:
    """Converte nome de seção do INI em identificador válido para conexão.
    Ex: '2332795 antares produto' → '2332795_antares_produto'
    """
    slug = nome_secao.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug


def _parse_conexao_ini(caminho: str) -> list[dict]:
    """Lê um Questor.Conexao.ini e retorna lista de seções com seus dados."""
    parser = configparser.ConfigParser()
    parser.read(caminho, encoding="utf-8-sig")

    conexoes = []
    for secao in parser.sections():
        dados = dict(parser[secao])
        conexoes.append({
            "secao": secao,
            "slug": _slugify(secao),
            "servidor": dados.get("servidor", ""),
            "porta": dados.get("portaconexao", ""),
            "banco": dados.get("arqbancodados", ""),
            "tipo_banco": dados.get("tipobancodados", ""),
            "usuario_banco": dados.get("usuariobancodados", ""),
        })
    return conexoes


@mcp.tool()
async def gc_conexao_importar_ini(
    caminho_ini: str,
    base_url: Optional[str] = None,
    usuario: Optional[str] = None,
    password: Optional[str] = None,
    registrar: bool = True,
) -> dict:
    """
    Lê um arquivo Questor.Conexao.ini e registra todas as conexões encontradas.

    Cada seção do INI vira uma conexão nomeada. O logon_name é montado como
    usuario@{nome_da_seção} (ex: "administrador@2332795 antares").

    caminho_ini: caminho completo do arquivo Questor.Conexao.ini
    base_url:    URL do servidor nHttp (padrão: usa a mesma da conexão default)
    usuario:     parte do usuário no logon (padrão: extrai da conexão default, ex: "administrador")
    password:    senha (padrão: usa a mesma da conexão default)
    registrar:   True = registra as conexões imediatamente (padrão True).
                 False = apenas lista o que seria registrado (dry-run).

    Requer GC_ALLOW_MULTI_CONN=true no .env.

    Exemplo: gc_conexao_importar_ini("D:/Questor/Questor.Conexao.ini")
    """
    ini_path = Path(caminho_ini)
    if not ini_path.exists():
        return {"erro": f"Arquivo não encontrado: {caminho_ini}"}

    # Defaults da conexão default
    default_client = get_client("default")
    default_base_url = base_url or default_client.base_url
    default_password = password or default_client.password

    if usuario:
        default_usuario = usuario
    else:
        # Extrai parte antes do @ do logon_name (ex: "cloud1@1" → "cloud1")
        logon = default_client.logon_name
        default_usuario = logon.split("@")[0] if "@" in logon else logon

    conexoes = _parse_conexao_ini(str(ini_path))

    if not conexoes:
        return {"erro": "Nenhuma seção encontrada no arquivo INI."}

    resultados = []
    for conn in conexoes:
        logon_name = f"{default_usuario}@{conn['secao']}"
        slug = conn["slug"]

        info = {
            "nome": slug,
            "secao_ini": conn["secao"],
            "logon_name": logon_name,
            "base_url": default_base_url,
            "servidor_db": conn["servidor"],
            "banco": conn["banco"],
            "tipo_banco": conn["tipo_banco"],
        }

        if registrar:
            try:
                register_client(slug, default_base_url, logon_name, default_password)
                info["status"] = "registrado"
            except PermissionError as e:
                info["status"] = f"erro: {e}"
            except Exception as e:
                info["status"] = f"erro: {e}"
        else:
            info["status"] = "dry-run"

        resultados.append(info)

    return {
        "arquivo": str(ini_path),
        "total_secoes": len(conexoes),
        "registradas": sum(1 for r in resultados if r["status"] == "registrado"),
        "conexoes": resultados,
        "dica": "Use gc_conexao_listar() para ver todas as conexões disponíveis.",
    }


# ---------------------------------------------------------------------------
# CONEXÃO / AUTENTICAÇÃO
# ---------------------------------------------------------------------------

@mcp.tool()
async def gc_versao(conexao: str = "default") -> dict:
    """
    Retorna a versão do MCP e do Questor Gestão Contábil.
    Use para testar conexão e para rastreabilidade em diagnósticos.

    Retorna:
      mcp_versao:    versão deste servidor MCP (gestao-contabil-mcp)
      questor_versao: versão do Questor Gestão Contábil no servidor nHttp
      base_url:       URL do servidor conectado
    """
    client = get_client(conexao)
    questor = await client.get("/api/TnWebDMDadosGerais/PegarVersaoQuestor")
    questor_versao = questor.get("Versao", str(questor)) if isinstance(questor, dict) else str(questor)
    return {
        "mcp_versao": __version__,
        "questor_versao": questor_versao,
        "base_url": client.base_url,
        "conexao": conexao,
        "atualizacao": await atualizacao.verificar(__version__),
    }


@mcp.tool()
async def gc_atualizar(confirmar: bool = False) -> dict:
    """
    Atualiza este MCP para a última versão publicada no GitHub.

    Sem confirmar, só mostra o que vai acontecer: versão atual e nova, o que
    será preservado e onde fica o backup. Com confirmar=True, baixa e aplica.

    O .env (URL, usuário e senha do nHttp) e as credenciais NÃO são tocados, e
    a versão atual fica guardada numa pasta de backup ao lado. Depois de
    atualizar é preciso reiniciar o Claude.

    Use quando aparecer o aviso de versão nova, ou quando gc_versao indicar
    atualizacao_disponivel=True.
    """
    return await atualizacao.aplicar(__version__, confirmar=confirmar)


@mcp.tool()
async def gc_health(conexao: str = "default") -> dict:
    """
    Diz se o servidor nHttp está respondendo, sem levantar erro.

    Use quando várias chamadas seguidas falharem, para distinguir "o MCP está
    ruim" de "o servidor travou" — e para saber se já dá para tentar de novo.

    saudavel=False significa que o circuit breaker está aberto: o servidor
    falhou várias vezes seguidas e as chamadas estão sendo recusadas de
    imediato, sem ir à rede, até a janela de espera passar.
    """
    client = get_client(conexao)
    resultado: dict = {
        "conexao": conexao,
        "base_url": client.base_url,
        "saudavel": client.saudavel,
        "falhas_seguidas": client._falhas_seguidas,
    }

    if not client.saudavel:
        resultado["diagnostico"] = client._ultimo_tipo
        resultado["acao"] = (
            "No servidor, encerre os processos nWebExe_Cx* ou reinicie o serviço "
            "Questor.nHttp."
            if client._ultimo_tipo == "worker_dead"
            else "O serviço Questor.nHttp parece parado — inicie-o no Painel de Serviços."
        )
        return resultado

    try:
        versao = await client.versao_questor()
        resultado["respondendo"] = True
        resultado["questor_versao"] = versao
    except Exception as e:
        resultado["respondendo"] = False
        resultado["erro"] = f"{type(e).__name__}: {e}"

    return resultado


@mcp.tool()
async def gc_login(conexao: str = "default") -> dict:
    """
    Autentica no servidor nHttp e retorna o Bearer Token de sessão.
    Normalmente não é necessário — o cliente gerencia o token automaticamente.
    Use para diagnóstico ou para forçar renovação do token.
    """
    client = get_client(conexao)
    client._token = None
    token = await client._get_token()
    return {"conexao": conexao, "token": token}


@mcp.tool()
async def gc_logout(conexao: str = "default") -> dict:
    """
    Encerra a sessão atual no servidor nHttp.
    O próximo request fará login automaticamente.
    """
    await get_client(conexao).logout()
    return {"sucesso": True, "conexao": conexao}



# ---------------------------------------------------------------------------
# MENUS — DESCOBERTA DE ROTINAS
# ---------------------------------------------------------------------------

def _extrair_flat(no: dict, termo: str | None) -> list[dict]:
    """Percorre a árvore e retorna lista plana de {Name, Text} dos nós folha.
    Se termo fornecido, filtra pelo termo em Name ou Text (case-insensitive)."""
    resultado = []
    if "Name" in no:
        if termo is None or termo in no.get("Text", "").lower() or termo in no["Name"].lower():
            resultado.append({"Name": no["Name"], "Text": no.get("Text", no["Name"])})
    for filho in no.get("Childs", []):
        resultado.extend(_extrair_flat(filho, termo))
    return resultado


def _podar_arvore(no: dict, termo: str) -> dict | None:
    """
    Percorre a árvore recursivamente e retorna apenas os galhos que contêm
    o termo no Text ou Name. Preserva a hierarquia de pais para contexto.
    Retorna None se o nó e nenhum filho contiver o termo.
    """
    text = no.get("Text", "").lower()
    name = no.get("Name", "").lower()
    match_direto = termo in text or termo in name

    filhos_filtrados = []
    for filho in no.get("Childs", []):
        filho_podado = _podar_arvore(filho, termo)
        if filho_podado is not None:
            filhos_filtrados.append(filho_podado)

    if not match_direto and not filhos_filtrados:
        return None

    resultado = {k: v for k, v in no.items() if k != "Childs"}
    if filhos_filtrados:
        resultado["Childs"] = filhos_filtrados
    return resultado


@mcp.tool()
async def gc_menus(
    tipo: int = 3,
    modulo: Optional[str] = None,
    filtro: Optional[str] = None,
    flat: bool = True,
    conexao: str = "default",
) -> dict:
    """
    Lista os nomes internos de rotinas disponíveis, agrupadas por tipo.
    Use para descobrir o action_name correto antes de chamar outros tools.

    tipo:
      1 = Cadastros
      2 = Consultas nativas
      3 = Processos (padrão)
      4 = Relatórios
      5 = Planilhas (descontinuado)
      6 = Consultas Personalizadas
    modulo: filtra pelo módulo raiz (ex: "Contabilidade", "Fiscal", "Folha de Pagamento").
            Match parcial case-insensitive. Use para reduzir payload em tipos grandes.
    filtro: busca textual nos campos Text e Name. Retorna apenas nós que contenham o
            termo (case-insensitive), preservando a hierarquia de pais até a raiz.

    flat:   True (padrão) → lista plana [{Name, Text}] — menor e mais fácil de usar.
            False → árvore hierárquica completa.
    Exemplos:
      gc_menus(tipo=4, filtro="balancete")            → relatórios com "balancete"
      gc_menus(tipo=2, modulo="Contabilidade")         → consultas do módulo Contabilidade
      gc_menus(tipo=4, modulo="Fiscal", filtro="SPED") → combina módulo + texto

    O nome interno da rotina estará na tag "Name" da resposta.
    """
    resultado = await get_client(conexao).get("/api/TnWebDMMenus/Pegar", params={"_ATipo": tipo})

    if not isinstance(resultado, list):
        return resultado

    if modulo:
        modulo_lower = modulo.lower()
        resultado = [m for m in resultado if modulo_lower in m.get("Text", "").lower()]

    if flat:
        termo = filtro.lower() if filtro else None
        items = []
        for m in resultado:
            items.extend(_extrair_flat(m, termo))
        return items

    if filtro:
        termo = filtro.lower()
        resultado = [
            podado for m in resultado
            if (podado := _podar_arvore(m, termo)) is not None
        ]

    return resultado


# ---------------------------------------------------------------------------
# PARÂMETROS DE ROTINAS
# ---------------------------------------------------------------------------

@mcp.tool()
async def gc_dados_objeto(
    action_name: str,
    conexao: str = "default",
) -> dict:
    """
    Retorna os parâmetros aceitos por uma rotina (Name + Required).
    Resultado armazenado em cache — chamadas repetidas são instantâneas.
    Use gc_cache_limpar após uma atualização do Questor.
    """
    params = await get_client(conexao).get_objeto_params(action_name)
    return {"action_name": action_name, "parametros": params}


@mcp.tool()
async def gc_cache_limpar(conexao: str = "default") -> dict:
    """
    Descarta os parâmetros de rotina guardados em memória e relê a versão do
    Questor. Use depois de uma atualização do sistema, quando os campos
    obrigatórios de alguma rotina podem ter mudado.

    A próxima chamada a gc_inspecionar / gc_dados_objeto volta a consultar o
    servidor.
    """
    client = get_client(conexao)
    rotinas_em_cache = len(client._objeto_cache)
    anterior, atual = await client.revalidar_versao()
    client.invalidar_cache()
    client._versao_cache = atual
    return {
        "conexao": conexao,
        "rotinas_descartadas": rotinas_em_cache,
        "versao_anterior": anterior,
        "versao_atual": atual,
        "versao_mudou": anterior is not None and anterior != atual,
    }


@mcp.tool()
async def gc_inspecionar(
    action_name: str,
    conexao: str = "default",
) -> dict:
    """
    Retorna obrigatórios e opcionais de uma rotina com label e valores aceitos.
    SEMPRE chame este tool antes de gc_relatorio_executar ou gc_processo_executar.
    Usa cache: sem custo após a primeira chamada para este action_name.
    """
    todos = await get_client(conexao).get_objeto_params(action_name)

    def _resumir(p: dict) -> dict:
        info: dict = {"name": p["Name"], "label": p.get("DisplayLabel", p["Name"])}
        nomes_valores = p.get("NomesValores")
        if nomes_valores:
            # Converte [{Label: valor}, ...] → {label: valor}
            info["valores_aceitos"] = {
                list(item.keys())[0]: list(item.values())[0]
                for item in nomes_valores
                if isinstance(item, dict)
            }
        return info

    obrig_nomes = {
        p["Name"] for p in todos
        if p.get("Required") is True or str(p.get("Required", "")).lower() == "true"
    }
    return {
        "action_name": action_name,
        "obrigatorios": [_resumir(p) for p in todos if p["Name"] in obrig_nomes],
        "opcionais": [_resumir(p) for p in todos if p["Name"] not in obrig_nomes],
    }



# ---------------------------------------------------------------------------
# CONSULTA DE TELAS / DADOS CADASTRAIS
# ---------------------------------------------------------------------------

@mcp.tool()
async def gc_consultar(
    action_name: str,
    inicio: int = 0,
    quantidade: int = 100,
    ordem: str = "Asc",
    coluna_ordem: Optional[str] = None,
    filtros_json: Optional[str | dict] = None,
    conexao: str = "default",
) -> dict:
    """
    Consulta dados de uma tela/cadastro do Questor Gestão Contábil.

    action_name:  nome interno (gc_menus tipo=1 ou tipo=2).
                  Exemplos: "TnGemDCEmpresa", "TnFolDCFuncionario", "TnFisDCCFOP"
    inicio:       offset (padrão 0)
    quantidade:   registros a retornar (padrão 100)
    ordem:        "Asc" ou "Desc"
    coluna_ordem: coluna para ordenação (opcional)
    filtros_json: objeto JSON com filtros adicionais. Ex: '{"Empresa": "001"}'
    """
    params = {
        "_AActionName": action_name,
        "_AiDisplayStart": inicio,
        "_AiDisplayLength": quantidade,
        "_AsEcho": ordem,
    }
    if coluna_ordem:
        params["_AOrderBy"] = coluna_ordem

    filtros = _parse_json(filtros_json, "filtros_json") or {}
    body: dict = {"Filter": filtros}

    return await get_client(conexao).get("/api/TnWebDMConsulta/Pegar", params=params, body=body)


# ---------------------------------------------------------------------------
# PROCESSOS / ROTINAS DO SISTEMA
# ---------------------------------------------------------------------------

@mcp.tool()
async def gc_processo_executar(
    action_name: str,
    parametros_json: Optional[str | dict] = None,
    conexao: str = "default",
) -> dict:
    """
    Executa uma rotina (processo) do Questor Gestão Contábil.

    action_name:     nome interno (gc_menus tipo=3).
    parametros_json: objeto JSON com os parâmetros (gc_inspecionar para ver quais).

    Confirmação: quando o servidor retorna um dialog de confirmação, a resposta
    inclui "confirmacao_pendente" com as instruções. Para responder, reenvie
    os mesmos parâmetros acrescentando o campo "dialogs" retornado, preenchendo
    "Resposta" com "0" (SIM) ou "1" (NÃO) em cada dialog.

    Importação NFe:  '{"pNomeArquivo": "nfe.xml"}'
    Importação ZIP:  '{"pNomePasta_DIRABRIR": {"FileName": "lote.zip", "Data": "<b64>"}}'
    """
    body = _parse_json(parametros_json, "parametros_json") or {}

    # Se já contém "dialogs", é uma resposta a confirmação — pula validação de obrigatórios
    if "dialogs" not in body:
        ausentes = await _validar_params(action_name, body, conexao)
        if ausentes:
            return {
                "erro": "Parâmetros obrigatórios ausentes",
                "ausentes": ausentes,
                "dica": f"Consulte gc_inspecionar('{action_name}') para ver todos os parâmetros.",
            }

    result = await get_client(conexao).post(
        "/api/TnWebDMProcesso/ProcessoExecutar",
        params={"_AActionName": action_name},
        body=body,
        pesado=True,
    )

    # Detecta dialog de confirmação e retorna instruções claras
    if isinstance(result, dict) and "dialogs" in result:
        dialogs = result["dialogs"]
        mensagens = []
        for d in dialogs:
            msg = d.get("Mensagem", d.get("mensagem", ""))
            # Limpa tags HTML
            msg = msg.replace("<br/>", "\n").replace("<br>", "\n")
            opcoes = d.get("Options", [])
            mensagens.append({"mensagem": msg, "opcoes": opcoes})

        # Monta o body de exemplo para responder
        dialogs_resposta = []
        for d in dialogs:
            dialogs_resposta.append({
                "Name": d.get("Name", ""),
                "Resposta": "__PREENCHER_0_SIM_1_NAO__",
            })

        params_originais = {k: v for k, v in body.items() if k != "dialogs"}

        return {
            "confirmacao_pendente": True,
            "dialogs": mensagens,
            "como_responder": (
                "Reenvie gc_processo_executar com os mesmos parâmetros + campo 'dialogs'. "
                "Preencha 'Resposta' com '0' (SIM) ou '1' (NÃO) em cada dialog."
            ),
            "exemplo_body": {**params_originais, "dialogs": dialogs_resposta},
        }

    if isinstance(result, dict) and "Widgets" in result:
        grids = _extrair_grids_resultado(result)
        total_items = sum(len(g["items"]) for g in grids)
        if total_items >= 100:
            arquivos = _salvar_grids_csv(grids, action_name)
            return {
                "message": result.get("Message", ""),
                "arquivos_salvos": arquivos,
                "aviso": "Volume elevado de dados — registros salvos em CSV no download dir.",
            }

    return result


# ---------------------------------------------------------------------------
# RELATÓRIOS
# ---------------------------------------------------------------------------

@mcp.tool()
async def gc_relatorio_executar(
    action_name: str,
    tipo_retorno: str = "nrwexPDF",
    base64: bool = True,
    parametros_json: Optional[str | dict] = None,
    conexao: str = "default",
) -> dict:
    """
    Executa um relatório do Questor Gestão Contábil.

    action_name:     nome interno (gc_menus tipo=4).
    tipo_retorno:    nrwexPDF | nrwexHtml | nrwexTxt | nrwexCSV | nrwexRtf | nrwexJpeg | nrwexNHZ
    base64:          True obrigatório para PDF, JPEG e NHZ (padrão True).
    parametros_json: objeto JSON com parâmetros (gc_inspecionar para ver quais).
    """
    body = _parse_json(parametros_json, "parametros_json") or {}

    ausentes = await _validar_params(action_name, body, conexao)
    if ausentes:
        return {
            "erro": "Parâmetros obrigatórios ausentes",
            "ausentes": ausentes,
            "dica": f"Consulte gc_inspecionar('{action_name}') para ver todos os parâmetros.",
        }

    result = await get_client(conexao).get(
        "/api/TnWebDMRelatorio/Executar",
        params={
            "_AActionName": action_name,
            "_ABase64": str(base64),
            "_ATipoRetorno": tipo_retorno,
        },
        body=body,
        pesado=True,
    )

    if not isinstance(result, dict) or "Data" not in result:
        return result

    ext_map = {
        "nrwexPDF":  (".pdf",  True),
        "nrwexHtml": (".html", False),
        "nrwexJpeg": (".jpg",  True),
        "nrwexNHZ":  (".nhz",  True),
        "nrwexRtf":  (".rtf",  False),
        "nrwexCSV":  (".csv",  False),
        "nrwexTxt":  (".txt",  False),
    }
    ext, is_binary = ext_map.get(tipo_retorno, (".bin", True))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = _get_download_dir() / f"{action_name}_{ts}{ext}"

    data = result["Data"]
    if is_binary:
        with open(tmp_path, "wb") as f:
            f.write(_base64.b64decode(data))
    else:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(data)

    return {
        "arquivo": str(tmp_path),
        "paginas": result.get("PageCount"),
        "tamanho_bytes": result.get("Size"),
        "tipo": tipo_retorno,
    }


# ---------------------------------------------------------------------------
# INTEGRAÇÃO — NLI / IMPORTAÇÃO / EXPORTAÇÃO
# ---------------------------------------------------------------------------

def _construir_nli_texto(
    dm_class_name: str,
    campos: list[dict],
    separador: str = ";",
    separador_decimal: str = ",",
    formato_data: str = "DD/MM/YYYY",
    eh_variavel: bool = True,
    indice: str | None = None,
    delimitador: str = "#0",
    nao_ignorar_lookup: bool = False,
) -> str:
    """Monta o texto de um arquivo NLI no formato Object Pascal (Delphi)."""
    linhas = ["object TnItemImportacao"]
    linhas.append(f"  Delimitador = {delimitador}")
    linhas.append(f"  DMClassName = '{dm_class_name}'")
    linhas.append(f"  EhVariavel = {str(eh_variavel)}")
    linhas.append(f"  FormatoData = '{formato_data}'")
    if indice:
        linhas.append(f"  Indice = '{indice}'")
    if nao_ignorar_lookup:
        linhas.append("  NaoIgnorarLookup = True")
    linhas.append(f"  Separador = '{separador}'")
    linhas.append(f"  SeparadorDecimal = '{separador_decimal}'")
    linhas.append("  SohExportarSeTiverDetalhe = False")

    pos = 1
    for i, campo in enumerate(campos, start=1):
        field_name = campo.get("field_name") or campo.get("FieldName", "")
        tamanho = int(campo.get("tamanho") or campo.get("Tamanho") or 50)
        precisao = int(campo.get("precisao") or campo.get("Precisao") or 0)
        coluna = int(campo.get("coluna") or campo.get("Coluna") or i)
        tipo = campo.get("tipo") or campo.get("Tipo") or "itCampo"
        posicao = int(campo.get("posicao_inicio") or campo.get("PosicaoInicio") or pos)

        linhas.append("  object TnCursorLayoutField")
        linhas.append(f"    FieldName = '{field_name}'")
        linhas.append(f"    PosicaoInicio = {posicao}")
        linhas.append(f"    Tamanho = {tamanho}")
        linhas.append(f"    Precisao = {precisao}")
        linhas.append(f"    Coluna = {coluna}")
        linhas.append(f"    Tipo = {tipo}")
        linhas.append("  end")

        pos = posicao + tamanho

    linhas.append("end")
    return "\n".join(linhas)


@mcp.tool()
async def gc_nli_construir(
    dm_class_name: str,
    campos_json: str | list,
    separador: str = ";",
    separador_decimal: str = ",",
    formato_data: str = "DD/MM/YYYY",
    eh_variavel: bool = True,
    indice: Optional[str] = None,
    nome_arquivo: Optional[str] = None,
    nao_ignorar_lookup: bool = False,
) -> dict:
    """
    Constrói um arquivo NLI (layout de integração) e retorna o conteúdo e o Base64.

    dm_class_name:    classe-alvo no Questor (ex: "TnFpaDMFuncPessoa", "TnGemDCEmpresa").
    campos_json:      lista de campos no formato:
                      [
                        {"field_name": "NOMEFUNC", "coluna": 1, "tamanho": 50},
                        {"field_name": "CPFFUNC",  "coluna": 2, "tamanho": 14},
                        {"field_name": "DATANASC", "coluna": 3, "tamanho": 10, "tipo": "itIgnorar"}
                      ]
                      Campos de cada item:
                        field_name    : nome do campo no banco (obrigatório)
                        coluna        : posição no CSV/layout (padrão: ordem na lista)
                        tamanho       : largura em caracteres (padrão: 50)
                        precisao      : casas decimais (padrão: 0)
                        posicao_inicio: posição byte (padrão: calculada sequencialmente)
                        tipo          : "itCampo" (padrão) ou "itIgnorar"
    separador:        separador de colunas para layout delimitado (padrão: ";")
    separador_decimal: separador decimal (padrão: ",")
    formato_data:     formato das datas (padrão: "DD/MM/YYYY")
    eh_variavel:      True = delimitado (CSV), False = posicional fixo (padrão: True)
    indice:           campo(s) chave para atualização (ex: "CODIGOFUNC")
    nome_arquivo:     nome do arquivo a salvar em temp (padrão: "<dm_class_name>.nli").
                      Passe None para não salvar.
    nao_ignorar_lookup: True = inclui campos Delphi fkLookup (ex: SIGLAESTADO, NOMEESTADO).
                        Adiciona NaoIgnorarLookup = True no NLI. Use com Tipo = itCampo nos
                        campos lookup. Padrão False (somente campos fkData do banco).

    Retorna: {"arquivo": "/tmp/...", "nli_base64": "...", "conteudo": "..."}
    O campo "nli_base64" está pronto para uso no leiautes_json de gc_integracao_importar/exportar.
    """
    campos = _parse_json(campos_json, "campos_json")
    if not isinstance(campos, list):
        return {"erro": "campos_json deve ser uma lista de objetos."}

    texto = _construir_nli_texto(
        dm_class_name=dm_class_name,
        campos=campos,
        separador=separador,
        separador_decimal=separador_decimal,
        formato_data=formato_data,
        eh_variavel=eh_variavel,
        indice=indice,
        nao_ignorar_lookup=nao_ignorar_lookup,
    )

    b64 = _base64.b64encode(texto.encode("utf-8")).decode("ascii")

    result: dict = {"conteudo": texto, "nli_base64": b64}

    if nome_arquivo is not False:
        fname = nome_arquivo or f"{dm_class_name}.nli"
        tmp_path = _get_download_dir() / fname
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(texto)
        result["arquivo"] = str(tmp_path)

    result["dica"] = (
        f'Use em gc_integracao_importar com leiautes_json='
        f'\'[{{"Nome": "{nome_arquivo or dm_class_name + ".nli"}", "Arquivo": "<nli_base64>"}}]\''
    )
    return result


@mcp.tool()
async def gc_integracao_importar(
    leiautes_json: str | list,
    dados_base64: Optional[str] = None,
    arquivo_dados: Optional[str] = None,
    pode_alterar_dados: bool = False,
    executar_validacao_final: bool = True,
    conexao: str = "default",
) -> dict:
    """
    Importa dados via integração NLI.

    leiautes_json:            lista de layouts NLI já encodados:
                              '[{"Nome": "Socios.nli", "Arquivo": "<base64_do_nli>"}]'
                              Use gc_nli_construir para gerar o base64 do NLI.
    dados_base64:             conteúdo do arquivo de dados em Base64 (CSV, TXT…).
    arquivo_dados:            caminho para o arquivo de dados (alternativa a dados_base64).
                              Um dos dois deve ser informado.
    pode_alterar_dados:       True = atualiza existentes / False = só novos (padrão False).
    executar_validacao_final: True = valida consistência antes de importar (padrão True).
    """
    if not dados_base64 and not arquivo_dados:
        return {"erro": "Informe dados_base64 ou arquivo_dados."}

    if arquivo_dados and not dados_base64:
        with open(arquivo_dados, "rb") as f:
            dados_base64 = _base64.b64encode(f.read()).decode("ascii")

    client = get_client(conexao)
    leiautes = _parse_json(leiautes_json, "leiautes_json")
    body = {
        "Leiautes": leiautes,
        "Dados": dados_base64,
        "PodeAlterarDados": pode_alterar_dados,
        "ExecutarValidacaoFinal": executar_validacao_final,
    }
    return await client.get("/api/Integracao/Importar", body=body, pesado=True)


@mcp.tool()
async def gc_integracao_exportar(
    leiautes_json: str | list,
    filtro: Optional[str] = None,
    conexao: str = "default",
) -> dict:
    """
    Exporta dados via integração NLI.

    leiautes_json: lista de layouts NLI encodados:
                   '[{"Nome": "TiposContrato.nli", "Arquivo": "<base64_do_nli>"}]'
                   Use gc_nli_construir para gerar o base64 do NLI.
    filtro:        expressão de filtro nos campos do banco (ex: "CODEMPRESA = '001'").
    """
    client = get_client(conexao)
    leiautes = _parse_json(leiautes_json, "leiautes_json")
    body = {"Leiautes": leiautes, "Filtro": filtro or ""}
    result = await client.get("/api/Integracao/Exportar", body=body, pesado=True)

    if not isinstance(result, dict):
        return result

    # Deriva nome base a partir do primeiro leiaute (ex: "TnGemDMBanco.nli" → "TnGemDMBanco")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    primeiro_nome = leiautes[0].get("Nome", "gc_exportacao") if leiautes else "gc_exportacao"
    nome_base = Path(primeiro_nome).stem  # remove extensão .nli se houver
    nome_arquivo = f"{nome_base}_{ts}.csv"

    # Resposta JSON com campo Dados (base64)
    raw_b64 = result.get("Dados") or result.get("dados_base64")
    if raw_b64:
        tmp_path = _get_download_dir() / nome_arquivo
        with open(tmp_path, "wb") as f:
            f.write(_base64.b64decode(raw_b64))
        result["arquivo"] = str(tmp_path)
        return result

    # Resposta texto direto (CSV/TXT sem envelope JSON)
    texto = result.get("resultado")
    if texto:
        tmp_path = _get_download_dir() / nome_arquivo
        with open(tmp_path, "w", encoding="latin-1") as f:
            f.write(texto)
        return {"arquivo": str(tmp_path), "tamanho_bytes": len(texto.encode("latin-1"))}

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
