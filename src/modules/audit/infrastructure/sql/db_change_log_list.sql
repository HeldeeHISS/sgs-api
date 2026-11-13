-- Objetivo: listar os registros do log do banco, do mais recente para o mais antigo, paginados.
-- Justificativa: o log do banco é onde o próprio PostgreSQL registra cada inclusão, alteração e
--   exclusão de unidade, equipamento e serviço, inclusive as feitas fora da API (011/R7.1,
--   R7.2). O Auditor o consulta para achar o que a trilha da aplicação não viu (011/R7.7).
--   - `ORDER BY occurred_at DESC, id DESC`: a ordem da tela; o `id` desempata registros da mesma
--     hora e deixa a paginação estável.
--   - `count(*) OVER ()` dá o total da consulta na mesma passada, para a paginação.
-- Uso: SqlLogDoBanco.listar, na rota GET /api/v1/auditoria/banco (011/T15). O
--   `ix_db_change_log_occurred_at` (011/T7) entrega as linhas na ordem da resposta.
-- Requisitos: 011/R7.7
SELECT
    l.id,
    l.occurred_at,
    l.table_name,
    l.operation,
    l.row_id,
    l.changed_columns,
    l.db_user,
    l.via_api,
    count(*) OVER () AS total
FROM db_change_log AS l
ORDER BY l.occurred_at DESC, l.id DESC
LIMIT :limit OFFSET :offset;
