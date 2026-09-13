const SERVIDOR = "http://127.0.0.1:8756";

// O servidor exige este cabecalho. Ele e o que distingue a extensao de uma
// pagina web qualquer que tente falar com o 127.0.0.1 - veja _cliente_ok()
// em server.py.
const CABECALHO = { "X-YTDL-Client": "extension" };

// Aba que ja e uma pagina de video do YouTube.
const YT_PAGINA = /^https:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?|shorts\/|live\/)|youtu\.be\/)/i;

const botao = document.getElementById("baixar");
const status = document.getElementById("status");
const titulo = document.getElementById("titulo");
const seletor = document.getElementById("seletor");
const barra = document.getElementById("barra");
const preenchimento = barra.firstElementChild;

let videos = [];
let servidorOk = false;
let baixando = false;

// Onde os arquivos vao parar. So o servidor sabe: no modo nativo e a pasta
// local, no Docker e o caminho do host que esta montado no container. Ter
// isso escrito aqui a mao era mentira assim que a configuracao mudava.
let pastaDestino = "";

function mostrar(texto, classe = "") {
  status.textContent = texto;
  status.className = classe;
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

// ------------------------------------------------------------------- estado

function atualizarBotao() {
  if (baixando) return;

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
  botao.disabled = false;
  botao.textContent = videos.length > 1 ? "Baixar selecionado" : "Baixar vídeo";
}

// A popup nao consegue se recarregar sozinha, entao em vez de pedir para o
// usuario fechar e abrir de novo, ela fica checando ate o servidor subir.
async function vigiarServidor() {
  let avisou = false;

  while (true) {
    try {
      const r = await fetch(`${SERVIDOR}/health`, { headers: CABECALHO });
      servidorOk = r.ok;
      if (r.ok) pastaDestino = (await r.json()).pasta ?? "";
    } catch {
      servidorOk = false;
    }

    if (servidorOk) {
      if (avisou) mostrar("Servidor conectado.", "aguardando");
      atualizarBotao();
      return;
    }

    if (!avisou) {
      mostrar("Servidor desligado. Suba com `docker compose up -d` — eu conecto sozinho.", "erro");
      avisou = true;
    }
    atualizarBotao();
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function iniciar() {
  const [aba] = await chrome.tabs.query({ active: true, currentWindow: true });
  videos = aba ? await detectar(aba) : [];

  if (!videos.length) {
    titulo.textContent = "";
    atualizarBotao();
    mostrar("Abra um vídeo do YouTube ou uma página que tenha um embutido.");
    return;
  }

  if (videos.length > 1) {
    seletor.innerHTML = "";
    videos.forEach((v, i) => {
      const opt = document.createElement("option");
      opt.textContent = v.titulo || `Vídeo ${i + 1} (${v.id})`;
      seletor.append(opt);
    });
    seletor.style.display = "block";
    titulo.textContent = `${videos.length} vídeos nesta página:`;
  } else {
    titulo.textContent = videos[0].titulo || videos[0].id;
  }

  atualizarBotao();
  vigiarServidor();
}

// ----------------------------------------------------------------- download

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

async function acompanhar(id) {
  const resp = await fetch(`${SERVIDOR}/status?id=${id}`, { headers: CABECALHO });
  const job = await resp.json();

  if (job.title) titulo.textContent = job.title;

  if (job.status === "downloading") {
    barra.style.display = "block";
    preenchimento.style.width = `${job.percent ?? 0}%`;
    mostrar(job.percent != null ? `Baixando... ${job.percent}%` : "Baixando...");
  } else if (job.status === "merging") {
    preenchimento.style.width = "100%";
    mostrar("Juntando vídeo e áudio...");
  } else if (job.status === "done") {
    preenchimento.style.width = "100%";
    mostrar(pastaDestino ? `Pronto! Salvo em ${pastaDestino}` : "Pronto!");
    baixando = false;
    botao.disabled = false;
    botao.textContent = "Baixar de novo";
    return;
  } else if (job.status === "error") {
    barra.style.display = "none";
    mostrar(job.error, "erro");
    baixando = false;
    botao.disabled = false;
    botao.textContent = "Tentar de novo";
    return;
  }

  setTimeout(() => acompanhar(id), 500);
}

botao.addEventListener("click", async () => {
  const video = videoEscolhido();
  if (!video) return;

  baixando = true;
  botao.disabled = true;
  botao.textContent = "Baixando...";
  preenchimento.style.width = "0";
  mostrar("Falando com o servidor local...");

  try {
    const r = await fetch(`${SERVIDOR}/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...CABECALHO },
      body: JSON.stringify({
        url: `https://www.youtube.com/watch?v=${video.id}`,
        cookies: await coletarCookies(),
      }),
    });
    const dados = await r.json();
    if (!r.ok) throw new Error(dados.error ?? "falha na requisição");
    acompanhar(dados.id);
  } catch (e) {
    mostrar(e.message, "erro");
    baixando = false;
    botao.disabled = false;
    botao.textContent = "Tentar de novo";
  }
});

iniciar();
