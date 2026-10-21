"""Formato de hash compartilhado pelo servidor e pelos mineradores."""
import hashlib
import json


def hash_bloco(trabalho, minerador, nonce):
    dados = {"trabalho": trabalho, "minerador": minerador, "nonce": nonce}
    serializado = json.dumps(dados, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(hashlib.sha256(serializado).digest()).hexdigest()


def prova_valida(trabalho, minerador, nonce):
    return hash_bloco(trabalho, minerador, nonce).startswith("0" * trabalho["dificuldade"])
