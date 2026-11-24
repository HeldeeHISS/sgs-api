# Frente C · Relatórios

- Para quem: quem implementa a frente C, com ou sem agente. Antes, leia o [README desta pasta](README.md).
- Entrega: a exportação dos relatórios `inventario_unidades` e `trilha_auditoria` em CSV e XLSX, com envio ao S3 por credenciais temporárias, criptografia e checksum conferidos, download por URL pré-assinada e o SeaweedFS no compose.
- Ordem: 005/T10 → T12 → T1 → T2 → T11 → T3 → T4 → T5 → T6 → T8. Estimativa: 35 a 52 h.
- Design de referência: `specs/005-exportacao-relatorios-s3/design.md`.
- Mexe em:
  - `src/modules/reports/` (módulo novo; a frente B cria nele só a assinatura, na 005/T9);
  - `migrations/versions/0005_relatorios.py`;
  - `src/core/config.py` (bloco S3);
  - `src/main.py`;
  - `tests/unit/reports/`, `tests/integration/reports/`, `tests/e2e/test_export_s3.py`;
  - `docker/` e `README.md` só na T8, depois que a 004/T13 entrar.

## 1. 005/T10 · Conferência antecipada do SeaweedFS (a primeira coisa a fazer)
- **Por quê.** A R2.8 e a R6.3 dependem de o SeaweedFS aplicar e informar a criptografia `AES256`. Se não aplicar, o plano muda (G1), e é melhor saber antes de escrever o resto.
- **Faça, fora do repositório:**
  1. Suba a imagem `chrislusf/seaweedfs` numa tag fixa, com o S3 em HTTPS (certificado de teste).
  2. Com o `boto3`, envie um objeto com `ServerSideEncryption="AES256"` e `ChecksumSHA256`.
  3. Veja o que a resposta traz e anote as opções de TLS do `weed server`.
- **Pronto quando:** o resultado (tag, comandos, resposta) está registrado em `specs/005-exportacao-relatorios-s3/design.md` § Riscos.
- **Se a SSE não vier confirmada, pare a frente e avise os donos.**

## 2. 005/T12 · Filtros e comparação de escopo (domínio puro)
- **Faça:**
  - os modelos dos filtros por tipo, com o período da trilha de até 366 dias;
  - `escopo_cobre(maior, menor)`: global cobre tudo; senão, cada conjunto contido no outro (conservador).

## 3. 005/T1 · Migração `0005_relatorios` e repositório
- **Leia:** design § Modelo de dados.
- **Faça:**
  - `report_jobs` com a versão do objeto, as colunas de assinatura e o `CHECK` de coerência;
  - o repositório em SQLAlchemy Core.
- **Cuidado:**
  - `down_revision` na cabeça do momento; se a 004/T1 entrar antes, aponte para ela;
  - a frente B pode ter criado `src/modules/reports/` (005/T9): rebaseie antes de começar.

## 4. 005/T2 · Geradores CSV e XLSX
- **Faça:**
  - CSV: UTF-8 com BOM, `;`, `\r\n`;
  - texto que começa por `=`, `+`, `-`, `@`, tabulação ou `\r` ganha um `'` na frente;
  - SHA-256 calculado durante a escrita.
- **Cuidado:**
  - o `openpyxl` grava como **fórmula** qualquer texto que comece por `=`: force a célula de texto (`data_type = "s"`). O teste confere que nenhuma célula do arquivo tem `data_type == "f"`;
  - colunas numéricas não recebem o `'`.

## 5. 005/T11 · Consultas em SQL explícito
- **Leia:** ADR-0009 (complemento da 011) e `src/core/escopo.py`.
- **Faça:**
  - os três arquivos `.sql` em `reports/infrastructure/sql/`, cada um com cabeçalho de objetivo, justificativa e uso, como os da 011;
  - o escopo entra pelo fragmento fixo, com parâmetros, nunca valor na string;
  - leitura por cursor do servidor em lotes.
