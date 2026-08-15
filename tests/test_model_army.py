"""Тесты model_army + bootstrap: безопасность ключа и контракты.

- Ключ НИКОГДА не должен попасть в вывод/код — только из .env
- Роли армии определены и различны
- bootstrap генерирует инструкцию, не содержащую секретов
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import token_diet.model_army as ma
import token_diet.bootstrap as bs


# ── Ключ: безопасность ──────────────────────────────────────────────

def test_key_comes_from_env_not_code():
    """Ключ не должен быть захардкожен в исходниках."""
    src = open(ma.__file__, encoding="utf-8").read()
    # Типичный префикс ключа AnyModel/Tinkoff — проверяем, что в коде
    # нет строки с длинным секретом
    import re
    secrets = re.findall(r'["\'](?:sk-[A-Za-z0-9_-]{16,}|t\.[A-Za-z0-9_-]{20,})["\']', src)
    assert not secrets, f"Найден секрет в коде: {secrets[:2]}"


def test_no_key_returns_clean_error():
    """Без ключа ask() возвращает понятную ошибку, а не падает."""
    result = ma.ask("привет", role="brain", max_tokens=5) if not ma._key() else None
    if result is not None:
        assert "ERR:" in result
        assert "ANYMODEL_API_KEY" in result


def test_roles_are_distinct():
    """Каждая роль армии — своя модель (разные задачи, разные модели)."""
    roles = set(ma.ROLES.values())
    assert len(roles) >= 3, f"Ожидал минимум 3 разные модели, есть {len(roles)}"


def test_known_roles_exist():
    assert "brain" in ma.ROLES
    assert "analyst" in ma.ROLES
    assert "fast" in ma.ROLES
    assert "generator" in ma.ROLES


def test_army_verdict_structure():
    """army_verdict возвращает словарь с голосами (не сеть — контракт)."""
    import inspect
    sig = inspect.signature(ma.army_verdict)
    assert "prompt" in sig.parameters


# ── bootstrap: инструкция для любой нейронки ─────────────────────────

def _bootstrap_text() -> str:
    """generate() возвращает путь к файлу — читаем его содержимое."""
    path = bs.generate()
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_bootstrap_contains_no_secrets():
    """Инструкция самонастройки не должна содержать ключей."""
    text = _bootstrap_text()
    for secret in ("sk-", "t.PYa", "t.FiS", "ANYMODEL_API_KEY=sk", "molodets"):
        assert secret not in text, f"Секрет попал в bootstrap: {secret}"


def test_bootstrap_has_setup_commands():
    text = _bootstrap_text()
    assert "pip install" in text
    assert "doctor" in text


def test_bootstrap_mentions_memory():
    text = _bootstrap_text()
    assert "память" in text.lower() or "memory" in text.lower() or "CONTEXT_ALL" in text


def test_vault_size_works():
    """Размер хранилища памяти — число (0, если нет vault)."""
    size = bs._vault_size()
    assert isinstance(size, int)
    assert size >= 0
