# 005 · Exportação de relatórios para S3 · Tarefas

- Status: Rascunho
- Depende de: 001, 003 (escopo de leitura). O gerador de inventário depende de `health_units` (spec 006); até lá, usa o stub da spec 003.

## Observações (revisão de 09/11/2026, agente; aguarda o dono)
- A spec 006 já está implementada: o gerador de inventário usa `health_units` real, sem o stub da 003.
- As permissões `relatorio:export` e `relatorio:download` já estão semeadas (`0003_access`).
- `boto3` e `openpyxl` estão em `steering/tech.md`, e `moto` nas ferramentas de teste, mas nenhum está no `pyproject.toml`.
- Falta a tarefa da R5 (assinatura digital). Proposta, a confirmar pelo dono: `T9. Assinatura do hash do relatório, chave própria com rotação e rota pública das chaves de verificação` (R5.1–R5.5).

## Ajustes pendentes do G1 (revisão de 24/11/2026, agente; aguarda o dono)
Não aplicados: as tarefas só mudam depois do G1 e do G2 (`/spec-tarefas`, passo 1). Proposta, a partir dos requisitos revistos:
- T3: inclui R2.8 e R3.6 (segundo cliente, para a URL pública).
- T4: passa a cobrir a R2.9; o "Pronto quando" ganha o caso sem `ExternalId` ou sem chave KMS.
- T6: R3.2 revisto (200 com `{url, expira_em}`).
- T8: passa a rastrear R6.1–R6.3, com TLS no armazenamento, exposição pelo proxy e teste ponta a ponta com download a partir do host.
- A T9 proposta em 09/11/2026 (R5) continua pendente.
- Tarefa opcional, se houver conta AWS: evidência do envio numa conta real (R2.1, R2.3, R2.9).
- Ordem sugerida:
  1. T8 primeiro: confirma TLS, endereço público e criptografia no SeaweedFS.
  2. Depois T1 → T2 → T3 → T4 → T5 → T6 → T7.

- [ ] T1. Migração `report_jobs` e repositório
  - Requisitos: R1.1, R2.5
  - Pronto quando: `tests/integration/reports/test_report_jobs.py` passa
- [ ] T2. Geradores CSV/XLSX com escopo, BOM e neutralização de fórmula
  - Requisitos: R1.2, R1.3, R4.1–R4.3
  - Pronto quando: `tests/unit/reports/test_generators.py` passa
- [ ] T3. `ObjectStoragePort` + adaptador boto3 com STS AssumeRole, SSE e checksum
  - Requisitos: R2.1–R2.4, R2.7
  - Pronto quando: `tests/integration/reports/test_s3_adapter.py` (moto) passa
- [ ] T4. Validação de configuração de produção (sem chave fixa)
  - Requisitos: R2.2
  - Pronto quando: `tests/unit/core/test_config.py::test_prod_exige_assume_role` passa
- [ ] T5. Caso de uso assíncrono de geração e envio com tratamento de falha
  - Requisitos: R2.5, R2.6
  - Pronto quando: `tests/integration/reports/test_gerar_relatorio.py` passa
- [ ] T6. Rotas de pedido, consulta, listagem e download pré-assinado com auditoria
  - Requisitos: R1.1, R1.4, R1.5, R3.1–R3.5
  - Pronto quando: `tests/integration/reports/test_report_routes.py` passa
- [ ] T7. Documento com exemplo de bucket policy, papel IAM e chave KMS
  - Requisitos: R2.1, R2.3
  - Pronto quando: `docs/architecture/aws-iam-exemplo.md` revisado por um integrante
- [ ] T8. SeaweedFS no compose e teste ponta a ponta
  - Requisitos: R2.7
  - Pronto quando: `tests/e2e/test_export_s3.py -m slow` passa
