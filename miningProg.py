"""Trabalhador do pool: minera lotes de nonces em várias threads e envia shares ao pool.

O laço de hash roda em C (minerador.dll no Windows, minerador.so no Linux, via ctypes) quando a
biblioteca está na pasta. O ctypes solta o GIL durante a chamada ao C, então
cada thread usa um núcleo de verdade. Sem a biblioteca, usa Python puro (mais
lento e preso ao GIL: as threads não somam velocidade).
"""
import argparse
import ctypes
import hashlib
import json
import os
import threading
import time
from urllib.error import URLError

from rede import requisicao

PASTA = os.path.dirname(os.path.abspath(__file__))


def carregar_c():
    nome = "minerador.dll" if os.name == "nt" else "minerador.so"
    caminho = os.path.join(PASTA, nome)
    if os.path.exists(caminho):
        try:
            lib = ctypes.CDLL(caminho)
        except OSError as erro:
            print(f"Não consegui carregar {nome}: {erro}")
            return None
        lib.minerar.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t,
                                ctypes.c_uint64, ctypes.c_uint64, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_uint64)]
        lib.minerar.restype = ctypes.c_int
        lib.usa_sha_ni.restype = ctypes.c_int
        return lib
    return None


def partes_fixas(trabalho, minerador):
    """Partes do JSON de hash_bloco (protocolo.py) que não mudam com o nonce.

    Com sort_keys a ordem é minerador, nonce, trabalho, então o texto hashado é
    prefixo + str(nonce) + sufixo, idêntico ao que o pool e o servidor calculam.
    """
    prefixo = ('{"minerador":' + json.dumps(minerador, ensure_ascii=False) + ',"nonce":').encode()
    sufixo = (',"trabalho":' + json.dumps(trabalho, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False) + "}").encode()
    return prefixo, sufixo


def criar_busca(lib, prefixo, sufixo, zeros):
    """Devolve buscar(inicio, fim) -> primeiro nonce cujo hash tem `zeros` zeros, ou None."""
    if lib:
        achado = ctypes.c_uint64()

        def buscar(inicio, fim):
            # ctypes solta o GIL durante a chamada; o C roda em velocidade nativa
            r = lib.minerar(prefixo, len(prefixo), sufixo, len(sufixo), inicio, fim, zeros, ctypes.byref(achado))
            if r < 0:
                raise MemoryError("minerador sem memória")
            return achado.value if r == 1 else None
        return buscar

    base = hashlib.sha256(prefixo)
    sha256 = hashlib.sha256
    alvo = 1 << (256 - 4 * zeros)  # "N zeros hexadecimais" == hash < 2^(256 - 4N)

    def buscar(inicio, fim):
        for nonce in range(inicio, fim):
            h = base.copy()
            h.update(b"%d" % nonce + sufixo)
            if int.from_bytes(sha256(h.digest()).digest(), "big") < alvo:
                return nonce
        return None
    return buscar


class Placar:
    """Contadores compartilhados entre as threads."""

    def __init__(self):
        self.trava = threading.Lock()
        self.tentativas = 0
        self.shares = 0
        self.rodada = 0  # a thread principal incrementa quando o trabalho do pool muda


