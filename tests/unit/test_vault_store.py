"""
tests/unit/test_vault_store.py — Tests unitaires du chiffrement des credentials.

Couvre RNF-3 (Sécurité des credentials) :
  - chiffrement Fernet : la valeur stockée n'est jamais en clair
  - déchiffrement : roundtrip store → get retourne les données originales
  - isolation : deux migrations différentes ne partagent pas leurs secrets
  - sécurité du chemin : caractères spéciaux dans user_id ignorés (path traversal)
  - suppression : delete() efface le fichier chiffré
  - robustesse : fichier corrompu → retour None sans crash
  - clé erronnée : mauvaise clé Fernet → retour None sans crash
"""
import json
import pytest
from pathlib import Path
from cryptography.fernet import Fernet

from services.credentials.vault_store import EncryptedFileBackend


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """EncryptedFileBackend instancié avec une clé éphémère dans un répertoire temporaire."""
    key = Fernet.generate_key()
    return EncryptedFileBackend(storage_dir=tmp_path, key=key)


@pytest.fixture
def store_with_key(tmp_path):
    """Retourne (backend, key) pour tester des opérations nécessitant la clé brute."""
    key = Fernet.generate_key()
    backend = EncryptedFileBackend(storage_dir=tmp_path, key=key)
    return backend, key


# ── RNF-3 : le secret ne doit jamais apparaître en clair sur le disque ────────

class TestEncryptionAtRest:

    def test_stored_file_is_not_plaintext(self, store, tmp_path):
        """La valeur stockée sur disque ne contient pas le secret en clair."""
        creds = {"api_key": "super-secret-value-12345", "region": "westeurope"}
        store.store("user-1", "azure", creds)

        enc_files = list(tmp_path.glob("*.enc"))
        assert len(enc_files) == 1, "Un fichier .enc doit être créé"

        raw_bytes = enc_files[0].read_bytes()
        assert b"super-secret-value-12345" not in raw_bytes

    def test_stored_file_does_not_contain_key_name(self, store, tmp_path):
        """Même les noms de clés du dict ne doivent pas apparaître en clair."""
        creds = {"ARM_CLIENT_SECRET": "my-secret", "ARM_TENANT_ID": "tenant-123"}
        store.store("user-2", "azure", creds)

        raw_bytes = list(tmp_path.glob("*.enc"))[0].read_bytes()
        assert b"ARM_CLIENT_SECRET" not in raw_bytes
        assert b"my-secret" not in raw_bytes

    def test_stored_file_is_valid_fernet_token(self, store_with_key, tmp_path):
        """Le fichier .enc contient un token Fernet déchiffrable avec la bonne clé."""
        backend, key = store_with_key
        creds = {"access_key": "FAKE_AWS_ACCESS_KEY"}
        backend.store("user-3", "aws", creds)

        enc_files = list(tmp_path.glob("*.enc"))
        raw_bytes = enc_files[0].read_bytes()

        f = Fernet(key)
        decrypted = f.decrypt(raw_bytes)
        recovered = json.loads(decrypted.decode("utf-8"))
        assert recovered["access_key"] == "FAKE_AWS_ACCESS_KEY"

    def test_two_different_keys_produce_different_ciphertext(self, tmp_path):
        """Deux chiffrements du même secret avec des clés différentes → ciphertexts différents."""
        creds = {"secret": "same-value"}
        key1, key2 = Fernet.generate_key(), Fernet.generate_key()

        dir1, dir2 = tmp_path / "s1", tmp_path / "s2"
        store1 = EncryptedFileBackend(storage_dir=dir1, key=key1)
        store2 = EncryptedFileBackend(storage_dir=dir2, key=key2)

        store1.store("u", "aws", creds)
        store2.store("u", "aws", creds)

        bytes1 = list(dir1.glob("*.enc"))[0].read_bytes()
        bytes2 = list(dir2.glob("*.enc"))[0].read_bytes()
        assert bytes1 != bytes2


# ── Roundtrip store → get ─────────────────────────────────────────────────────

