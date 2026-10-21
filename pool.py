"""Pool de mineração: une vários trabalhadores sob um único nome no servidor principal.

Os trabalhadores recebem faixas de nonce disjuntas e enviam "shares": provas com
dificuldade menor que a do bloco. As shares medem quanto cada um contribuiu, e
quando uma delas também atende à dificuldade do bloco o pool envia a solução.
"""
import argparse
import json
import random
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError

from protocolo import hash_bloco
from rede import requisicao


def nome_valido(nome):
    return isinstance(nome, str) and nome.strip() != "" and len(nome) <= 60


class Pool:
    def __init__(self, servidor, nome, folga, tamanho_lote):
        self.servidor = servidor
        self.nome = nome
        self.folga = folga
        self.tamanho_lote = tamanho_lote
        self.trava = threading.Lock()
        self.trabalho = None
        self.rodada = None        # id do último trabalho recebido do servidor
        self.resolvido = None     # id do trabalho cuja solução o pool já enviou
        self.proximo_nonce = 0
        self.vistos = set()       # nonces já aceitos no trabalho atual
        self.shares_rodada = Counter()  # zerado a cada bloco encontrado pelo pool
        self.shares_total = Counter()
        self.ultimo_contato = {}
        self.blocos = []

    def dificuldade_share(self, trabalho):
        return max(1, trabalho["dificuldade"] - self.folga)

    def trabalho_aberto(self):
        if self.trabalho is None or self.trabalho["id"] == self.resolvido:
            return None
        return self.trabalho

    def acompanhar_servidor(self):
        ultima_presenca = 0
        while True:
            agora = time.monotonic()
            try:
                if agora - ultima_presenca >= 5:
                    requisicao(self.servidor, "/presenca", {"minerador": self.nome})
                    ultima_presenca = agora
                codigo, estado = requisicao(self.servidor, "/estado")
                if codigo != 200:
                    raise ValueError(f"Resposta do servidor: {estado}")
                self.atualizar_trabalho(estado["trabalho"])
                time.sleep(0.5)
            except (URLError, OSError, ValueError, KeyError) as erro:
                print(f"Servidor principal indisponível: {erro}. Nova tentativa em 2 segundos.")
                with self.trava:
                    self.trabalho = None
                ultima_presenca = 0
                time.sleep(2)

    def atualizar_trabalho(self, trabalho):
        with self.trava:
            self.trabalho = trabalho
            if trabalho and trabalho["id"] != self.rodada:
                self.rodada = trabalho["id"]
                self.proximo_nonce = random.randrange(2**63)
                self.vistos.clear()
                print(f"Novo trabalho: bloco {trabalho['altura']} | dificuldade {trabalho['dificuldade']}"
                      f" | shares com dificuldade {self.dificuldade_share(trabalho)}")

    def estado(self):
        with self.trava:
            trabalho = self.trabalho_aberto()
            return 200, {"trabalho": trabalho, "minerador": self.nome}

    def presenca(self, dados):
        nome = dados.get("trabalhador")
        if not nome_valido(nome):
            return 400, {"erro": "Informe um nome de trabalhador de 1 a 60 caracteres."}
        with self.trava:
            self.ultimo_contato[nome] = time.monotonic()
        return 200, {"ok": True}

    def lote(self, dados):
        nome = dados.get("trabalhador")
        if not nome_valido(nome):
            return 400, {"erro": "Informe um nome de trabalhador de 1 a 60 caracteres."}
        with self.trava:
            self.ultimo_contato[nome] = time.monotonic()
            trabalho = self.trabalho_aberto()
            if trabalho is None:
                return 503, {"erro": "Pool sem trabalho no momento."}
            inicio = self.proximo_nonce
            self.proximo_nonce += self.tamanho_lote
            return 200, {
                "trabalho": trabalho,
                "minerador": self.nome,
                "dificuldade_share": self.dificuldade_share(trabalho),
                "inicio": inicio,
                "fim": inicio + self.tamanho_lote,
            }

    def share(self, dados):
        nome, nonce = dados.get("trabalhador"), dados.get("nonce")
        if not nome_valido(nome) or type(nonce) is not int or not 0 <= nonce < 2**64:
            return 400, {"erro": "Share malformada."}
        with self.trava:
            self.ultimo_contato[nome] = time.monotonic()
            trabalho = self.trabalho_aberto()
            if trabalho is None or dados.get("id") != trabalho["id"]:
                return 409, {"erro": "Share de um trabalho que já terminou."}
            if nonce in self.vistos:
                return 409, {"erro": "Share repetida."}
            digest = hash_bloco(trabalho, self.nome, nonce)
            if not digest.startswith("0" * self.dificuldade_share(trabalho)):
                return 400, {"erro": "Share inválida."}
            self.vistos.add(nonce)
            self.shares_rodada[nome] += 1
            self.shares_total[nome] += 1
            if not digest.startswith("0" * trabalho["dificuldade"]):
                return 201, {"bloco": False}
            self.resolvido = trabalho["id"]
            participacao = self.shares_rodada.copy()
            self.shares_rodada.clear()

        print(f"{nome} encontrou o bloco {trabalho['altura']}: {digest}")
        try:
            codigo, resultado = requisicao(self.servidor, "/solucao",
                                           {"id": trabalho["id"], "minerador": self.nome, "nonce": nonce})
        except (URLError, OSError) as erro:
            codigo, resultado = None, str(erro)
        aceito = codigo == 201
        print("Bloco aceito pelo servidor!" if aceito else f"Bloco recusado: {resultado}")

        with self.trava:
            if aceito:
                total = sum(participacao.values())
                self.blocos.append({
                    "altura": trabalho["altura"],
                    "encontrado_por": nome,
                    "participacao": {n: round(100 * s / total, 2) for n, s in participacao.most_common()},
                })
            else:
                # Outro minerador venceu a rodada: as shares continuam valendo para o próximo bloco.
                self.shares_rodada.update(participacao)
        return 201, {"bloco": True, "aceito": aceito}

    def estatisticas(self):
        with self.trava:
            agora = time.monotonic()
            total = sum(self.shares_rodada.values())
            return 200, {
                "pool": self.nome,
                "trabalho": self.trabalho_aberto(),
                "trabalhadores_online": sorted(n for n, t in self.ultimo_contato.items() if agora - t <= 15),
                "rodada_atual": {n: {"shares": s, "participacao": round(100 * s / total, 2)}
                                 for n, s in self.shares_rodada.most_common()},
                "shares_total": dict(self.shares_total.most_common()),
                "blocos": self.blocos,
            }


