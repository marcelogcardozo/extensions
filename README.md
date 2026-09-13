# extensions

Pequenas extensões de navegador para resolver problemas reais do dia a dia.
Cada projeto nasce independente, com seu próprio código, instruções, testes e
forma de execução. A ideia é manter as ferramentas simples de instalar,
transparentes no que fazem e úteis fora de uma demonstração bonita.

## Sumário

| Projeto | Descrição | Navegadores |
|---|---|---|
| [youtube-downloader](youtube-downloader/) | Baixa vídeos da aba atual ou de players do YouTube embutidos, usando `yt-dlp` localmente | Chrome, Edge |

## Como explorar

Comece pelo README do projeto que você quer usar. Este arquivo é apenas o mapa;
cada pasta de projeto explica instalação, uso, configuração, limitações e testes
sem depender de conhecimento do restante do repositório.

```text
extensions/
├── README.md                  este índice
├── .github/workflows/         automações de CI por projeto
└── <projeto>/
    ├── README.md              instalação e uso
    ├── extension/             arquivos carregados no navegador
    └── server/                serviços locais, quando necessários
```

Os projetos são versionados separadamente. Por exemplo, uma versão do
`youtube-downloader` é marcada como `youtube-downloader/v1.1.0`, sem obrigar
as outras extensões a acompanhar o mesmo ciclo.

## Desenvolvimento

O lint de Python é compartilhado e configurado no
[`pyproject.toml`](pyproject.toml). Os testes e demais comandos ficam junto do
projeto que os utiliza.

## Licença

[MIT](LICENSE).
