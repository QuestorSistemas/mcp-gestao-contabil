"""
Script de configuração inicial do servidor MCP Questor Gestão Contábil (nHttp).
Execute uma vez antes de iniciar o servidor:

    python setup_config.py
"""

import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"
_DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / "gestao-contabil")


def main():
    print("=" * 60)
    print("  Configuração do Servidor MCP - Questor Gestão Contábil")
    print("=" * 60)
    print()

    existing = {}
    if ENV_FILE.exists():
        print(f"Arquivo .env encontrado em: {ENV_FILE}")
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
        print("Pressione Enter para manter o valor atual (mostrado entre colchetes).")
        print()

    # URL base
    current_url = existing.get("GC_BASE_URL", "")
    prompt_url = (
        f"URL base do servidor nHttp [{current_url}]: "
        if current_url
        else "URL base do servidor nHttp (ex: http://localhost:8080): "
    )
    url = input(prompt_url).strip()
    if not url and current_url:
        url = current_url
    url = url.rstrip("/")

    if not url:
        print("ERRO: URL base é obrigatória.")
        sys.exit(1)

    # LogonName (usuário@conexão)
    current_logon = existing.get("GC_LOGON_NAME", "")
    prompt_logon = (
        f"Usuário de login [{current_logon}]: "
        if current_logon
        else "Usuário de login no formato usuario@conexao (ex: administrador@questor): "
    )
    logon = input(prompt_logon).strip()
    if not logon and current_logon:
        logon = current_logon

    if not logon:
        print("ERRO: Usuário de login é obrigatório.")
        sys.exit(1)

    # Senha
    current_pw = existing.get("GC_PASSWORD", "")
    preview = current_pw[:3] + "..." if current_pw else "(não configurado)"
    password = input(f"Senha [{preview}] (Enter para manter): ").strip()
    if not password and current_pw:
        password = current_pw

    if not password:
        print("ERRO: Senha é obrigatória.")
        sys.exit(1)

    # Pasta de downloads
    current_dl = existing.get("GC_DOWNLOAD_DIR", "")
    sugestao = current_dl or _DEFAULT_DOWNLOAD_DIR
    download_dir = input(f"Pasta para arquivos baixados [{sugestao}]: ").strip()
    if not download_dir:
        download_dir = sugestao

    # Cria a pasta já (para o usuário ver que funcionou)
    try:
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        print(f"  Pasta criada/confirmada: {download_dir}")
    except Exception as e:
        print(f"  Aviso: não foi possível criar a pasta agora ({e}). Ela será criada ao iniciar o servidor.")

    # Salvar .env
    content = f"""# Configuração do servidor MCP - Questor Gestão Contábil (nHttp)
# Gerado por setup_config.py

# URL base do servidor nHttp (rodando localmente ou na rede)
GC_BASE_URL={url}

# Credenciais de login: usuario@conexao (ex: administrador@questor)
GC_LOGON_NAME={logon}
GC_PASSWORD={password}

# Pasta local onde relatórios, exportações e outros arquivos são salvos
GC_DOWNLOAD_DIR={download_dir}
"""
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print()
    print(f"Configuração salva em: {ENV_FILE}")
    print()
    print("Próximos passos:")
    print("  1. Instale as dependências:  pip install -e .")
    print("  2. Inicie o servidor:        python server.py")
    print()


if __name__ == "__main__":
    main()
