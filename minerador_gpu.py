"""Trabalhador do pool que minera na placa de vídeo NVIDIA (CUDA, via CuPy).

Cada thread da GPU testa um nonce. A mensagem é o mesmo JSON de protocolo.hash_bloco;
o Python a monta uma vez com o padding do SHA-256 e a GPU só encaixa os dígitos do nonce.
Os blocos de 64 bytes sem dígitos são iguais para todos os nonces, então a expansão
deles (K[i] + W[i]) também é calculada uma vez só, no Python.
"""
import argparse
import math
import time
from urllib.error import URLError

import cupy as cp
import numpy as np

from protocolo import hash_bloco, partes_mensagem
from rede import requisicao

K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]
MAX_BLOCOS = 64  # mensagens de até 4 KB

KERNEL = r"""
typedef unsigned int u32;
typedef unsigned long long u64;

#define MAX_BLOCOS @MAX_BLOCOS@
__constant__ u32 K[64] = {@K@};
__constant__ u32 H0[8] = {
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
__constant__ u32 MODELO[MAX_BLOCOS * 16];  // mensagem com padding e bytes do nonce zerados
__constant__ u32 KW[MAX_BLOCOS * 64];      // K[i] + W[i] já expandidos, por bloco

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
#define S0(a) (ROTR(a, 2) ^ ROTR(a, 13) ^ ROTR(a, 22))
#define S1(e) (ROTR(e, 6) ^ ROTR(e, 11) ^ ROTR(e, 25))
#define CH(e, f, g) ((e & f) ^ (~e & g))
#define MAJ(a, b, c) ((a & b) ^ (a & c) ^ (b & c))
#define RODADA(kw) { \
    u32 t1 = h + S1(e) + CH(e, f, g) + (kw); \
    u32 t2 = S0(a) + MAJ(a, b, c); \
    h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2; }

__device__ __forceinline__ void transformar(u32* st, u32* w) {
    u32 a = st[0], b = st[1], c = st[2], d = st[3], e = st[4], f = st[5], g = st[6], h = st[7];
    #pragma unroll
    for (int i = 0; i < 64; i++) {
        if (i >= 16) {
            u32 w15 = w[(i - 15) & 15], w2 = w[(i - 2) & 15];
            w[i & 15] += (ROTR(w15, 7) ^ ROTR(w15, 18) ^ (w15 >> 3)) + w[(i - 7) & 15]
                       + (ROTR(w2, 17) ^ ROTR(w2, 19) ^ (w2 >> 10));
        }
        RODADA(K[i] + w[i & 15]);
    }
    st[0] += a; st[1] += b; st[2] += c; st[3] += d; st[4] += e; st[5] += f; st[6] += g; st[7] += h;
}

__device__ __forceinline__ void transformar_constante(u32* st, int base) {
    u32 a = st[0], b = st[1], c = st[2], d = st[3], e = st[4], f = st[5], g = st[6], h = st[7];
    #pragma unroll
    for (int i = 0; i < 64; i++) RODADA(KW[base + i]);
    st[0] += a; st[1] += b; st[2] += c; st[3] += d; st[4] += e; st[5] += f; st[6] += g; st[7] += h;
}

extern "C" __global__ void minerar(int num_blocos, int pos_digitos, int num_digitos,
                                   u64 inicio, u64 quantidade, int zeros,
                                   u64* saida, u32* contador, u32 max_saida) {
    u64 i = (u64)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= quantidade) return;
    u64 nonce = inicio + i;

    unsigned char dig[20];
    u64 v = nonce;
    for (int k = num_digitos - 1; k >= 0; k--) { dig[k] = '0' + (unsigned char)(v % 10); v /= 10; }

    u32 st[8];
    #pragma unroll
    for (int j = 0; j < 8; j++) st[j] = H0[j];
    int primeiro = pos_digitos >> 6, ultimo = (pos_digitos + num_digitos - 1) >> 6;
    for (int b = 0; b < num_blocos; b++) {
        if (b < primeiro || b > ultimo) {
            transformar_constante(st, b * 64);
            continue;
        }
        u32 w[16];
        #pragma unroll
        for (int j = 0; j < 16; j++) {
            u32 x = MODELO[b * 16 + j];
            #pragma unroll
            for (int k = 0; k < 4; k++) {
                int q = b * 64 + j * 4 + k - pos_digitos;
                if (q >= 0 && q < num_digitos) x |= (u32)dig[q] << (24 - 8 * k);
            }
            w[j] = x;
        }
        transformar(st, w);
    }

    // Segundo SHA-256, sobre os 32 bytes do primeiro: cabe em um único bloco.
    u32 w[16];
    #pragma unroll
    for (int j = 0; j < 8; j++) w[j] = st[j];
    w[8] = 0x80000000;
    #pragma unroll
    for (int j = 9; j < 15; j++) w[j] = 0;
    w[15] = 256;
    #pragma unroll
    for (int j = 0; j < 8; j++) st[j] = H0[j];
    transformar(st, w);

    for (int j = 0; j < zeros; j++)
        if ((st[j >> 3] >> (28 - ((j & 7) << 2))) & 0xF) return;
    u32 k = atomicAdd(contador, 1);
    if (k < max_saida) saida[k] = nonce;
}
""".replace("@MAX_BLOCOS@", str(MAX_BLOCOS)).replace("@K@", ", ".join(map(hex, K)))

