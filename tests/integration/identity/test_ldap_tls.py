"""Conferência do TLS do LDAP com um servidor TLS local em *thread* (spec 004, T11; design
§ Riscos: o `ldap3` 2.9.1 confere o nome por um *backport* próprio).

O servidor de teste registra os bytes LDAP que recebe: com a CA errada ou o nome errado, o
adaptador recusa a conexão sem enviar nenhum (nenhuma credencial sai da API, R1.13).
"""

import datetime as dt
import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr

from src.core.config import Settings
from src.modules.identity.domain.diretorio import DirectoryUnavailable
from src.modules.identity.infrastructure.ldap3_directory import Ldap3Directory, LdapConfig
from src.tools import sgs_admin
from tests.factories import make_settings

SENHA_DO_SERVICO = "senha-exemplo-servico-tls"
STARTTLS_OID = b"1.3.6.1.4.1.1466.20037"


@dataclass(frozen=True)
class Autoridade:
    chave: ec.EllipticCurvePrivateKey
    certificado: x509.Certificate


def _nome(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _uso_de_ca() -> x509.KeyUsage:
    # O contexto padrão do Python 3.13 usa VERIFY_X509_STRICT: CA com keyUsage e AKI na folha.
    return x509.KeyUsage(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False,
    )


def _autoridade(cn: str) -> Autoridade:
    chave = ec.generate_private_key(ec.SECP256R1())
    agora = dt.datetime.now(dt.UTC)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(_nome(cn))
        .issuer_name(_nome(cn))
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(minutes=5))
        .not_valid_after(agora + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(_uso_de_ca(), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(chave.public_key()), critical=False
        )
        .sign(chave, hashes.SHA256())
    )
    return Autoridade(chave, certificado)


