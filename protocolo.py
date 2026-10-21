"""Formato de hash compartilhado pelo servidor e pelos mineradores."""
import hashlib
import json


def hash_bloco(trabalho, minerador, nonce):
    dados = {"trabalho": trabalho, "minerador": minerador, "nonce": nonce}
    serializado = json.dumps(dados, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(hashlib.sha256(serializado).digest()).hexdigest()


def prova_valida(trabalho, minerador, nonce):
    return hash_bloco(trabalho, minerador, nonce).startswith("0" * trabalho["dificuldade"])


def partes_mensagem(trabalho, minerador):
    """Divide a mensagem de hash_bloco em (antes do nonce, depois do nonce).

    A mensagem é antes + str(nonce).encode() + depois, o que evita refazer o JSON a cada nonce.
    """
    marcador = 1234567
    texto = json.dumps({"trabalho": trabalho, "minerador": minerador, "nonce": marcador},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    antes, depois = texto.split(f'"nonce":{marcador}', 1)
    return (antes + '"nonce":').encode(), depois.encode()