MAX_RESULTADOS = 4096
SHARES_POR_SEGUNDO = 10  # alvo para não sobrecarregar o pool
LOTE_EM_SEGUNDOS = 10    # quanto trabalho pedir ao pool de cada vez
TEMPO_POR_CHAMADA = 0.1  # duração desejada de cada execução do kernel


def expandir(bloco):
    """Devolve K[i] + W[i] das 64 rodadas do SHA-256 para um bloco de 16 palavras."""
    rotr = lambda x, n: ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF
    w = [int(x) for x in bloco]
    for i in range(16, 64):
        s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
        s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
        w.append((w[i - 16] + s0 + w[i - 7] + s1) & 0xFFFFFFFF)
    return [(k + x) & 0xFFFFFFFF for k, x in zip(K, w)]


class MineradorGPU:
    def __init__(self):
        self.modulo = cp.RawModule(code=KERNEL)
        self.kernel = self.modulo.get_function("minerar")
        self.modelo = cp.ndarray((MAX_BLOCOS * 16,), cp.uint32, self.modulo.get_global("MODELO"))
        self.kw = cp.ndarray((MAX_BLOCOS * 64,), cp.uint32, self.modulo.get_global("KW"))
        self.saida = cp.zeros(MAX_RESULTADOS, dtype=cp.uint64)
        self.contador = cp.zeros(1, dtype=cp.uint32)

    def preparar(self, trabalho, minerador):
        self.antes, self.depois = partes_mensagem(trabalho, minerador)
        self.num_digitos = None

    def carregar(self, num_digitos):
        """Envia à GPU a mensagem (com padding) para nonces com essa quantidade de dígitos."""
        mensagem = self.antes + bytes(num_digitos) + self.depois
        tamanho = len(mensagem)
        mensagem += b"\x80" + bytes((55 - tamanho) % 64) + (tamanho * 8).to_bytes(8, "big")
        num_blocos = len(mensagem) // 64
        if num_blocos > MAX_BLOCOS:
            raise RuntimeError("A mensagem do trabalho é grande demais para o kernel da GPU.")
        palavras = np.frombuffer(mensagem, dtype=">u4").astype(np.uint32)
        kw = [x for b in range(num_blocos) for x in expandir(palavras[b * 16:(b + 1) * 16])]
        self.modelo[:palavras.size] = cp.asarray(palavras)
        self.kw[:len(kw)] = cp.asarray(np.array(kw, dtype=np.uint32))
        self.num_blocos = num_blocos
        self.num_digitos = num_digitos

    def buscar(self, inicio, quantidade, zeros):
        """Devolve os nonces de [inicio, inicio + quantidade) cujo hash tem `zeros` zeros iniciais."""
        achados = []
        fim = inicio + quantidade
        while inicio < fim:
            # Cada execução do kernel usa nonces com a mesma quantidade de dígitos.
            num_digitos = len(str(inicio))
            parte = min(fim, 10 ** num_digitos) - inicio
            if num_digitos != self.num_digitos:
                self.carregar(num_digitos)
            self.contador.fill(0)
            self.kernel(((parte + 255) // 256,), (256,),
                        (np.int32(self.num_blocos), np.int32(len(self.antes)), np.int32(num_digitos),
                         np.uint64(inicio), np.uint64(parte), np.int32(zeros),
                         self.saida, self.contador, np.uint32(MAX_RESULTADOS)))
            total = min(int(self.contador.get()[0]), MAX_RESULTADOS)
            achados += [int(n) for n in self.saida[:total].get()]
            inicio += parte
        return sorted(achados)

    def autoteste(self):
        """Compara a GPU com protocolo.hash_bloco em mensagens curtas, longas e com o nonce
        atravessando a divisa entre dois blocos de 64 bytes."""
        for minerador in ("gpu", "x" * 30):
            for dados in ("curto", "longo ação " * 30):
                trabalho = {"id": "teste", "altura": 1, "dificuldade": 1, "dados": dados}
                inicio = 10**18 - 2000  # atravessa a mudança de 18 para 19 dígitos
                self.preparar(trabalho, minerador)
                esperado = [n for n in range(inicio, inicio + 4096)
                            if hash_bloco(trabalho, minerador, n).startswith("0")]
                if self.buscar(inicio, 4096, 1) != esperado:
                    raise RuntimeError("A GPU calculou hashes diferentes de protocolo.hash_bloco.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pool", required=True, help="Ex.: http://192.168.1.20:8002")
    parser.add_argument("--nome", required=True, help="Nome único do aluno ou da máquina")
    parser.add_argument("--dispositivo", type=int, default=0, help="Índice da placa de vídeo (padrão 0)")
    args = parser.parse_args()
    if not args.nome.strip() or len(args.nome) > 60:
        parser.error("Use nome de 1 a 60 caracteres.")
    pool = args.pool.rstrip("/")

    cp.cuda.Device(args.dispositivo).use()
    nome_placa = cp.cuda.runtime.getDeviceProperties(args.dispositivo)["name"].decode()
    print(f"Placa de vídeo: {nome_placa}. Compilando e testando o kernel...")
    gpu = MineradorGPU()
    gpu.autoteste()
    print("Autoteste da GPU aprovado. Ctrl+C para encerrar.")

    lote = None
    rodada = None
    nonce = 0
    passo = 2**20  # nonces por execução do kernel, ajustado conforme a velocidade
    taxa = 0.0     # hashes por segundo medidos na GPU
    tentativas = shares = 0
    ultima_consulta = ultimo_relatorio = ultima_presenca = 0
    inicio = time.monotonic()
    try:
        while True:
            agora = time.monotonic()
            try:
                if agora - ultima_presenca >= 5:
                    requisicao(pool, "/presenca", {"trabalhador": args.nome})
                    ultima_presenca = agora
                if agora - ultima_consulta >= 0.2:
                    codigo, estado = requisicao(pool, "/estado")
                    if codigo != 200:
                        raise ValueError(f"Resposta do pool: {estado}")
                    ultima_consulta = agora
                    atual = estado["trabalho"]
                    if lote and (atual is None or atual["id"] != lote["trabalho"]["id"]):
                        lote = None
                if lote is None:
                    quantidade = max(2**24, int(taxa * LOTE_EM_SEGUNDOS))
                    codigo, resposta = requisicao(pool, "/lote", {"trabalhador": args.nome, "quantidade": quantidade})
                    if codigo == 503:
                        time.sleep(0.1)
                        continue
                    if codigo != 200:
                        raise ValueError(f"Resposta do pool: {resposta}")
                    lote = resposta
                    nonce = lote["inicio"]
                    gpu.preparar(lote["trabalho"], lote["minerador"])
                    if lote["trabalho"]["id"] != rodada:
                        rodada = lote["trabalho"]["id"]
                        trabalho = lote["trabalho"]
                        print(f"Minerando bloco {trabalho['altura']} | dificuldade {trabalho['dificuldade']}"
                              f" | dados: {trabalho['dados']}")
                trabalho = lote["trabalho"]

                # Shares mais difíceis que o mínimo valem mais no pool; com isso a GPU
                # envia poucas shares por segundo sem perder participação.
                extra = 2 if taxa == 0 else max(0, math.ceil(math.log(
                    taxa / (SHARES_POR_SEGUNDO * 16 ** lote["dificuldade_share"]), 16)))
                dificuldade = min(trabalho["dificuldade"], lote["dificuldade_share"] + extra)

                quantidade = min(passo, lote["fim"] - nonce)
                t0 = time.perf_counter()
                achados = gpu.buscar(nonce, quantidade, dificuldade)
                duracao = max(time.perf_counter() - t0, 1e-6)
                tentativas += quantidade
                nonce += quantidade
                taxa = quantidade / duracao if taxa == 0 else 0.8 * taxa + 0.2 * quantidade / duracao
                passo = min(max(int(passo * TEMPO_POR_CHAMADA / duracao), 2**16), 2**28)

                alvo = "0" * dificuldade
                if any(not hash_bloco(trabalho, lote["minerador"], n).startswith(alvo) for n in achados):
                    raise RuntimeError("A GPU devolveu um nonce inválido.")
                for i in range(0, len(achados), 1000):
                    codigo, resultado = requisicao(pool, "/share", {
                        "id": trabalho["id"], "trabalhador": args.nome,
                        "nonces": achados[i:i + 1000], "dificuldade": dificuldade})
                    if codigo == 201:
                        shares += resultado["aceitas"]
                        if resultado["bloco"]:
                            print("Você encontrou o bloco do pool!")
                            print("Bloco aceito!" if resultado["aceito"] else "O servidor recusou o bloco.")
                            lote = None
                            ultima_consulta = 0
                            break
                    elif codigo == 409:
                        lote = None
                        ultima_consulta = 0
                        break
                    else:
                        print(f"Shares recusadas: {resultado}")
                if lote and nonce >= lote["fim"]:
                    lote = None

                if agora - ultimo_relatorio >= 3:
                    segundos = max(time.monotonic() - inicio, 0.001)
                    print(f"{tentativas:,} tentativas | {tentativas / segundos / 1e6:,.1f} MH/s"
                          f" | {shares} shares aceitas (dificuldade {dificuldade})")
                    ultimo_relatorio = agora
            except (URLError, OSError, ValueError, KeyError) as erro:
                print(f"Conexão com o pool indisponível: {erro}. Nova tentativa em 2 segundos.")
                lote = None
                ultima_consulta = ultima_presenca = 0
                time.sleep(2)
    except KeyboardInterrupt:
        print("\nTrabalhador encerrado.")


if __name__ == "__main__":
    main()
