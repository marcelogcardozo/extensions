// Acompanha os downloads enquanto a popup esta fechada: mantem o badge do
// icone atualizado e avisa quando cada um termina.
//
// Este arquivo existe por causa de um bug concreto. A popup e efemera - some
// assim que voce clica fora dela - e com ela sumia qualquer nocao de "tem um
// download rolando". Reabrindo, o botao aparecia zerado, o usuario clicava de
// novo, e um segundo yt-dlp subia escrevendo nos MESMOS arquivos .part do
// primeiro. Os dois se atropelavam e o download travava pela metade.
//
// O servidor agora recusa o download duplicado, mas isso so conserta o
// estrago; o que conserta a causa e ter o progresso visivel sem a popup.

const SERVIDOR = "http://127.0.0.1:8756";
const CABECALHO = { "X-YTDL-Client": "extension" };
const ATIVOS = ["queued", "starting", "downloading", "merging"];
const INTERVALO_MS = 1000;

// So avisa de downloads que terminaram ha pouco. Sem esta janela, o worker
// notificaria de novo tudo que o servidor ainda guarda na memoria toda vez
// que o Chrome o reiniciasse.
const JANELA_AVISO_S = 300;

let rodando = false;

function ativo(job) {
  return ATIVOS.includes(job.status);
}

async function buscarJobs() {
  const r = await fetch(`${SERVIDOR}/jobs`, { headers: CABECALHO });
  if (!r.ok) throw new Error(`servidor respondeu ${r.status}`);
  return r.json();
}

function pintarBadge(ativos) {
  if (!ativos.length) {
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setTitle({ title: "Baixar este vídeo" });
    return;
  }

  // Um download: mostra a porcentagem. Varios: mostra quantos sao, porque
  // quatro caracteres nao contam duas historias ao mesmo tempo.
  const texto =
    ativos.length > 1
      ? String(ativos.length)
      : ativos[0].percent != null
        ? String(Math.round(ativos[0].percent))
        : "...";

  chrome.action.setBadgeText({ text: texto });
  chrome.action.setBadgeBackgroundColor({ color: "#C20000" });
  chrome.action.setBadgeTextColor({ color: "#FFFFFF" });

  // O detalhe inteiro cabe no tooltip, que e onde da para le-lo.
  chrome.action.setTitle({
    title: ativos
      .map((j) => `${Math.round(j.percent ?? 0)}% — ${j.title || "baixando..."}`)
      .join("\n"),
  });
}

async function avisarTerminados(jobs, pasta) {
  const { avisados = [] } = await chrome.storage.session.get("avisados");
  const jaAvisados = new Set(avisados);
  const agora = Date.now() / 1000;

  for (const job of jobs) {
    if (ativo(job) || jaAvisados.has(job.id)) continue;
    if (agora - (job.finalizado ?? 0) > JANELA_AVISO_S) continue;

    jaAvisados.add(job.id);
    const pronto = job.status === "done";
    chrome.notifications.create(`ytdl-${job.id}`, {
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      title: pronto ? "Download concluído" : "O download falhou",
      message: pronto
        ? `${job.title || "Vídeo"}\nSalvo em ${pasta}`
        : (job.error || "erro desconhecido").split("\n")[0],
    });
  }

  await chrome.storage.session.set({ avisados: [...jaAvisados] });
}

async function acompanhar() {
  if (rodando) return;
  rodando = true;
  try {
    while (true) {
      let dados;
      try {
        dados = await buscarJobs();
      } catch {
        pintarBadge([]); // servidor desligado: badge limpo em vez de travado
        break;
      }

      const jobs = dados.jobs ?? [];
      const ativos = jobs.filter(ativo);
      pintarBadge(ativos);
      await avisarTerminados(jobs, dados.pasta ?? "");

      // Uma volta acontece sempre, mesmo sem nada ativo: pode haver download
      // que terminou enquanto este worker estava dormindo.
      if (!ativos.length) break;
      await new Promise((r) => setTimeout(r, INTERVALO_MS));
    }
  } finally {
    rodando = false;
  }
}

// A popup avisa quando dispara um download. Os outros tres gatilhos cobrem o
// Chrome reiniciando o worker com download em andamento - o fetch de 1 em 1
// segundo segura o worker vivo, mas se ele cair mesmo assim, a proxima
// inicializacao reencontra o job pelo /jobs.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.tipo === "acompanhar") acompanhar();
});
chrome.runtime.onStartup.addListener(acompanhar);
chrome.runtime.onInstalled.addListener(acompanhar);
acompanhar();