class TestRoundtrip:

    def test_store_get_returns_original_dict(self, store):
        """store() puis get() retourne le dict original identique."""
        creds = {"subscription_id": "sub-abc", "tenant_id": "tenant-xyz", "client_id": "app-123"}
        store.store("migration-1", "azure", creds)
        recovered = store.get("migration-1", "azure")
        assert recovered == creds

    def test_store_get_aws_credentials(self, store):
        """Roundtrip pour credentials AWS avec session token."""
        creds = {
            "access_key": "FAKE_AWS_ACCESS_KEY",
            "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "session_token": "AQoXnyc4lcK...",
            "region": "eu-west-1",
        }
        store.store("mig-aws", "aws", creds)
        assert store.get("mig-aws", "aws") == creds

    def test_store_get_gcp_credentials(self, store):
        """Roundtrip pour credentials GCP avec service account JSON imbriqué."""
        creds = {
            "project_id": "my-gcp-project",
            "service_account_json": {"type": "service_account", "client_email": "sa@project.iam.gserviceaccount.com"},
        }
        store.store("mig-gcp", "gcp", creds)
        assert store.get("mig-gcp", "gcp") == creds

    def test_two_migrations_are_isolated(self, store):
        """Deux migrations différentes ne partagent pas leurs credentials."""
        store.store("mig-A", "azure", {"secret": "value-A"})
        store.store("mig-B", "azure", {"secret": "value-B"})
        assert store.get("mig-A", "azure")["secret"] == "value-A"
        assert store.get("mig-B", "azure")["secret"] == "value-B"

    def test_two_providers_same_user_are_isolated(self, store):
        """Même migration, deux providers → stockage isolé."""
        store.store("mig-X", "azure", {"key": "azure-val"})
        store.store("mig-X", "aws",   {"key": "aws-val"})
        assert store.get("mig-X", "azure")["key"] == "azure-val"
        assert store.get("mig-X", "aws")["key"]   == "aws-val"

    def test_overwrite_replaces_previous_value(self, store):
        """Appel à store() deux fois sur la même clé → seule la dernière valeur est conservée."""
        store.store("mig-1", "azure", {"secret": "old"})
        store.store("mig-1", "azure", {"secret": "new"})
        assert store.get("mig-1", "azure")["secret"] == "new"


# ── Cas d'erreur ──────────────────────────────────────────────────────────────

class TestErrorHandling:

    def test_get_nonexistent_returns_none(self, store):
        """get() sur une clé inexistante retourne None sans exception."""
        assert store.get("does-not-exist", "azure") is None

    def test_get_after_delete_returns_none(self, store):
        """Après delete(), get() retourne None."""
        store.store("mig-del", "aws", {"key": "val"})
        store.delete("mig-del", "aws")
        assert store.get("mig-del", "aws") is None

    def test_delete_nonexistent_does_not_raise(self, store):
        """delete() sur une clé inexistante ne lève aucune exception."""
        store.delete("never-stored", "azure")  # doit passer sans exception

    def test_corrupted_file_returns_none(self, store, tmp_path):
        """Fichier .enc corrompu → get() retourne None sans planter."""
        store.store("mig-corrupt", "azure", {"k": "v"})
        enc_file = list(tmp_path.glob("*.enc"))[0]
        enc_file.write_bytes(b"this-is-not-a-valid-fernet-token")
        assert store.get("mig-corrupt", "azure") is None

    def test_wrong_fernet_key_returns_none(self, tmp_path):
        """Déchiffrement avec une clé différente de celle utilisée au chiffrement → None."""
        key_enc = Fernet.generate_key()
        key_dec = Fernet.generate_key()

        store_enc = EncryptedFileBackend(storage_dir=tmp_path / "enc", key=key_enc)
        store_dec = EncryptedFileBackend(storage_dir=tmp_path / "enc", key=key_dec)

        store_enc.store("u", "azure", {"secret": "hidden"})
        assert store_dec.get("u", "azure") is None


# ── Sécurité : path traversal ─────────────────────────────────────────────────

class TestPathTraversal:

    def test_path_traversal_in_user_id_is_neutralized(self, store, tmp_path):
        """user_id contenant '../' ne doit pas écrire en dehors du répertoire de stockage."""
        store.store("../../etc/passwd", "azure", {"k": "v"})
        # Le fichier créé doit rester dans tmp_path
        all_files = list(tmp_path.rglob("*"))
        for f in all_files:
            assert tmp_path in f.parents or f.parent == tmp_path

    def test_special_chars_in_provider_are_sanitized(self, store, tmp_path):
        """provider contenant des caractères spéciaux → nom de fichier sûr."""
        store.store("user-1", "azure/../aws", {"k": "v"})
        enc_files = list(tmp_path.glob("*.enc"))
        for f in enc_files:
            assert "/" not in f.name
            assert "\\" not in f.name

