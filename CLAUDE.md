# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Objetivo do Projeto

Servidor MCP (Model Context Protocol) em Python para a **API nHttp do Questor Gestão Contábil**. O nHttp é um servidor HTTP local que roda na máquina Windows do usuário e expõe endpoints REST para consultar dados cadastrais, executar rotinas, gerar relatórios e importar/exportar dados via NLI.

**Projeto de referência**: `d:\WorkSpace\qn-mcp` — MCP para o Questor Negócio, mesmos padrões estruturais.

## Estrutura dos Arquivos

```
gestao-contabil-mcp/
├── instalar.bat       # Wrapper Windows: checa Python e chama install.py
├── install.py         # Instalador completo (deps + .env + teste + registro no Claude)
├── server.py          # Servidor MCP principal — todos os tools registrados aqui
├── client.py          # Cliente HTTP httpx com login/token automático
├── setup_config.py    # Script interativo de configuração (grava só o .env)
├── pyproject.toml     # Metadados do projeto e dependências
├── .env               # Configuração local (não commitado)
├── .env.example       # Exemplo de configuração
├── GUIA_USO.md        # Guia de usuário final (vai junto no ZIP distribuído)
└── docs/              # Documentação HTML salva localmente
```

## Como Instalar e Configurar

**Usuário final (Windows):** duplo-clique em `instalar.bat`. Ele verifica o Python,
instala as dependências, pede as credenciais, testa a conexão com o nHttp e registra
o servidor em todos os arquivos de configuração do Claude encontrados na máquina
(Desktop tradicional, Desktop MS Store e Claude Code). É idempotente — rodar de novo
oferece os valores atuais e não remove os outros MCPs já configurados.

**Desenvolvimento / outros SOs:**

```bash
# 1. Instalar dependências
pip install -e .

# 2. Configurar URL base, usuário e senha (salva em .env)
python setup_config.py

# 3. Iniciar o servidor MCP
python server.py
```

> `setup_config.py` grava **apenas** o `.env`. Quem faz o ciclo completo
> (deps + teste de conexão + registro no Claude) é o `install.py`.

## Arquitetura

### Diferença em relação ao qn-mcp

| Aspecto | qn-mcp (Questor Negócio) | gestao-contabil-mcp (nHttp) |
|---------|--------------------------|------------------------------|
| Tipo de servidor | API REST remota (cloud) | nHttp local **ou** Cloud Questor |
| Autenticação | x-api-key → JWT renovável | POST `/home/LoginModulo` → cookie + token |
| Base URL | `https://empresa.questornegocio.com.br` | `http://localhost:8080` ou `https://gc-xxx.questor.com.br` |

### nHttp local vs Cloud Questor — diferenças críticas de autenticação

O `client.py` implementa o padrão da **Cloud API** (validado com Postman). O nHttp local pode aceitar Bearer header, mas a Cloud exige:

| Aspecto | nHttp local (documentação antiga) | Cloud Questor (comportamento real) |
|---------|-----------------------------------|-------------------------------------|
| Auth header | `Authorization: Bearer <token>` | **Não usado** |
| Token na URL | não usado | `?token=<token>` em **todas** as requests |
| Cookie de sessão | não usado | `QuestorCloud=<val>` obrigatório — retornado no login, deve ser reenviado manualmente em todas as requests (Path=/home não cobre /api/) |
| Logout | `GET /home/Logout` | `POST /home/logout` |
| SSL | sem (localhost) | `verify=False` — proxy nginx na frente |
| Filter em consultas | opcional | `{"Filter": {}}` **obrigatório** no body |

> **IMPORTANTE:** O cookie `QuestorCloud` tem `Path=/home` no Set-Cookie, mas deve ser enviado para `/api/` também. O httpx não faz isso automaticamente — o `client.py` força via header `Cookie:` explícito.

### Variáveis de Ambiente (.env)

