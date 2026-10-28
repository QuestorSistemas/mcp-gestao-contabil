# MCP Questor Gestão Contábil

Conecta o **Claude** ao **Questor Gestão Contábil**: você pede em português e o Claude
consulta cadastros, gera relatórios, executa rotinas e importa ou exporta dados direto
no seu sistema.

> Versão atual: **0.3.2-beta** · Para Windows, com Claude Desktop ou Claude Code

---

## O que você pode pedir

Escreva para o Claude como escreveria para um colega:

- *"Lista as empresas cadastradas"*
- *"Quais relatórios de contabilidade existem?"*
- *"Gera o balancete de saldo da empresa 1, de janeiro de 2025"*
- *"Que parâmetros o balancete precisa?"*
- *"Exporta a lista de bancos para uma planilha"*
- *"Qual é a versão do Questor?"*

Os relatórios e exportações são salvos na sua pasta **Downloads**, em
`gestao-contabil`.

O Claude descobre sozinho qual rotina usar e quais informações ela exige. Se faltar
alguma, ele pergunta antes de executar.

---

## Antes de instalar

Você vai precisar de:

| | |
|---|---|
| **Python 3.10 ou mais novo** | [python.org/downloads](https://www.python.org/downloads/). Na instalação, marque **"Add Python to PATH"**. |
| **Claude Desktop** ou **Claude Code** | [claude.ai/download](https://claude.ai/download) |
| **Acesso ao Questor Gestão Contábil** | O endereço do seu nHttp, um usuário e a senha. Peça ao responsável pelo Questor no seu escritório se não souber. |

O endereço costuma ser um destes:

- **Questor Cloud:** `https://gc-SEUCODIGO.questor.com.br`
- **Servidor no escritório:** `http://localhost:8080` ou o endereço do servidor na sua rede

O usuário vai no formato `usuario@conexao` — por exemplo, `administrador@escritorio`.

---

## Instalação

1. **[Baixe o MCP](https://github.com/QuestorSistemas/mcp-gestao-contabil/archive/refs/heads/main.zip)**
   e extraia numa pasta onde ele vai ficar, por exemplo `C:\MCPs\gestao-contabil-mcp`.
2. Dê dois cliques em **`instalar.bat`** e responda o que ele perguntar: endereço,
   usuário e senha.
3. **Feche o Claude por completo** — inclusive o ícone ao lado do relógio do Windows — e
   abra de novo.

O instalador confere o Python, instala o que precisa, **testa a conexão com o Questor**
antes de terminar e registra o MCP no Claude Desktop e no Claude Code.

Para conferir, abra uma conversa nova e pergunte: *"qual a versão do Questor?"*

> Pode rodar o `instalar.bat` de novo quando quiser — por exemplo, para trocar a senha.
> Ele mostra os valores atuais e não mexe nos outros MCPs que você já tenha.

---

## Atualizações

Quando sair uma versão nova, o MCP **avisa** numa das respostas do Claude:

> 📦 Saiu a versão X do MCP Gestão Contábil (você está na Y). Para atualizar, use a
> ferramenta `gc_atualizar`.

Para atualizar, é só pedir: *"atualiza o MCP do Gestão Contábil"*. O Claude mostra antes
o que vai mudar e só aplica depois que você confirmar. Em seguida, feche e abra o Claude.

- **Seus dados de acesso não são tocados** — endereço, usuário e senha continuam como estão.
- **A versão anterior fica guardada** numa pasta de backup ao lado da instalação.
- **O MCP nunca se atualiza sozinho.** Ele só avisa; quem decide é você.

> Quem instalou a versão **0.2.0** ou anterior não recebe o aviso, porque ele ainda não
> existia. Baixe esta versão uma vez pelo link acima — daí em diante o aviso funciona.

---

## Antes de usar — leia os Termos de Uso

O uso do MCP segue os **[Termos de Uso de Inteligência Artificial](TERMOS_DE_USO.md)** da
Questor ([versão oficial no Questor Docs](https://docs.questor.com.br/pt-br/Produtos/termos-de-uso/termosdeusoia)).
Os pontos principais para quem usa o MCP:

- **O MCP altera o sistema de verdade.** As operações executadas por ele alteram
  diretamente o Questor e o banco de dados, podendo inclusive alterar ou excluir
  informações. **Revise cada operação antes de confirmá-la.**
- **Tenha um backup atualizado** antes de executar qualquer procedimento.
- **A responsabilidade é do usuário** — pela instalação, pela desinstalação e por cada
  procedimento executado.
- **Não inclua nas consultas** dados pessoais, dados sensíveis ou informações protegidas
  por sigilo, conforme a LGPD (Lei nº 13.709/2018).
- **As respostas da IA são apoio**, e não substituem a análise técnica e profissional.
  Confira e valide as informações antes de usá-las.

### Como os seus dados circulam

- **O MCP roda no seu computador** e conversa direto com o seu Questor — no servidor do
  escritório ou na Questor Cloud. Não existe nenhum serviço intermediário no meio.
- **Seu usuário e sua senha ficam só na sua máquina**, no arquivo `.env` da pasta de
  instalação. Não compartilhe esse arquivo.
- **A ferramenta de IA é sua**, contratada e operada por você. O que o Claude lê do Questor
  é enviado a ele para que possa responder, e o tratamento dessas informações segue as
  condições do fornecedor da ferramenta.
- **Quando o próprio Questor exige confirmação** numa rotina, o Claude mostra a pergunta a
  você antes de responder.

---

## Se algo não funcionar

**O Claude não mostra as ferramentas do Questor**
Feche o Claude por completo, incluindo o ícone ao lado do relógio, e abra de novo. Se
continuar, rode o `instalar.bat` outra vez.

**"Python não encontrado" ao instalar**
Instale o Python marcando **"Add Python to PATH"**, feche a janela e rode o
`instalar.bat` de novo.

**O Questor parou de responder**
Peça ao Claude: *"o servidor do Questor está respondendo?"*. Ele diz se o problema está
no servidor e o que fazer. Em geral é preciso reiniciar o serviço **Questor.nHttp** no
servidor — tarefa para quem administra o Questor no escritório.

**Usuário ou senha mudaram**
Rode o `instalar.bat` de novo e informe os novos dados.

---

## Documentação

- **[Termos de Uso](TERMOS_DE_USO.md)** — inclusive as orientações específicas para MCPs
- **[Como instalar e configurar MCPs](https://docs.questor.com.br/pt-br/Produtos/MCPs/instalacao-mcp)** e
  **[Como utilizar o MCP do Gestão Contábil](https://docs.questor.com.br/pt-br/Produtos/MCPs/utilizacao-mcp)** — guias oficiais no Questor Docs
- **[Guia de uso completo](GUIA_USO.md)** — todas as ferramentas, exemplos, múltiplas
  conexões, formatos de relatório e configuração manual (Linux/macOS)
- **[Documentação da API nHttp](docs/)** — as páginas de referência da API usada pelo MCP

---

<sub>Desenvolvido pela **Questor Sistemas** — Chapecó, SC.</sub>
