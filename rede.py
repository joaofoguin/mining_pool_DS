"""Requisições HTTP com corpo JSON, usadas pelo pool e pelos trabalhadores."""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def requisicao(servidor, rota, dados=None):
    corpo = None if dados is None else json.dumps(dados).encode()
    req = Request(servidor + rota, data=corpo, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as resposta:
            return resposta.status, json.load(resposta)
    except HTTPError as erro:
        return erro.code, json.load(erro)
