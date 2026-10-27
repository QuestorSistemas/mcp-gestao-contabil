"""
Verificação e aplicação de atualizações do MCP, a partir do GitHub.

Duas peças, decididas em 27/10/2026 (Marcos, Guilherme e Fabio):

  AVISAR   — ao iniciar, compara a versão local com a última tag publicada e
             deixa um aviso pendente, anexado à primeira resposta da sessão.
  ATUALIZAR — `gc_atualizar` baixa a versão nova e aplica, sob confirmação.

Por que NÃO atualiza sozinho: o código se trocaria na máquina do cliente sem
ninguém olhar, então qualquer coisa publicada por engano chegaria a todos de
imediato. Avisar + um comando dá o mesmo resultado com uma conferência no meio.
"""

from __future__ import annotations

import io
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import httpx

REPO = "QuestorSistemas/mcp-gestao-contabil"

# A versão é lida do pyproject publicado, via raw.githubusercontent, e NÃO da
# API do GitHub: a API sem autenticação permite 60 requisições por hora e por
# IP — num escritório atrás de um NAT, os clientes estourariam isso entre si
# (visto na prática em 27/10/2026, com o limite já zerado). O raw é CDN e não
# tem esse teto. A API fica de reserva, para o caso de o pyproject mudar de
# forma.
RAW_PYPROJECT = f"https://raw.githubusercontent.com/{REPO}/main/pyproject.toml"
API_TAGS = f"https://api.github.com/repos/{REPO}/tags"
# O tarball vem pelo codeload (sem rate limit), não pela API.
TARBALL = "https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"
RAIZ = Path(__file__).parent

# Nunca sobrescrever: é configuração e credencial do cliente.
PRESERVAR = {".env", "token.json", "credentials.json", ".claude"}

_TIMEOUT = 6.0
_cache: dict = {"quando": None, "versao": None}
_CACHE_TTL = timedelta(hours=6)

# Preenchido pela verificação de início; a primeira resposta da sessão o consome.
aviso_pendente: str | None = None


def _normalizar(v: str) -> tuple:
    """'v0.3.1-beta' -> (0, 3, 1). Sufixo textual não entra na comparação."""
    limpo = str(v).lstrip("vV").split("-")[0]
    partes = []
    for p in limpo.split("."):
        try:
            partes.append(int(p))
        except ValueError:
            partes.append(0)
    while len(partes) < 3:
        partes.append(0)
    return tuple(partes[:3])


def e_mais_nova(publicada: str, local: str) -> bool:
    return _normalizar(publicada) > _normalizar(local)


async def versao_publicada(forcar: bool = False) -> str | None:
    """Última tag do repositório, ou None se não der para consultar."""
    agora = datetime.now()
    if not forcar and _cache["quando"] and agora - _cache["quando"] < _CACHE_TTL:
        return _cache["versao"]

    versao = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as http:
            resp = await http.get(RAW_PYPROJECT)
            if resp.status_code == 200:
                m = re.search(r'^version\s*=\s*"([^"]+)"', resp.text, re.MULTILINE)
                if m:
                    versao = m.group(1)

            if versao is None:
                # Reserva: a API de tags. Pode responder 403 por limite de taxa.
                resp = await http.get(API_TAGS, headers={"Accept": "application/vnd.github+json"})
                if resp.status_code == 200:
                    tags = resp.json()
                    if tags:
                        versao = tags[0].get("name")
    except Exception:
        # Sem internet, repositório privado ou GitHub fora: silêncio, nunca erro.
        return None

    if versao is None:
        return None

    _cache.update({"quando": agora, "versao": versao})
    return versao


async def verificar(versao_local: str) -> dict:
    """Compara local com publicada. Não levanta exceção."""
    publicada = await versao_publicada()
    if publicada is None:
        return {"verificado": False, "motivo": "não foi possível consultar o GitHub", "versao_local": versao_local}

    nova = e_mais_nova(publicada, versao_local)
    return {
        "verificado": True,
        "versao_local": versao_local,
        "versao_publicada": publicada,
        "atualizacao_disponivel": nova,
        "como_atualizar": "use a ferramenta gc_atualizar" if nova else None,
    }