def minerar(pool, nome, pausa, lib, placar, id_thread):
    """Laço de uma thread mineradora. Cada thread pede os próprios lotes ao pool.

    A thread principal consulta /estado e incrementa placar.rodada quando o
    trabalho muda; aqui só comparamos o número, sem consultar o pool.
    """
    passo = 1_000_000 if lib else 20_000  # ~0,2 s de trabalho por chamada
    lote = None
    rodada_lote = -1
    while True:
        try:
            if lote and rodada_lote != placar.rodada:
                lote = None  # o trabalho mudou: lote velho não vale mais
            if lote is None:
                rodada_lote = placar.rodada
                codigo, resposta = requisicao(pool, "/lote", {"trabalhador": nome})
                if codigo == 503:
                    time.sleep(0.2)
                    continue
                if codigo != 200:
                    raise ValueError(f"Resposta do pool: {resposta}")
                lote = resposta
                trabalho = lote["trabalho"]
                nonce = lote["inicio"]
                lote_fim = min(lote["fim"], 2**64 - 1)  # o pool só aceita nonce < 2^64
                buscar = criar_busca(lib, *partes_fixas(trabalho, lote["minerador"]), lote["dificuldade_share"])

            fim = min(nonce + passo, lote_fim)
            achado = buscar(nonce, fim)
            feitos = (achado + 1 if achado is not None else fim) - nonce
            with placar.trava:
                placar.tentativas += feitos
            nonce += feitos

            if achado is not None:
                codigo, resultado = requisicao(pool, "/share",
                                               {"id": trabalho["id"], "trabalhador": nome, "nonce": achado})
                if codigo == 201:
                    with placar.trava:
                        placar.shares += 1
                    if resultado["bloco"]:
                        print(f"Você encontrou o bloco {trabalho['altura']} do pool! (nonce {achado})")
                        print("Bloco aceito!" if resultado["aceito"] else "O servidor recusou o bloco.")
                        lote = None
                elif codigo == 409:
                    lote = None
                else:
                    print(f"Share recusada: {resultado}")
            if lote is not None and nonce >= lote_fim:
                lote = None
            if pausa:
                time.sleep(pausa)
        except (URLError, OSError, ValueError, KeyError) as erro:
            if id_thread == 0:
                print(f"Conexão com o pool indisponível: {erro}. Nova tentativa em 2 segundos.")
            lote = None
            time.sleep(2)


def main():
    nucleos = os.cpu_count() or 2
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pool", required=True, help="Ex.: http://192.168.1.20:8002")
    parser.add_argument("--nome", required=True, help="Nome único do aluno ou da máquina")
    parser.add_argument("--threads", type=int, default=nucleos,
                        help=f"Threads mineradoras (padrão: núcleos lógicos = {nucleos})")
    parser.add_argument("--pausa", type=float, default=0,
                        help="Descanso entre chamadas de cada thread, em segundos (padrão 0)")
    args = parser.parse_args()
    args.nome = args.nome.strip()
    if not args.nome or len(args.nome) > 60 or not 0 <= args.pausa <= 60 or args.threads < 1:
        parser.error("Use nome de 1 a 60 caracteres, pausa entre 0 e 60 segundos e threads >= 1.")
    pool = args.pool.rstrip("/")

    lib = carregar_c()
    if lib:
        print("Motor em C carregado" + (" com instruções SHA-NI." if lib.usa_sha_ni() else " (C puro)."))
    else:
        print("minerador.dll/.so não encontrado: usando Python puro (bem mais lento). Veja o README.")

    placar = Placar()
    for i in range(args.threads):
        threading.Thread(target=minerar, args=(pool, args.nome, args.pausa, lib, placar, i), daemon=True).start()
    print(f"Trabalhador iniciado com {args.threads} threads. Ctrl+C para encerrar.")

    inicio = time.monotonic()
    id_trabalho = None
    ultima_presenca = ultimo_relatorio = 0
    try:
        while True:
            agora = time.monotonic()
            try:
                if agora - ultima_presenca >= 5:
                    requisicao(pool, "/presenca", {"trabalhador": args.nome})
                    ultima_presenca = agora
                codigo, estado = requisicao(pool, "/estado")
                if codigo == 200:
                    trabalho = estado["trabalho"]
                    novo_id = trabalho and trabalho["id"]
                    if novo_id != id_trabalho:
                        id_trabalho = novo_id
                        placar.rodada += 1
                        if trabalho:
                            print(f"Minerando bloco {trabalho['altura']} | dificuldade {trabalho['dificuldade']}"
                                  f" | dados: {trabalho['dados']}")
            except (URLError, OSError, ValueError, KeyError):
                pass  # as threads mineradoras já avisam quando o pool cai
            if agora - ultimo_relatorio >= 3:
                segundos = max(agora - inicio, 0.001)
                print(f"{placar.tentativas:,} tentativas | {placar.tentativas / segundos / 1e6:,.2f} MH/s"
                      f" | {placar.shares} shares aceitas")
                ultimo_relatorio = agora
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nTrabalhador encerrado.")  # as threads são daemon e terminam junto


if __name__ == "__main__":
    main()
