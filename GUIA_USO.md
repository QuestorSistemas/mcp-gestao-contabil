# Gestao Contabil MCP - Guia de Uso

Servidor MCP (Model Context Protocol) para integrar o Claude com o **Questor Gestao Contabil** via API Cloud (nHttp).

Permite consultar dados, executar processos, gerar relatorios e importar/exportar dados diretamente pelo Claude Desktop ou Claude Code.

---

## 1. Requisitos

- **Python 3.10+**
- **Questor Gestao Contabil** com acesso nHttp habilitado (Cloud ou local)
- **Claude Desktop** ou **Claude Code**

---

## 2. Instalacao

### Jeito recomendado - instalar.bat (Windows)

1. Extraia o ZIP numa pasta de facil acesso, ex.: `C:\MCPs\gestao-contabil-mcp\`
2. De duplo-clique em **`instalar.bat`**
3. Feche o Claude Desktop **por completo** - inclusive o icone na bandeja do
   sistema, ao lado do relogio - e abra de novo

O instalador faz tudo sozinho:

- verifica se o Python 3.10+ esta presente;
- instala as dependencias (`pip install -e .`);
- pergunta URL, usuario e senha e grava o `.env`;
- **testa a conexao com o Questor** antes de terminar;
- registra o servidor no Claude Desktop e no Claude Code, em todos os
  caminhos de configuracao encontrados na maquina.

Rodar de novo e seguro: ele mostra os valores atuais entre colchetes (Enter
mantem) e nao remove os outros MCPs ja configurados.

Para conferir se funcionou, abra uma conversa nova e peca:
*"qual a versao do Questor?"*

### Jeito manual (Linux/macOS, ou se preferir controlar cada passo)

```bash
cd gestao-contabil-mcp
pip install -e .
python setup_config.py    # interativo: pede URL, usuario e senha
```

O `setup_config.py` cria o arquivo `.env` com:
```
GC_BASE_URL=https://gc-xxx.questor.com.br   # ou http://localhost:8080
GC_LOGON_NAME=administrador@conexao          # formato: usuario@conexao
GC_PASSWORD=senha
```

> O `setup_config.py` **so grava o `.env`**. Instalar dependencias e registrar
> no Claude ficam por sua conta - veja a secao 3.

---

## 3. Configuracao manual no Claude

Necessario apenas se voce nao usou o `instalar.bat`.

Adicione ao arquivo de configuracao:

```json
{
  "mcpServers": {
    "gestao-contabil": {
      "command": "python",
      "args": ["C:/caminho/para/gestao-contabil-mcp/server.py"]
    }
  }
}
```

> Se houver mais de um Python na maquina, troque `"python"` pelo caminho
> completo do executavel (ex.:
> `"C:/Users/<voce>/AppData/Local/Programs/Python/Python313/python.exe"`).
> E o que o `instalar.bat` faz, para nao depender do PATH.

Onde fica o arquivo:

| Instalacao | Caminho |
|------------|---------|
| Claude Desktop (instalador tradicional) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (Microsoft Store) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json` |
| Claude Code | `~/.claude/settings.json` |

Reinicie o Claude Desktop. O servidor MCP sera iniciado automaticamente.

---

## 4. Tools Disponiveis

### Conexao e Autenticacao
| Tool | Descricao |
|------|-----------|
| `gc_versao` | Testa conexao e retorna versao do Questor |
| `gc_health` | Diz se o servidor esta respondendo e o que fazer se nao estiver |
| `gc_login` | Login manual (diagnostico) |
| `gc_logout` | Encerra sessao |
| `gc_conexao_listar` | Lista conexoes registradas |
| `gc_conexao_adicionar` | Registra nova conexao em runtime |
| `gc_conexao_importar_ini` | Importa conexoes de um Questor.Conexao.ini |

### Descoberta e Inspecao
| Tool | Descricao |
|------|-----------|
| `gc_menus` | Lista rotinas por tipo (cadastros, consultas, processos, relatorios) |
| `gc_dados_objeto` | Parametros aceitos por uma rotina |
| `gc_inspecionar` | Obrigatorios/opcionais com labels e valores aceitos |
| `gc_cache_limpar` | Descarta o cache de parametros apos uma atualizacao do Questor |

### Operacoes
| Tool | Descricao |
|------|-----------|
| `gc_consultar` | Consulta dados de telas/cadastros |
| `gc_processo_executar` | Executa processos e rotinas |
| `gc_relatorio_executar` | Gera relatorios (PDF, HTML, RTF, CSV) |

