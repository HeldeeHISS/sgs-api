"""Retrato do diretório LDAP (spec 004, design § Portas e adaptadores).

Só os tipos do retrato, usados pelo plano da sincronização (004/T12). A porta `DirectoryPort`
e `DirectoryUnavailable` são da 004/T2.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectoryUser:
    external_id: str  # entryUUID/objectGUID; o DN se LDAP_ATTR_ID estiver vazio (R1.5)
    dn: str
    login: str  # minúsculo
    email: str | None  # minúsculo
    display_name: str


@dataclass(frozen=True)
class DirectoryGroup:
    external_id: str
    dn: str
    name: str
    member_dns: frozenset[str]


@dataclass(frozen=True)
class DirectorySnapshot:
    users: tuple[DirectoryUser, ...]
    groups: tuple[DirectoryGroup, ...]
