# ISSUE: gc_menus retorna payload maior que o limite MCP (1MB)

## Problema

A tool `gc_menus` chama `GET /api/TnWebDMMenus/Pegar?_ATipo={tipo}` e retorna a árvore completa de menus do Questor Gestão Contábil sem nenhum filtro. Para tipos com muitas rotinas (especialmente tipo 2 = Consultas e tipo 4 = Relatórios), o JSON retornado excede **1MB**, que é o limite de resposta do protocolo MCP.

### Comportamento atual

```
gc_menus(tipo=1)  → ~900KB  — funciona no limite (Claude salva em arquivo temporário)
gc_menus(tipo=2)  → >1MB    — ERRO: "Tool result is too large. Maximum size is 1MB."
gc_menus(tipo=3)  → ~200KB  — funciona
gc_menus(tipo=4)  → ~800KB  — funciona no limite
```

O tipo 2 (Consultas nativas) falha consistentemente. Tipos 1 e 4 funcionam mas ficam no limite, e o Claude precisa salvar em arquivo e usar grep para buscar — workflow pouco eficiente.

### Impacto

- O LLM não consegue listar consultas nativas (tipo 2)
- Mesmo quando funciona, o payload enorme consome grande parte da context window do Claude
- O workflow de descoberta de rotinas fica lento e impreciso

## Solução Proposta

Adicionar **filtragem server-side** na tool `gc_menus` com dois parâmetros opcionais: `modulo` e `filtro`. A filtragem acontece DEPOIS de receber a resposta do nHttp (o endpoint do Questor não aceita filtros), mas ANTES de retornar ao MCP client.

### Parâmetros novos

| Parâmetro | Tipo | Default | Descrição |
|-----------|------|---------|-----------|
| `modulo` | `str \| None` | `None` | Filtra pela raiz da árvore (Text do nó raiz). Ex: `"Gerenciador de Empresas"`, `"Contabilidade"`, `"Fiscal"`, `"Folha de Pagamento"` |
| `filtro` | `str \| None` | `None` | Busca textual (case-insensitive) nos campos `Text` e `Name` da árvore. Retorna apenas os nós que contêm o termo, preservando a hierarquia de pais |

### Comportamento esperado

```python
# Sem filtros — comportamento atual (pode estourar em tipo=2)
gc_menus(tipo=2)

# Filtro por módulo — retorna só a árvore do módulo Fiscal
gc_menus(tipo=2, modulo="Fiscal")

# Filtro por texto — retorna nós que contenham "empresa" no Text ou Name
gc_menus(tipo=4, filtro="empresa")

# Combinação — busca "balancete" só dentro de Contabilidade
gc_menus(tipo=4, modulo="Contabilidade", filtro="balancete")
```

## Implementação

### Arquivo: `server.py`

Alterar a função `gc_menus` (atualmente na linha ~247):

```python
# ANTES (atual)
@mcp.tool()
async def gc_menus(
    tipo: int = 3,
    conexao: str = "default",
) -> dict:
    """..."""
    return await get_client(conexao).get("/api/TnWebDMMenus/Pegar", params={"_ATipo": tipo})
```

```python
# DEPOIS (proposta)
@mcp.tool()
async def gc_menus(
    tipo: int = 3,
    modulo: Optional[str] = None,
    filtro: Optional[str] = None,
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
            Match parcial case-insensitive.
    filtro: busca textual nos campos Text e Name. Retorna apenas nós que contenham o
            termo (case-insensitive), preservando a hierarquia de pais até a raiz.

    O nome interno da rotina estará na tag "Name" da resposta.
    """
    resultado = await get_client(conexao).get(
        "/api/TnWebDMMenus/Pegar", params={"_ATipo": tipo}
    )

    # O endpoint retorna uma lista de módulos (nós raiz), cada um com Childs recursivo
    if not isinstance(resultado, list):
        return resultado

    # Filtro por módulo (nó raiz)
    if modulo:
        modulo_lower = modulo.lower()
        resultado = [
            m for m in resultado
            if modulo_lower in m.get("Text", "").lower()
        ]

    # Filtro por texto (busca recursiva)
    if filtro:
        resultado = [
            podado for m in resultado
            if (podado := _podar_arvore(m, filtro.lower())) is not None
        ]

    return resultado
```

### Função auxiliar `_podar_arvore` (adicionar no bloco Helpers do server.py)

```python
def _podar_arvore(no: dict, termo: str) -> dict | None:
    """
    Percorre a árvore recursivamente e retorna apenas os galhos
    que contêm o termo no Text ou Name.
    Preserva a hierarquia de pais para contexto.
    Retorna None se o nó e nenhum filho conter o termo.
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

    # Copia o nó sem Childs, depois adiciona só os filhos filtrados
    resultado = {k: v for k, v in no.items() if k != "Childs"}
    if filhos_filtrados:
        resultado["Childs"] = filhos_filtrados
    return resultado
```

### Atualizar o docstring das instructions do MCP

No `FastMCP(instructions=...)` em `server.py`, adicionar no bloco "Fluxo obrigatório":

```
- gc_menus(tipo=4, filtro="empresa") → busca relatórios que contenham "empresa"
- gc_menus(tipo=2, modulo="Contabilidade") → consultas do módulo Contabilidade
```

### Atualizar o CLAUDE.md

Na seção "Tools Implementados", atualizar a linha do gc_menus para incluir os novos parâmetros:

```
| `gc_menus` | `GET /api/TnWebDMMenus/Pegar` | Lista rotinas por tipo (1-6), com filtros opcionais `modulo` e `filtro` |
```

## Testes

### Teste 1 — Tipo 2 sem filtro (deve continuar funcionando até o limite)
```python
resultado = await gc_menus(tipo=2)
assert isinstance(resultado, list)
```

### Teste 2 — Tipo 2 com módulo (deve caber no MCP)
```python
resultado = await gc_menus(tipo=2, modulo="Fiscal")
assert isinstance(resultado, list)
assert len(resultado) == 1
assert "Fiscal" in resultado[0]["Text"]
```

### Teste 3 — Filtro por texto
```python
resultado = await gc_menus(tipo=4, filtro="balancete")
# Deve retornar somente nós que contenham "balancete" no Text ou Name
# com a hierarquia de pais preservada
def contar_names(nos):
    count = 0
    for no in nos:
        if "Name" in no:
            count += 1
        count += contar_names(no.get("Childs", []))
    return count
assert contar_names(resultado) > 0
```

### Teste 4 — Combinação módulo + filtro
```python
resultado = await gc_menus(tipo=4, modulo="Contabilidade", filtro="empresa")
assert len(resultado) <= 1  # no máximo 1 módulo raiz
```

### Teste 5 — Filtro sem resultado
```python
resultado = await gc_menus(tipo=4, filtro="xyzinexistente123")
assert resultado == []
```

## Estimativa

Mudança pequena e isolada: ~30 linhas de código novo em `server.py` (função `_podar_arvore` + alteração do `gc_menus`). Zero mudança no `client.py`. Sem breaking change — sem filtros o comportamento é idêntico ao atual.

## Módulos válidos (para referência)

Estes são os nós raiz retornados pelo gc_menus (campo Text do primeiro nível):

- Gerenciador de Empresas
- Contabilidade
- Folha de Pagamento
- Fiscal
- Inventário
- Controle Organizacional
- Controle de Tributos
- Financeiro
- Controle Patrimonial
- Arquivos Magnéticos
- Protocolo
- Lean
- Tribunal de Contas do Estado
- Serviço Trabalho Temporário
- Cloud
- Web
- Quiu - Automatizações
- eSocial
