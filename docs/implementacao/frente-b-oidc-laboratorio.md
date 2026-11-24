# Frente B · OIDC e laboratório

- Para quem: quem implementa a frente B, com ou sem agente. Antes, leia o [README desta pasta](README.md).
- Entrega:
  - as peças compartilhadas do compose (certificados de laboratório, redes);
  - o Keycloak e o `slapd`;
  - a assinatura dos relatórios da 005;
  - o login OIDC na API e no front;
  - a demonstração ponta a ponta da 004.
- Ordem: 004/T8 → T13 → 005/T9 → 004/T6 → T10 → T7 → T14. Estimativa: 30 a 48 h.
- Design de referência: `specs/004-diretorio-ldap-oidc/design.md` (§ Ambiente, § Fluxo, Login OIDC, § Front-end) e `specs/005-exportacao-relatorios-s3/design.md` § Assinatura.
- Mexe em:
  - `docker/` (`compose.yml`, `caddy/Caddyfile`, pastas novas `servicos/`, `keycloak/` e `ldap/`), `.env.example`, `README.md`: até a 004/T13 entrar, só esta frente mexe neles;
  - `src/modules/identity/` (OIDC: domínio, aplicação, `infrastructure/authlib_oidc.py`, `api/oidc.py`);
  - `src/modules/reports/` (só a assinatura);
  - `src/core/config.py` (blocos OIDC e chaves de relatório);
  - `src/main.py`;
  - `frontend/app/js/`;
  - `tests/e2e/`, `tests/e2e_front/`, `tests/unit/identity/`, `tests/integration/identity/`, `tests/architecture/test_rotas_protegidas.py`.

## 1. 004/T8 · Peças compartilhadas e Keycloak (destrava a T8 da frente C)
- **Primeira hora, antes de tudo.** Suba o Keycloak com `KC_HOSTNAME`, `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` e `KC_PROXY_HEADERS=xforwarded`. Compare o `issuer` da descoberta pelo navegador (`https://keycloak.localhost/realms/sgs/.well-known/openid-configuration`) com o da rede interna (`https://keycloak:8443/...`, de dentro do contêiner da API). **Se forem diferentes, pare e avise os donos** (design § Riscos).
- **Faça:**
  - `docker/servicos/gerar-certificados.sh` e o serviço *one-shot* `servicos-certs`, no padrão do `db-certs`:
    - uma CA de laboratório e certificados para `ldap`, `keycloak` **e `s3`** (a frente C usa o de `s3`);
    - a chave da CA descartada;
    - cada chave com o dono do usuário do seu contêiner;
  - as redes `integracoes` (`internal: true`) e `exposicao`. O proxy e a API **não** ganham rede nova em comum (013/R3.1);
  - o Keycloak com tag de versão fixa (nunca `latest`) e `docker/keycloak/realm-sgs.json`, sem federação, com o cliente `sgs-api` (PKCE S256), o *mapper* `groups` e um usuário por papel;
  - o bloco `keycloak.localhost` no Caddy, conferindo o certificado do Keycloak pela CA de laboratório;
  - as variáveis `SGS_PUBLIC_URL` e `SGS_PUBLIC_KEYCLOAK_URL`, que alimentam o Keycloak e a API ao mesmo tempo;
  - o `.env.example` e o README.
- **Cuidado:**
  - os segredos do laboratório levam `exemplo` no valor (`sgs-lab-exemplo-oidc`): é assim que a API os recusa em `prod`;
  - a API **não** depende do Keycloak para ficar saudável.
- **Avise:** "Integrada: 004/T8".

## 2. 004/T13 · `slapd` só com LDAPS
- **Faça:**
  - `docker/ldap/Dockerfile` (Debian e `slapd`), escutando só `ldaps:///` e `ldapi:///`;
  - `seed.ldif` com a conta de serviço só leitura (ACL), um usuário por papel, um a mais para a demonstração da desativação e grupos `groupOfNames`;
  - as senhas com hash `{SSHA}`;
  - o *alias* de rede `ldap-nome-errado`.