def _certificado_do_servidor(ca: Autoridade, nome: str, destino: Path) -> tuple[Path, Path]:
    chave = ec.generate_private_key(ec.SECP256R1())
    agora = dt.datetime.now(dt.UTC)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(_nome(nome))
        .issuer_name(ca.certificado.subject)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(minutes=5))
        .not_valid_after(agora + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(nome)]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.chave.public_key()),
            critical=False,
        )
        .sign(ca.chave, hashes.SHA256())
    )
    cert = destino / f"{nome}.crt"
    key = destino / f"{nome}.key"
    cert.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(
        chave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert, key


def _gravar_ca(ca: Autoridade, destino: Path) -> Path:
    caminho = destino / "ca.crt"
    caminho.write_bytes(ca.certificado.public_bytes(serialization.Encoding.PEM))
    return caminho


@dataclass
class ServidorTls:
    """Aceita uma conexão e guarda os bytes LDAP recebidos (antes e depois do TLS)."""

    contexto: ssl.SSLContext
    start_tls: bool
    # Com um resultCode, responde ao bind (0: sucesso; 49: invalidCredentials) e espera o unbind.
    resultado_do_bind: int | None = None
    porta: int = 0
    em_claro: bytearray = field(default_factory=bytearray)
    cifrados: bytearray = field(default_factory=bytearray)

    def rodar(self, escuta: socket.socket) -> None:
        try:
            conexao, _ = escuta.accept()
        except OSError:
            return
        conexao.settimeout(5)
        try:
            if self.start_tls:
                pedido = conexao.recv(4096)
                self.em_claro.extend(pedido)
                if STARTTLS_OID not in pedido:
                    return
                # extendedResp com sucesso e o mesmo messageID do pedido (comprimentos curtos).
                resposta = bytes([0x30, 0x0C, 0x02, 0x01, pedido[4]])
                conexao.sendall(resposta + bytes.fromhex("78070a010004000400"))
            with self.contexto.wrap_socket(conexao, server_side=True) as tls:
                while dados := tls.recv(4096):
                    self.cifrados.extend(dados)
                    if self.resultado_do_bind is None:
                        break  # o bind chegou: basta para o controle positivo
                    if dados[5] == 0x60:  # bindRequest → bindResponse com o mesmo messageID
                        cabecalho = bytes([0x30, 0x0C, 0x02, 0x01, dados[4], 0x61, 0x07])
                        corpo = bytes([0x0A, 0x01, self.resultado_do_bind, 0x04, 0x00, 0x04, 0x00])
                        tls.sendall(cabecalho + corpo)
        except (OSError, ssl.SSLError):
            pass  # a recusa do cliente (alerta TLS, conexão fechada) é o esperado
        finally:
            conexao.close()


@pytest.fixture
def autoridades() -> tuple[Autoridade, Autoridade]:
    return _autoridade("CA do laboratório"), _autoridade("CA de outra pessoa")


@contextmanager
def _servidor(
    cert: Path, key: Path, *, start_tls: bool, resultado_do_bind: int | None = None
) -> Iterator[ServidorTls]:
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(cert, key)
    servidor = ServidorTls(contexto, start_tls, resultado_do_bind)
    with socket.create_server(("127.0.0.1", 0)) as escuta:
        escuta.settimeout(10)
        servidor.porta = escuta.getsockname()[1]
        thread = threading.Thread(target=servidor.rodar, args=(escuta,), daemon=True)
        thread.start()
        yield servidor
        thread.join(timeout=10)


def _diretorio(url: str, ca: Path, *, start_tls: bool) -> Ldap3Directory:
    return Ldap3Directory(
        LdapConfig(
            url=url,
            start_tls=start_tls,
            ca_file=ca,
            bind_dn="cn=sgs-sync,ou=servicos,dc=teste",
            bind_password=SecretStr(SENHA_DO_SERVICO),
            user_base="ou=pessoas,dc=teste",
            user_filter="(objectClass=inetOrgPerson)",
            group_base="ou=grupos,dc=teste",
            group_filter="(objectClass=groupOfNames)",
            attr_login="uid",
            attr_email="mail",
            attr_name="cn",
            attr_id="entryUUID",
            attr_member="member",
            page_size=500,
        )
    )


CASOS = [
    pytest.param("certo", "localhost", True, id="ca-e-nome-certos"),
    pytest.param("errada", "localhost", False, id="ca-errada"),
    pytest.param("certo", "outro.teste", False, id="nome-errado"),
]


@pytest.mark.parametrize("start_tls", [False, True], ids=["ldaps", "starttls"])
@pytest.mark.parametrize(("ca_do_cliente", "nome_no_certificado", "aceita"), CASOS)
async def test_tls_do_ldap_e_conferido_antes_de_qualquer_credencial(
    tmp_path: Path,
    autoridades: tuple[Autoridade, Autoridade],
    start_tls: bool,
    ca_do_cliente: str,
    nome_no_certificado: str,
    aceita: bool,
) -> None:
    """004/R1.2, R1.13 (CA errada e nome errado recusados sem nenhum byte LDAP cifrado; o caso
    certo é o controle positivo: o bind chega ao servidor)"""
    certa, errada = autoridades
    cert, key = _certificado_do_servidor(certa, nome_no_certificado, tmp_path)
    ca = _gravar_ca(certa if ca_do_cliente == "certo" else errada, tmp_path)

    with _servidor(cert, key, start_tls=start_tls) as servidor:
        esquema = "ldap" if start_tls else "ldaps"
        url = f"{esquema}://localhost:{servidor.porta}"
        with pytest.raises(DirectoryUnavailable) as erro:
            # O servidor de teste não responde ao bind: até o caso certo termina indisponível.
            await _diretorio(url, ca, start_tls=start_tls).find_user("ana")
        assert SENHA_DO_SERVICO not in str(erro.value)

    if start_tls:  # em claro, só o pedido de StartTLS: uma mensagem, extendedReq (0x77)
        assert len(servidor.em_claro) == servidor.em_claro[1] + 2
        assert servidor.em_claro[5] == 0x77
        assert STARTTLS_OID in servidor.em_claro
    else:
        assert servidor.em_claro == b""
    if aceita:
        assert SENHA_DO_SERVICO.encode() in servidor.cifrados  # o bind veio, e só sob TLS
    else:
        assert servidor.cifrados == b""


# `sgs-admin diretorio testar` (design § Roteiro e ferramenta): conexão e bind da conta de
# serviço, sem gravar nada; responde "ok" ou o motivo genérico.

INVALID_CREDENTIALS = 49


def _settings_ldap(url: str, ca: Path, *, start_tls: bool = False) -> Settings:
    return make_settings(
        ldap_url=url,
        ldap_start_tls=start_tls,
        ldap_ca_file=str(ca),
        ldap_bind_dn="cn=sgs-sync,ou=servicos,dc=teste",
        ldap_bind_password=SecretStr(SENHA_DO_SERVICO),
        ldap_user_base="ou=pessoas,dc=teste",
        ldap_user_filter="(objectClass=inetOrgPerson)",
        ldap_group_base="ou=grupos,dc=teste",
        ldap_group_filter="(objectClass=groupOfNames)",
    )


@pytest.mark.parametrize(
    ("ca_do_cliente", "nome_no_certificado", "resultado_do_bind", "codigo", "saida"),
    [
        pytest.param("certo", "localhost", 0, 0, "ok", id="ok"),
        pytest.param("certo", "localhost", INVALID_CREDENTIALS, 1, "credencial", id="credencial"),
        pytest.param("errada", "localhost", 0, 1, "certificado", id="ca-errada"),
        pytest.param("certo", "outro.teste", 0, 1, "certificado", id="nome-errado"),
    ],
)
@pytest.mark.parametrize("start_tls", [False, True], ids=["ldaps", "starttls"])
def test_sgs_admin_diretorio_testar_responde_ok_ou_o_motivo(
    tmp_path: Path,
    autoridades: tuple[Autoridade, Autoridade],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    start_tls: bool,
    ca_do_cliente: str,
    nome_no_certificado: str,
    resultado_do_bind: int,
    codigo: int,
    saida: str,
) -> None:
    """004/R1.13, R5.3 (o operador e o teste ponta a ponta conferem a recusa do certificado;
    nada da senha na saída)"""
    certa, errada = autoridades
    cert, key = _certificado_do_servidor(certa, nome_no_certificado, tmp_path)
    ca = _gravar_ca(certa if ca_do_cliente == "certo" else errada, tmp_path)
    nenhuma_ca = tmp_path / "nao-usada.crt"
    nenhuma_ca.write_text("x", encoding="utf-8")
    # A configuração aponta para outro servidor e outra CA: --url e --ca valem por cima dela.
    invalido = "ldap://ldap.invalido" if start_tls else "ldaps://ldap.invalido"
    configurado = _settings_ldap(invalido, nenhuma_ca, start_tls=start_tls)
    monkeypatch.setattr(sgs_admin, "load_settings", lambda: configurado)

    with _servidor(cert, key, start_tls=start_tls, resultado_do_bind=resultado_do_bind) as sv:
        url = f"{'ldap' if start_tls else 'ldaps'}://localhost:{sv.porta}"
        retorno = sgs_admin.main(["diretorio", "testar", "--url", url, "--ca", str(ca)])

    capturado = capsys.readouterr()
    assert retorno == codigo
    assert saida in (capturado.out if codigo == 0 else capturado.err)
    assert SENHA_DO_SERVICO not in capturado.out + capturado.err


def test_sgs_admin_diretorio_testar_sem_servidor_responde_conexao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """004/R1.10 (diretório fora do ar: motivo genérico, sem a senha)"""
    ca = tmp_path / "ca.crt"
    ca.write_text("x", encoding="utf-8")
    with socket.create_server(("127.0.0.1", 0)) as livre:
        porta = livre.getsockname()[1]
    monkeypatch.setattr(
        sgs_admin, "load_settings", lambda: _settings_ldap(f"ldaps://localhost:{porta}", ca)
    )

    retorno = sgs_admin.main(["diretorio", "testar"])

    capturado = capsys.readouterr()
    assert retorno == 1
    assert "conexao" in capturado.err
    assert SENHA_DO_SERVICO not in capturado.out + capturado.err


@pytest.mark.parametrize(
    ("argumentos", "mensagem"),
    [
        pytest.param([], "LDAP_URL", id="ldap-desligado"),
        pytest.param(["--url", "ldap://ldap.teste"], "LDAP_URL: use ldaps://", id="sem-tls"),
    ],
)
def test_sgs_admin_diretorio_testar_recusa_configuracao_invalida(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    argumentos: list[str],
    mensagem: str,
) -> None:
    """004/R1.2 (o teste usa as mesmas regras da subida: só LDAPS ou StartTLS)"""
    ca = tmp_path / "ca.crt"
    ca.write_text("x", encoding="utf-8")
    configurado = _settings_ldap("ldaps://ldap.teste", ca) if argumentos else make_settings()
    monkeypatch.setattr(sgs_admin, "load_settings", lambda: configurado)

    assert sgs_admin.main(["diretorio", "testar", *argumentos]) == 2
    assert mensagem in capsys.readouterr().err