### Integracao NLI
| Tool | Descricao |
|------|-----------|
| `gc_nli_construir` | Monta arquivo NLI (layout de integracao) |
| `gc_integracao_importar` | Importa dados via NLI |
| `gc_integracao_exportar` | Exporta dados via NLI |

---

## 5. Fluxo Basico de Uso

### Descobrir rotinas disponiveis
```
"Liste os relatorios de contabilidade"
→ Claude chama: gc_menus(tipo=4, modulo="Contabilidade")
```

### Inspecionar parametros
```
"Quais parametros preciso para o balancete de saldo?"
→ Claude chama: gc_inspecionar("nCtbRRBalanceteSaldo")
```

### Executar relatorio
```
"Gera o balancete de saldo da empresa 1, janeiro/2025"
→ Claude chama: gc_relatorio_executar("nCtbRRBalanceteSaldo", parametros_json={...})
→ Resultado: PDF salvo em ~/Downloads/gestao-contabil/
```

### Consultar dados cadastrais
```
"Lista as empresas cadastradas"
→ Claude chama: gc_consultar("TnGemDMEmpresa")
```

### Exportar dados via NLI
```
"Exporta a lista de bancos"
→ Claude chama: gc_nli_construir(...) + gc_integracao_exportar(...)
→ Resultado: CSV salvo em ~/Downloads/gestao-contabil/
```

---

## 6. Tipos de Menu

| Tipo | Conteudo |
|------|----------|
| 1 | Cadastros |
| 2 | Consultas nativas |
| 3 | Processos |
| 4 | Relatorios |
| 5 | Planilhas (descontinuado) |
| 6 | Consultas Personalizadas |

---

## 7. Multiplas Conexoes

Para trabalhar com mais de uma base de dados:

### Via .env
```
GC_ALLOW_MULTI_CONN=true

GC_CONN_FILIAL2_BASE_URL=http://seu-servidor:8080
GC_CONN_FILIAL2_LOGON_NAME=admin@empresa2
GC_CONN_FILIAL2_PASSWORD=senha2
```

### Via Questor.Conexao.ini
```
"Importa as conexoes do arquivo Questor.Conexao.ini"
→ Claude chama: gc_conexao_importar_ini("C:/Questor/Questor.Conexao.ini")
```

### Usando uma conexao especifica
```
"Lista empresas da filial2"
→ Claude chama: gc_consultar("TnGemDMEmpresa", conexao="filial2")
```

---

## 8. Formatos de Relatorio

| Formato | Parametro | Base64 |
|---------|-----------|--------|
| PDF | `nrwexPDF` | Sim |
| HTML | `nrwexHtml` | Nao |
| RTF | `nrwexRtf` | Nao |
| CSV | `nrwexCSV` | Nao |
| TXT | `nrwexTxt` | Nao |
| JPEG | `nrwexJpeg` | Sim |
| NHZ | `nrwexNHZ` | Sim |

Os relatorios sao salvos automaticamente em `~/Downloads/gestao-contabil/` ou no diretorio configurado em `GC_DOWNLOAD_DIR`.

---

## 9. Dicas

- **Login e automatico** - nunca precisa chamar `gc_login` manualmente
- **Sempre inspecione antes de executar** - use `gc_inspecionar` para ver parametros obrigatorios
- **Cache de parametros** - apos a primeira inspecao, chamadas repetidas sao instantaneas
- **Filtros na consulta** - use `filtros_json` com o nome exato da coluna (ex: `{"CODIGOEMPRESA": "1"}`)
- **Confirmacao de processos** - alguns processos pedem confirmacao; reenvie com `{"Resposta": 0}` (Sim) ou `{"Resposta": 1}` (Nao)

---

## 10. Estrutura do Projeto

```
gestao-contabil-mcp/
  instalar.bat       # Instalador para Windows (duplo-clique) - comeca por aqui
  install.py         # Instalador propriamente dito, chamado pelo .bat
  server.py          # Servidor MCP - todos os tools
  client.py          # Cliente HTTP com login automatico
  setup_config.py    # Configuracao interativa (so o .env)
  pyproject.toml     # Dependencias
  .env.example       # Modelo de configuracao
  CLAUDE.md          # Documentacao tecnica detalhada
  GUIA_USO.md        # Este guia
  docs/              # Documentacao HTML oficial do nHttp
```
