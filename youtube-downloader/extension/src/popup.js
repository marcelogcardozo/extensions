const SERVIDOR = "http://127.0.0.1:8756";

// O servidor exige este cabecalho. Ele e o que distingue a extensao de uma
// pagina web qualquer que tente falar com o 127.0.0.1 - veja _cliente_ok()
// em server.py.
const CABECALHO = { "X-YTDL-Client": "extension" };
const JSON_CABECALHO = { "Content-Type": "application/json", ...CABECALHO };

// Status em que um download ainda esta vivo, espelhando o server.py.
const ATIVOS = ["queued", "starting", "downloading", "merging"];

// Por quanto tempo o desfecho de um download continua sendo noticia na tela.
const NOTICIA_S = 60;

const INTERVALO_MS = 600;

// Aba que ja e uma pagina de video do YouTube.
const YT_PAGINA =
  /^https:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?|shorts\/|live\/)|youtu\.be\/)/i;

const botao = document.getElementById("baixar");
const status = document.getElementById("status");
const titulo = document.getElementById("titulo");
const seletor = document.getElementById("seletor");
const campoNome = document.getElementById("nome");
const avisoBaixado = document.getElementById("ja-baixado");
const secAndamento = document.getElementById("sec-andamento");
const listaAndamento = document.getElementById("lista-andamento");
const secInterrompidos = document.getElementById("sec-interrompidos");
const listaInterrompidos = document.getElementById("lista-interrompidos");

let videos = [];
let servidorOk = false;

// Mensagem de base da tela, para o desfecho de um download nao apagar a dica
// de "nao achei video nesta pagina" ao expirar.
let dica = "";

// Onde os arquivos vao parar. So o servidor sabe: no modo nativo e a pasta
// local, no Docker e o caminho do host montado no container.
let pastaDestino = "";

function mostrar(texto, classe = "") {
  status.textContent = texto;
  status.className = classe;
  // Vazio, ele reservava altura e abria um vao morto acima das listas.
  status.hidden = !texto;
}

function velocidade(bytesPorSegundo) {
  if (!bytesPorSegundo) return "";
  const mb = bytesPorSegundo / 1024 / 1024;
  return mb >= 1
    ? `${mb.toFixed(1).replace(".", ",")} MB/s`
    : `${Math.round(bytesPorSegundo / 1024)} kB/s`;
}

function restante(segundos) {
  if (segundos == null) return "";
  if (segundos < 60) return `${Math.round(segundos)} s`;
  if (segundos < 3600) return `${Math.round(segundos / 60)} min`;
  const horas = Math.floor(segundos / 3600);
  return `${horas} h ${Math.round((segundos % 3600) / 60)} min`;
}

