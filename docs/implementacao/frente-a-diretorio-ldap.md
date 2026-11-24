# Frente A · Diretório LDAP

- Para quem: quem implementa a frente A, com ou sem agente. Antes, leia o [README desta pasta](README.md).
- Entrega: o mapeamento grupo→papel na `main` (porte da 003/T8), as tabelas do diretório, a sincronização LDAP com rotas e agendamento, o login por LDAP e o documento de exemplo da AWS da 005.
- Ordem: 003/T8 → 004/T1 → T2 → T11 → T12 → T3 → T5 → T4 → T9 → 005/T7. Estimativa: 35 a 50 h.
- Design de referência: `specs/004-diretorio-ldap-oidc/design.md`.
- Mexe em:
  - `src/modules/access/` (só no porte);
  - `src/modules/identity/` (domínio, aplicação, infraestrutura, `api/auth.py`, rotas novas do diretório);
  - `migrations/versions/0004_diretorio.py`;
  - `src/core/config.py` (bloco LDAP);
  - `src/main.py`;
  - `src/tools/sgs_admin.py`;
  - `tests/unit/identity/`, `tests/integration/identity/`;
  - `docs/architecture/`.

## 1. Porte da 003/T8 (spec 003; destrava a sua T3 e T5 e a T7 da frente B)
- **O quê.** O mapeamento grupo→papel e o recálculo das atribuições derivadas já foram feitos no commit `71eb072`, do ramo `origin/feat/011-integracao`, mas nunca chegaram à `main`.
- **Como:**
  1. `git cherry-pick -x 71eb072`. Espere conflitos em `src/main.py`, `src/modules/access/` e `contracts/openapi.yaml`.
  2. Resolva os conflitos no código. O contrato não se resolve à mão: rode `make openapi` no fim.
  3. Leia também dois ajustes posteriores, no mesmo ramo:
     - `b77932a` (`actor_roles` com os papéis vigentes): aplique se a `main` ainda não tiver o equivalente;
     - `a9be55c` (helper `problema()`): **não** aplique como está, porque a `main` não tem esse helper; siga o padrão de erros da `main`.
  4. Mantenha o `SemMembrosDeGrupo` (nenhum grupo tem membros até a 004/T1). A troca pela leitura real é na sua T4.
- **Pronto quando:** `tests/integration/access/test_mapeamento_grupos.py` passa e o `make check` está verde.
- **Marque** a T8 em `specs/003-rbac/tasks.md`. A spec 003 é do Vinicius: avise-o.
- **Avise:** "Integrada: 003/T8".

## 2. 004/T1 · Migração `0004_diretorio` e repositórios (destrava a T7 da frente B)
- **Leia:** design § Modelo de dados e § Portas e adaptadores (repositórios).
- **Faça:**
  - a migração à mão (`make migrate m="diretorio"` e revise), com `groups`, `user_groups`, `directory_sync_runs`, `oidc_login_requests`, o índice único parcial `ux_directory_sync_runs_em_andamento` e a FK de `group_role_mappings.group_id`. Antes da FK, a migração confere se há `group_id` órfão e para com mensagem clara;
  - as tabelas iguais em `identity/infrastructure/tables.py` (o teste `test_migracoes_sincronizadas` confere);
  - os repositórios, com *upserts* por `ON CONFLICT (source, external_id)` e o consumo do `state` num `UPDATE ... RETURNING` atômico.
- **Cuidado:** `down_revision` na cabeça do momento. Se a 005/T1 entrar antes, aponte para ela (README § Regras de integração).
- **Avise:** "Integrada: 004/T1".

## 3. 004/T2 · Configuração LDAP e adaptador `ldap3`
- **Leia:** design § Configuração (linhas `LDAP_*`) e § Portas e adaptadores.
- **Faça:**
  - `_ldap_problems()` em `Settings`: esquema `ldaps://`, ou `ldap://` só com StartTLS; CA obrigatória; senha de exemplo recusada em `prod`;
  - as portas no domínio;
  - `ldap3_directory.py`, com o `MOCK_SYNC` do `ldap3` nos testes de unidade, carregado de um LDIF de teste.