- **Cuidado:** a trilha exportada não lê `metadata`, `ip`, `user_agent` nem `session_id`, e deixa de fora `auth.*`, `acesso.*` e `auditoria.*` (R4.3).

## 6. 005/T3 · Armazenamento S3
- **Leia:** design § Adaptador S3.
- **Faça:**
  - **`AssumeRole` por operação**, com `RoleSessionName=f"sgs-{usuario_id}"`, `ExternalId` e 900 s. Não use `RefreshableCredentials`: a sessão de 15 minutos cai na janela de renovação do botocore (D005-2);
  - **dois clientes:** o interno envia; o público (`S3_PUBLIC_ENDPOINT_URL`) gera a URL, porque a assinatura cobre o host;
  - `Config(request_checksum_calculation="when_required", response_checksum_validation="when_required")`, com o `ChecksumSHA256` explícito;
  - `conferir_gravacao` no domínio; se não conferir, remova **pela versão** (`VersionId`).
- **Teste** com `moto` (`mock_aws`). Para a resposta sem SSE, use o evento `after-call.s3.PutObject` do botocore ou o `Stubber`.
- **Cuidado:** nenhuma URL, chave ou credencial em log.

## 7. 005/T4 · Configuração S3
- **Faça:** `_s3_problems()`:
  - só `https://`;
  - em `prod`: papel, `ExternalId` e ARN de KMS obrigatórios, `aws:kms` exigido;
  - qualquer chave fixa (`S3_ACCESS_KEY_ID`, `AWS_ACCESS_KEY_ID` e afins) recusada em `prod`;
  - exportação desligada sem `S3_BUCKET`.

## 8. 005/T5 · Geração e envio (espera a 005/T9 da frente B)
- **Leia:** design § Fluxo, Geração e envio (passos 1 a 8).
- **Faça:**
  - `gerando` só se o pedido estiver `pendente`;
  - geração no escopo **gravado**;
  - assinatura, envio com a sessão de quem pediu, conferência;
  - `concluido` com hash, assinatura e `expires_at`;
  - na falha, transação própria e mensagem genérica;
  - pedidos interrompidos marcados na subida.
- **Teste** com um armazenamento falso e o PostgreSQL do Testcontainers.

## 9. 005/T6 · Rotas
- **Faça:**
  - o pedido exige `relatorio:export` **e** a permissão de dados do tipo (`unidade:read` ou `auditoria:read`);
  - o download responde 200 `{url, expira_em}` com `Cache-Control: no-store`;
  - `relatorio.baixado` é auditado **sem** a URL;
  - `make openapi`.
- **Teste:** o `AUDITOR` não exporta o inventário, a listagem do `GESTOR_DADOS` traz tudo, e 401, 403 e 422.

## 10. 005/T8 · SeaweedFS no compose (espera a 004/T8, e a 004/T13 para mexer no compose)
- **Primeira hora:** confira de novo, já no compose, a SSE e o TLS vistos na T10.
- **Faça:**
  - os serviços `s3` e `s3-init` (cria o bucket), nas redes `integracoes` e `exposicao`, com o certificado `s3` do `servicos-certs`;
  - o bloco `s3.localhost` no Caddy, que mantém o `Host` original;
  - `SGS_PUBLIC_S3_URL`;
  - o segredo `report_keys` montado na API;
  - `docs/operacao/demo-relatorios.md`.
- **Pronto quando:** `tests/e2e/test_export_s3.py -m slow` passa. O teste baixa a URL pelo navegador do host e confere hash e assinatura com a JWK pública.

## Ao terminar a frente
- Todas as tarefas acima `[x]`, inclusive as das outras frentes que são da 005 (T7 na A, T9 na B), antes do PR da 005.
- `uv run alembic heads` com uma cabeça só.
- O `.env.example` sem nenhum `http://` para o S3.