class Manipulador(BaseHTTPRequestHandler):
    pool = None

    def responder(self, codigo, dados):
        corpo = json.dumps(dados, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        rotas = {"/estado": self.pool.estado, "/estatisticas": self.pool.estatisticas}
        acao = rotas.get(self.path)
        if acao is None:
            self.responder(404, {"erro": "Rota inexistente."})
        else:
            self.responder(*acao())

    def do_POST(self):
        rotas = {"/presenca": self.pool.presenca, "/lote": self.pool.lote, "/share": self.pool.share}
        acao = rotas.get(self.path)
        if acao is None:
            self.responder(404, {"erro": "Rota inexistente."})
            return
        try:
            tamanho = int(self.headers.get("Content-Length", 0))
            if not 0 < tamanho <= 4096:
                raise ValueError
            dados = json.loads(self.rfile.read(tamanho))
            if not isinstance(dados, dict):
                raise ValueError
        except ValueError:
            self.responder(400, {"erro": "Corpo JSON inválido."})
            return
        self.responder(*acao(dados))

    def log_message(self, formato, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--servidor", required=True, help="Servidor principal. Ex.: http://192.168.1.10:8001")
    parser.add_argument("--nome", required=True, help="Nome com que o pool aparece no servidor principal")
    parser.add_argument("--porta", type=int, default=8002, help="Porta em que os trabalhadores se conectam")
    parser.add_argument("--folga", type=int, default=2,
                        help="Quantos zeros a menos uma share exige em relação ao bloco")
    parser.add_argument("--lote", type=int, default=200_000, help="Quantidade de nonces por lote")
    args = parser.parse_args()
    if not nome_valido(args.nome) or args.folga < 0 or args.lote < 1:
        parser.error("Use nome de 1 a 60 caracteres, folga >= 0 e lote >= 1.")

    pool = Pool(args.servidor.rstrip("/"), args.nome, args.folga, args.lote)
    threading.Thread(target=pool.acompanhar_servidor, daemon=True).start()
    Manipulador.pool = pool
    servidor = ThreadingHTTPServer(("0.0.0.0", args.porta), Manipulador)
    print(f"Pool '{args.nome}' ouvindo na porta {args.porta}. Ctrl+C para encerrar.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPool encerrado.")


if __name__ == "__main__":
    main()