```
# Conexão padrão (obrigatória)
GC_BASE_URL=http://localhost:8080
GC_LOGON_NAME=administrador@questor   # formato: usuario@conexao
GC_PASSWORD=masterkey

# Habilitar múltiplas conexões (padrão false)
GC_ALLOW_MULTI_CONN=false

# Conexões adicionais pré-configuradas (requer GC_ALLOW_MULTI_CONN=true)
# GC_CONN_{NOME}_BASE_URL=http://seu-servidor:8080
# GC_CONN_{NOME}_LOGON_NAME=admin@empresa2
# GC_CONN_{NOME}_PASSWORD=senha2
```

### Múltiplas conexões

- **Modo padrão** (`GC_ALLOW_MULTI_CONN=false`): só `conexao="default"` funciona. Qualquer tentativa de usar outra conexão levanta `PermissionError`.
- **Modo multi** (`GC_ALLOW_MULTI_CONN=true`): permite `conexao="filial2"` nos tools e registro dinâmico via `gc_conexao_adicionar`.
- Conexões pré-configuradas no `.env` (prefixo `GC_CONN_{nome}_*`) são carregadas automaticamente na inicialização.
- Cada conexão tem seu próprio token de sessão e cache de parâmetros independentes.

### Autenticação

1. `POST /home/LoginModulo?LogonName=usuario@conexao&PlainPassword=senha` → retorna `{"Token": "..."}` + seta cookie `QuestorCloud`
2. Todas as requests subsequentes precisam de **dois** elementos:
   - `?token=<token>` na query string
   - Header `Cookie: QuestorCloud=<valor>` (extraído da resposta do login)
3. `POST /home/logout?token=<token>` encerra a sessão
4. Em caso de 401: limpa token e cookie, refaz login automaticamente

### Tools Implementados (server.py)

| Tool | Endpoint | Descrição |
|------|----------|-----------|
| `gc_versao` | `GET /TnWebDMDadosGerais/PegarVersaoQuestor` | Versão do sistema / teste de conexão |
| `gc_login` | `POST /Home/LoginModulo` | Login manual (diagnóstico) |
| `gc_logout` | `POST /home/logout` | Encerra sessão |
| `gc_cache_limpar` | — | Descarta o cache de parâmetros e relê a versão do Questor |
| `gc_health` | `GET /api/TnWebDMDadosGerais/PegarVersaoQuestor` | Diz se o nHttp responde, sem levantar erro |
| `gc_atualizar` | GitHub (raw + codeload) | Atualiza o próprio MCP para a última versão publicada |
| `gc_conexao_listar` | — | Lista conexões registradas |
| `gc_conexao_adicionar` | — | Registra conexão em runtime |
| `gc_conexao_importar_ini` | — | Importa conexões de um Questor.Conexao.ini |
| `gc_menus` | `GET /api/TnWebDMMenus/Pegar` | Lista rotinas por tipo (1-6) |
| `gc_dados_objeto` | `GET /api/TnWebDMDadosObjetos/Pegar` | Parâmetros de uma rotina *(usa cache)* |
| `gc_inspecionar` | cache + `/api/TnWebDMDadosObjetos/Pegar` | Retorna obrigatórios/opcionais separados *(usa cache)* |
| `gc_consultar` | `GET /api/TnWebDMConsulta/Pegar` | Consulta dados de telas/cadastros |
| `gc_processo_executar` | `GET /api/TnWebDMProcesso/ProcessoExecutar` | Executa processos/rotinas *(valida obrigatórios)* |
| `gc_relatorio_executar` | `GET /Api/TnWebDMRelatorio/Executar` | Gera relatórios (PDF, HTML, CSV…) *(valida obrigatórios)* |
| `gc_integracao_importar` | `GET /api/Integracao/Importar` | Importa dados via NLI (Base64) |
| `gc_integracao_exportar` | `GET /api/Integracao/Exportar` | Exporta dados via NLI (Base64) |