async def preparar_aviso(versao_local: str) -> None:
    """Chamada no início. Deixa o aviso pronto para a primeira resposta."""
    global aviso_pendente
    r = await verificar(versao_local)
    if r.get("atualizacao_disponivel"):
        aviso_pendente = (
            f"\n\n---\n📦 Saiu a versão {r['versao_publicada']} do MCP Gestão Contábil "
            f"(você está na {versao_local}). Para atualizar, use a ferramenta `gc_atualizar`."
        )


def consumir_aviso() -> str:
    """Devolve o aviso uma única vez por sessão."""
    global aviso_pendente
    if not aviso_pendente:
        return ""
    texto, aviso_pendente = aviso_pendente, None
    return texto


async def aplicar(versao_local: str, confirmar: bool = False) -> dict:
    """
    Baixa a última versão publicada e aplica sobre esta instalação.

    A origem é fixa no código (REPO) — não aceita URL de fora, para que uma
    chamada não consiga apontar a atualização para outro lugar.
    """
    r = await verificar(versao_local)
    if not r.get("verificado"):
        return {"ok": False, "erro": f"Não deu para consultar o GitHub ({r.get('motivo')}). Tente mais tarde."}

    publicada = r["versao_publicada"]
    if not r["atualizacao_disponivel"]:
        return {"ok": True, "atualizado": False, "mensagem": f"Já está na versão mais nova ({versao_local})."}

    if not confirmar:
        return {
            "ok": True,
            "atualizado": False,
            "previa": True,
            "versao_local": versao_local,
            "versao_publicada": publicada,
            "o_que_vai_acontecer": [
                f"baixar {publicada} de github.com/{REPO}",
                f"substituir os arquivos em {RAIZ}",
                f"preservar sem tocar: {', '.join(sorted(PRESERVAR))}",
                "guardar a versão atual numa pasta de backup ao lado",
                "é preciso reiniciar o Claude depois",
            ],
            "para_confirmar": "chame de novo com confirmar=True",
        }

    # A versão vem do pyproject ("0.3.1-beta"), mas a tag no GitHub leva o
    # prefixo ("v0.3.1-beta"). Tenta a forma com "v" primeiro e cai para a sem,
    # para não depender de quem criou a tag ter seguido a convenção.
    candidatas = [publicada] if publicada.startswith(("v", "V")) else [f"v{publicada}", publicada]
    dados = None
    ultimo_status = None
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as http:
            for tag in candidatas:
                resp = await http.get(TARBALL.format(repo=REPO, tag=tag))
                ultimo_status = resp.status_code
                if resp.status_code == 200:
                    dados = resp.content
                    break
    except Exception as e:
        return {"ok": False, "erro": f"Falha ao baixar: {e}"}

    if dados is None:
        return {
            "ok": False,
            "erro": f"Não achei a tag da versão {publicada} no GitHub (tentei {', '.join(candidatas)}; "
                    f"último HTTP {ultimo_status}). A versão foi publicada sem tag?",
        }

    temp = Path(tempfile.mkdtemp(prefix="gc-mcp-update-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(dados), mode="r:gz") as tar:
            tar.extractall(temp)
        # O tarball do GitHub vem dentro de uma pasta única.
        internas = [p for p in temp.iterdir() if p.is_dir()]
        if len(internas) != 1:
            return {"ok": False, "erro": "Formato inesperado do pacote baixado."}
        origem = internas[0]

        backup = RAIZ.parent / f"{RAIZ.name}.backup-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copytree(RAIZ, backup, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))

        copiados = 0
        for item in origem.rglob("*"):
            if item.is_dir():
                continue
            rel = item.relative_to(origem)
            if rel.parts[0] in PRESERVAR:
                continue
            destino = RAIZ / rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destino)
            copiados += 1

        return {
            "ok": True,
            "atualizado": True,
            "de": versao_local,
            "para": publicada,
            "arquivos": copiados,
            "backup": str(backup),
            "proximo_passo": "Reinicie o Claude Code / Claude Desktop para carregar a versão nova.",
        }
    except Exception as e:
        return {"ok": False, "erro": f"Falha ao aplicar: {e}"}
    finally:
        shutil.rmtree(temp, ignore_errors=True)
