# Rodando as frentes com agentes

- Para quem: os três integrantes que vão rodar, cada um na sua máquina, um agente de código (Claude Code) numa frente, ao mesmo tempo.
- Leia antes: o [README desta pasta](README.md) (regras, integração, pontos de encontro) e o guia da sua frente.

## O modelo
| Quem | Frente | Primeira tarefa |
|---|---|---|
| Pessoa 1 | [A · Diretório LDAP](frente-a-diretorio-ldap.md) | porte da 003/T8 |
| Pessoa 2 | [B · OIDC e laboratório](frente-b-oidc-laboratorio.md) | 004/T8 (conferência do `iss` do Keycloak) |
| Pessoa 3 | [C · Relatórios](frente-c-relatorios.md) | 005/T10 (conferência do SeaweedFS) |

- **Uma tarefa por sessão.** O agente pega a próxima tarefa da frente, faz com teste primeiro, roda o `make check`, faz o commit e **para**.
- **A pessoa revisa e integra.** Ela lê o diff e responde "integre", e o agente rebaseia, roda o `make check` de novo e envia ao `feat/004-005`. Depois, `/clear` e o mesmo prompt de novo.
- **Ninguém precisa avisar para destravar.** Todo commit integrado cita a tarefa (`(004/T1)`), então o agente confere sozinho no `git log` se o bloqueio de outra frente já entrou. O aviso no canal do grupo continua, para todo mundo saber o andamento.

## Preparar a máquina (uma vez, uns 20 minutos)
1. **Instale e confira:**
   - Docker 26 ou mais novo, `uv`, GNU Make e git com chave SSH no GitHub;
   - o Claude Code no terminal do sistema (`claude --version`).

   **Rode o agente num terminal comum, não dentro de um editor em sandbox** (por exemplo, o VS Code em Flatpak): ali o agente não enxerga o Docker nem o `uv`, e os testes de integração não rodam.
2. **Clone e crie o *worktree* da frente.** O `feat/004-005` já tem a T0:

   ```bash
   git clone git@github.com:rafareloM/sgs-api.git && cd sgs-api
   git worktree add ../sgs-frente-a -b frente/a origin/feat/004-005   # a, b ou c
   cd ../sgs-frente-a
   ```

3. **Prepare o projeto:** siga o README § Antes de começar (`uv sync`, `pre-commit install`, `secrets/`, `docker/.env`). O `make check` tem de passar antes de abrir o agente.
4. **Instale as permissões** (o arquivo fica fora do Git):

   ```bash
   mkdir -p .claude && cp docs/implementacao/claude-settings.exemplo.json .claude/settings.local.json
   ```

   - **Liberado sem pergunta:** `uv run`, `make check`, `make openapi`, `make migrate`, leitura e commits no git, `git push origin HEAD:feat/004-005`, `docker compose` dos arquivos do projeto, edição de `src/`, `tests/`, `migrations/`, `specs/`, `docs/`, `contracts/`, `docker/` e `frontend/app/`.
   - **Proibido:** push na `main`, push forçado, `--no-verify`, edição de `.claude/` e leitura de `secrets/`.
   - **O resto pergunta.**
5. **Abra o agente na pasta do *worktree*:** `claude`.

## O prompt de cada tarefa
Copie, troque a frente e o arquivo do guia, e cole a cada sessão nova:

```text
Você implementa a frente A das specs 004 e 005 (guia: docs/implementacao/frente-a-diretorio-ldap.md).
Leia docs/implementacao/README.md, docs/implementacao/agentes.md e o guia da frente.

1. Atualize: git fetch origin && git rebase origin/feat/004-005.
2. Ache a primeira tarefa da "Ordem" do guia que ainda está [ ] no tasks.md da spec dela e cujos
   bloqueios ("Bloqueada por") já estão integrados. Para cada bloqueio NNN/Tn, confira com
   git log origin/feat/004-005 --oneline -E --grep='NNN/Tn[,)]'
   Se nenhuma tarefa estiver livre, diga quais bloqueios faltam e pare.
3. Implemente só essa tarefa seguindo .claude/commands/spec-implementar.md: teste primeiro (falhando
   pelo motivo certo), código mínimo, make check verde, make openapi se a rota mudou, [x] com a linha
   de prova, commit "tipo(módulo): descrição (NNN/Tn)" com o trailer Co-Authored-By.
4. Respeite os cuidados do guia e as condições de parada do README. Se precisar mudar requisito,
   design, dependência ou arquivo fora da lista da frente, pare e me explique.
5. No fim, mostre o resumo do diff e os testes que passaram, e pare. Não integre sem eu responder "integre".
```

Quando a pessoa responder **"integre"**, o agente roda:

```bash
git fetch origin && git rebase origin/feat/004-005 && make check && git push origin HEAD:feat/004-005
```

Se o push for recusado porque outra frente integrou antes, ele repete a linha. Um conflito no `tasks.md` costuma ser só de marcação: as duas marcações ficam. Depois: aviso no canal ("Integrada: 004/T1, `<hash>`"), `/clear` e o prompt de novo.

## O que revisar no diff (5 minutos por tarefa)
- O teste de "Pronto quando" existe, cita o critério na docstring e testa o comportamento, não a implementação.
- Rota nova tem `require(...)` ou está em `PUBLICAS`, e tem testes 401, 403 e 422.
- Nenhum segredo, token, senha ou URL pré-assinada em log, exceção ou resposta.
- Auditoria na mesma transação.
- Nada fora da tarefa e nada fora da lista de arquivos da frente.
- O `contracts/openapi.yaml` saiu do `make openapi`, não de edição à mão.

## Para andar rápido sem quebrar nada
- **Sessões curtas.** Uma tarefa por sessão, com `/clear` entre elas: a tarefa e o design bastam, e o contexto limpo erra menos.
- **Testes durante o trabalho:** o agente roda só a pasta da tarefa (`uv run pytest tests/unit/identity -q`); o `make check` completo fica para antes do commit e antes do push. Os testes `slow` (`tests/e2e/`) só nas tarefas de ambiente e de ponta a ponta.
- **Bloqueado?** O prompt já escolhe a próxima tarefa livre da ordem. Se nenhuma estiver livre, aproveite para revisar o diff integrado de outra frente.
- **Conferências de risco** (primeira tarefa de B e de C, T11 de A): a pessoa acompanha. Se falharem, o plano muda, e quem decide são os donos (Rafael e Vinicius).
- **Duas frentes na mesma máquina?** Use subnets e portas diferentes no `docker/.env` de cada *worktree* (README § Ramos e *worktrees*). Em máquinas separadas, os valores padrão servem.