### Tipos de Menu (`gc_menus`)

| tipo | Conteúdo |
|------|----------|
| 1 | Cadastros — rotinas de dados cadastrais |
| 2 | Consultas nativas |
| 3 | Processos nativos e personalizados |
| 4 | Relatórios nativos e personalizados |
| 5 | Planilhas (em descontinuação) |
| 6 | Consultas Personalizadas |

### Cache de parâmetros (`client.py`)

`GestaoContabilClient` mantém um dict `_objeto_cache` em memória:
- Chave: `action_name`
- Populado na primeira chamada a `get_objeto_params(action_name)`
- A versão do Questor é registrada em `_versao_cache` na primeira chamada. A comparação que
  de fato descarta o cache acontece em `revalidar_versao()`, disparada pela tool
  `gc_cache_limpar` — **não** a cada chamada, para não gastar um request extra contra um
  servidor que trava sob carga (ver seção *Serialização*).
- `gc_processo_executar` e `gc_relatorio_executar` consultam o cache automaticamente para validar parâmetros obrigatórios antes de executar

### Serialização de chamadas pesadas (`client.py`)

O worker `nWebExe_Cx*` do nHttp **trava de forma permanente** quando recebe duas chamadas
pesadas simultâneas — e a partir daí o servidor inteiro para de responder até alguém matar
os processos ou reiniciar o serviço `Questor.nHttp`. Reproduzido em 07/05/2026
com 3 `gc_inspecionar` em paralelo.

Mitigação no lado do MCP: cada `GestaoContabilClient` tem um `asyncio.Lock` (`_heavy_lock`)
e as chamadas marcadas com `pesado=True` passam por ele, uma de cada vez, **por conexão**:

| Marcada como pesada | Segue em paralelo |
|---|---|
| `get_objeto_params` (→ `gc_inspecionar`, `gc_dados_objeto`) | `gc_versao` |
| `gc_processo_executar` | `gc_menus` |
| `gc_relatorio_executar` | `gc_consultar` |
| `gc_integracao_importar` / `_exportar` | `gc_conexao_*` |

O retry de 401 chama `_executar` diretamente, e não `_request`, para não tentar readquirir
um lock que já está na mão — isso causaria deadlock no próprio MCP.

### Circuit breaker

Depois de `GC_BREAKER_FALHAS` (padrão **3**) timeouts ou recusas de conexão **seguidos**, o
cliente para de ir à rede por `GC_BREAKER_ESPERA` segundos (padrão **60**) e levanta
`ServidorIndisponivel` na hora, com instrução de o que fazer no servidor. Sem isso, cada
chamada depois do travamento ficaria esperando o timeout inteiro — e empilhando requisições
sobre um nHttp já engasgado.

- Qualquer **resposta HTTP recebida zera o contador**, inclusive 4xx/5xx: erro de negócio
  significa que o servidor está vivo. Só timeout e recusa de conexão contam.
- Ao abrir, o token e o cookie são descartados — a sessão morre junto com o worker.
- `gc_health` informa o estado sem levantar erro, e diz se é `worker_dead` (matar
  `nWebExe_Cx*`) ou `connection_refused` (serviço parado).

### Conexão HTTP

`_client()` mantém um `httpx.AsyncClient` persistente por conexão (antes era um cliente novo
a cada request, com handshake TCP/TLS toda vez). `aclose()` fecha; `logout()` já chama.

`verify` vem de `GC_VERIFY_SSL` (padrão `false`): a Cloud fica atrás de um proxy nginx com
certificado que o httpx não valida, e o nHttp local é http puro. Ligue onde o certificado
for válido.

### Atualização (`atualizacao.py`)

Decidido em 27/10/2026 com Produto e Operações: o MCP **avisa** quando há versão
nova e **atualiza sob comando** (`gc_atualizar`). Nunca se atualiza sozinho — senão
qualquer publicação errada chegaria a todos os clientes de imediato, sem conferência.

