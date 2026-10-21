# Pool de Mineração

Um computador roda o **pool**: ele conversa com o servidor principal da turma e reparte o trabalho. Todos os computadores, inclusive o do pool, rodam o **trabalhador**, que minera e envia as provas ao pool. No placar do servidor aparece só o nome do pool. A participação de cada um fica registrada no próprio pool.

```
Servidor principal  <──>  pool.py  <──>  miningProg.py (trabalhador 1)
                                    <──>  miningProg.py (trabalhador 2)
                                    <──>  ...
```

## Arquivos

| Arquivo | Para que serve |
|---|---|
| `pool.py` | Pool: busca o trabalho no servidor, distribui faixas de nonce e envia os blocos encontrados |
| `miningProg.py` | Trabalhador: minera os lotes recebidos do pool usando o processador |
| `minerador_gpu.py` | Trabalhador que minera na placa de vídeo NVIDIA (opcional, muito mais rápido) |
| `protocolo.py` | Cálculo do hash, usado pelos dois |
| `rede.py` | Requisições HTTP, usadas pelos dois |

## Instalação (todos os computadores)

1. Instale o **Python 3.8 ou mais recente** ([python.org](https://www.python.org/downloads/)). No Windows, marque **"Add Python to PATH"** durante a instalação. Não é preciso instalar nenhuma biblioteca.
2. Baixe o projeto:
   ```
   git clone <URL_DO_REPOSITORIO>
   cd <PASTA_DO_REPOSITORIO>
   ```
   Se preferir, baixe o ZIP pelo GitHub e extraia.
3. Confira se o Python está funcionando:
   ```
   python --version
   ```
   No Linux ou macOS, troque `python` por `python3` em todos os comandos.

Todos os computadores precisam estar na **mesma rede**. Se a rede da universidade bloquear conexões entre as máquinas, usem o hotspot de um celular.

## Computador do pool

Só **um** computador faz esta parte.

### 1. Descubra o IP da máquina

```
ipconfig
```

Anote o **Endereço IPv4** (ex.: `192.168.1.20`). Os trabalhadores vão precisar dele. No Linux ou macOS, use `ip addr` ou `ifconfig`.

### 2. Libere a porta no firewall

No Windows, abra o PowerShell **como administrador** e rode:

```
New-NetFirewallRule -DisplayName "Pool mineracao" -Direction Inbound -Protocol TCP -LocalPort 8002 -Action Allow
```

Se aparecer **"Acesso negado"**, o PowerShell não está como administrador. Para abrir certo: aperte a tecla Windows, digite `PowerShell`, clique com o botão direito em **Windows PowerShell**, escolha **Executar como administrador** e rode o comando de novo. Se ninguém tiver acesso de administrador nesse computador, rodem o pool em outra máquina. Só o computador do pool precisa liberar a porta.

Quando terminar a atividade, a regra pode ser removida com:

```
Remove-NetFirewallRule -DisplayName "Pool mineracao"
```

### 3. Inicie o pool

```
python pool.py --servidor http://IP_DO_SERVIDOR:8001 --nome NomeDoGrupo
```

| Opção | Obrigatória | Descrição |
|---|---|---|
| `--servidor` | sim | Endereço do servidor principal da turma |
| `--nome` | sim | Nome com que o grupo aparece no placar (1 a 60 caracteres) |
| `--porta` | não | Porta em que os trabalhadores se conectam (padrão `8002`) |
| `--folga` | não | Quantos zeros a menos uma share exige em relação ao bloco (padrão `4`). Quanto maior, mais fácil para quem minera na CPU conseguir shares |
| `--lote` | não | Quantidade de nonces entregue por lote (padrão `200000`) |

Deixe esse terminal aberto. Para minerar também nesta máquina, abra **outro terminal** e siga a seção abaixo, usando `http://127.0.0.1:8002` como endereço do pool.

## Computadores dos trabalhadores

```
python miningProg.py --pool http://IP_DO_POOL:8002 --nome SeuNome
```

| Opção | Obrigatória | Descrição |
|---|---|---|
| `--pool` | sim | Endereço do pool (IP do computador do pool + porta) |
| `--nome` | sim | Seu nome. Precisa ser **diferente** do nome dos outros trabalhadores |
| `--processos` | não | Quantos núcleos do processador usar (padrão: todos) |
| `--pausa` | não | Descanso entre lotes, em segundos (padrão `0`). Use `--processos` menor ou uma pausa se o computador ficar lento |

Exemplo com o pool em `192.168.1.20`:

```
python miningProg.py --pool http://192.168.1.20:8002 --nome maria
```

Para encerrar o pool ou um trabalhador, pressione **Ctrl+C**.

## Trabalhador com placa de vídeo NVIDIA (opcional)

Quem tem placa NVIDIA pode usar `minerador_gpu.py` no lugar de `miningProg.py`. Numa GTX 1650, ele fez **240 a 330 milhões de hashes por segundo**. O trabalhador de processador faz cerca de 600 mil por núcleo.

### Instalação

1. Atualize o driver da NVIDIA ([nvidia.com/drivers](https://www.nvidia.com/drivers)). Para conferir, rode `nvidia-smi`: ele deve mostrar o nome da placa.
2. Instale o CuPy com as bibliotecas CUDA. Não é preciso instalar o CUDA Toolkit separadamente:
   ```
   python -m pip install "cupy-cuda12x[ctk]"
   ```

### Execução

```
python minerador_gpu.py --pool http://IP_DO_POOL:8002 --nome SeuNome
```

| Opção | Obrigatória | Descrição |
|---|---|---|
| `--pool` | sim | Endereço do pool |
| `--nome` | sim | Seu nome (diferente dos outros trabalhadores) |
| `--dispositivo` | não | Índice da placa, se houver mais de uma (padrão `0`) |

Ao iniciar, o programa compila o código da GPU e confere se os hashes dela são iguais aos de `protocolo.py`. Se aparecer "Autoteste da GPU aprovado", está tudo certo. O aviso `CUDA path could not be detected` pode ser ignorado.

Não rode `miningProg.py` e `minerador_gpu.py` com o mesmo `--nome`. Use nomes diferentes ou só um dos dois.

## Acompanhando o pool

Em qualquer navegador da rede, abra:

```
http://IP_DO_POOL:8002/estatisticas
```

A página mostra:
- quem está online;
- as shares de cada trabalhador na rodada atual, com o percentual de cada um;
- o total de shares de cada trabalhador;
- os blocos encontrados, com quem achou e a divisão da recompensa.

## Como funciona

- **Hash com o nome do pool:** todos os trabalhadores calculam o hash com o **nome do pool**. Por isso a solução vale para o pool quando ele a envia ao servidor.
- **Faixas de nonce:** cada trabalhador recebe uma faixa de nonces só sua, e ninguém repete o trabalho de outro.
- **Shares:** uma *share* é uma prova mais fácil que o bloco. Ela serve para medir quanto cada um contribuiu. Quando uma share também atende à dificuldade do bloco, o pool envia a solução ao servidor.
- **Shares com peso:** a GPU acharia milhares de shares por segundo. Para não sobrecarregar o pool, ela envia shares mais difíceis. Cada zero a mais exige 16 vezes mais hashes, então vale 16 vezes mais. Por isso os números de shares em `/estatisticas` são ponderados, e a divisão continua proporcional ao trabalho de cada um.
- **Divisão da recompensa:** é proporcional. Cada bloco encontrado pelo pool é dividido conforme as shares enviadas desde o bloco anterior do pool.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| `python` não é reconhecido | Reinstale o Python marcando "Add Python to PATH", ou use `py` no lugar de `python` |
| Trabalhador mostra "Conexão com o pool indisponível" | Confira o IP e a porta, se o pool está rodando e se a porta foi liberada no firewall. Tente abrir `/estatisticas` no navegador desse computador |
| Pool mostra "Servidor principal indisponível" | Confira o endereço passado em `--servidor` e se o servidor da turma está no ar |
| Trabalhador parado sem minerar | O pool ainda não recebeu trabalho do servidor. Ele volta sozinho quando houver trabalho |
| `ModuleNotFoundError: protocolo` ou `rede` | Todos os arquivos `.py` precisam estar na mesma pasta |
| `ModuleNotFoundError: cupy` | Instale com `python -m pip install "cupy-cuda12x[ctk]"` |
| Erro de CUDA ou de driver no `minerador_gpu.py` | Atualize o driver da NVIDIA. Se não resolver, use `miningProg.py` |