- **Cuidado:**
  - todo valor que entra em filtro passa por `escape_filter_chars`;
  - senha vazia é recusada antes do *bind*;
  - toda chamada roda em `anyio.to_thread.run_sync`;
  - nenhuma senha em log ou exceção.

## 4. 004/T11 · Conferência do TLS do LDAP (risco: faça cedo)
- **O risco.** O `ldap3` 2.9.1 não tem release desde 2021. A conferência do nome do servidor usa um *backport* da própria biblioteca no Python 3.13.
- **Faça:**
  - no teste, gere com `cryptography` duas CAs e certificados (um com o nome certo, outro com nome errado) e suba um servidor TLS local numa *thread*;
  - o adaptador tem de recusar a CA errada e o nome errado **sem enviar nenhum byte LDAP**: o servidor de teste registra o que recebeu.
- **Se o nome errado passar,** troque para um `ssl.SSLContext` com `check_hostname = True` (design § Riscos) e registre isso no commit.
- **Faça também** o subcomando `sgs-admin diretorio testar [--url] [--ca]`, que a frente B usa no teste ponta a ponta.
- **Avise:** "Integrada: 004/T11".

## 5. 004/T12 · Plano da sincronização (domínio puro)
- **Faça:** `planejar(retrato, locais)`, sem banco, que devolve criações, atualizações, reativações, desativações, grupos, associações e conflitos.
- **Cuidado:** conflito por e-mail **e** por `username`, porque os dois são únicos em todas as origens (D004-11). Um conflito não para os demais.

## 6. 004/T3 · Caso de uso da sincronização
- **Leia:** design § Fluxo, Sincronização (passos 1 a 6).
- **Faça:**
  - leia o diretório **fora** de transação e aplique tudo numa transação só;
  - chame `recalcular_derivadas_do_usuario` para quem mudou de grupo e para os desativados (com grupos vazios);
  - na falha, outra transação grava o `falhou` com mensagem genérica e o detalhe vai só para o log;
  - na subida, execuções `em_andamento` viram `falhou` ("Interrompida").
- **Teste** com um `DirectoryPort` falso e o PostgreSQL do Testcontainers.

## 7. 004/T5 · Login por LDAP
- **Leia:** design § Fluxo, Login LDAP.
- **Faça:**
  - `provider: Literal["local", "ldap"] = "local"` em `LoginIn`;
  - `entrar_por_ldap` em arquivo novo (`identity/application/login_ldap.py`);
  - `_concluir_mfa` escolhe o `amr` pela origem da conta (`ldap+otp`).
- **Cuidado:**
  - bloqueio conferido **antes** do *bind*;
  - conta inativa nunca é reativada pelo login;
  - conflito de e-mail ou `username` recusa sem criar nada;
  - diretório fora responde 503 (`ServiceUnavailable`, da T0).
- **Cuidado com o arquivo:** a frente B também mexe em `auth.py` e `login.py`, na T7. Mantenha as suas mudanças nos trechos do login e do 2FA.
- **Avise:** "Integrada: 004/T5".

## 8. 004/T4 · Rotas da sincronização, das execuções e dos grupos
- **Faça:**
  - as rotas de design § Rotas (`diretorio:sync` e, nos grupos, `papel:assign`);
  - a troca do `SemMembrosDeGrupo` pela leitura de `user_groups` com usuários ativos;
  - `make openapi`.
- **Teste** também 401, 403 e 422.
- **Avise:** "Integrada: 004/T4".

## 9. 004/T9 · Agendamento
- **Faça:**
  - o analisador do subconjunto de cron (design § Agendamento), puro e no fuso de Recife (`src/core/fuso.py`);
  - a tarefa `asyncio` no lifespan.
- **Teste** com relógio e espera falsos, sem dormir de verdade.

## 10. 005/T7 · Documento de exemplo da AWS
- **Faça:** `docs/architecture/aws-iam-exemplo.md`, com o conteúdo de `specs/005-exportacao-relatorios-s3/design.md` § Ambiente, "AWS real".
- **Pronto quando:** outro integrante revisou o documento.

## Ao terminar a frente
- Todas as tarefas acima `[x]`, com a linha de prova.
- `uv run alembic heads` com uma cabeça só no `feat/004-005`.
- Avise a frente B, que fecha a demonstração da 004 na T14.