- **Aviso:** `_GestaoContabilMCP.call_tool` dispara a verificação em segundo plano na
  primeira chamada da sessão (a resposta não espera pelo GitHub) e anexa o aviso, **uma
  vez**, à primeira resposta que o encontrar pronto. `gc_versao` também traz o campo
  `atualizacao`.
- **Fonte da versão:** o `pyproject.toml` via `raw.githubusercontent.com`, e **não** a API
  do GitHub. A API sem autenticação permite 60 requisições/hora **por IP** — num escritório
  atrás de NAT os clientes estourariam isso entre si. Visto na prática: o IP da Questor
  chegou a 0 de 60. A API de tags fica só de reserva.
- **Download:** pelo codeload (`/archive/refs/tags/<tag>.tar.gz`), também fora do limite.
  A tag leva prefixo `v` e o pyproject não — o download tenta `v<versão>` e depois `<versão>`.
- **Nunca sobrescreve** `.env`, `token.json`, `credentials.json` nem `.claude/`. Guarda a
  versão anterior em `<pasta>.backup-<data>` antes de aplicar.
- **Para publicar uma versão nova:** subir o `version` no `pyproject.toml` **e** no
  `__version__` do `server.py`, e criar a tag `v<versão>` apontando para o commit. Sem a
  tag, o cliente é avisado mas o download falha. ⚠️ Se `atualizacao.py` ganhar um módulo
  novo, ele precisa entrar em `py-modules` no `pyproject.toml`.

### Fluxo típico de uso

**Descoberta (cold — 2 calls):**
```
gc_menus(tipo=4)                   → lista relatórios disponíveis
gc_inspecionar("nContRRBalancete") → mostra obrigatórios/opcionais separados
```

**Execução (após descoberta — 1 call, cache hit):**
```
gc_relatorio_executar("nContRRBalancete", parametros_json='{"Empresa":"001","Periodo":"2024-01"}')
```
→ valida params via cache (sem rede), executa direto

**Se faltar parâmetro obrigatório:**
```json
{
  "erro": "Parâmetros obrigatórios ausentes",
  "ausentes": ["Periodo"],
  "dica": "Consulte gc_inspecionar('nContRRBalancete') para ver todos os parâmetros."
}
```

### Detalhes importantes

- **GET com body**: o nHttp aceita body JSON em requisições GET. O `client.py` usa `httpx.request()` com `json=body` para isso.
- **Confirmação de processos**: algumas rotinas retornam mensagem pedindo confirmação. Reenvie o request com `{"Resposta": 0}` (SIM) ou `{"Resposta": 1}` (NÃO) no body.
- **Relatórios PDF/JPEG/NHZ**: exigem `base64=True`. O conteúdo retorna em Base64.
- **Importação NLI**: tanto o arquivo `.nli` quanto o arquivo de dados devem estar em Base64.
- **`_AsEcho`** no endpoint de consulta: controla ordenação ("Asc"/"Desc"), não é um echo literal.

### Documentação Local

Os HTMLs da documentação oficial estão em `docs/`:
- `Primeiros Passos para Utilizar o SYN Privado.html` — login, logout, descoberta de menus
- `Consultar Telas do Sistema.html` — endpoint `/api/TnWebDMConsulta/Pegar`
- `Rotinas do Sistema.html` — endpoint `/api/TnWebDMProcesso/ProcessoExecutar`
- `Relatórios do Sistema.html` — endpoint `/Api/TnWebDMRelatorio/Executar`
- `Importação de Dados.html` — endpoint `/api/Integracao/Importar`
- `Exportação de Dados.html` — endpoint `/api/Integracao/Exportar`

## Integração com Claude Desktop

Adicione ao `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "gestao-contabil": {
      "command": "python",
      "args": ["d:/WorkSpace/gestao-contabil-mcp/server.py"]
    }
  }
}
```
