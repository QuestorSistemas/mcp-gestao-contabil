#!/usr/bin/env python
"""
Instalador do MCP Questor Gestão Contábil.

Guia completo: verifica Python, instala dependências, pede as credenciais,
testa a conexão com o nHttp e registra o servidor no Claude e no OpenAI Codex.

Uso: python install.py   (ou duplo-clique em instalar.bat)
"""

import json
import os
import shutil
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
        "usuario.mcp@questor — nao use o Administrador",
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
    proteger_env()
    print(f"  Credenciais salvas em: {ENV_FILE}")
    return {"url": url, "logon": logon, "senha": senha}


def proteger_env() -> None:
    """
    A senha fica em texto claro no .env: deixa o arquivo legível só pelo dono.

    No Windows o chmod não mexe nas ACLs — o arquivo herdaria as permissões da
    pasta, que costumam liberar leitura para outros usuários da máquina. Lá a
    proteção é feita com icacls: corta a herança e deixa só o usuário atual.
    """
    if os.name != "nt":
        try:
            os.chmod(ENV_FILE, 0o600)
        except OSError:
            pass
        return

    usuario = os.environ.get("USERNAME", "")
    dominio = os.environ.get("USERDOMAIN", "")
    if not usuario:
        print("  Aviso: nao deu para identificar o usuario; o .env ficou com as permissoes da pasta.")
        return
    conta = f"{dominio}\\{usuario}" if dominio else usuario
    r = subprocess.run(
        ["icacls", str(ENV_FILE), "/inheritance:r", "/grant:r", f"{conta}:F"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        print(f"  .env protegido: so {conta} pode ler.")
    else:
        print(f"  Aviso: nao deu para restringir o .env ({(r.stderr or r.stdout).strip()}).")


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
                # No corpo, nunca na URL: a URL fica nos logs do servidor.
                data={"LogonName": cfg["logon"], "PlainPassword": cfg["senha"]},
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

    # Claude Code: os MCPs ficam no ~/.claude.json. O ~/.claude/settings.json NÃO
    # é lido para MCPs — e pode estar protegido contra gravação (configuração
    # gerenciada), então não é tocado. Quando o comando `claude` existe, o
    # registro vai por ele (registrar_no_claude_code) e este arquivo é pulado.
    claude_json = home / ".claude.json"
    if claude_json.exists() and not shutil.which("claude"):
        encontrados.append((claude_json, "Claude Code (.claude.json)"))

    # Para o Desktop basta a PASTA existir: quem nunca configurou um MCP ainda
    # nao tem o claude_desktop_config.json, e nesse caso nos criamos o arquivo.
    # Quem instalou e ainda nao abriu o Desktop nao tem nem a pasta: a pasta do
    # programa (%LOCALAPPDATA%\AnthropicClaude) tambem conta.
    desktop_dir = appdata / "Claude"
    if desktop_dir.is_dir() or (local_appdata / "AnthropicClaude").is_dir():
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


def comando_claude_code() -> list[str]:
    return ["claude", "mcp", "add", "-s", "user", MCP_NAME, "--", sys.executable, str(SERVER_PY)]


def registrar_no_claude_code() -> bool:
    """
    Registra pelo próprio CLI do Claude Code, quando ele existe. É o caminho
    oficial e não disputa o ~/.claude.json com um Claude Code aberto.
    """
    exe = shutil.which("claude")
    if not exe:
        return False
    try:
        # Remove o registro anterior (se houver) para o add não falhar por duplicado.
        subprocess.run([exe, "mcp", "remove", "-s", "user", MCP_NAME], capture_output=True, text=True, timeout=60)
        r = subprocess.run([exe, *comando_claude_code()[1:]], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  [Claude Code] Aviso: nao deu para registrar ({e}).")
        print("  Rode num terminal: " + " ".join(f'"{p}"' if " " in p else p for p in comando_claude_code()))
        return False
    if r.returncode != 0:
        print(f"  [Claude Code] Aviso: o registro falhou ({(r.stderr or r.stdout).strip()}).")
        print("  Rode num terminal: " + " ".join(f'"{p}"' if " " in p else p for p in comando_claude_code()))
        return False
    print("  [Claude Code] registrado (claude mcp add -s user)")
    return True


def registrar_no_claude() -> list[str]:
    """Registra em cada Claude encontrado. Devolve os rótulos onde deu certo."""
    entrada = {"command": sys.executable, "args": [str(SERVER_PY)]}

    registrados = ["Claude Code"] if registrar_no_claude_code() else []
    destinos = caminhos_config_claude()
    if not destinos and not registrados:
        print("  Claude nao encontrado nesta maquina — pulando.")
        return []

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
        # Um destino protegido contra gravação não pode derrubar os outros.
        try:
            escrever_json(caminho, config)
        except OSError as e:
            print(f"  [{rotulo}] Aviso: sem permissao para gravar em {caminho} ({e.strerror}) — pulando.")
            continue
        print(f"  [{rotulo}] {acao} em {caminho}")
        registrados.append(rotulo)

    return registrados


def caminho_config_codex() -> Path | None:
    """config.toml do OpenAI Codex, se o Codex estiver nesta máquina."""
    base = Path(os.environ.get("CODEX_HOME", "") or Path.home() / ".codex")
    if base.is_dir() or shutil.which("codex"):
        return base / "config.toml"
    return None


def _bloco_codex() -> str:
    # Strings literais do TOML (aspas simples): o caminho do Windows vai sem
    # escapar as barras invertidas.
    return (
        f"[mcp_servers.{MCP_NAME}]\n"
        f"command = '{sys.executable}'\n"
        f"args = ['{SERVER_PY}']\n"
        # O padrão do Codex é 10 s para subir e 60 s por chamada; a primeira
        # carga do Python e um relatório pesado passam disso.
        "startup_timeout_sec = 30\n"
        "tool_timeout_sec = 180\n"
    )


def registrar_no_codex() -> bool:
    """
    Registra no Codex editando o config.toml, no mesmo formato que o
    `codex mcp add` grava. Edita direto (e não pelo CLI) para poder ajustar os
    timeouts e para funcionar também onde só há a extensão de IDE ou o app.
    Idempotente: troca o bloco existente e não mexe nos outros servidores.
    """
    caminho = caminho_config_codex()
    if caminho is None:
        print("  Codex nao encontrado nesta maquina — pulando.")
        return False

    texto = caminho.read_text(encoding="utf-8-sig") if caminho.exists() else ""
    cabecalho = f"[mcp_servers.{MCP_NAME}]"
    linhas = texto.splitlines()
    inicio = next((i for i, l in enumerate(linhas) if l.strip() == cabecalho), None)

    if inicio is None:
        acao = "adicionado"
        if texto and not texto.endswith("\n"):
            texto += "\n"
        novo = texto + ("\n" if texto.strip() else "") + _bloco_codex()
    else:
        acao = "atualizado"
        # O bloco vai até a próxima tabela, inclusive subtabelas dele próprio
        # ([mcp_servers.gestao-contabil.env]), que descartamos junto.
        fim = next(
            (j for j in range(inicio + 1, len(linhas))
             if linhas[j].lstrip().startswith("[")
             and not linhas[j].strip().startswith(f"[mcp_servers.{MCP_NAME}.")),
            len(linhas),
        )
        novo = "\n".join(linhas[:inicio]) + ("\n" if inicio else "") + _bloco_codex()
        resto = "\n".join(linhas[fim:])
        if resto:
            novo += "\n" + resto + "\n"

    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(novo, encoding="utf-8")
    except OSError as e:
        print(f"  [Codex] Aviso: sem permissao para gravar em {caminho} ({e.strerror}) — pulando.")
        return False
    print(f"  [Codex] {acao} em {caminho}")
    return True


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
    print("[5/5] Registrando no Claude e no Codex")
    registrados = registrar_no_claude()
    no_codex = registrar_no_codex()

    if not registrados and not no_codex:
        titulo("MCP instalado, mas nao registrado em nenhuma IA")
        print("Nao encontramos o Claude nem o OpenAI Codex nesta maquina.")
        print("Instale um deles, abra-o uma vez e rode este instalador de novo.")
        print()
        print("Ou registre a mao:")
        print("  Claude Desktop — em %APPDATA%\\Claude\\claude_desktop_config.json:")
        entrada = {"command": sys.executable, "args": [str(SERVER_PY)]}
        print(json.dumps({"mcpServers": {MCP_NAME: entrada}}, indent=2))
        print("  Claude Code: " + " ".join(f'"{p}"' if " " in p else p for p in comando_claude_code()))
        print(f'  Codex: codex mcp add {MCP_NAME} -- "{sys.executable}" "{SERVER_PY}"')
    else:
        titulo("Instalacao concluida")
        print("Proximos passos:")
        if any(r.startswith("Claude Desktop") for r in registrados):
            print("  Claude Desktop: feche POR COMPLETO — inclusive o icone na bandeja")
            print("                  do sistema, ao lado do relogio — e abra de novo.")
        if any(r.startswith("Claude Code") for r in registrados):
            print("  Claude Code:    abra uma sessao nova e confira com /mcp")
        if no_codex:
            print("  Codex:          feche e abra o Codex. Confira com: codex mcp list")
        print('  Depois, numa conversa nova, peca: "qual a versao do Questor?"')
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
