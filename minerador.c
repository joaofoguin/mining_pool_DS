/*
 * Núcleo de mineração em C, chamado pelo miningProg.py via ctypes.
 *
 * Calcula SHA256(SHA256(prefixo + str(nonce) + sufixo)), exatamente o mesmo
 * formato de protocolo.hash_bloco, para nonces em [inicio, fim).
 *
 * Otimizações:
 *  - nonce em decimal incrementado direto no buffer (sem sprintf a cada tentativa);
 *  - blocos de 64 bytes antes do nonce são calculados uma vez só (midstate);
 *  - padding do SHA-256 montado uma vez por quantidade de dígitos;
 *  - instruções SHA-NI do processador (Ryzen tem) quando disponíveis.
 *
 * Compilar no Windows (MinGW):  gcc -O3 -shared -o minerador.dll minerador.c
 * Compilar no Linux:            gcc -O3 -shared -fPIC -o minerador.so minerador.c
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define EXPORTAR __declspec(dllexport)
#else
#define EXPORTAR __attribute__((visibility("default")))
#endif

#if defined(__x86_64__) || defined(__i386__)
#define TEM_X86 1
#include <cpuid.h>
#include <immintrin.h>
#endif

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

static const uint32_t H_INICIAL[8] = {
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

/* ---------- SHA-256 em C puro (funciona em qualquer processador) ---------- */
static void compressao_c(uint32_t estado[8], const uint8_t bloco[64]) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
        w[i] = (uint32_t)bloco[4 * i] << 24 | (uint32_t)bloco[4 * i + 1] << 16 |
               (uint32_t)bloco[4 * i + 2] << 8 | bloco[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROTR(w[i - 15], 7) ^ ROTR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROTR(w[i - 2], 17) ^ ROTR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = estado[0], b = estado[1], c = estado[2], d = estado[3];
    uint32_t e = estado[4], f = estado[5], g = estado[6], h = estado[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + (ROTR(e, 6) ^ ROTR(e, 11) ^ ROTR(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        uint32_t t2 = (ROTR(a, 2) ^ ROTR(a, 13) ^ ROTR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    estado[0] += a; estado[1] += b; estado[2] += c; estado[3] += d;
    estado[4] += e; estado[5] += f; estado[6] += g; estado[7] += h;
}

/* ---------- SHA-256 com instruções SHA-NI (Ryzen, Intel a partir de Ice Lake) ---------- */
#ifdef TEM_X86
__attribute__((target("sha,sse4.1")))
static void compressao_ni(uint32_t estado[8], const uint8_t bloco[64]) {
    const __m128i MASCARA = _mm_set_epi64x(0x0c0d0e0f08090a0bULL, 0x0405060700010203ULL);
    __m128i tmp = _mm_loadu_si128((const __m128i *)&estado[0]);
    __m128i st1 = _mm_loadu_si128((const __m128i *)&estado[4]);
    tmp = _mm_shuffle_epi32(tmp, 0xB1);                 /* CDAB */
    st1 = _mm_shuffle_epi32(st1, 0x1B);                 /* EFGH */
    __m128i st0 = _mm_alignr_epi8(tmp, st1, 8);         /* ABEF */
    st1 = _mm_blend_epi16(st1, tmp, 0xF0);              /* CDGH */
    __m128i abef = st0, cdgh = st1;

    __m128i m[4];
    for (int i = 0; i < 4; i++)
        m[i] = _mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)(bloco + 16 * i)), MASCARA);

    /* 16 grupos de 4 rodadas; m[] guarda as últimas 16 palavras da mensagem */
    for (int r = 0; r < 16; r++) {
        __m128i atual = m[r & 3];
        __m128i msg = _mm_add_epi32(atual, _mm_loadu_si128((const __m128i *)&K[4 * r]));
        st1 = _mm_sha256rnds2_epu32(st1, st0, msg);
        if (r >= 3 && r <= 14) {
            __m128i prox = _mm_add_epi32(m[(r + 1) & 3], _mm_alignr_epi8(atual, m[(r + 3) & 3], 4));
            m[(r + 1) & 3] = _mm_sha256msg2_epu32(prox, atual);
        }
        msg = _mm_shuffle_epi32(msg, 0x0E);
        st0 = _mm_sha256rnds2_epu32(st0, st1, msg);
        if (r >= 1 && r <= 12)
            m[(r + 3) & 3] = _mm_sha256msg1_epu32(m[(r + 3) & 3], atual);
    }
    st0 = _mm_add_epi32(st0, abef);
    st1 = _mm_add_epi32(st1, cdgh);
    tmp = _mm_shuffle_epi32(st0, 0x1B);                 /* FEBA */
    st1 = _mm_shuffle_epi32(st1, 0xB1);                 /* DCHG */
    st0 = _mm_blend_epi16(tmp, st1, 0xF0);              /* DCBA */
    st1 = _mm_alignr_epi8(st1, tmp, 8);                 /* ABEF -> HGFE */
    _mm_storeu_si128((__m128i *)&estado[0], st0);
    _mm_storeu_si128((__m128i *)&estado[4], st1);
}

static int cpu_tem_sha(void) {
    unsigned a, b, c, d;
    if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return 0;
    if (!(b & (1u << 29))) return 0;                    /* SHA */
    if (!__get_cpuid(1, &a, &b, &c, &d)) return 0;
    return (c & (1u << 19)) && (c & (1u << 9));         /* SSE4.1 e SSSE3 */
}
#endif

typedef void (*funcao_compressao)(uint32_t[8], const uint8_t[64]);
static funcao_compressao compressao = 0;

static void escolher_compressao(void) {
    if (compressao) return;
#ifdef TEM_X86
    if (cpu_tem_sha()) { compressao = compressao_ni; return; }
#endif
    compressao = compressao_c;
}

/* 1 = usando SHA-NI, 0 = C puro. Só para o Python mostrar no terminal. */
EXPORTAR int usa_sha_ni(void) {
    escolher_compressao();
#ifdef TEM_X86
    return compressao == compressao_ni;
#else
    return 0;
#endif
}

/* Força o SHA-256 em C puro (usado nos testes). */
EXPORTAR void forcar_c_puro(void) { compressao = compressao_c; }

static int digitos(uint64_t n) {
    int d = 1;
    while (n >= 10) { n /= 10; d++; }
    return d;
}

static uint64_t potencia10(int d) {      /* 10^d, saturando em UINT64_MAX */
    uint64_t p = 1;
    for (int i = 0; i < d; i++) {
        if (p > UINT64_MAX / 10) return UINT64_MAX;
        p *= 10;
    }
    return p;
}

static int tem_zeros(const uint32_t h[8], int zeros) {
    int bits = 4 * zeros;
    for (int i = 0; i < 8 && bits > 0; i++, bits -= 32) {
        if (bits >= 32) { if (h[i]) return 0; }
        else return (h[i] >> (32 - bits)) == 0;
    }
    return 1;
}

/*
 * Testa os nonces de [inicio, fim). Para no primeiro hash que começa com
 * `zeros` zeros hexadecimais: devolve 1 e grava o nonce em *achado.
 * Se nenhum servir, devolve 0. Devolve -1 se faltar memória.
 */
EXPORTAR int minerar(const uint8_t *prefixo, size_t tam_prefixo,
                     const uint8_t *sufixo, size_t tam_sufixo,
                     uint64_t inicio, uint64_t fim, int zeros, uint64_t *achado) {
    escolher_compressao();
    size_t maximo = tam_prefixo + 20 + tam_sufixo + 72;
    uint8_t *buf = malloc(maximo);
    if (!buf) return -1;
    memcpy(buf, prefixo, tam_prefixo);

    /* blocos inteiros antes do nonce: calculados uma vez (midstate) */
    size_t blocos_fixos = tam_prefixo / 64;
    uint32_t meio[8];
    memcpy(meio, H_INICIAL, sizeof meio);
    for (size_t i = 0; i < blocos_fixos; i++) compressao(meio, buf + 64 * i);

    /* segundo hash: 32 bytes de entrada, padding fixo */
    uint8_t bloco2[64] = {0};
    bloco2[32] = 0x80;
    bloco2[62] = 0x01;                                   /* 256 bits = 0x0100 */

    uint64_t nonce = inicio;
    int resultado = 0;
    while (nonce < fim) {
        /* monta o buffer para nonces com esta quantidade de dígitos */
        int d = digitos(nonce);
        uint64_t limite = potencia10(d);
        if (limite > fim) limite = fim;
        char *num = (char *)buf + tam_prefixo;
        uint64_t n = nonce;
        for (int i = d - 1; i >= 0; i--) { num[i] = '0' + n % 10; n /= 10; }
        size_t tam = tam_prefixo + d + tam_sufixo;
        memcpy(buf + tam_prefixo + d, sufixo, tam_sufixo);
        size_t total = (tam + 9 + 63) / 64 * 64;
        memset(buf + tam, 0, total - tam);
        buf[tam] = 0x80;
        uint64_t bits = (uint64_t)tam * 8;
        for (int i = 0; i < 8; i++) buf[total - 1 - i] = (uint8_t)(bits >> (8 * i));
        size_t blocos = total / 64;

        for (; nonce < limite; nonce++) {
            uint32_t h[8];
            memcpy(h, meio, sizeof h);
            for (size_t i = blocos_fixos; i < blocos; i++) compressao(h, buf + 64 * i);
            for (int i = 0; i < 8; i++) {
                bloco2[4 * i] = h[i] >> 24; bloco2[4 * i + 1] = h[i] >> 16;
                bloco2[4 * i + 2] = h[i] >> 8; bloco2[4 * i + 3] = (uint8_t)h[i];
            }
            uint32_t h2[8];
            memcpy(h2, H_INICIAL, sizeof h2);
            compressao(h2, bloco2);
            if (tem_zeros(h2, zeros)) {
                *achado = nonce;
                resultado = 1;
                goto fim_busca;
            }
            /* incrementa o número decimal dentro do buffer */
            int i = d - 1;
            while (i >= 0 && num[i] == '9') num[i--] = '0';
            if (i >= 0) num[i]++;
        }
    }
fim_busca:
    free(buf);
    return resultado;
}