function tamanho(bytes) {
  const mb = bytes / 1024 / 1024;
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

// O que o download esta fazendo agora, em palavras. Antes eram fragmentos
// soltos ("iniciando...", "juntando...") jogados na direita da linha, onde
// nao tinham largura garantida e eram os primeiros a ser cortados.
const FAIXAS = { video: "Baixando vídeo", audio: "Baixando áudio" };

function etapaDoJob(job) {
  if (job.status === "queued") {
    return job.posicao > 1 ? `Na fila · ${job.posicao}º` : "Na fila · é o próximo";
  }
  if (job.status === "starting") return "Consultando o YouTube...";
  if (job.status === "merging") return "Juntando vídeo e áudio...";

  // O YouTube entrega as faixas separadas, entao o yt-dlp baixa duas vezes.
  // Dizer qual esta vindo e o que impede a segunda parecer um recomeco.
  const rotulo = FAIXAS[job.faixa] ?? "Baixando";
  const partes = [job.percent == null ? null : `${Math.round(job.percent)}%`];
  partes.push(velocidade(job.velocidade), restante(job.eta));
  return [rotulo, ...partes.filter(Boolean)].join(" · ");
}

function urlDoVideo(id) {
  return `https://www.youtube.com/watch?v=${id}`;
}

// ---------------------------------------------------------------- deteccao

// Roda dentro da pagina. Muitos sites (plataformas de curso, blogs) embutem o
// video num <iframe>, entao a URL da aba nao ajuda - o id esta no src do
// iframe, que o documento de cima consegue ler mesmo sendo cross-origin.
function varrerPagina() {
  const achados = [];
  const vistos = new Set();
  const padrao =
    /^https?:\/\/(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)\/embed\/([\w-]{11})/;

  for (const frame of document.querySelectorAll("iframe")) {
    const m = (frame.src || "").match(padrao);
    if (!m || vistos.has(m[1])) continue;
    vistos.add(m[1]);
    achados.push({
      id: m[1],
      titulo: (frame.title || "").replace(/^Player for\s*/i, "").trim(),
    });
  }
  return achados;
}

async function detectar(aba) {
  if (YT_PAGINA.test(aba.url)) {
    const u = new URL(aba.url);
    const id =
      u.searchParams.get("v") || u.pathname.split("/").filter(Boolean).pop();
    return [{ id, titulo: (aba.title || "").replace(/ - YouTube$/, "") }];
  }

  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId: aba.id },
      func: varrerPagina,
    });
    return r?.result ?? [];
  } catch {
    return []; // paginas internas do Chrome, PDFs etc.
  }
}

function videoEscolhido() {
  return videos.length > 1 ? videos[seletor.selectedIndex] : videos[0];
}

// Vazio, o campo nao muda nada: vale o titulo do YouTube, que ja aparece
// logo acima. O placeholder instrui em vez de repetir esse titulo.

// ----------------------------------------------------------------- servidor

// O YouTube exige cookies para liberar o download. Lemos pela API do Chrome
// porque o yt-dlp nao consegue mais decifrar o banco de cookies do Chrome
// no Windows (App-Bound Encryption, Chrome 127+).
async function coletarCookies() {
  const cookies = await chrome.cookies.getAll({ domain: "youtube.com" });
  return cookies.map((c) => ({
    domain: c.domain,
    path: c.path,
    secure: c.secure,
    expires: c.expirationDate ?? 0,
    name: c.name,
    value: c.value,
  }));
}

async function baixar(id, nome = "") {
  const r = await fetch(`${SERVIDOR}/download`, {
    method: "POST",
    headers: JSON_CABECALHO,
    body: JSON.stringify({
      url: urlDoVideo(id),
      nome,
      cookies: await coletarCookies(),
    }),
  });
  const dados = await r.json();
  if (!r.ok) throw new Error(dados.error ?? "falha na requisição");

  // Quem mantem o badge do icone vivo com a popup fechada.
  chrome.runtime.sendMessage({ tipo: "acompanhar" }).catch(() => {});
  return dados;
}

// --------------------------------------------------------------- renderizacao

// Atualiza uma lista no lugar em vez de recria-la a cada volta: recriar
// reiniciaria a transicao da barra de progresso a cada 600 ms.
function sincronizar(lista, itens, criar, atualizar) {
  const existentes = new Map(
    [...lista.children].map((el) => [el.dataset.id, el]),
  );

  for (const item of itens) {
    let linha = existentes.get(item.id);
    if (linha) {
      existentes.delete(item.id);
    } else {
      linha = criar(item);
      linha.dataset.id = item.id;
      lista.append(linha);
    }
    atualizar(linha, item);
  }

  for (const orfa of existentes.values()) orfa.remove();
}

function criarLinhaAndamento(job) {
  const linha = document.createElement("li");
  linha.className = "linha";
  linha.innerHTML = `
    <span class="nome"></span>
    <button class="cancelar" title="Cancelar este download">&times;</button>
    <div class="barra"><div></div></div>
    <span class="etapa"></span>`;

  linha.querySelector(".cancelar").addEventListener("click", async () => {
    await fetch(`${SERVIDOR}/cancelar`, {
      method: "POST",
      headers: JSON_CABECALHO,
      body: JSON.stringify({ id: job.id }),
    }).catch(() => {});
  });

  return linha;
}

