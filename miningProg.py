"""Trabalhador do pool: minera lotes de nonces e envia shares ao pool."""
import argparse
import time
from urllib.error import URLError

from protocolo import hash_bloco
from rede import requisicao


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, help="Ex.: http://192.168.1.20:8002")
    parser.add_argument("--nome", required=True, help="Nome único do aluno ou da máquina")
    parser.add_argument("--pausa", type=float, default=0.02, help="Descanso entre lotes, em segundos")
    args = parser.parse_args()
    if not args.nome.strip() or len(args.nome) > 60 or not 0 <= args.pausa <= 60:
        parser.error("Use nome de 1 a 60 caracteres e pausa entre 0 e 60 segundos.")
    pool = args.pool.rstrip("/")
    lote = None
    rodada = None
    nonce = 0
    tentativas = shares = 0
    ultima_consulta = ultimo_relatorio = ultima_presenca = 0
    inicio = time.monotonic()
    print("Trabalhador iniciado. Ctrl+C para encerrar.")
    try:
        while True:
            agora = time.monotonic()
            try:
                if agora - ultima_presenca >= 5:
                    requisicao(pool, "/presenca", {"trabalhador": args.nome})
                    ultima_presenca = agora
                if agora - ultima_consulta >= 0.5:
                    codigo, estado = requisicao(pool, "/estado")
                    if codigo != 200:
                        raise ValueError(f"Resposta do pool: {estado}")
                    ultima_consulta = agora
                    atual = estado["trabalho"]
                    if lote and (atual is None or atual["id"] != lote["trabalho"]["id"]):
                        lote = None
                if lote is None:
                    codigo, resposta = requisicao(pool, "/lote", {"trabalhador": args.nome})
                    if codigo == 503:
                        time.sleep(0.2)
                        continue
                    if codigo != 200:
                        raise ValueError(f"Resposta do pool: {resposta}")
                    lote = resposta
                    nonce = lote["inicio"]
                    if lote["trabalho"]["id"] != rodada:
                        rodada = lote["trabalho"]["id"]
                        trabalho = lote["trabalho"]
                        print(f"Minerando bloco {trabalho['altura']} | dificuldade {trabalho['dificuldade']}"
                              f" | shares com dificuldade {lote['dificuldade_share']} | dados: {trabalho['dados']}")
                trabalho = lote["trabalho"]
                alvo_share = "0" * lote["dificuldade_share"]
                for _ in range(500):
                    if nonce >= lote["fim"]:
                        lote = None
                        break
                    digest = hash_bloco(trabalho, lote["minerador"], nonce)
                    tentativas += 1
                    if digest.startswith(alvo_share):
                        codigo, resultado = requisicao(pool, "/share",
                                                       {"id": trabalho["id"], "trabalhador": args.nome, "nonce": nonce})
                        if codigo == 201:
                            shares += 1
                            if resultado["bloco"]:
                                print(f"Você encontrou o bloco do pool: {digest}")
                                print("Bloco aceito!" if resultado["aceito"] else "O servidor recusou o bloco.")
                                lote = None
                                ultima_consulta = 0
                                break
                        elif codigo == 409:
                            lote = None
                            ultima_consulta = 0
                            break
                        else:
                            print(f"Share recusada: {resultado}")
                    nonce += 1
                if agora - ultimo_relatorio >= 3:
                    segundos = max(time.monotonic() - inicio, 0.001)
                    print(f"{tentativas:,} tentativas | {tentativas / segundos:,.0f} hashes/s | {shares} shares aceitas")
                    ultimo_relatorio = agora
                time.sleep(args.pausa)
            except (URLError, OSError, ValueError, KeyError) as erro:
                print(f"Conexão com o pool indisponível: {erro}. Nova tentativa em 2 segundos.")
                lote = None
                ultima_consulta = ultima_presenca = 0
                time.sleep(2)
    except KeyboardInterrupt:
        print("\nTrabalhador encerrado.")


if __name__ == "__main__":
    main()
