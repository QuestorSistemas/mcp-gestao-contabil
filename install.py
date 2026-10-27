#!/usr/bin/env python
"""
Instalador do MCP Questor Gestão Contábil.

Guia completo: verifica Python, instala dependências, pede as credenciais,
testa a conexão com o nHttp e registra o servidor no Claude.

Uso: python install.py   (ou duplo-clique em instalar.bat)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
MCP_NAME = "gestao-contabil"
ENV_FILE = PROJECT_DIR / ".env"
SERVER_PY = PROJECT_DIR / "server.py"
_DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / "gestao-contabil")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def titulo(texto: str) -> None:
    print()
    print("=" * 64)
    print(f"  {texto}")
    print("=" * 64)
    print()


def ler_json(caminho: Path) -> dict | None:
    """Lê JSON tolerando BOM. Retorna None se não existir ou estiver corrompido."""
    try:
        raw = caminho.read_text(encoding="utf-8-sig")
        return json.loads(raw)
    except Exception:
        return None


def escrever_json(caminho: Path, dados: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ler_env() -> dict:
    """Lê o .env atual, se existir, para oferecer os valores como padrão."""
    valores: dict[str, str] = {}
    if not ENV_FILE.exists():
        return valores
    for linha in ENV_FILE.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, _, valor = linha.partition("=")
            valores[chave.strip()] = valor.strip()
    return valores


def perguntar(rotulo: str, atual: str = "", exemplo: str = "", obrigatorio: bool = True) -> str:
    """Pergunta mostrando o valor atual entre colchetes; Enter mantém o que já existe."""
    if atual:
        resposta = input(f"{rotulo} [{atual}]: ").strip() or atual
    else:
        sufixo = f" (ex: {exemplo})" if exemplo else ""
        resposta = input(f"{rotulo}{sufixo}: ").strip()
    if obrigatorio and not resposta:
        print(f"  [ERRO] {rotulo} e obrigatorio.")
        sys.exit(1)
    return resposta


# --------------------------------------------------------------------------
# Etapas
# --------------------------------------------------------------------------

def verificar_python() -> None:
    if sys.version_info < (3, 10):
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        print(f"[ERRO] Python 3.10+ e necessario. Voce esta usando {v}.")
        print("       Baixe em https://python.org/downloads")
        sys.exit(1)
    print(f"  Python {sys.version_info.major}.{sys.version_info.minor} OK")
    print(f"  Executavel: {sys.executable}")


def instalar_dependencias() -> None:
    print("  Instalando dependencias (pip install -e .) ...")
    resultado = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
        cwd=PROJECT_DIR,
    )
    if resultado.returncode != 0:
        print()
        print("[ERRO] Falha ao instalar as dependencias.")
        print("       Rode manualmente para ver o erro completo:")
        print(f"       {sys.executable} -m pip install -e .")
        sys.exit(1)
    print("  Dependencias instaladas.")


def configurar_credenciais() -> dict:
    atual = ler_env()

    url = perguntar(
        "URL do servidor nHttp",
        atual.get("GC_BASE_URL", ""),
        "http://localhost:8080 ou https://gc-xxx.questor.com.br",
    ).rstrip("/")

    logon = perguntar(
        "Usuario no formato usuario@conexao",
        atual.get("GC_LOGON_NAME", ""),
        "administrador@questor",
    )

    senha_atual = atual.get("GC_PASSWORD", "")
    if senha_atual:
        senha = input("Senha [Enter mantem a atual]: ").strip() or senha_atual
    else:
        senha = input("Senha: ").strip()
    if not senha:
        print("  [ERRO] Senha e obrigatoria.")
        sys.exit(1)

    sugestao = atual.get("GC_DOWNLOAD_DIR", "") or _DEFAULT_DOWNLOAD_DIR
    download_dir = input(f"Pasta para relatorios baixados [{sugestao}]: ").strip() or sugestao
    try:
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        print(f"  Pasta pronta: {download_dir}")
    except OSError as e:
        print(f"  Aviso: nao deu para criar a pasta agora ({e}). Sera criada no primeiro uso.")

    ENV_FILE.write_text(
        "# Configuracao do servidor MCP - Questor Gestao Contabil (nHttp)\n"
        "# Gerado por install.py\n"
        "\n"
        "# URL base do servidor nHttp (local ou Cloud Questor)\n"
        f"GC_BASE_URL={url}\n"
        "\n"
        "# Credenciais de login: usuario@conexao\n"
        f"GC_LOGON_NAME={logon}\n"
        f"GC_PASSWORD={senha}\n"
        "\n"
        "# Pasta local onde relatorios e exportacoes sao salvos\n"
        f"GC_DOWNLOAD_DIR={download_dir}\n",
        encoding="utf-8",
    )
    # A senha fica em texto claro no .env. Restringe ao dono onde o SO respeita
    # o modo POSIX; no Windows a chamada passa sem efeito pratico nas ACLs.
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass

    print(f"  Credenciais salvas em: {ENV_FILE}")
    return {"url": url, "logon": logon, "senha": senha}


def testar_conexao(cfg: dict) -> bool:
    """Faz um login real no nHttp. Falha aqui não impede a instalação."""
    print("  Testando conexao com o servidor ...")
    try:
        import httpx
    except ImportError:
        print("  Aviso: httpx nao disponivel — teste pulado.")
        return False

    try:
        with httpx.Client(verify=False, timeout=30.0, follow_redirects=True) as c:
            r = c.post(
                f"{cfg['url']}/home/LoginModulo",
                params={"LogonName": cfg["logon"], "PlainPassword": cfg["senha"]},
            )
            r.raise_for_status()
            token = (r.json() or {}).get("Token")
            if not token:
                print("  Aviso: o servidor respondeu, mas nao devolveu um token de sessao.")
                print("         Confira usuario, conexao e senha.")
                return False

            cookie = r.cookies.get("QuestorCloud")
            headers = {"Cookie": f"QuestorCloud={cookie}"} if cookie else {}
            rv = c.get(
                f"{cfg['url']}/api/TnWebDMDadosGerais/PegarVersaoQuestor",
                params={"token": token},
                headers=headers,
            )
            versao = ""
            if rv.status_code == 200:
                corpo = rv.json()
                versao = corpo.get("Versao", "") if isinstance(corpo, dict) else str(corpo)
            print(f"  Conexao OK{f' — Questor {versao}' if versao else ''}")
            return True
    except Exception as e:
        print(f"  Aviso: nao foi possivel conectar agora ({type(e).__name__}: {e}).")
        print("         A instalacao segue; corrija a URL/credenciais no .env depois.")
        return False


def caminhos_config_claude() -> list[tuple[Path, str]]:
    """Descobre os arquivos de configuração do Claude presentes nesta máquina."""
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    local_appdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))

    encontrados: list[tuple[Path, str]] = []

    code_settings = home / ".claude" / "settings.json"
    if code_settings.exists():
        encontrados.append((code_settings, "Claude Code (settings.json)"))

    claude_json = home / ".claude.json"
    if claude_json.exists():
        encontrados.append((claude_json, "Claude Code (.claude.json)"))

    # Para o Desktop basta a PASTA existir: quem nunca configurou um MCP ainda
    # nao tem o claude_desktop_config.json, e nesse caso nos criamos o arquivo.
    desktop_dir = appdata / "Claude"
    if desktop_dir.is_dir():
        encontrados.append((desktop_dir / "claude_desktop_config.json", "Claude Desktop"))

    pacotes = local_appdata / "Packages"
    if pacotes.exists():
        try:
            for pasta in pacotes.iterdir():
                if not pasta.name.startswith("Claude_"):
                    continue
                ms_dir = pasta / "LocalCache" / "Roaming" / "Claude"
                if ms_dir.is_dir():
                    encontrados.append((ms_dir / "claude_desktop_config.json", "Claude Desktop (MS Store)"))
        except OSError:
            pass

    return encontrados


def registrar_no_claude() -> int:
    entrada = {"command": sys.executable, "args": [str(SERVER_PY)]}

    destinos = caminhos_config_claude()
    if not destinos:
        print("  Nenhuma instalacao do Claude encontrada nesta maquina.")
        print("  Registre manualmente, em claude_desktop_config.json:")
        print(json.dumps({"mcpServers": {MCP_NAME: entrada}}, indent=2))
        return 0

    registrados = 0
    for caminho, rotulo in destinos:
        config = ler_json(caminho)
        if config is None:
            if caminho.exists():
                print(f"  [{rotulo}] Arquivo ilegivel — pulando para nao sobrescrever: {caminho}")
                continue
            config = {}
        servidores = config.setdefault("mcpServers", {})
        acao = "atualizado" if MCP_NAME in servidores else "adicionado"
        servidores[MCP_NAME] = entrada
        escrever_json(caminho, config)
        print(f"  [{rotulo}] {acao} em {caminho}")
        registrados += 1

    return registrados


# --------------------------------------------------------------------------

def main() -> int:
    titulo("Instalador — MCP Questor Gestao Contabil")

    print("[1/5] Verificando o Python")
    verificar_python()

    print()
    print("[2/5] Instalando dependencias")
    instalar_dependencias()

    print()
    print("[3/5] Configurando o acesso ao Questor")
    cfg = configurar_credenciais()

    print()
    print("[4/5] Testando a conexao")
    conectou = testar_conexao(cfg)

    print()
    print("[5/5] Registrando no Claude")
    registrados = registrar_no_claude()

    titulo("Instalacao concluida")

    if registrados:
        print("Proximos passos:")
        print("  1. Feche o Claude Desktop POR COMPLETO — inclusive o icone")
        print("     na bandeja do sistema, ao lado do relogio.")
        print("  2. Abra o Claude novamente e inicie uma conversa nova.")
        print('  3. Peca: "qual a versao do Questor?"')
        print()
        print("Se responder com a versao, esta funcionando.")
    if not conectou:
        print()
        print("Atencao: o teste de conexao nao passou. Revise GC_BASE_URL,")
        print(f"GC_LOGON_NAME e GC_PASSWORD em {ENV_FILE} e rode este instalador de novo.")

    print()
    print("Guia de uso completo: GUIA_USO.md (nesta mesma pasta)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInstalacao cancelada.")
        sys.exit(1)