function atualizarLinhaAndamento(linha, job) {
  linha.querySelector(".nome").textContent = job.title || "Vídeo do YouTube";
  linha.querySelector(".etapa").textContent = etapaDoJob(job);
  linha.querySelector(".barra > div").style.width = `${job.percent ?? 0}%`;
}

function criarLinhaInterrompida(item) {
  const linha = document.createElement("li");
  linha.className = "linha interrompida";
  linha.innerHTML = `
    <span class="nome"></span>
    <span class="etapa"></span>
    <div class="acoes">
      <button class="continuar">Continuar</button>
      <button class="descartar">Descartar</button>
    </div>`;

  linha.querySelector(".continuar").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      // O nome original, e nao o que estiver no campo: o parcial no disco
      // usa esse nome, e mudar de nome faria o download recomecar do zero.
      await baixar(item.id, item.titulo);
    } catch (erro) {
      mostrar(erro.message, "erro");
      e.target.disabled = false;
    }
  });

  // Apagar centenas de MB merece dois cliques. Um confirm() do navegador
  // seria mais brusco do que o gesto pede, e some junto com a popup.
  const descartar = linha.querySelector(".descartar");
  descartar.addEventListener("click", async () => {
    if (!descartar.classList.contains("confirmando")) {
      descartar.classList.add("confirmando");
      descartar.textContent = "Apagar mesmo?";
      setTimeout(() => {
        descartar.classList.remove("confirmando");
        descartar.textContent = "Descartar";
      }, 4000);
      return;
    }
    descartar.disabled = true;
    await fetch(`${SERVIDOR}/descartar`, {
      method: "POST",
      headers: JSON_CABECALHO,
      body: JSON.stringify({ id: item.id }),
    }).catch(() => {});
  });

  return linha;
}

function atualizarLinhaInterrompida(linha, item) {
  linha.querySelector(".nome").textContent = item.titulo;
  linha.querySelector(".etapa").textContent =
    `${tamanho(item.bytes)} já baixados · parado`;
}

// ------------------------------------------------------------------- estado

let ativosAgora = [];

// Ids que ja tem arquivo pronto na pasta. Num curso com dezenas de aulas
// parecidas, e o que evita rebaixar 800 MB por engano.
let baixados = [];

function atualizarBotao(ativos) {
  ativosAgora = ativos;
  botao.hidden = false;
  titulo.hidden = false;
  avisoBaixado.hidden = true;
  campoNome.style.display = videos.length ? "block" : "none";

  if (!videos.length) {
    botao.disabled = true;
    botao.textContent = "Nenhum vídeo do YouTube nesta página";
    return;
  }
  if (!servidorOk) {
    botao.disabled = true;
    botao.textContent = "Aguardando o servidor local...";
    return;
  }

  // Quando o video desta aba ja esta baixando, o topo nao tem nada a oferecer:
  // a lista logo abaixo mostra o mesmo video com progresso e botao de
  // cancelar. Manter um botao morto aqui so dizia a mesma coisa duas vezes.
  const escolhido = videoEscolhido();
  const jaBaixando =
    escolhido && ativos.some((j) => (j.url || "").includes(escolhido.id));
  if (jaBaixando) {
    botao.hidden = true;
    campoNome.style.display = "none";
    // Com varios videos na pagina o titulo nao repete nada - ele diz quantos
    // sao, e o seletor continua servindo para escolher outro.
    titulo.hidden = videos.length === 1;
    return;
  }

  const jaNaPasta = baixados.includes(escolhido?.id);
  avisoBaixado.hidden = !jaNaPasta;

  botao.disabled = false;
  botao.textContent = jaNaPasta
    ? "Baixar de novo"
    : videos.length > 1
      ? "Baixar selecionado"
      : "Baixar vídeo";
}