- **Pronto quando:** `tests/e2e/test_lab_ldap.py -m slow` passa. O teste usa os `ldap-utils` de dentro do contêiner `ldap`; o compose não publica porta LDAP no host.
- **Avise:** "Integrada: 004/T13". A partir daí, a frente C pode mexer no compose (005/T8).

## 3. 005/T9 · Assinatura dos relatórios (destrava a T5 da frente C)
- **Leia:** `specs/005-exportacao-relatorios-s3/design.md` § Assinatura.
- **Faça:**
  - `REPORT_SIGNING_KEYS(_FILE)` com o mesmo leitor das chaves JWT, recusando chave igual a uma JWT (R5.2);
  - a porta `AssinadorDeRelatorio` em `reports/domain/` e o adaptador ES256 (`ECDSA(Prehashed(SHA256()))`, assinatura `r‖s` em base64url);
  - a rota pública `GET /.well-known/relatorios-jwks.json`;
  - `docs/operacao/verificar-relatorio.md`, com a rotação e a verificação fora da API (Python e `openssl`).
- **Cuidado:** a pasta `src/modules/reports/` também nasce na frente C (005/T1). Crie só o que a assinatura precisa e integre logo, para a C rebasear em cima.
- **Avise:** "Integrada: 005/T9".

## 4. 004/T6 · Configuração OIDC e validação do `id_token`
- **Leia:** design § Configuração (linhas `OIDC_*`) e § Portas e adaptadores (`authlib_oidc.py`).
- **Faça:**
  - `_oidc_problems()`: `https://`, caminho do callback, origem do `FRONTEND_URL` em `CORS_ORIGINS`, segredo de exemplo recusado em `prod`;
  - o adaptador: Authlib para a URL (S256) e a troca do código, PyJWT para o `id_token`.
- **Cuidado:**
  - algoritmos fixos `RS256` e `ES256` (nunca `none` nem `HS*`);
  - `kid` desconhecido recarrega o JWKS uma vez, no máximo uma vez por minuto;
  - o `issuer` da descoberta tem de ser igual a `OIDC_ISSUER`.
- **Teste** com JWKS e `id_token` gerados no próprio teste, sem rede.

## 5. 004/T10 · Botão OIDC no front
- **Faça:**
  - `views/login.js` chama `GET /api/v1/auth/provedores` e, com `oidc = true`, mostra o link "Entrar pelo provedor de identidade";
  - `main.js` trata `?login_erro=oidc`.
- **Teste** com o Playwright (ADR-0011) e a API simulada por `page.route`. A rota real só chega na T7.

## 6. 004/T7 · Rotas OIDC (espera a 003/T8 e a 004/T1 da frente A)
- **Leia:** design § Fluxo, Login OIDC (início e callback, passos 1 a 6).
- **Faça:**
  - as três rotas em arquivo novo `identity/api/oidc.py`;
  - as três em `PUBLICAS`, com justificativa;
  - `make openapi`.
- **Cuidado:**
  - o consumo do `state` vai numa transação **confirmada antes** da troca do código;
  - o callback sempre responde 302 e nunca põe token na URL;
  - o sucesso grava só o cookie de refresh;
  - a falha audita `auth.oidc_failed` com motivo de lista fechada.
- **Cuidado com o arquivo:** a frente A mexe em `auth.py` e `login.py` na T5. Para abrir a sessão, acrescente uma função pública nova no fim de `login.py`, sem mexer em `_concluir_mfa`.

## 7. 004/T14 · Demonstração ponta a ponta (espera a 004/T4, T5 e T11 da frente A)
- **Faça:**
  - `tests/e2e/test_sync_ldap.py`;
  - `tests/e2e/test_login_ldap_oidc.py`, com o TOTP por `pyotp` e o OIDC pelo navegador até o `/me`;
  - `docs/operacao/demo-diretorio.md`;
  - a nota de referência no design da 012 (§ Abertura), sem mudar o texto normativo dela.
- **Esperando a frente A?** Adiante o roteiro e o teste do OIDC, que já não dependem dela.

## Ao terminar a frente
- Todas as tarefas acima `[x]`.
- `docker compose -f docker/compose.yml up -d --build --wait` sobe tudo do zero (apague os volumes antes).
- O README descreve as senhas do laboratório e as variáveis públicas.
