# extensions

Extensões de navegador que eu escrevo para uso próprio. Cada uma é um projeto
independente numa pasta, com o seu próprio README, os seus testes e a sua forma
de rodar — você não precisa ler este arquivo para usar nenhuma delas.

| Projeto | O que faz | Navegadores |
|---|---|---|
| [youtube-downloader](youtube-downloader/) | Baixa o vídeo da aba atual (ou embutido em `<iframe>`) com o `yt-dlp` rodando localmente | Chrome, Edge |

## Como este repositório é organizado

```
extensions/
├── README.md                      este índice
├── .github/workflows/             um workflow por projeto, filtrado por caminho
└── <projeto>/
    ├── README.md                  instalação e uso — o projeto se explica sozinho
    ├── extension/                 o que se carrega no navegador
    └── server/                    o que rodar fora dele, quando houver
```

Três decisões que valem explicar, porque não são óbvias:

**Não existe `shared/`.** Enquanto houver um projeto só, não há o que
compartilhar — e abstrair cedo demais é o jeito clássico de um monorepo dar
errado. Quando o segundo projeto chegar, a duplicação real vai mostrar o que
extrair, e provavelmente não é o que eu adivinharia hoje.

**A divisão é por projeto, não por navegador.** Uma pasta `chrome/` e outra
`firefox/` duplicariam o projeto inteiro por causa de umas poucas linhas de
manifest. Quando um projeto precisar de mais de um navegador, o padrão é
`src/` compartilhado + `manifest.<alvo>.json` + um build que monta
`dist/<alvo>/` — dentro do projeto.

**Tags são por projeto**: `youtube-downloader/v1.1.0`. Uma versão do
repositório inteiro não significaria nada com cinco projetos dentro.

## Licença

[MIT](LICENSE).