function noticiar(jobs) {
  const agora = Date.now() / 1000;
  const recentes = jobs.filter(
    (j) => !ATIVOS.includes(j.status) && agora - (j.finalizado ?? 0) < NOTICIA_S,
  );

  const falhou = recentes.find((j) => j.status === "error");
  if (falhou) {
    mostrar(falhou.error, "erro");
    return;
  }
  const pronto = recentes.find((j) => j.status === "done");
  if (pronto) {
    mostrar(`Pronto! Salvo em ${pastaDestino}`);
    return;
  }
  const cancelado = recentes.find((j) => j.status === "canceled");
  if (cancelado) {
    mostrar("Download cancelado. O que já baixou ficou em Interrompidos.");
    return;
  }
  mostrar(dica);
}

// A popup nao consegue se recarregar sozinha, entao em vez de pedir para o
// usuario fechar e abrir de novo, ela fica batendo ate o servidor subir - e,
// depois, ate ela ser fechada, porque e dai que sai a tela toda.
async function acompanhar() {
  while (true) {
    let dados = null;
    try {
      const r = await fetch(`${SERVIDOR}/jobs`, { headers: CABECALHO });
      if (r.ok) dados = await r.json();
    } catch {
      dados = null;
    }

    if (!dados) {
      if (servidorOk || status.textContent === "") {
        mostrar(
          "Servidor desligado. Suba com `docker compose up -d` — eu conecto sozinho.",
          "erro",
        );
      }
      servidorOk = false;
      atualizarBotao([]);
      sincronizar(listaAndamento, [], criarLinhaAndamento, atualizarLinhaAndamento);
      secAndamento.hidden = true;
      secInterrompidos.hidden = true;
    } else {
      servidorOk = true;
      pastaDestino = dados.pasta ?? "";
      baixados = dados.baixados ?? [];

      const jobs = dados.jobs ?? [];
      const ativos = jobs.filter((j) => ATIVOS.includes(j.status));
      const interrompidos = dados.interrompidos ?? [];

      sincronizar(
        listaAndamento,
        ativos,
        criarLinhaAndamento,
        atualizarLinhaAndamento,
      );
      sincronizar(
        listaInterrompidos,
        interrompidos,
        criarLinhaInterrompida,
        atualizarLinhaInterrompida,
      );
      secAndamento.hidden = !ativos.length;
      secInterrompidos.hidden = !interrompidos.length;

      noticiar(jobs);
      atualizarBotao(ativos);
    }

    await new Promise((r) => setTimeout(r, INTERVALO_MS));
  }
}

botao.addEventListener("click", async () => {
  const video = videoEscolhido();
  if (!video) return;

  botao.disabled = true;
  botao.textContent = "Baixando...";
  mostrar("Falando com o servidor local...");

  try {
    const dados = await baixar(video.id, campoNome.value);
    if (dados.ja_em_andamento) mostrar("Este vídeo já estava baixando.");
  } catch (e) {
    mostrar(e.message, "erro");
    botao.disabled = false;
    botao.textContent = "Tentar de novo";
  }
});

async function iniciar() {
  const [aba] = await chrome.tabs.query({ active: true, currentWindow: true });
  videos = aba ? await detectar(aba) : [];

  if (!videos.length) {
    titulo.textContent = "";
    dica = "Abra um vídeo do YouTube ou uma página que tenha um embutido.";
    mostrar(dica);
  } else if (videos.length > 1) {
    seletor.innerHTML = "";
    videos.forEach((v, i) => {
      const opt = document.createElement("option");
      opt.textContent = v.titulo || `Vídeo ${i + 1} (${v.id})`;
      seletor.append(opt);
    });
    seletor.style.display = "block";
    seletor.addEventListener("change", () => atualizarBotao(ativosAgora));
    titulo.textContent = `${videos.length} vídeos nesta página:`;
  } else {
    titulo.textContent = videos[0].titulo || videos[0].id;
  }

  atualizarBotao([]);

  // Vale mesmo sem video nesta aba: pode haver download de outra rolando, ou
  // algo interrompido esperando para ser retomado.
  acompanhar();
}

iniciar();
