# 004 · Diretório LDAP e OIDC · Design

- Status: Rascunho

## Portas e adaptadores
```python
class DirectoryPort(Protocol):
    def iter_users(self) -> Iterator[DirectoryUser]: ...
    def iter_groups(self) -> Iterator[DirectoryGroup]: ...
    def find_user_dn(self, login: str) -> str | None: ...
    def verify_password(self, dn: str, password: str) -> bool: ...

class OidcPort(Protocol):
    async def authorization_url(self, redirect_uri: str) -> AuthRequest: ...
    async def exchange(self, code: str, auth_request: AuthRequest) -> OidcIdentity: ...
```
- `infrastructure/ldap3_directory.py`: implementação com `ldap3.Server(use_ssl=True, tls=Tls(validate=CERT_REQUIRED))`, `Connection(auto_bind=..., read_only=True)`, `extend.standard.paged_search`. Chamado pelo caso de uso via `anyio.to_thread.run_sync`.
- `infrastructure/authlib_oidc.py`: `authlib.integrations.httpx_client.AsyncOAuth2Client` com metadados da descoberta e validação de `id_token` via `authlib.jose` / JWKS em cache.

## Configuração
| Variável | Exemplo |
|---|---|
| `LDAP_URL` | `ldaps://ldap:636` |
| `LDAP_BIND_DN` / `LDAP_BIND_PASSWORD` | `cn=sgs-sync,ou=servicos,dc=recife,dc=pe,dc=gov,dc=br` |
| `LDAP_USER_BASE` / `LDAP_USER_FILTER` | `ou=pessoas,...` / `(objectClass=inetOrgPerson)` |
| `LDAP_GROUP_BASE` / `LDAP_GROUP_FILTER` | `ou=grupos,...` / `(objectClass=groupOfNames)` |
| `LDAP_ATTR_*` | `uid`, `mail`, `cn`, `entryUUID`, `member` (AD: `sAMAccountName`, `objectGUID`) |
| `LDAP_CA_FILE` | `/run/secrets/ldap-ca.pem` |
| `OIDC_ISSUER` | `https://keycloak.local/realms/sgs` |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | cliente confidencial `sgs-api` |
| `OIDC_GROUPS_CLAIM` | `groups` |
| `OIDC_REQUIRED_ACR` | opcional |

## Estado do fluxo OIDC
`state`, `nonce` e `code_verifier` ficam em cookie cifrado (AES-GCM, `HttpOnly`, `SameSite=Lax`, 10 min), porque o retorno do provedor é uma navegação cross-site.

## Sincronização (algoritmo)
1. Abre execução (`status = em_andamento`), com trava por `pg_advisory_lock` (R1.11).
2. Lê todos os usuários e grupos do diretório para memória (volume esperado: milhares, cabe).
3. Em uma transação: upsert de usuários por (`source`, `external_id`); desativa ausentes; upsert de grupos; substitui `user_groups` de origem `ldap`; recalcula atribuições derivadas (spec 003).
4. Fecha execução com contagens; audita.
5. Qualquer exceção antes do commit → rollback e `status = falhou` (R1.10).

## Ambiente de desenvolvimento
- `docker/ldap/`: Dockerfile Debian + `slapd`, LDIF com `ou=pessoas`, `ou=grupos`, `ou=servicos` e usuários de exemplo por perfil (um por papel), certificado gerado no build.
- `docker/keycloak/realm-sgs.json`: realm `sgs`, cliente `sgs-api` (confidencial, PKCE S256), *mapper* de grupos na claim `groups`, federação LDAP apontando para o mesmo `slapd`.

## Rotas
| Método e rota | Permissão | Requisitos |
|---|---|---|
| `POST /api/v1/admin/diretorio/sincronizar` | `diretorio:sync` | R1.1, R1.11 |
| `GET /api/v1/admin/diretorio/execucoes` · `/{id}` | `diretorio:sync` | R1.9 |
| `POST /api/v1/auth/login` com `provider=ldap` | pública | R2 |
| `GET /api/v1/auth/oidc/login` | pública | R3.1 |
| `GET /api/v1/auth/oidc/callback` | pública | R3.2–R3.5 |

## Estratégia de testes
- Unidade: sincronização com `ldap3` `MOCK_SYNC` carregado de um LDIF de teste (criar, atualizar, desativar, grupos); escape de filtro com entradas maliciosas (`*)(uid=*`).
- Unidade: OIDC com JWKS e `id_token` gerados no teste (nonce errado, `aud` errado, expirado).
- Integração (marcador `slow`): compose com `slapd` e Keycloak reais, login ponta a ponta.

## Ajustes pendentes do G1 (revisão de 24/11/2026, agente; aguarda o dono)
Não aplicados: o design só muda depois do G1 dos requisitos revistos (`/spec-design`, passo 1). Com eles aprovados, este design precisa de:
- **Modelo de dados:** falta a seção. Colunas de `groups` (origem, `external_id`, nome), `user_groups` (com a origem da associação, que a sincronização substitui) e `directory_sync_runs` (status, contagens, conflitos e erro), e a FK de `group_role_mappings.group_id` (003, pergunta de T1).
- **Rotas:** `GET /api/v1/admin/diretorio/grupos` (R1.14), com a permissão a decidir entre `diretorio:sync` e `papel:assign`.
- **Ambiente de desenvolvimento (R5):**
  - o realm do Keycloak passa a ter usuários e grupos próprios, sem a federação com o `slapd` (pergunta de 24/11/2026 dos requisitos);
  - o `iss` dos tokens tem de ser o mesmo para o navegador, que chega ao Keycloak pelo proxy, e para a API, que chega pela rede interna;
  - o certificado do `slapd` vem de uma CA de desenvolvimento, no padrão do `db-certs` do compose;
  - os segredos do laboratório são recusados em `prod` (R5.4).
- **OIDC:**
  - `OIDC_REDIRECT_URI` e `FRONTEND_URL` vêm da configuração (R3.8);
  - o cookie do `state` é apagado no callback, com ou sem sucesso (R3.7);
  - o JWKS é recarregado uma vez quando o `kid` é desconhecido;
  - no sucesso, o callback abre a sessão da spec 002 (cookie de refresh) e redireciona ao front-end, que obtém o access token pela renovação (R3.4, R3.9).
- **Spec 003/T8:** a sincronização (T3), o login LDAP (T5) e o callback (T7) recalculam as atribuições derivadas na mesma transação, pelo contrato que a 003/T8 fixou na `origin/feat/011-integracao` (`recalcular_derivadas_do_usuario(usuario_id, grupos)`), ainda fora da `main`.
- **Testes:** recusa de certificado com CA errada contra o `slapd` real (R1.13); sincronização real (R5.3); `state` repetido e vencido (R3.7); e-mail em uso e conta inativa (R4.3, R4.4).
